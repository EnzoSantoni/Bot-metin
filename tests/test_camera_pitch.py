"""Angulo vertical de la camara: detector de "mira el piso" (bot/vision/pitch.py + bot/camera_pitch.py), reinicio con teclas
(rueda, G, toques de T, rueda), cuando lo hace el bot y `calibrate.py camara` (guiado). Todo simulado (nada de input real)."""
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
from bot.camera_pitch import CameraPitch, looks_at_floor
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
    assert (p["g_hold_s"], p["t_taps"], p["t_tap_ms"], p["t_gap_ms"]) == (3.0, 4, 120, 200)
    assert c["controls"]["camera_top"] == "g" and c["controls"]["camera_flat"] == "t"
    for old in ("ref_horizon_frac", "tolerance", "limit", "to_limit_px", "from_limit_px", "max_drag_px", "drag_s",
                "step_px", "calib_max_px"):
        assert old not in p and ("camera_pitch", old) in OBSOLETE_KEYS
    for key, bad in (("enabled", "si"), ("zoom_out_sign", 0), ("zoom_out_sign", 2), ("g_hold_s", 0), ("t_taps", -1),
                     ("t_taps", 1.5), ("t_tap_ms", 0), ("t_tap_ms", 2000), ("floor_frames", 0), ("every_s", -1),
                     ("side_half_frac", 0.9)):
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
    assert cp.observe(floor, p, 105.0) is False and cp.observe(floor, p, 107.9) is False   # antes de min_gap_s (8)
    assert cp.observe(floor, p, 108.0) is True
    cp.fruitless = 1   # el reinicio anterior no arreglo nada: espera el doble
    assert cp.observe(floor, p, 115.0) is False and cp.observe(floor, p, 116.0) is True
    cp.c["enabled"] = False
    assert cp.observe(floor, p, 1e6) is False


# ------------------------------------------------------------------ reinicio con teclas (rueda, G, toques de T, rueda)
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


def test_reset_secuencia_rueda_g_toques_de_t_y_rueda():
    g, be, _, _ = make()
    c = cfg()
    held = []
    real_hold = g.hold
    g.hold = lambda action, s: held.append((action, s)) or real_hold(action, s)
    cp = CameraPitch(c)
    assert cp.reset(one(), g, lambda s: None, 100.0, "prueba") is True
    seq = presses(be)
    assert seq[0] == ("rueda", 20) and seq[-1] == ("rueda", 20)        # rueda lejos al principio y al final
    assert seq[1:-1] == [("g", "ok")] + [("t", "ok")] * 4               # G una vez y 4 toques de T
    assert held == [("camera_top", 3.0)] + [("camera_flat", 0.12)] * 4   # G mantenida 3 s; T toques de 120 ms (nunca larga)
    assert not any(e[0] in ("mouse_down", "mouse_up") for e in be.events)   # nada de clics ni arrastres
    assert be.pressed == ([], []) and cp.resets == 1 and cp.last_reset_t == 100.0


def test_reset_usa_el_sentido_de_la_rueda_de_la_config():
    g, be, _, _ = make()
    c = cfg()
    c["camera_pitch"]["zoom_out_sign"] = -1
    CameraPitch(c).reset(one(), g, lambda s: None, 0.0, "x")
    assert [e for e in be.events if e[0] == "scroll"] == [("scroll", -20), ("scroll", -20)]


def test_reset_log(caplog):
    caplog.set_level(logging.INFO)
    g, _, _, _ = make()
    CameraPitch(cfg()).reset(one(), g, lambda s: None, 0.0, "arranque")
    assert any("Camara: reinicio (rueda +20, G 3.0 s, T x4 de 120 ms, rueda +20) [arranque]" in r.getMessage()
               for r in caplog.records)


def test_reset_apagado_no_manda_nada():
    g, be, _, _ = make()
    c = cfg()
    c["camera_pitch"]["enabled"] = False
    assert CameraPitch(c).reset(one(), g, lambda s: None, 0.0, "x") is False and be.events == []


def test_reset_se_corta_si_perceive_da_none():
    g, be, _, _ = make()
    assert CameraPitch(cfg()).reset(lambda: None, g, lambda s: None, 0.0, "x") is False
    assert be.events == []


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
    real_hold = h.gi.hold
    h.cam_states = []

    def hold(action, seconds):
        if action in ("camera_top", "camera_flat"):
            h.cam_states.append((h.bot.state, h.clock.t, action))
            if action == "camera_top":
                cam.floor, cam.taps = True, 0
            else:
                cam.taps += 1
                if cam.taps >= int(h.bot.cfg["camera_pitch"]["t_taps"]) and not cam.keep_floor:
                    cam.floor = False
        return real_hold(action, seconds)
    h.gi.hold = hold


def pitch_cfg(**over):
    cfg_ = make_cfg(**over)
    cfg_["camera_pitch"].update(enabled=True, g_hold_s=0.05, t_tap_ms=10, t_gap_ms=0, step_pause_s=0.0)
    return cfg_


def g_holds(h):
    return sum(1 for _, _, a in h.cam_states if a == "camera_top")


def scrolls(h):
    return [e for e in h.be.events if e[0] == "scroll"]


def test_reinicia_al_arrancar_despues_de_la_cuenta_regresiva():
    h = Harness(lambda h, n: perc(), cfg=pitch_cfg())
    with_cam(h, SimCam())
    h.bot.run(max_minutes=0.001)   # corre y termina enseguida por tiempo
    assert scrolls(h)[:2] == [("scroll", 20), ("scroll", 20)]
    assert h.cam_states and h.cam_states[0][0] is State.BUSCAR and g_holds(h) == 1
    assert h.bot.pitch.resets == 1 and h.be.pressed == ([], [])


def test_con_camera_pitch_apagada_no_toca_la_camara_para_nada():
    """enabled false: ni al arrancar, ni despues de cada metin, ni por piso, ni periodico: cero rueda, G o T."""
    c = pitch_cfg()
    c["camera_pitch"]["enabled"] = False
    h = Harness(lambda h, n: perc(), cfg=c)
    cam = SimCam(floor_at_start=True)   # aunque mire el piso
    with_cam(h, cam)
    h.bot.run(max_minutes=0.001)                                   # al arrancar
    h.bot.state = State.BUSCAR
    assert h.bot._pitch_reset("metin roto y recogido") is False    # despues de cada metin
    h.bot._img = cam.image()
    for _ in range(5):                                             # mirando el piso y con every_s vencido
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


def test_reinicia_despues_de_romper_y_recoger_y_nunca_atacando_ni_recogiendo(caplog):
    caplog.set_level(logging.INFO)
    h = Harness(break_script, cfg=pitch_cfg(camera_pitch={"every_s": 1e9}))
    with_cam(h, SimCam())
    h.bot.pitch.last_reset_t = h.clock.t   # ya reinicio al arrancar: aqui solo interesa el de despues de recoger
    h.run_until(lambda: h.bot.stats.metins == 1 and g_holds(h) >= 1)
    assert State.RECOGER in h.states() and h.be.pressed == ([], [])
    assert scrolls(h) == [("scroll", 20), ("scroll", 20)]
    assert any("[metin roto y recogido]" in r.getMessage() for r in caplog.records)
    assert all(s not in (State.ATACAR, State.RECOGER, State.CAPTCHA) for s, _, _ in h.cam_states)


def test_mirar_el_piso_reinicia_y_respeta_el_minimo_entre_reinicios():
    h = Harness(lambda h, n: perc(), cfg=pitch_cfg(explore={"camera_steps": 100000}, camera_pitch={"every_s": 1e9}))
    cam = SimCam(floor_at_start=True)
    with_cam(h, cam)
    h.bot.pitch.last_reset_t = h.clock.t - 1000   # sin reinicios recientes
    h.run_until(lambda: g_holds(h) >= 1)
    t1, n1 = h.cam_states[0][1], g_holds(h)
    h.run_until(lambda: h.bot.pitch.resets == 1)
    cam.floor = True   # el juego volvio a subir la camara enseguida: no se reinicia antes de min_gap_s
    while h.clock.t - t1 < 7.5:
        h.bot.step()
    assert g_holds(h) == n1
    h.run_until(lambda: g_holds(h) > n1, limit=3000)
    assert h.cam_states[-1][1] - t1 >= 8.0
    assert all(s is State.EXPLORAR for s, _, _ in h.cam_states)   # solo mientras se busca


def test_reinicio_por_piso_que_no_arregla_nada_espera_mas():
    h = Harness(lambda h, n: perc(), cfg=pitch_cfg(explore={"camera_steps": 100000}, camera_pitch={"every_s": 1e9}))
    cam = SimCam(floor_at_start=True)
    cam.keep_floor = True   # terreno alto: tras reiniciar sigue todo piso
    with_cam(h, cam)
    h.bot.pitch.last_reset_t = h.clock.t - 1000
    h.run_until(lambda: h.bot.pitch.resets >= 1)
    assert h.bot.pitch.fruitless == 1 and h.bot.pitch.floor_gap_s() == 16.0


def test_periodico_solo_al_empezar_una_vuelta_y_tras_every_s():
    h = Harness(lambda h, n: perc(), cfg=pitch_cfg())
    cam = SimCam()
    with_cam(h, cam)
    h.win.capture()
    bot, p = h.bot, perc()
    bot._img = cam.image()
    assert bot._pitch_search_check(p, h.clock.t, _Explore(dir="left")) is True     # nunca reinicio: toca
    n = g_holds(h)
    assert bot._pitch_search_check(p, h.clock.t, _Explore(dir="left")) is False    # recien reiniciado
    h.clock.t += 61
    assert bot._pitch_search_check(p, h.clock.t, _Explore(dir="left", steps=1)) is False   # a mitad de vuelta no
    assert bot._pitch_search_check(p, h.clock.t, _Explore(dir="left")) is True
    assert g_holds(h) > n


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


def test_camara_guiada_toques_uno_menos_listo_guarda_t_taps_y_comprueba_3_veces(tmp_cfg, tmp_path, monkeypatch):
    original = tmp_cfg.read_text(encoding="utf-8")
    # Empiezo, Enter, Enter, Enter (3 toques), b (uno menos: rehace G y toca 2), s (listo), s (guardar)
    s = cam_session(tmp_cfg, monkeypatch, ["s", "", "", "", "b", "s", "s"])
    assert cal.cmd_camara(s) == 0
    assert raw(tmp_cfg)["camera_pitch"]["t_taps"] == 2 and raw(tmp_cfg)["mi_clave_extra"] == {"a": 1}
    assert tmp_cfg.with_name("config.json.bak").read_text(encoding="utf-8") == original
    # G: arriba al empezar + al rehacer por "b" + 3 reinicios de comprobacion; T: 3 + 2 (rehacer) + 3 x 2 (comprobacion)
    assert key_downs(s.be, "g") == 1 + 1 + 3 and key_downs(s.be, "t") == 3 + 2 + 3 * 2
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
