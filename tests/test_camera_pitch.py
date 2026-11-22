"""Angulo vertical de la camara: detector de "mira el piso" (bot/vision/pitch.py + bot/camera_pitch.py), reinicio desde el tope
(rueda + arrastres del clic derecho), cuando lo hace el bot, GameInput.drag y `calibrate.py camara-tope`. Todo simulado (nada de
input real)."""
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
from bot.camera_pitch import CameraPitch, looks_at_floor, split_px
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
from test_game_input import FakeKeyboard, make, open_mask

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
DATA = Path(__file__).resolve().parent / "data"
ORIGIN = (8, 31)   # esquina del cliente en pantalla (FakeWindow)


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
    assert p["enabled"] is True and p["zoom_out_clicks"] == 20 and p["zoom_out_sign"] == -1
    assert (p["to_limit_px"], p["from_limit_px"], p["max_drag_px"], p["min_drag_px"]) == (700, 60, 200, 20)
    assert (p["every_s"], p["floor_frames"], p["min_gap_s"], p["step_px"], p["calib_max_px"]) == (60.0, 2, 8.0, 15, 300)
    for old in ("ref_horizon_frac", "tolerance", "gain_px_per_frac", "max_steps", "gain_ema"):
        assert old not in p and ("camera_pitch", old) in OBSOLETE_KEYS
    for key, bad in (("enabled", "si"), ("zoom_out_sign", 0), ("zoom_out_sign", 2), ("to_limit_px", 0), ("from_limit_px", 0),
                     ("min_drag_px", 10), ("max_drag_px", 30), ("to_limit_px", 10), ("floor_frames", 0), ("every_s", -1),
                     ("side_half_frac", 0.9)):
        c = cfg()
        c["camera_pitch"][key] = bad
        with pytest.raises(ConfigError):
            validate_config(c)


def test_config_vieja_con_claves_obsoletas_se_ignora_con_aviso(tmp_path, caplog):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"camera_pitch": {"ref_horizon_frac": 0.2, "tolerance": 0.04, "from_limit_px": 80}}),
                    encoding="utf-8")
    caplog.set_level(logging.WARNING)
    c = load_config(path)
    assert c["camera_pitch"]["from_limit_px"] == 80 and "ref_horizon_frac" not in c["camera_pitch"]
    assert any("camera_pitch.ref_horizon_frac esta obsoleta" in r.getMessage() for r in caplog.records)


def test_split_px_parejo_y_exacto():
    assert split_px(700, 200) == [175, 175, 175, 175]
    assert split_px(60, 200) == [60]
    assert sum(split_px(451, 200)) == 451 and max(split_px(451, 200)) <= 200


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


# ------------------------------------------------------------------ reinicio (secuencia de eventos)
def drags_of(be):
    """Arrastres con el clic derecho: [(inicio cliente, dy)] en orden."""
    out, start, last, down = [], None, None, False
    for e in be.events:
        if e[0] == "move_to":
            last = (e[1] - ORIGIN[0], e[2] - ORIGIN[1])
            if not down:
                start = last
        elif e[0] == "mouse_down":
            assert e[1] == "right"
            down = True
        elif e[0] == "mouse_up":
            out.append((start, last[1] - start[1]))
            down = False
    return out


def one(img=None, p=None):
    """perceive() falso: siempre la misma percepcion (con una captura negra)."""
    p = p or perc()
    return lambda: (np.zeros((H, W, 3), np.uint8) if img is None else img, p)


def fast_cfg(**over):
    c = cfg()
    c["camera_pitch"].update(drag_s=0.0, **over)
    return c


def test_reset_secuencia_rueda_arriba_y_abajo():
    g, be, _, _ = make()
    c = fast_cfg()
    cp = CameraPitch(c)
    sleeps = []
    assert cp.reset(one(), g, sleeps.append, 100.0, "prueba") is True
    assert be.events[0][0] == "move_to" and be.events[1] == ("scroll", -20)   # primero la rueda (zoom lejos, sentido -1)
    d = drags_of(be)
    ups, downs = [dy for _, dy in d if dy < 0], [dy for _, dy in d if dy > 0]
    assert sum(ups) == -700 and all(dy <= -20 and abs(dy) <= 200 for dy in ups)
    assert downs == [60]
    assert [dy < 0 for _, dy in d] == [True] * len(ups) + [False]   # primero todo hacia arriba, al final hacia abajo
    assert all(abs(dy) >= 20 for _, dy in d)                         # nunca un clic derecho sin mover
    assert be.pressed == ([], []) and cp.resets == 1 and cp.last_reset_t == 100.0
    assert sleeps[-1] == c["explore"]["settle_s"]                    # pausa corta al final


def test_reset_log(caplog):
    caplog.set_level(logging.INFO)
    g, _, _, _ = make()
    CameraPitch(fast_cfg()).reset(one(), g, lambda s: None, 0.0, "arranque")
    assert any("Camara: reinicio desde el tope (zoom lejos, arriba 700 px, abajo 60 px)" in r.getMessage()
               for r in caplog.records)


def test_reset_abajo_menor_al_minimo_baja_mas_y_sube_neto_igual():
    g, be, _, _ = make()
    CameraPitch(fast_cfg(from_limit_px=15)).reset(one(), g, lambda s: None, 0.0, "x")
    d = [dy for _, dy in drags_of(be)]
    assert d[-2:] == [35, -20] and sum(d) == -700 + 15 and all(abs(x) >= 20 for x in d)


def test_reset_puntos_libres_de_monstruos_etiquetas_y_ui():
    cp = CameraPitch(fast_cfg())
    first = cp.free_point(perc(), -175)
    assert first is not None
    # una etiqueta de metin y un monstruo cerca de los lugares probables, y la barra de abajo prohibida
    p = perc(metins=[metin((first[0], first[1] - 60))])
    p.monsters = [MonsterLabel(Rect(first[0] - 40, first[1] - 200, 80, 12), (first[0], first[1] - 194))]
    mask = np.zeros((H, W), np.uint8)
    mask[880:1057] = 255
    p.mask = ScreenMask([], mask)
    g, be, _, _ = make()
    assert cp.reset(one(p=p), g, lambda s: None, 0.0, "x") is True
    rects = [d.label_rect for d in p.metins] + [m.rect for m in p.monsters]
    for (x, y), dy in drags_of(be):
        for k in (0.0, 0.5, 1.0):
            q = (x, y + int(round(dy * k)))
            gaps = [max(r.x - q[0], 0, q[0] - r.right) ** 2 + max(r.y - q[1], 0, q[1] - r.bottom) ** 2 for r in rects]
            assert p.mask.allows(*q) and min(gaps) >= 140 ** 2, (q, dy)


def test_reset_sin_punto_libre_se_corta_sin_mandar_arrastres(caplog):
    caplog.set_level(logging.WARNING)
    cp = CameraPitch(fast_cfg())
    p = perc()
    p.mask = ScreenMask([], np.full((H, W), 255, np.uint8))   # todo prohibido
    g, be, _, _ = make()
    assert cp.reset(one(p=p), g, lambda s: None, 0.0, "x") is False
    assert not any(e[0] == "mouse_down" for e in be.events) and cp.drags == 0
    assert any("no hay un punto libre" in r.getMessage() for r in caplog.records)


def test_reset_se_corta_si_perceive_da_none():
    g, be, _, _ = make()
    assert CameraPitch(fast_cfg()).reset(lambda: None, g, lambda s: None, 0.0, "x") is False and be.events == []


def test_reset_suelta_el_boton_si_se_corta_a_mitad():
    g, be, ctl, _ = make()
    cp = CameraPitch(fast_cfg())
    orig, n = ctl.check, {"v": 0}

    def check():
        n["v"] += 1
        if any(e == ("mouse_down", "right") for e in be.events) and n["v"] > 14:
            raise PanicStop()
        orig()
    ctl.check = check
    with pytest.raises(PanicStop):
        cp.reset(one(), g, lambda s: None, 0.0, "x")
    assert be.events.count(("mouse_up", "right")) == be.events.count(("mouse_down", "right")) >= 1
    assert be.pressed == ([], [])


# ------------------------------------------------------------------ cuando reinicia el bot
class SimCam:
    """Camara simulada: `floor` = mira el piso. Un arrastre hacia abajo (el final del reinicio) la arregla salvo `keep_floor`."""

    def __init__(self, floor_at_start=False):
        self.floor, self.keep_floor = floor_at_start, False

    def image(self):
        return synth(0.0) if self.floor else synth(0.3)


def with_cam(h, cam):
    """Ventana que captura la camara simulada y registra (estado del bot, hora, dy) de cada arrastre."""
    def capture(region=None):
        h.win.last_capture_rect = h.win.client_rect_on_screen()
        return cam.image()
    h.win.capture = capture
    real_drag = h.gi.drag
    h.drag_states = []

    def drag(button, x, y, dx, dy, mask, seconds, steps=6):
        h.drag_states.append((h.bot.state, h.clock.t, dy))
        ok = real_drag(button, x, y, dx, dy, mask, seconds, steps)
        if ok and dy > 0 and not cam.keep_floor:
            cam.floor = False
        return ok
    h.gi.drag = drag


def pitch_cfg(**over):
    cfg_ = make_cfg(**over)
    cfg_["camera_pitch"].update(enabled=True, drag_s=0.0)
    return cfg_


def right_drags(h):
    return sum(1 for e in h.be.events if e == ("mouse_down", "right"))


def scrolls(h):
    return [e for e in h.be.events if e[0] == "scroll"]


def test_reinicia_al_arrancar_despues_de_la_cuenta_regresiva():
    h = Harness(lambda h, n: perc(), cfg=pitch_cfg())
    with_cam(h, SimCam())
    h.bot.run(max_minutes=0.001)   # corre y termina enseguida por tiempo
    assert scrolls(h)[0] == ("scroll", -20)
    assert h.drag_states and h.drag_states[0][0] is State.BUSCAR
    assert h.bot.pitch.resets == 1 and h.be.pressed == ([], [])


def test_no_reinicia_con_camera_pitch_apagada():
    h = Harness(lambda h, n: perc(), cfg=make_cfg())
    with_cam(h, SimCam())
    h.bot.run(max_minutes=0.001)
    for _ in range(40):
        h.bot.step()
    assert right_drags(h) == 0 and scrolls(h) == []


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
    h.run_until(lambda: h.bot.stats.metins == 1 and right_drags(h) >= 1)
    assert State.RECOGER in h.states() and ("mouse_down", "left") in h.be.events and h.be.pressed == ([], [])
    assert scrolls(h) == [("scroll", -20)]
    assert any("[metin roto y recogido]" in r.getMessage() for r in caplog.records)
    assert all(s not in (State.ATACAR, State.RECOGER, State.CAPTCHA) for s, _, _ in h.drag_states)


def test_mirar_el_piso_reinicia_y_respeta_el_minimo_entre_reinicios():
    h = Harness(lambda h, n: perc(), cfg=pitch_cfg(explore={"camera_steps": 100000}, camera_pitch={"every_s": 1e9}))
    cam = SimCam(floor_at_start=True)
    with_cam(h, cam)
    h.bot.pitch.last_reset_t = h.clock.t - 1000   # sin reinicios recientes
    h.run_until(lambda: right_drags(h) >= 1)
    t1, n1 = h.drag_states[0][1], right_drags(h)
    assert h.bot.pitch.resets == 1
    cam.floor = True   # el juego volvio a subir la camara enseguida: no se reinicia antes de min_gap_s
    while h.clock.t - t1 < 7.5:
        h.bot.step()
    assert right_drags(h) == n1
    h.run_until(lambda: right_drags(h) > n1, limit=3000)
    assert h.drag_states[-1][1] - t1 >= 8.0
    assert all(s is State.EXPLORAR for s, _, _ in h.drag_states)   # solo mientras se busca


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
    n = right_drags(h)
    assert bot._pitch_search_check(p, h.clock.t, _Explore(dir="left")) is False    # recien reiniciado
    h.clock.t += 61
    assert bot._pitch_search_check(p, h.clock.t, _Explore(dir="left", steps=1)) is False   # a mitad de vuelta no
    assert bot._pitch_search_check(p, h.clock.t, _Explore(dir="left")) is True
    assert right_drags(h) > n


# ------------------------------------------------------------------ calibrate.py camara-tope
class SimGameWindow(ScriptedWindow):
    """Ventana cuya captura sale de la camara simulada: los px netos de arrastre (con tope en 0, el tope horizontal) bajan el
    horizonte de 0.4 a razon de 0.0012 por px."""

    def __init__(self, be, top_frac=0.4, per_px=0.0012):
        super().__init__([np.zeros((10, 10, 3), np.uint8)])
        self.be, self.top_frac, self.per_px = be, top_frac, per_px

    def pos(self):
        pos, start, last, down = 0.0, None, None, False
        for e in self.be.events:
            if e[0] == "move_to":
                last = e[2]
                if not down:
                    start = e[2]
            elif e[0] == "mouse_down":
                down = True
            elif e[0] == "mouse_up":
                pos = max(0.0, pos + last - start)
                down = False
        return pos

    def capture(self, region=None):
        self.last_capture_rect = self.client_rect_on_screen()
        return synth(max(0.0, self.top_frac - self.per_px * self.pos()))


def tope_session(tmp_cfg, tmp_path, monkeypatch, answers=("s",), yes=False, ref_frac=0.1):
    out, answers = [], list(answers)

    def ask(prompt):
        out.append(prompt)
        return answers.pop(0) if answers else "n"
    cfg_ = load_config(tmp_cfg)
    cfg_["input"].update(delay_ms=[0, 0], key_hold_ms=[0, 0], move_settle_ms=[0, 0])
    cfg_["camera_pitch"]["drag_s"] = 0.0
    be = FakeBackend()
    win = SimGameWindow(be)
    ctl = BotControl(cfg_, keyboard=FakeKeyboard(), cursor_pos=lambda: (9999, 9999), exit_fn=lambda c: None)
    ticks = []
    s = cal.Session(cfg=cfg_, config_path=str(tmp_cfg), yes=yes, window=win, say=out.append, ask=ask, sleep=lambda x: None,
                    gi=GameInput(win, be, ctl, cfg_), countdown=lambda: ticks.append(1))
    s.out, s.be, s.ticks = out, be, ticks
    ref = tmp_path / "ref.png"
    cv2.imwrite(str(ref), synth(ref_frac))
    s.ref = str(ref)
    monkeypatch.setattr(cal, "perceive", lambda img, c, parts=None: perc())
    return s


def test_propone_el_paso_cuyo_horizonte_se_parece_a_la_referencia():
    rows = [(0, 0.40), (15, 0.30), (30, 0.21), (45, 0.12), (60, 0.05), (75, None)]
    assert cal.propose_step(rows, 0.11) == 45
    assert cal.propose_step(rows, 0.45) == 0
    assert cal.propose_step([(0, None)], 0.1) is None


def test_camara_tope_pasos_capturas_propuesta_y_guardado_con_bak(tmp_cfg, tmp_path, monkeypatch):
    original = tmp_cfg.read_text(encoding="utf-8")
    s = tope_session(tmp_cfg, tmp_path, monkeypatch, answers=["s", "", "s"])   # empiezo, Enter = el propuesto, guardar
    assert cal.cmd_camara_tope(s, referencia=[s.ref]) == 0
    files = sorted((tmp_path / "debug" / "calibrate_camara_tope").glob("paso_*.png"))
    assert [f.name for f in files][:3] == ["paso_00_000px.png", "paso_01_015px.png", "paso_02_030px.png"]
    assert files[-1].name == "paso_20_300px.png" and len(files) == 21
    # horizonte = 0.4 - 0.0012 * px; referencia 0.1 -> 250 px; de a 15: 255 (0.094) esta mas cerca que 240 (0.112)
    assert raw(tmp_cfg)["camera_pitch"]["from_limit_px"] == 255
    assert raw(tmp_cfg)["mi_clave_extra"] == {"a": 1}
    assert tmp_cfg.with_name("config.json.bak").read_text(encoding="utf-8") == original
    assert s.ticks == [1] and any("propuesto: 255 px" in line for line in s.out)
    ev = s.be.events
    assert [e for e in ev if e[0] == "scroll"][0] == ("scroll", -20)   # primero la rueda
    assert ("mouse_down", "left") not in ev and s.be.pressed == ([], [])
    assert ev.count(("mouse_down", "right")) == ev.count(("mouse_up", "right")) > 20
    assert all(abs(dy) >= 20 for _, dy in drags_of(s.be))              # siempre moviendo el mouse
    assert s.window.pos() == pytest.approx(255, abs=1)                # al final deja la camara en el angulo elegido


def test_camara_tope_eleccion_manual_y_valor_invalido(tmp_cfg, tmp_path, monkeypatch):
    s = tope_session(tmp_cfg, tmp_path, monkeypatch, answers=["s", "120", "s"])
    assert cal.cmd_camara_tope(s, referencia=[s.ref]) == 0
    assert raw(tmp_cfg)["camera_pitch"]["from_limit_px"] == 120
    original = tmp_cfg.read_text(encoding="utf-8")
    s = tope_session(tmp_cfg, tmp_path, monkeypatch, answers=["s", "77", "s"])   # 77 no es uno de los pasos
    assert cal.cmd_camara_tope(s, referencia=[s.ref]) == 1
    assert tmp_cfg.read_text(encoding="utf-8") == original


def test_camara_tope_no_manda_nada_si_se_rechaza(tmp_cfg, tmp_path, monkeypatch):
    original = tmp_cfg.read_text(encoding="utf-8")
    s = tope_session(tmp_cfg, tmp_path, monkeypatch, answers=["n"])
    assert cal.cmd_camara_tope(s, referencia=[s.ref]) == 1
    assert s.be.events == [] and s.ticks == [] and tmp_cfg.read_text(encoding="utf-8") == original


def test_camara_tope_sin_referencia_medible_no_manda_nada(tmp_cfg, tmp_path, monkeypatch):
    s = tope_session(tmp_cfg, tmp_path, monkeypatch)
    assert cal.cmd_camara_tope(s, referencia=[str(tmp_path / "no_existe.png")]) == 1 and s.be.events == []


def test_camara_tope_con_si_no_pregunta(tmp_cfg, tmp_path, monkeypatch):
    s = tope_session(tmp_cfg, tmp_path, monkeypatch, yes=True)
    assert cal.cmd_camara_tope(s, step_px=30, referencia=[s.ref]) == 0
    assert raw(tmp_cfg)["camera_pitch"]["from_limit_px"] == 240 and not s.out[-1].startswith("Cuantos")


def test_calibrate_py_tiene_el_subcomando_camara_tope():
    import calibrate
    args = calibrate.build_parser().parse_args(["camara-tope", "--si", "--paso", "10", "--referencia", "a.png", "b.png"])
    assert args.cmd == "camara-tope" and args.paso == 10 and args.referencia == ["a.png", "b.png"]
    assert "camara-tope" in calibrate.NEEDS_GAME
    with pytest.raises(SystemExit):
        calibrate.build_parser().parse_args(["camara"])   # el subcomando viejo ya no existe


# ------------------------------------------------------------------ GameInput.drag
def test_drag_secuencia_de_eventos_y_suelta():
    g, be, _, _ = make()
    assert g.drag("right", 900, 700, 0, -80, open_mask(), 0.3, steps=4)
    ev = [e for e in be.events if e[0] in ("move_to", "mouse_down", "mouse_up")]
    assert ev[0] == ("move_to", 908, 731)                       # al punto inicial (cliente + origen de la ventana)
    assert ev[1] == ("mouse_down", "right")
    assert [e[0] for e in ev[2:-1]] == ["move_to"] * 4
    assert ev[-2] == ("move_to", 908, 651) and ev[-1] == ("mouse_up", "right")
    ys = [e[2] for e in ev[2:-1]]
    assert ys == sorted(ys, reverse=True)                       # movimiento monotono hacia arriba
    assert be.pressed == ([], [])


def test_drag_siempre_suelta_aunque_se_pare_a_mitad():
    g, be, ctl, _ = make()
    calls = {"n": 0}
    orig = ctl.check

    def check():
        calls["n"] += 1
        if any(e == ("mouse_down", "right") for e in be.events) and calls["n"] > 6:
            raise PanicStop()
        orig()
    ctl.check = check
    with pytest.raises(PanicStop):
        g.drag("right", 900, 700, 0, 100, open_mask(), 0.3, steps=6)
    assert ("mouse_up", "right") in be.events and be.pressed == ([], [])


def test_drag_respeta_la_mascara_en_inicio_y_fin(caplog):
    g, be, _, _ = make()
    assert g.drag("right", 900, 700, 0, 100, open_mask(blocked=Rect(850, 650, 100, 100)), 0.3) is False   # inicio
    assert g.drag("right", 900, 700, 0, 100, open_mask(blocked=Rect(850, 780, 100, 60)), 0.3) is False    # fin
    assert g.drag("right", 900, 700, 0, 100, open_mask(), 0.3) is True
    downs = [e for e in be.events if e[0] == "mouse_down"]
    assert len(downs) == 1   # solo el ultimo mando algo
    with pytest.raises(ValueError):
        g.drag("right", 900, 700, 0, 100, None, 0.3)
    assert g.drag("right", 900, 700, 0, 5000, open_mask(), 0.3) is False   # el final cae fuera del cliente
