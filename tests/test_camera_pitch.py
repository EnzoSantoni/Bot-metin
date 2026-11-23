"""Angulo vertical de la camara: detector de "mira el piso" (bot/vision/pitch.py + bot/camera_pitch.py), reinicio con teclas
(G, toques de T, una rueda al final), cuando lo hace el bot y `calibrate.py camara` (mide G y es guiado). Todo simulado (nada de
input real)."""
import ctypes
import copy
import json
import logging
from pathlib import Path

import cv2
import numpy as np
import pytest

import bot.input.base as base
from bot import calibration as cal
from bot.bot import State, _Explore
from bot.camera_pitch import CameraPitch, g_arrival, looks_at_floor, small_gray
from bot.config import DEFAULTS, OBSOLETE_KEYS, ConfigError, load_config, validate_config
from bot.control import BotControl, PanicStop
from bot.geometry import Rect
from bot.input.fake import FakeBackend
from bot.input.game_input import GameInput
from bot.vision.monsters import MonsterLabel
from bot.vision.pitch import draw_horizon, measure_horizon
from bot.vision.screen_mask import ScreenMask
from test_bot_states import H, OPEN, PLAYER, W, Harness, make_cfg, metin, perc
from test_calibration import ScriptedWindow, raw, tmp_cfg  # noqa: F401  (tmp_cfg es una fixture)
from test_game_input import FakeKeyboard, make

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
DATA = Path(__file__).resolve().parent / "data"


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


def hsv_bgr(h, s, v):
    return tuple(int(c) for c in cv2.cvtColor(np.uint8([[[h, s, v]]]), cv2.COLOR_HSV2BGR)[0, 0])


SKY, FLOOR = hsv_bgr(20, 8, 55), hsv_bgr(117, 60, 70)


def synth(horizon_frac, h=H, w=W, floor=FLOOR, sky=SKY):
    """Cielo gris desaturado arriba y piso azul uniforme desde la fila horizon_frac * alto."""
    img = np.zeros((h, w, 3), np.uint8)
    row = int(round(horizon_frac * h))
    img[:row] = sky
    img[row:] = floor
    return img


def cfg():
    return copy.deepcopy(DEFAULTS)


# ------------------------------------------------------------------ medicion
@pytest.mark.parametrize("frac", [0.1, 0.2, 0.3, 0.4])
def test_horizonte_sintetico(frac):
    r = measure_horizon(synth(frac), cfg(), OPEN, PLAYER)
    assert r is not None and r.confidence == 1.0 and not r.saturated
    assert abs(r.horizon_frac - frac) < 0.01


def test_horizonte_mas_chico_cuanto_mas_inclinada_hacia_el_piso():
    vals = [measure_horizon(synth(f), cfg(), OPEN, PLAYER).horizon_frac for f in (0.4, 0.3, 0.2, 0.1)]
    assert vals == sorted(vals, reverse=True)


def test_todo_piso_es_cero_con_confianza_media_y_saturado():
    r = measure_horizon(synth(0.0), cfg(), OPEN, PLAYER)
    assert r.horizon_frac == 0.0 and r.saturated and r.confidence == 0.5


def test_sin_piso_no_hay_confianza():
    img = np.zeros((H, W, 3), np.uint8)   # negro: no hay muestra de piso con color
    r = measure_horizon(img, cfg(), OPEN, PLAYER)
    assert r is None or r.confidence == 0.0
    noise = np.random.default_rng(0).integers(0, 255, (H, W, 3), dtype=np.uint8)
    r = measure_horizon(noise, cfg(), OPEN, PLAYER)
    assert r is None or r.confidence == 0.0


def test_ignora_personaje_etiquetas_y_ui():
    img = synth(0.25)
    img[600:680, 700:760] = (30, 60, 200)    # un monstruo rojo angosto a la izquierda del personaje
    img[400:430, 1000:1100] = (0, 200, 255)  # una etiqueta de texto
    mask = np.zeros((H, W), np.uint8)
    mask[700:760, 800:1000] = 255            # una ventana de UI debajo
    r = measure_horizon(img, cfg(), ScreenMask([], mask), PLAYER)
    assert abs(r.horizon_frac - 0.25) < 0.01 and r.confidence >= 0.9


def test_dibuja_la_linea_del_horizonte():
    img = synth(0.2)
    out = draw_horizon(img, measure_horizon(img, cfg(), OPEN, PLAYER), 0.2, 0.04)
    assert out.shape == img.shape and (out != img).any()


@pytest.mark.parametrize("name,lo,hi", [
    ("real_tres_metin_hover", 0.12, 0.20), ("real_metin_lejos_1", 0.15, 0.22),
    ("real_metin_negro_seleccionado", 0.05, 0.14), ("explorando_zona", 0.40, 0.50),
    ("metin_muy_lejos", 0.17, 0.23), ("metin_oscuridad_a_distancia", 0.17, 0.23),
    ("real_rodeado", 0.0, 0.03), ("real_inventario_abierto", 0.0, 0.03)])
def test_capturas_reales_dan_valores_razonables(name, lo, hi):
    path = SAMPLES / f"{name}.png"
    if not path.exists():
        pytest.skip(f"falta {path}")
    img = cv2.imread(str(path))
    c = cfg()
    r = measure_horizon(img, c)
    assert r is not None and r.confidence >= c["camera_pitch"]["min_confidence"]
    assert lo <= r.horizon_frac <= hi, f"{name}: {r.horizon_frac:.3f}"


# ------------------------------------------------------------------ config
def test_config_camera_pitch_por_defecto_y_validacion():
    c = cfg()
    validate_config(c)
    p = c["camera_pitch"]
    assert p["enabled"] is True and p["zoom_out_clicks"] == 20 and p["zoom_out_sign"] == 1
    assert (p["g_hold_s"], p["g_margin_s"], p["t_taps"], p["t_tap_ms"], p["t_gap_ms"]) == (3.0, 0.25, 4, 120, 150)
    assert p["step_pause_s"] == 0.15
    assert c["controls"]["camera_top"] == "g" and c["controls"]["camera_flat"] == "t"
    for old in ("ref_horizon_frac", "tolerance", "limit", "to_limit_px", "from_limit_px", "max_drag_px", "drag_s",
                "step_px", "calib_max_px"):
        assert old not in p and ("camera_pitch", old) in OBSOLETE_KEYS
    for key, bad in (("enabled", "si"), ("zoom_out_sign", 0), ("zoom_out_sign", 2), ("g_hold_s", 0), ("t_taps", -1),
                     ("t_taps", 1.5), ("t_tap_ms", 0), ("t_tap_ms", 2000), ("floor_frames", 0), ("min_gap_s", -1),
                     ("side_half_frac", 0.9), ("g_margin_s", -0.1)):
        c = cfg()
        c["camera_pitch"][key] = bad
        with pytest.raises(ConfigError):
            validate_config(c)


def test_config_vieja_con_claves_del_metodo_de_arrastre_se_ignora_con_aviso(tmp_path, caplog):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"camera_pitch": {"ref_horizon_frac": 0.2, "from_limit_px": 80, "limit": "arriba",
                                                 "t_taps": 6}}), encoding="utf-8")
    caplog.set_level(logging.WARNING)
    c = load_config(path)
    assert c["camera_pitch"]["t_taps"] == 6 and "from_limit_px" not in c["camera_pitch"]
    assert any("camera_pitch.from_limit_px esta obsoleta" in r.getMessage() for r in caplog.records)


# ------------------------------------------------------------------ detector de "mira el piso" (capturas reales)
def real(name):
    path = DATA / name
    assert path.exists(), f"falta {path}"
    return cv2.imread(str(path))


@pytest.mark.parametrize("name", ["cam_08_montana_camara_subida.png", "cam_09_montana_despues_de_alejarse.png",
                                  "camara_mira_piso_0048.png"])
def test_detector_marca_piso_en_las_capturas_reales(name):
    assert looks_at_floor(real(name), cfg()) is True


@pytest.mark.parametrize("name", ["cam_01_plano_normal.png", "cam_02_plano_tope_horizontal.png",
                                  "cam_04_plano_normal_otra_vez.png", "cam_10_plano_zoom_tope_lejos.png"])
def test_detector_no_marca_piso_con_horizonte_a_la_vista(name):
    assert looks_at_floor(real(name), cfg()) is False


def test_detector_sintetico():
    assert looks_at_floor(synth(0.0), cfg(), OPEN, PLAYER) is True
    assert looks_at_floor(synth(0.3), cfg(), OPEN, PLAYER) is False
    assert looks_at_floor(np.zeros((H, W, 3), np.uint8), cfg(), OPEN, PLAYER) is False   # nada medible: no se toca


def test_observe_pide_dos_capturas_seguidas_y_respeta_el_minimo_entre_reinicios():
    cp = CameraPitch(cfg())
    p = perc()
    floor, ok = synth(0.0), synth(0.3)
    assert [cp.observe(img, p, 100.0) for img in (floor, ok, floor, ok, floor)] == [False] * 5   # nunca dos seguidas
    assert cp.observe(floor, p, 100.0) is True                                                    # dos seguidas: reinicia
    cp.last_reset_t = 100.0
    assert cp.observe(floor, p, 130.0) is False and cp.observe(floor, p, 159.9) is False   # antes de min_gap_s (60)
    assert cp.observe(floor, p, 160.0) is True
    cp.fruitless = 1   # el reinicio anterior no arreglo nada: espera el doble
    assert cp.observe(floor, p, 219.0) is False and cp.observe(floor, p, 220.0) is True
    cp.c["enabled"] = False
    assert cp.observe(floor, p, 1e6) is False


# ------------------------------------------------------------------ reinicio con teclas (G, toques de T, rueda)
def one(img=None, p=None):
    """perceive() falso: siempre la misma percepcion (con una captura negra)."""
    p = p or perc()
    return lambda: (np.zeros((H, W, 3), np.uint8) if img is None else img, p)


def presses(be):
    """[(tecla, segundos apretada)] en orden, con los eventos de rueda intercalados como ("rueda", clics)."""
    out, down = [], {}
    for e in be.events:
        if e[0] == "key_down":
            down[e[1]] = len(out)
            out.append([e[1], None])
        elif e[0] == "key_up" and e[1] in down:
            out[down.pop(e[1])][1] = "ok"
        elif e[0] == "scroll":
            out.append(["rueda", e[1]])
    return [tuple(x) for x in out]


def test_reset_secuencia_g_toques_de_t_y_una_rueda_al_final():
    g, be, _, _ = make()
    c = cfg()
    held, tapped = [], []
    real_hold, real_tap = g.hold, g.tap
    g.hold = lambda action, s: held.append((action, s)) or real_hold(action, s)
    g.tap = lambda action, s: tapped.append((action, s)) or real_tap(action, s)
    cp = CameraPitch(c)
    assert cp.reset(one(), g, lambda s: None, 100.0, "prueba") is True
    seq = presses(be)
    assert seq == [("g", "ok")] + [("t", "ok")] * 4 + [("rueda", 20)]   # G, 4 toques de T y UNA rueda lejos, al final
    assert held == [("camera_top", 3.0)]                                 # G mantenida 3 s
    assert tapped == [("camera_flat", 0.12)] * 4                         # T: toques precisos de 120 ms (nunca mantenida)
    assert len(cp.tap_ms) == 4 and all(119.9 <= v <= 135.0 for v in cp.tap_ms)
    assert not any(e[0] in ("mouse_down", "mouse_up") for e in be.events)   # nada de clics ni arrastres
    assert be.pressed == ([], []) and cp.resets == 1 and cp.last_reset_t == 100.0


def test_reset_usa_el_sentido_de_la_rueda_de_la_config():
    g, be, _, _ = make()
    c = cfg()
    c["camera_pitch"]["zoom_out_sign"] = -1
    CameraPitch(c).reset(one(), g, lambda s: None, 0.0, "x")
    assert [e for e in be.events if e[0] == "scroll"] == [("scroll", -20)]


def test_reset_log(caplog):
    caplog.set_level(logging.INFO)
    g, _, _, _ = make()
    CameraPitch(cfg()).reset(one(), g, lambda s: None, 0.0, "arranque")
    assert any("Camara: reinicio (G 3.00 s, T x4 de 120 ms, rueda +20) [arranque]" in r.getMessage()
               for r in caplog.records)
    assert any(r.getMessage().startswith("Camara: reinicio listo en ") for r in caplog.records)   # cuanto tardo


def test_reset_pausas_entre_toques_fijas_y_cortas():
    """Tiempo del reinicio sin la G: pausas de step_pause_s y t_gap_ms fijos entre toques (sin la espera al azar de
    input.delay_ms despues de cada toque)."""
    g, be, _, _ = make()
    c = cfg()
    waits = []
    CameraPitch(c).reset(one(), g, waits.append, 0.0, "x")
    p = c["camera_pitch"]
    assert waits[:-1] == [p["step_pause_s"]] + [p["t_gap_ms"] / 1000] * 4 + [p["step_pause_s"]]
    assert waits[-1] == c["explore"]["settle_s"]


def test_reset_apagado_no_manda_nada():
    g, be, _, _ = make()
    c = cfg()
    c["camera_pitch"]["enabled"] = False
    assert CameraPitch(c).reset(one(), g, lambda s: None, 0.0, "x") is False and be.events == []


def test_reset_se_corta_si_perceive_da_none():
    """Sin captura para la rueda del final (minimizada, captcha) el reinicio queda cortado: no hay rueda."""
    g, be, _, _ = make()
    assert CameraPitch(cfg()).reset(lambda: None, g, lambda s: None, 0.0, "x") is False
    assert not any(e[0] == "scroll" for e in be.events) and be.pressed == ([], [])


# ------------------------------------------------------------------ medir cuanto tarda G
def cam_frames(arrive_s, fps=30.0, hold_s=1.5, noise=0.0, seed=0):
    """Cuadros simulados de G: la imagen se corre (la camara sube) hasta `arrive_s` y despues queda quieta (con ruido)."""
    rng = np.random.default_rng(seed)
    scene = rng.integers(0, 255, (H, W), dtype=np.uint8)
    blur = cv2.GaussianBlur(scene, (0, 0), 6)

    def frame(shift):
        img = np.roll(blur, int(shift), axis=0)
        if noise:
            img = np.clip(img.astype(np.int16) + rng.normal(0, noise, img.shape), 0, 255).astype(np.uint8)
        return small_gray(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))
    base = [frame(0) for _ in range(6)]
    frames = [(i / fps, frame(min(i / fps, arrive_s) * 300)) for i in range(1, int(hold_s * fps))]
    return base, frames


@pytest.mark.parametrize("arrive", [0.45, 0.8, 1.1])
def test_g_arrival_da_cuando_la_imagen_queda_quieta(arrive):
    base, frames = cam_frames(arrive, noise=2.0)
    got = g_arrival(base, frames)
    assert got is not None and arrive - 0.01 <= got <= arrive + 1 / 30 + 0.01


def test_g_arrival_sin_movimiento_o_sin_llegar_no_mide():
    base, frames = cam_frames(0.0, noise=2.0)      # ya estaba arriba: no se movio
    assert g_arrival(base, frames) is None
    base, frames = cam_frames(5.0, hold_s=1.5)     # se seguia moviendo al soltar G: G no alcanzo
    assert g_arrival(base, frames) is None


def test_reset_suelta_la_tecla_si_se_para_a_mitad():
    g, be, ctl, _ = make()
    real_hold = g.hold

    def hold(action, s):
        if action == "camera_top":
            ctl.panic("prueba")
        return real_hold(action, s)
    g.hold = hold
    with pytest.raises(PanicStop):
        CameraPitch(cfg()).reset(one(), g, lambda s: None, 0.0, "x")
    assert be.pressed == ([], [])


# ------------------------------------------------------------------ cuando reinicia el bot
class SimCam:
    """Camara simulada: G la deja mirando el piso (vista desde arriba); `t_taps` toques de T la bajan al angulo de farmeo
    salvo `keep_floor` (terreno alto)."""

    def __init__(self, floor_at_start=False):
        self.floor, self.keep_floor, self.taps = floor_at_start, False, 0

    def image(self):
        return synth(0.0) if self.floor else synth(0.3)


def with_cam(h, cam):
    """Ventana que captura la camara simulada; registra (estado del bot, hora, accion) de cada G/T."""
    def capture(region=None):
        h.win.last_capture_rect = h.win.client_rect_on_screen()
        return cam.image()
    h.win.capture = capture
    real_hold, real_tap = h.gi.hold, h.gi.tap
    h.cam_states = []

    def tap(action, seconds):
        hold(action, None)
        return real_tap(action, seconds)

    def hold(action, seconds):
        if action in ("camera_top", "camera_flat"):
            h.cam_states.append((h.bot.state, h.clock.t, action))
            if action == "camera_top":
                cam.floor, cam.taps = True, 0
            else:
                cam.taps += 1
                if cam.taps >= int(h.bot.cfg["camera_pitch"]["t_taps"]) and not cam.keep_floor:
                    cam.floor = False
        return True if seconds is None else real_hold(action, seconds)
    h.gi.hold = hold
    h.gi.tap = tap


def pitch_cfg(**over):
    cfg_ = make_cfg(**over)
    cfg_["camera_pitch"].update(enabled=True, g_hold_s=0.05, t_tap_ms=10, t_gap_ms=0, step_pause_s=0.0)
    return cfg_


def g_holds(h):
    return sum(1 for _, _, a in h.cam_states if a == "camera_top")


def scrolls(h):
    return [e for e in h.be.events if e[0] == "scroll"]


def test_no_reinicia_al_arrancar():
    """Enzo deja la camara acomodada antes de empezar: al arrancar no se toca (ni rueda, ni G, ni T)."""
    h = Harness(lambda h, n: perc(), cfg=pitch_cfg())
    with_cam(h, SimCam())
    h.bot.run(max_minutes=0.001)   # corre y termina enseguida por tiempo
    assert scrolls(h) == [] and g_holds(h) == 0 and h.bot.pitch.resets == 0 and h.be.pressed == ([], [])


def test_con_camera_pitch_apagada_no_toca_la_camara_para_nada():
    """enabled false: ni por piso ni de ninguna otra forma: cero rueda, G o T."""
    c = pitch_cfg()
    c["camera_pitch"]["enabled"] = False
    h = Harness(lambda h, n: perc(), cfg=c)
    cam = SimCam(floor_at_start=True)   # aunque mire el piso
    with_cam(h, cam)
    h.bot.run(max_minutes=0.001)                                   # al arrancar
    h.bot.state = State.BUSCAR
    assert h.bot._pitch_reset("metin roto") is False    # despues de cada metin
    h.bot._img = cam.image()
    for _ in range(5):                                             # mirando el piso y sin reinicios recientes
        h.clock.t += 100
        assert h.bot._pitch_search_check(perc(), h.clock.t, _Explore(dir="left")) is False
    assert scrolls(h) == [] and h.cam_states == [] and h.bot.pitch.resets == 0
    assert not any(e[0] == "key_down" and e[1] in ("g", "t") for e in h.be.events)


def break_script(h, n):
    """BUSCAR ve un metin; en ATACAR la vida baja y despues el panel desaparece (roto)."""
    from test_bot_states import target_panel
    if h.bot.state is State.ATACAR:
        h.clock.t += 20.0 if h.bot.stats.metins == 0 and getattr(h, "broke", 0) == 0 and n > 3 else 0.0
        h.broke = getattr(h, "broke", 0) + (1 if n > 3 else 0)
        return perc(panel=target_panel(0.03) if h.broke <= 3 else None)
    if h.bot.state is State.BUSCAR and h.bot.stats.metins == 0:
        return perc(metins=[metin()])
    return perc()


def test_no_reinicia_despues_de_romper_y_recoger():
    h = Harness(break_script, cfg=pitch_cfg())
    with_cam(h, SimCam())
    h.run_until(lambda: h.bot.stats.metins == 1 and State.BUSCAR in h.states()[1:])
    for _ in range(5):
        h.bot.step()
    assert State.RECOGER in h.states() and scrolls(h) == [] and g_holds(h) == 0 and h.bot.pitch.resets == 0


def test_mirar_el_piso_reinicia_y_respeta_60_s_entre_reinicios(caplog):
    caplog.set_level(logging.INFO)
    h = Harness(lambda h, n: perc(), cfg=pitch_cfg(explore={"camera_steps": 100000}))
    cam = SimCam(floor_at_start=True)
    with_cam(h, cam)
    h.run_until(lambda: g_holds(h) >= 1)
    t1, n1 = h.cam_states[0][1], g_holds(h)
    h.run_until(lambda: h.bot.pitch.resets == 1)
    cam.floor = True   # el juego volvio a subir la camara enseguida: no se reinicia antes de 60 s (22:28:08 y 22:28:27)
    while h.clock.t - t1 < 59.5:
        h.bot.step()
    assert g_holds(h) == n1
    h.run_until(lambda: g_holds(h) > n1, limit=6000)
    assert h.cam_states[-1][1] - t1 >= 60.0
    assert all(s is State.EXPLORAR for s, _, _ in h.cam_states)   # solo mientras se busca
    assert all("[mira el piso]" in r.getMessage() for r in caplog.records if "Camara: reinicio (" in r.getMessage())


def test_reinicio_por_piso_que_no_arregla_nada_espera_mas():
    h = Harness(lambda h, n: perc(), cfg=pitch_cfg(explore={"camera_steps": 100000}))
    cam = SimCam(floor_at_start=True)
    cam.keep_floor = True   # terreno alto: tras reiniciar sigue todo piso
    with_cam(h, cam)
    h.run_until(lambda: h.bot.pitch.resets >= 1)
    assert h.bot.pitch.fruitless == 1 and h.bot.pitch.floor_gap_s() == 120.0


def test_sin_reinicio_periodico():
    """Con la camara bien (horizonte a la vista) nunca reinicia, por mas vueltas y tiempo que pasen."""
    h = Harness(lambda h, n: perc(), cfg=pitch_cfg())
    cam = SimCam()
    with_cam(h, cam)
    h.win.capture()
    bot, p = h.bot, perc()
    bot._img = cam.image()
    for _ in range(5):
        h.clock.t += 120
        assert bot._pitch_search_check(p, h.clock.t, _Explore(dir="left")) is False
    assert g_holds(h) == 0 and bot.pitch.resets == 0


# ------------------------------------------------------------------ calibrate.py camara (guiado por Enzo)
class CamWindow(ScriptedWindow):
    """Ventana falsa que registra la captura (las acciones con mouse exigen una captura previa del mismo rect)."""

    def capture(self, region=None):
        self.last_capture_rect = self.client_rect_on_screen()
        return np.zeros((H, W, 3), np.uint8)


def cam_session(tmp_cfg, monkeypatch, answers):
    out, answers = [], list(answers)

    def ask(prompt):
        out.append(prompt)
        return answers.pop(0) if answers else "s"
    cfg_ = load_config(tmp_cfg)
    cfg_["input"].update(delay_ms=[0, 0], key_hold_ms=[0, 0], move_settle_ms=[0, 0])
    cfg_["camera_pitch"].update(g_hold_s=0.01, t_tap_ms=5, t_gap_ms=0, step_pause_s=0.0)
    be = FakeBackend()
    win = CamWindow([np.zeros((H, W, 3), np.uint8)])
    ctl = BotControl(cfg_, keyboard=FakeKeyboard(), cursor_pos=lambda: (9999, 9999), exit_fn=lambda c: None)
    ticks = []
    s = cal.Session(cfg=cfg_, config_path=str(tmp_cfg), yes=False, window=win, say=out.append, ask=ask, sleep=lambda x: None,
                    gi=GameInput(win, be, ctl, cfg_), countdown=lambda: ticks.append(1))
    s.out, s.be, s.ticks = out, be, ticks
    monkeypatch.setattr(cal, "perceive", lambda img, c, parts=None: perc())
    return s


def key_downs(be, key):
    return sum(1 for e in be.events if e == ("key_down", key))


def test_camara_guiada_mide_g_y_guarda_g_hold_s_con_margen(tmp_cfg, monkeypatch):
    """La primera G (desde el angulo de farmeo) se mide: g_hold_s = lo medido + g_margin_s, guardado junto con t_taps; los 3
    reinicios de comprobacion ya usan la G nueva y dicen cuanto tardan."""
    s = cam_session(tmp_cfg, monkeypatch, ["s", "", "s", "s"])
    monkeypatch.setattr(cal, "g_arrival", lambda base, frames: 0.83)
    held = []
    real_hold = s.gi.hold
    s.gi.hold = lambda action, sec: held.append((action, sec)) or real_hold(action, sec)
    assert cal.cmd_camara(s) == 0
    assert raw(tmp_cfg)["camera_pitch"]["g_hold_s"] == 1.08 and raw(tmp_cfg)["camera_pitch"]["t_taps"] == 1
    assert held[0] == ("camera_top", 0.01) and held[1:] == [("camera_top", 1.08)] * 3
    assert any("G tarda 0.83 s en llegar a la vista desde arriba" in line for line in s.out)
    assert sum("reinicio" in line and " s, captura en " in line for line in s.out) == 3
    assert any(line.startswith("El reinicio tarda ") for line in s.out)


def test_camara_guiada_sin_poder_medir_g_deja_g_hold_s(tmp_cfg, monkeypatch):
    s = cam_session(tmp_cfg, monkeypatch, ["s", "s", "s"])   # capturas negras: no se ve la camara moverse
    assert cal.cmd_camara(s) == 0
    assert "g_hold_s" not in raw(tmp_cfg).get("camera_pitch", {})
    assert any("No pude medir cuanto tarda G" in line for line in s.out)


def test_camara_guiada_toques_uno_menos_listo_guarda_t_taps_y_comprueba_3_veces(tmp_cfg, tmp_path, monkeypatch):
    original = tmp_cfg.read_text(encoding="utf-8")
    # Empiezo, Enter, Enter, Enter (3 toques), b (uno menos: rehace G y toca 2), s (listo), s (guardar)
    s = cam_session(tmp_cfg, monkeypatch, ["s", "", "", "", "b", "s", "s"])
    assert cal.cmd_camara(s) == 0
    assert raw(tmp_cfg)["camera_pitch"]["t_taps"] == 2 and raw(tmp_cfg)["mi_clave_extra"] == {"a": 1}
    assert tmp_cfg.with_name("config.json.bak").read_text(encoding="utf-8") == original
    # G: arriba al empezar (medida) + al rehacer por "b" + 3 reinicios de comprobacion; T: 3 + 2 (rehacer) + 3 x 2 (comprobacion)
    assert key_downs(s.be, "g") == 1 + 1 + 3 and key_downs(s.be, "t") == 3 + 2 + 3 * 2
    # una rueda al final de la parte guiada y una por cada reinicio de comprobacion (ninguna antes de la G)
    assert sum(1 for e in s.be.events if e[0] == "scroll") == 1 + 3 and s.be.events[0] == ("key_down", "g")
    shots = sorted(f.name for f in (tmp_path / "debug" / "calibrate_camara").glob("reinicio_*.png"))
    assert shots == ["reinicio_1.png", "reinicio_2.png", "reinicio_3.png"]
    assert s.ticks == [1] and s.be.pressed == ([], []) and not any(e[0] == "mouse_down" for e in s.be.events)
    assert any("Asi? (Enter = un toque mas de T, b = uno menos, s = listo)" in line for line in s.out)


def test_camara_guiada_no_manda_nada_si_se_rechaza(tmp_cfg, monkeypatch):
    original = tmp_cfg.read_text(encoding="utf-8")
    s = cam_session(tmp_cfg, monkeypatch, ["n"])
    assert cal.cmd_camara(s) == 1
    assert s.be.events == [] and s.ticks == [] and tmp_cfg.read_text(encoding="utf-8") == original


def test_calibrate_py_tiene_el_subcomando_camara_y_no_camara_tope():
    import calibrate
    args = calibrate.build_parser().parse_args(["camara"])
    assert args.cmd == "camara" and "camara" in calibrate.NEEDS_GAME and "camara-tope" not in calibrate.NEEDS_GAME
    with pytest.raises(SystemExit):
        calibrate.build_parser().parse_args(["camara-tope"])


# ---------------------------------------------------------------- sentido de la rueda (Guabina: hacia adelante aleja)
def test_send_wheel_positivo_es_hacia_adelante(monkeypatch):
    """La cadena real: gi.zoom(+N) -> backend.scroll(+N) -> send_wheel(+N) = N eventos con delta +WHEEL_DELTA (adelante)."""
    import bot.input.pydirect as pd
    sent = []

    class _U32:
        def SendInput(self, n, ref, size):
            inp = ref._obj
            sent.append(ctypes.c_int32(inp.mi.mouseData).value)
            return 1

    monkeypatch.setattr(pd, "_user32", lambda: _U32())
    pd.send_wheel(3)
    assert sent == [pd.WHEEL_DELTA] * 3
    sent.clear()
    pd.send_wheel(-2)
    assert sent == [-pd.WHEEL_DELTA] * 2


# ------------------------------------------------------------------ log: una linea por reinicio y estado al arrancar
@pytest.mark.parametrize("enabled", [True, False])
def test_al_arrancar_dice_si_el_reinicio_esta_encendido_o_apagado(caplog, enabled):
    caplog.set_level(logging.INFO)
    c = pitch_cfg()
    c["camera_pitch"]["enabled"] = enabled
    h = Harness(lambda h, n: perc(), cfg=c)
    with_cam(h, SimCam())
    h.bot.run(max_minutes=0.001)
    word = "ENCENDIDO" if enabled else "APAGADO"
    rec = [r for r in caplog.records if f"Camara: reinicio {word}" in r.getMessage()]
    assert len(rec) == 1 and rec[0].levelno == (logging.INFO if enabled else logging.WARNING)
    assert not any("[arranque]" in r.getMessage() for r in caplog.records)   # nunca reinicia al arrancar


def test_reinicio_por_piso_dice_el_motivo(caplog):
    caplog.set_level(logging.INFO)
    h = Harness(lambda h, n: perc(), cfg=pitch_cfg(explore={"camera_steps": 100000}))
    with_cam(h, SimCam(floor_at_start=True))
    h.bot.pitch.last_reset_t = h.clock.t - 1000
    h.run_until(lambda: h.bot.pitch.resets >= 1)
    assert any("[mira el piso]" in r.getMessage() for r in caplog.records)


def test_reinicio_cortado_queda_en_el_log(caplog):
    caplog.set_level(logging.INFO)
    g, _, _, _ = make()
    assert CameraPitch(cfg()).reset(lambda: None, g, lambda s: None, 0.0, "metin roto") is False
    assert any("Camara: reinicio cortado" in r.getMessage() and "[metin roto]" in r.getMessage() for r in caplog.records)
