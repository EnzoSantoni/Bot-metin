"""Regresiones de la revision de la Fase 6: loops sin progreso, giro externo de la camara, clics fuera de tolerancia."""
import math
import random

import numpy as np
import pytest

import bot.input.base as base
from bot.bot import State
from bot.navigation import CameraModel, Navigator
from bot.zone import FarmZone
from sim_bot_env import SimEnv, make_cfg
from sim_game import Box, make_a0

OSC = "metin_de_oscuridad"
NO_VOLVER = ("no puede volver a la zona", "no puede llegar al centro")   # alarma por VOLVER / por CENTRO con timeout
MARGIN = 6.0   # margen chico sobre outside_tolerance (latencia de un ciclo, redondeo)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


# ---------------------------------------------------------------- B1: seleccion equivocada repetida
def test_seleccion_equivocada_repetida_no_descarta_un_metin_con_nombre_y_sigue():
    env = SimEnv(9, phi=2.0)
    env.world.add_metin((580, 150), name=OSC)
    env.world.wrong_select = 10**6
    env.run_until(max_s=200)
    assert not any("seleccion equivocada" in t.reason for t in env.transitions)   # con nombre: no se descarta
    # tras approach.max_clicks sin el panel del metin se ignora esa posicion approach.no_label_ignore_s y se elige otro
    assert any(t.reason.startswith("sin confirmar:") for t in env.transitions)
    assert not env.bot.discarded
    # nunca dos clics a menos de `attack.min_click_gap_s` y el reintento no es un bucle de clics
    times = sorted(t for t, _ in env.world.clicks_on_metin)
    assert all(b - a >= float(env.cfg["attack"]["min_click_gap_s"]) - 1e-3 for a, b in zip(times, times[1:]))
    assert len(times) <= 4 * (200 // 30 + 2)
    assert env.bot.state in (State.EXPLORAR, State.BUSCAR, State.VOLVER, State.DESTRABAR, State.ATACAR, State.ACERCARSE)


# ---------------------------------------------------------------- B2: pared entre el personaje (afuera) y el poligono
def test_pared_entre_el_personaje_y_la_zona_termina_sin_loop():
    env = SimEnv(3, start=(400, 150))
    env.world.obstacles.append(Box(410, -3000, 416, 3000))
    env.run_until(max_s=600)
    assert env.bot.finished and env.bot.stats.end_reason in NO_VOLVER   # run_bot deja la alarma sonando con los dos motivos
    assert any(a == "start" and r in NO_VOLVER for _, a, r in env.alarm.log)
    assert env.count_unstucks() < 60
    assert env.world.t < 400


@pytest.mark.parametrize("seed", [5, 6, 7])
def test_pared_corta_vuelve_por_otro_lado_o_termina_con_alarma(seed):
    env = SimEnv(seed, start=(400, 150))
    env.world.obstacles.append(Box(410, 100, 416, 200))   # pared corta: se puede rodear
    env.run_until(lambda: env.zone.signed_distance(tuple(env.world.pos)) > 0, max_s=400)
    if env.bot.finished:
        assert env.bot.stats.end_reason in NO_VOLVER and env.alarm.is_active
    else:
        assert env.zone.signed_distance(tuple(env.world.pos)) > 0


def test_volver_acumula_el_tiempo_entre_reentradas():
    env = SimEnv(3, start=(400, 150))
    env.world.obstacles.append(Box(410, -3000, 416, 3000))
    env.run_until(lambda: env.count_unstucks() >= 2, max_s=300)
    assert env.bot._vl_total(env.world.t) > float(env.cfg["stuck"]["window_s"])


# ---------------------------------------------------------------- I1: giro externo de la camara
def rotation_worst(ang: float, seed: int, during: str, cfg=None) -> tuple[float, SimEnv]:
    """Gira la camara `ang` grados por fuera durante EXPLORAR o VOLVER. Devuelve el peor alejamiento del poligono."""
    start = (480 + seed, 150 + seed) if during == "EXPLORAR" else (426 + seed, 168)
    env = SimEnv(seed, start=start, cfg=cfg)
    w, cam = env.world, env.bot.camera
    dist = env.track_zone()
    st = {"fired": False, "n": 0}

    def ext(ww):
        if st["fired"] or not cam.ready or env.bot.state.name != during:
            return
        st["n"] += 1
        if st["n"] < 2:
            return
        st["fired"], st["t"] = True, ww.t
        ww.rotate(abs(ang) / 60.0, 1 if ang >= 0 else -1)
    w.on_step.append(ext)
    for _ in range(900):
        env.bot.step()
        if env.bot.finished or (st["fired"] and w.t > st["t"] + 60):
            break
    return max(dist), env


@pytest.mark.parametrize("during", ["EXPLORAR", "VOLVER"])
@pytest.mark.parametrize("ang", [60, 90, 150, -120])
def test_giro_externo_nunca_pasa_de_la_tolerancia(ang, during):
    worst = 0.0
    for seed in (0, 1, 2, 3):
        d, env = rotation_worst(ang, seed, during)
        worst = max(worst, d)
        tol = env.zone.outside_tolerance
        if during == "EXPLORAR":
            assert d <= tol + MARGIN, f"seed {seed}: se alejo {d:.1f} u"
        else:   # arranca afuera (~40 u): no debe alejarse mas de un margen chico de donde estaba
            assert d <= max(tol, 46.0) + MARGIN, f"seed {seed}: se alejo {d:.1f} u"
    print(f"PEOR giro {ang} durante {during}: {worst:.1f} u")


def test_camara_giro_externo_avisa_y_reestima(caplog):
    cam = CameraModel(make_cfg())
    cam.update((100, 0), (20, 0))
    cam.update((0, 100), (0, 20))
    assert cam.ready and cam.rotations == 0
    c, s = math.cos(math.radians(70)), math.sin(math.radians(70))
    with caplog.at_level("WARNING"):
        assert cam.update((100, 0), (20 * c, 20 * s))
    assert cam.rotations == 1 and any("girada por fuera" in r.message for r in caplog.records)
    assert abs(math.degrees(cam.phi) - 70) < 1 and cam.ready


def test_camara_giro_chico_no_cuenta_como_externo():
    cam = CameraModel(make_cfg())
    cam.update((100, 0), (20, 0))
    cam.update((0, 100), (0, 20))
    c, s = math.cos(math.radians(8)), math.sin(math.radians(8))
    cam.update((100, 0), (20 * c, 20 * s))
    assert cam.rotations == 0


def test_tras_giro_externo_game_pos_of_no_usa_phi_invalido():
    env = SimEnv(0, start=(500, 150))
    env.run_until(lambda: env.bot.camera.ready, max_s=200)
    cam = env.bot.camera
    cam.on_camera_rotated()
    assert not cam.ready
    assert env.bot._game_pos_of((1000, 600), env.bot.last_perception) is None


# ---------------------------------------------------------------- I2: clics de caminata dentro de la tolerancia
class ConeMask:
    """Bloquea un cono alrededor de la direccion deseada (un monstruo o un panel)."""

    def __init__(self, player, want, half_deg):
        self.player, self.want, self.half = player, math.atan2(want[1], want[0]), math.radians(half_deg)

    def allows(self, x, y):
        a = math.atan2(y - self.player[1], x - self.player[0])
        return abs((a - self.want + math.pi) % (2 * math.pi) - math.pi) > self.half


def test_planes_con_direccion_bloqueada_nunca_terminan_fuera_de_la_tolerancia():
    cfg = make_cfg()
    zone = FarmZone.from_config(cfg)
    tol, player = zone.outside_tolerance, (929, 528)
    rng = random.Random(0)
    n = worst = 0
    for _ in range(3000):
        cam = CameraModel(cfg)
        a0 = make_a0(rng.uniform(0.15, 0.25), rng.uniform(0.5, 1.0))
        cam._a0, cam._phi, cam._phi_valid = np.asarray(a0, float), rng.uniform(-math.pi, math.pi), True
        sv = np.linalg.svd(a0, compute_uv=False)
        cam._scale = (float(sv[-1]), float(sv[0]))
        nav = Navigator(cfg, zone, cam, rng)
        p = zone.random_point_inside(rng, 15.0)
        if zone.signed_distance(p) > 25:
            continue
        tgt = zone.random_point_inside(rng, 15.0)
        want = cam.game_to_screen((tgt[0] - p[0], tgt[1] - p[1]))
        plan = nav.plan_click(p, tgt, player, ConeMask(player, want, rng.uniform(20, 70)))
        if plan is None:
            continue
        n += 1
        d = cam.screen_to_game((plan.x - player[0], plan.y - player[1]))
        worst = max(worst, -zone.signed_distance((p[0] + d[0], p[1] + d[1])))
    assert n > 500 and worst <= tol
    print(f"I2: {n} planes, peor afuera {worst:.1f} u")


# ---------------------------------------------------------------- B2 (unidad, reloj falso) y M2 / M4 / I3
from test_bot_states import FakeWindow, Harness, OUT_XY, INSIDE_XY, make_cfg as states_cfg, perc, run_main  # noqa: E402
from bot.capture import WindowMinimizedError  # noqa: E402
from bot.tracking import CoordTracker  # noqa: E402
from bot.vision.coords import Coords  # noqa: E402


def test_volver_sin_progreso_va_al_centro_y_termina_con_alarma():
    h = Harness(lambda h, n: perc(coords=OUT_XY))   # el personaje no se mueve nunca: pared infinita
    h.train_camera()
    h.run_until(lambda: h.bot.finished, limit=5000)
    assert h.bot.stats.end_reason == "no puede llegar al centro"   # motivo de fin propio (run_bot deja la alarma sonando)
    assert h.alarm.starts == ["no puede llegar al centro"]   # el timeout vence ya en CENTRO: mensaje propio (log y alarma)
    assert State.CENTRO in h.states() and h.bot.stats.centros == 1   # primero el centro, despues la alarma
    assert h.bot._vl_total(h.clock()) >= float(h.cfg["volver"]["max_seconds"])


def test_tracker_acota_el_dt_del_filtro():
    cfg = states_cfg()
    tr = CoordTracker(cfg)
    tr.update(Coords(500, 150, 1.0), 0.0)
    far = 500 + int(cfg["coords_filter"]["max_speed"] * cfg["coords_filter"]["max_dt_s"] + cfg["coords_filter"]["slack"]) + 20
    tr.update(Coords(far, 150, 1.0), 60.0)   # 60 s sin lecturas: con dt sin acotar el salto pasaria
    assert tr.last_known == (500, 150)
    tr.update(Coords(510, 150, 1.0), 60.2)
    assert tr.position == (510, 150)


def test_ventana_minimizada_entre_ensure_ready_y_capture_reintenta_sin_traceback():
    h = Harness(lambda h, n: perc())
    real = h.win.capture
    calls = {"n": 0}

    def capture(region=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise WindowMinimizedError("minimizada")
        return real(region)
    h.win.capture = capture
    h.bot.step()                 # no lanza
    assert h.win.ensure_ready_calls == 1 and h.seen == []
    h.bot.step()
    assert h.win.ensure_ready_calls == 2 and h.seen            # el siguiente ciclo ya decide


def test_observar_nunca_activa_la_ventana_ni_manda_alt(monkeypatch):
    import bot.capture as capture
    sent = []
    monkeypatch.setattr(capture.win32api, "keybd_event", lambda *a, **k: sent.append(a))

    class SpyWindow(FakeWindow):
        """Ventana al frente y libre; cualquier intento de activarla se anota."""
        activations = 0

        def activate(self, *a, **k):
            SpyWindow.activations += 1
            return True

        def is_minimized(self):
            return False

        def is_unobstructed(self):
            return True

        def ensure_ready(self, timeout=None, should_stop=None):   # lo que haria la real: activar
            self.activate()
            return True
    code, _, _ = run_main(lambda h, n: perc(), ["--observar", "--max-minutos", "0.01"], window=SpyWindow())
    assert code == 0 and SpyWindow.activations == 0 and sent == []


def test_ventana_pasiva_espera_sin_activar_y_es_cancelable(capsys):
    import run_bot

    class Hidden(FakeWindow):
        activated = 0

        def activate(self, *a, **k):
            Hidden.activated += 1
    win = Hidden()
    win.foreground = False
    passive = run_bot.PassiveWindow(win, poll_s=0.0)
    stops = iter([False, False, True])
    assert passive.ensure_ready(should_stop=lambda: next(stops)) is False
    assert Hidden.activated == 0 and capsys.readouterr().out.count("MODO OBSERVAR") == 1
    win.foreground = True
    assert passive.ensure_ready() is True


def test_run_bot_no_puede_volver_deja_la_alarma(capsys):
    cfg = states_cfg(loop={"cycle_ms": 5}, volver={"max_seconds": 0.5})
    code, ctl, alarm = run_main(lambda h, n: perc(coords=OUT_XY), ["--observar", "--max-minutos", "5"], cfg=cfg)
    out = capsys.readouterr().out
    assert code == 0 and "no puede volver a la zona" in out and "Alarma sonando" in out
    assert alarm.starts == ["no puede volver a la zona"] and alarm.stops == 0


def test_config_de_la_revision_validada():
    import copy
    from bot.config import DEFAULTS, ConfigError, validate_config
    validate_config(copy.deepcopy(DEFAULTS))
    for sec, key, val in [("attack", "max_wrong_selections", 0), ("volver", "max_seconds", 0),
                          ("navigation", "external_rotation_deg", 0), ("navigation", "heading_tolerance_deg", 0),
                          ("navigation", "zone_guard_margin", -1), ("navigation", "max_walk_px", 0),
                          ("coords_filter", "max_dt_s", 0)]:
        cfg = copy.deepcopy(DEFAULTS)
        cfg[sec][key] = val
        with pytest.raises(ConfigError):
            validate_config(cfg)
