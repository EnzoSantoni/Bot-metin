"""Angulo vertical de la camara: medicion del horizonte (bot/vision/pitch.py), calibrate.py camara, GameInput.drag, aprendizaje
del sentido y la ganancia del arrastre (bot/camera_pitch.py) y cuando corrige el bot. Todo simulado (nada de input real)."""
import json
import copy
import logging
from pathlib import Path

import cv2
import numpy as np
import pytest

import bot.input.base as base
from bot.bot import State
from bot.camera_pitch import CameraPitch
from bot.config import DEFAULTS, ConfigError, validate_config
from bot.control import PanicStop
from bot.geometry import Rect
from bot.vision.metin import MetinDetection
from bot.vision.pitch import draw_horizon, measure_horizon
from bot.vision.screen_mask import ScreenMask
from test_bot_states import H, OPEN, PLAYER, W, Harness, make_cfg, metin, perc
from test_calibration import make_session, raw, tmp_cfg  # noqa: F401  (tmp_cfg es una fixture)
from test_game_input import make, moves, open_mask

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


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


def test_dibuja_la_linea_del_horizonte(tmp_path):
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
    assert c["camera_pitch"]["ref_horizon_frac"] is None and c["camera_pitch"]["enabled"] is True
    assert c["camera_pitch"]["tolerance"] == 0.04 and c["camera_pitch"]["max_drag_px"] == 150
    assert c["camera_pitch"]["max_steps"] == 3 and c["camera_pitch"]["every_s"] == 30
    assert (c["attack"]["scan_after_s"], c["attack"]["scan_steps"], c["attack"]["next_keep_s"]) == (5.0, 12, 60.0)
    assert c["attack"]["scan_enabled"] is True
    for path, bad in ((("camera_pitch", "ref_horizon_frac"), 1.5), (("camera_pitch", "tolerance"), 0),
                      (("camera_pitch", "enabled"), "si"), (("attack", "scan_steps"), 0),
                      (("attack", "scan_enabled"), 1)):
        c = cfg()
        c[path[0]][path[1]] = bad
        with pytest.raises(ConfigError):
            validate_config(c)


# ------------------------------------------------------------------ calibrate.py camara
def test_calibrar_camara_promedia_guarda_con_bak_e_imagen(tmp_cfg):
    original = tmp_cfg.read_text(encoding="utf-8")
    imgs = [synth(f, 1057, 1858) for f in (0.19, 0.20, 0.21)]
    from bot import calibration as cal
    s = make_session(tmp_cfg, imgs, answers=["s"], sleep=lambda x: None)
    s.cfg["camera_pitch"]["calib_samples"] = 3
    assert cal.cmd_camara(s) == 0
    # camera_pitch.ref_horizon_frac es del jugador: se guarda en el perfil del personaje, no en config.json
    assert tmp_cfg.read_text(encoding="utf-8") == original
    prof = tmp_cfg.parent / "profiles" / "personajes" / (s.cfg["_profiles"]["personaje"] + ".json")
    saved = json.loads(prof.read_text(encoding="utf-8"))
    assert abs(saved["camera_pitch"]["ref_horizon_frac"] - 0.2) < 0.01
    assert prof.with_name(prof.name + ".bak").exists()
    assert (tmp_cfg.parent / "debug" / "calibrate_camara.png").exists()
    assert any("Horizonte medido" in line for line in s.out)


def test_calibrar_camara_responder_n_no_guarda(tmp_cfg):
    original = tmp_cfg.read_text(encoding="utf-8")
    from bot import calibration as cal
    s = make_session(tmp_cfg, [synth(0.2, 1057, 1858)], answers=["n"], sleep=lambda x: None)
    assert cal.cmd_camara(s) == 1
    assert tmp_cfg.read_text(encoding="utf-8") == original and not tmp_cfg.with_name("config.json.bak").exists()


def test_calibrar_camara_sin_piso_no_guarda(tmp_cfg):
    original = tmp_cfg.read_text(encoding="utf-8")
    from bot import calibration as cal
    s = make_session(tmp_cfg, [np.zeros((1057, 1858, 3), np.uint8)], yes=True, sleep=lambda x: None)
    assert cal.cmd_camara(s) == 1
    assert tmp_cfg.read_text(encoding="utf-8") == original


def test_calibrate_py_tiene_el_subcomando_camara():
    import calibrate
    args = calibrate.build_parser().parse_args(["camara", "--si"])
    assert args.cmd == "camara" and "camara" in calibrate.NEEDS_GAME


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


# ------------------------------------------------------------------ CameraPitch (aprendizaje y correccion)
class SimCam:
    """Camara simulada: `h` = horizon_frac; un arrastre de dy px lo mueve `s * dy` (s con signo, desconocido para el bot)."""

    def __init__(self, h, s):
        self.h, self.s, self.drags = h, s, []

    def capture(self):
        return synth(self.h)

    def drag(self, button, x, y, dx, dy, mask, seconds, steps=6):
        self.drags.append((button, x, y, dx, dy))
        self.h = max(0.0, self.h + self.s * dy)
        return True


def make_cp(ref=0.2, **over):
    c = cfg()
    c["camera_pitch"].update(ref_horizon_frac=ref, **over)
    return CameraPitch(c), c


def run_correct(cp, cam, p=None, max_steps=None):
    p = p or perc()
    return cp.correct(p, cam.capture(), cam, lambda: cam.capture(), lambda: None, "prueba", max_steps)


def test_sin_referencia_no_hace_nada_y_avisa_una_vez(caplog):
    caplog.set_level(logging.INFO)
    cp, _ = make_cp(ref=None)
    assert cp.active is False and cp.active is False
    msgs = [r.getMessage() for r in caplog.records if "sin calibrar" in r.getMessage()]
    assert msgs == ["Angulo vertical sin calibrar: corre calibrate.py camara"]


def test_corrige_dentro_de_un_arrastre_con_el_signo_inicial():
    cp, _ = make_cp(ref=0.2)
    cam = SimCam(0.30, s=-0.0016)   # error +0.10 -> dy = +60 -> baja el horizonte 0.096
    assert run_correct(cp, cam) is True
    assert len(cam.drags) == 1
    button, x, y, dx, dy = cam.drags[0]
    assert button == "right" and dx == 0 and dy == 60
    assert abs(cam.h - 0.2) <= 0.04


def test_aprende_el_signo_y_la_ganancia_si_el_arrastre_va_al_reves():
    cp, _ = make_cp(ref=0.2)
    cam = SimCam(0.30, s=+0.0016)   # el signo inicial esta al reves
    assert run_correct(cp, cam) is True
    assert cp.gain < 0 and 400 < abs(cp.gain) < 800   # aprendio el signo (y una magnitud parecida a 1/0.0016)
    assert 1 < len(cam.drags) <= 3
    assert abs(cam.h - 0.2) <= 0.04


def test_ganancia_ema_hacia_la_observada():
    cp, _ = make_cp(ref=0.2, gain_px_per_frac=300.0, gain_ema=0.5)
    from bot.vision.pitch import PitchResult
    a, b = PitchResult(300, 0.30, 1.0), PitchResult(200, 0.20, 1.0)
    assert cp.learn(a, b, 60) is True   # efecto -0.00167 frac/px -> ganancia observada 600
    assert cp.gain == pytest.approx(450.0)


def test_no_aprende_con_mediciones_saturadas_ni_sin_movimiento():
    cp, _ = make_cp(ref=0.2)
    from bot.vision.pitch import PitchResult
    sat, ok, near = PitchResult(0, 0.0, 0.5, saturated=True), PitchResult(200, 0.2, 1.0), PitchResult(201, 0.2005, 1.0)
    assert cp.learn(sat, ok, 60) is False and cp.learn(ok, sat, 60) is False and cp.learn(ok, near, 60) is False
    assert cp.gain == 600.0


def test_dentro_de_la_tolerancia_no_corrige():
    cp, _ = make_cp(ref=0.2)
    cam = SimCam(0.23, s=-0.0016)
    assert run_correct(cp, cam) is True and cam.drags == []


def test_tope_de_arrastre_y_de_pasos():
    cp, _ = make_cp(ref=0.1)
    cam = SimCam(0.45, s=0.0)       # el arrastre no mueve nada
    assert run_correct(cp, cam) is False
    assert len(cam.drags) == 3 and all(abs(d[4]) == 150 for d in cam.drags)   # max_steps y max_drag_px
    cp, _ = make_cp(ref=0.1, max_steps=3)
    cam = SimCam(0.45, s=0.0)
    run_correct(cp, cam, max_steps=1)
    assert len(cam.drags) == 1


def test_confianza_baja_no_corrige():
    cp, _ = make_cp(ref=0.2)
    cam = SimCam(0.6, s=-0.0016)
    cam.capture = lambda: np.zeros((H, W, 3), np.uint8)
    assert run_correct(cp, cam) is True and cam.drags == []   # no se puede medir: no se toca


def test_todo_piso_sin_cambio_da_vuelta_el_sentido(caplog):
    caplog.set_level(logging.WARNING)
    cp, _ = make_cp(ref=0.2)
    cam = SimCam(0.0, s=0.0)    # mirando el piso y el arrastre no cambia nada medible
    assert run_correct(cp, cam) is False
    assert cp.gain < 0   # la proxima correccion arrastra al reves
    assert any("doy vuelta el sentido" in r.getMessage() for r in caplog.records)


def test_punto_libre_evita_etiquetas_y_respeta_la_mascara():
    cp, _ = make_cp()
    p = perc()
    first = cp.free_point(p)
    assert first is not None and OPEN.allows(*first)
    p2 = perc(metins=[metin(first)])   # una etiqueta justo ahi
    second = cp.free_point(p2)
    assert second is not None and second != first
    assert abs(second[0] - first[0]) + abs(second[1] - first[1]) > 100
    cp.c["drag_clear_px"] = 5000   # ningun punto queda tan lejos de todo: no hay punto libre
    assert cp.free_point(p) is None
    cam = SimCam(0.4, s=-0.0016)
    assert cp.correct(p, cam.capture(), cam, cam.capture, lambda: None, "x") is False and cam.drags == []
    cp.c["drag_clear_px"] = 140
    walled = perc()
    walled.mask = ScreenMask([], np.full((H, W), 255, np.uint8))   # todo prohibido
    assert cp.free_point(walled) is None


# ------------------------------------------------------------------ cuando corrige el bot
def pitch_cfg(ref=0.2, **over):
    cfg_ = make_cfg(**over)
    cfg_["camera_pitch"].update(ref_horizon_frac=ref)
    return cfg_


def with_cam(h, cam):
    """Ventana que captura la camara simulada y arrastres reales de GameInput que la mueven."""
    def capture(region=None):
        h.win.last_capture_rect = h.win.client_rect_on_screen()
        return cam.capture()
    h.win.capture = capture
    real = h.gi.drag

    def drag(button, x, y, dx, dy, mask, seconds, steps=6):
        ok = real(button, x, y, dx, dy, mask, seconds, steps)
        if ok:
            cam.drag(button, x, y, dx, dy, mask, seconds)
        return ok
    h.gi.drag = drag


def right_drags(h):
    return sum(1 for e in h.be.events if e == ("mouse_down", "right"))


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


def test_corrige_despues_de_romper_y_recoger():
    h = Harness(break_script, cfg=pitch_cfg(0.2))
    cam = SimCam(0.34, s=-0.0016)
    with_cam(h, cam)
    h.run_until(lambda: h.bot.stats.metins == 1 and right_drags(h) >= 1)
    assert State.RECOGER in h.states()
    assert abs(cam.h - 0.2) <= 0.04
    assert ("mouse_down", "left") in h.be.events and h.be.pressed == ([], [])


def test_no_corrige_sin_referencia():
    h = Harness(break_script, cfg=pitch_cfg(None))
    cam = SimCam(0.34, s=-0.0016)
    with_cam(h, cam)
    h.run_until(lambda: h.bot.stats.metins == 1)
    for _ in range(60):
        h.bot.step()
    assert right_drags(h) == 0 and cam.drags == []


def test_no_corrige_con_confianza_baja_en_el_bot():
    h = Harness(break_script, cfg=pitch_cfg(0.2))
    cam = SimCam(0.34, s=-0.0016)
    with_cam(h, cam)
    cam.capture = lambda: np.zeros((H, W, 3), np.uint8)
    h.run_until(lambda: h.bot.stats.metins == 1)
    for _ in range(60):
        h.bot.step()
    assert right_drags(h) == 0


def test_explorando_revisa_cada_every_s():
    h = Harness(lambda h, n: perc(), cfg=pitch_cfg(0.2, explore={"camera_steps": 100000}))   # el barrido no termina
    cam = SimCam(0.30, s=-0.0016)
    with_cam(h, cam)
    h.run_until(lambda: right_drags(h) >= 1)   # primer chequeo apenas explora (un arrastre alcanza)
    t0, n0 = h.clock.t, right_drags(h)
    cam.h = 0.30   # la camara se desvia de nuevo
    while h.clock.t - t0 < 20:
        h.bot.step()
    assert right_drags(h) == n0   # antes de every_s no vuelve a mirar
    h.run_until(lambda: right_drags(h) > n0, limit=2000)
    assert h.clock.t - t0 >= 29.0


def test_explorando_revisa_al_final_de_cada_barrido(caplog):
    caplog.set_level(logging.INFO)
    h = Harness(lambda h, n: perc(), cfg=pitch_cfg(0.2, camera_pitch={"every_s": 100000.0}))
    cam = SimCam(0.30, s=-0.0016)
    with_cam(h, cam)
    h.run_until(lambda: right_drags(h) >= 1)   # el primer chequeo siempre toca
    n0 = right_drags(h)
    cam.h = 0.30
    h.run_until(lambda: right_drags(h) > n0, limit=2000)
    assert any("(fin del barrido)" in r.getMessage() for r in caplog.records)
