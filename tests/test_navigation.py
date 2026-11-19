import math
import random

import numpy as np
import pytest

from bot.config import load_config
from bot.navigation import CameraModel, Navigator
from bot.vision.screen_mask import ScreenMask
from bot.zone import FarmZone, ZoneStatus
from sim_game import SimGame, make_a0

CFG = load_config("config.json")
ZONE = FarmZone.from_config(CFG)
SHAPE = (1057, 1858)
PLAYER = (929, 548)
MAX_CLICKS = 20   # clics maximos (sondeos incluidos) para llegar a <= arrive_dist (tramos de max_walk_px)


def open_mask() -> ScreenMask:
    return ScreenMask([], np.zeros(SHAPE, np.uint8))


def walk(sim: SimGame, vec, target):
    """Clic + caminar + registrar el tramo con coordenadas enteras, como lo vera el bot."""
    before = sim.coords()
    sim.click(vec)
    sim.settle()
    after = sim.coords()
    delta = (after[0] - before[0], after[1] - before[1])
    if isinstance(target, CameraModel):
        target.update(vec, delta)
    else:
        target.record_segment(vec, delta)


A0S = [make_a0(0.2, 0.7), make_a0(0.15, 0.5, flip_x=True), make_a0(0.25, 1.0, flip_y=True),
       make_a0(0.2, 0.9, flip_x=True, flip_y=True)]
PHIS = [0.0, 1.0, -2.0, 3.0, 5.5]


def angle_err(a, b):
    return abs((a - b + math.pi) % (2 * math.pi) - math.pi)


def heading(v):
    return math.atan2(v[1], v[0])


@pytest.mark.parametrize("a0", A0S)
@pytest.mark.parametrize("phi", PHIS)
def test_camara_aprende_a0_y_phi(a0, phi):
    sim = SimGame(a0, phi, (500, 150))
    cam = CameraModel(CFG)
    assert not cam.ready
    walk(sim, (150, 0), cam)
    assert not cam.ready
    walk(sim, (0, 150), cam)
    assert cam.ready
    for v in [(100, -80), (-200, 50), (30, 250)]:
        g = sim._m() @ np.array(v)
        assert np.allclose(cam.screen_to_game(v), g, atol=2.0 + 0.05 * np.hypot(*g))
        back = cam.game_to_screen(tuple(g))
        assert np.hypot(back[0] - v[0], back[1] - v[1]) < 0.07 * np.hypot(*v) + 12
    # giro de camara: 1 tramo alcanza
    sim.rotate(1.3, +1)
    cam.on_camera_rotated()
    assert not cam.ready
    walk(sim, (140, 60), cam)
    assert cam.ready
    for v in [(100, -80), (-200, 50)]:
        g = sim._m() @ np.array(v)
        assert angle_err(heading(cam.screen_to_game(v)), heading(g)) < math.radians(8)


def test_tramos_cortos_o_bloqueados_se_ignoran():
    cam = CameraModel(CFG)
    assert not cam.update((5, 5), (1, 1))        # pantalla corta
    assert not cam.update((100, 0), (1, 0))      # juego corto (bloqueado)
    assert not cam.has_a0
    cam.update((150, 0), (30, 0))
    cam.update((0, 150), (0, 30))
    assert cam.ready
    assert not cam.update((150, 0), (10, 0))     # frenado a mitad de camino: longitud inconsistente
    assert cam.ready


def test_camara_se_reaprende_tras_inconsistencias():
    cam = CameraModel(CFG)
    cam.update((150, 0), (30, 0))
    cam.update((0, 150), (0, 30))
    for _ in range(CFG["navigation"]["relearn_after_rejects"]):
        cam.update((150, 0), (100, 0))
    assert not cam.has_a0


def run_nav(sim, nav, target, max_clicks=MAX_CLICKS):
    mask = open_mask()
    for n in range(max_clicks + 1):
        pos = sim.coords()
        if nav.arrived(pos, target):
            return n
        plan = nav.plan_click(pos, target, PLAYER, mask)
        assert plan is not None
        walk(sim, (plan.x - PLAYER[0], plan.y - PLAYER[1]), nav)
    return None


@pytest.mark.parametrize("a0", A0S)
@pytest.mark.parametrize("phi", [0.0, 2.2, -1.1])
def test_llega_al_destino_desde_20_inicios(a0, phi):
    rng = random.Random(7)
    for _ in range(20):
        start = ZONE.random_point_inside(rng, 0)
        target = ZONE.random_point_inside(rng, CFG["navigation"]["explore_margin"])
        sim = SimGame(a0, phi, start)
        nav = Navigator(CFG, ZONE, CameraModel(CFG), rng)
        assert run_nav(sim, nav, target) is not None, f"no llego desde {start} a {target}"


def test_estadistica_de_clics():
    rng = random.Random(1)
    counts = []
    for a0 in A0S:
        for phi in PHIS:
            for _ in range(10):
                start = ZONE.random_point_inside(rng, 0)
                target = ZONE.random_point_inside(rng, 15)
                counts.append(run_nav(SimGame(a0, phi, start), Navigator(CFG, ZONE, CameraModel(CFG), rng), target))
    assert None not in counts
    print(f"CLICS promedio={sum(counts) / len(counts):.2f} max={max(counts)}")
    assert max(counts) <= MAX_CLICKS


def test_giro_de_camara_en_medio_del_viaje():
    rng = random.Random(3)
    sim = SimGame(make_a0(0.2, 0.7), 0.5, (450, 150))
    nav = Navigator(CFG, ZONE, CameraModel(CFG), rng)
    assert run_nav(sim, nav, (700.0, 110.0)) is not None
    sim.rotate(2.0, -1)
    nav.camera.on_camera_rotated()
    assert run_nav(sim, nav, (450.0, 180.0)) is not None


@pytest.mark.parametrize("phi", [0.0, 1.7, -2.5])
def test_desde_afuera_vuelve_adentro(phi):
    rng = random.Random(11)
    mask = open_mask()
    for start in [(410, 150), (470, 225), (770, 80), (600, 60), (420, 120), (700, 230)]:
        assert ZONE.status(start) is not ZoneStatus.INSIDE
        sim = SimGame(make_a0(), phi, start)
        nav = Navigator(CFG, ZONE, CameraModel(CFG), rng)
        for _ in range(MAX_CLICKS * 2):
            pos = sim.coords()
            if ZONE.status(pos) is ZoneStatus.INSIDE:
                break
            plan = nav.plan_click(pos, nav.return_target(pos), PLAYER, mask)
            walk(sim, (plan.x - PLAYER[0], plan.y - PLAYER[1]), nav)
        assert ZONE.status(sim.coords()) is ZoneStatus.INSIDE, start


def forbidden_mask() -> ScreenMask:
    m = np.zeros(SHAPE, np.uint8)
    m[:, :700] = 255          # todo el lado izquierdo
    m[:450, :] = 255          # y toda la franja superior
    m[600:700, 800:1100] = 255
    return ScreenMask([], m)


def calibrated_camera() -> CameraModel:
    cam = CameraModel(CFG)
    cam.update((150, 0), (30, 0))
    cam.update((0, 150), (0, 30))
    return cam


def test_plan_click_siempre_permitido():
    rng = random.Random(5)
    mask = forbidden_mask()
    nav = Navigator(CFG, ZONE, CameraModel(CFG), rng)
    for _ in range(100):   # camara sin calibrar: sondeos
        plan = nav.plan_click((500, 150), (600, 150), PLAYER, mask)
        assert plan is not None and plan.probe
        assert mask.allows(plan.x, plan.y)
    nav = Navigator(CFG, ZONE, calibrated_camera(), rng)
    n_ok = 0
    for _ in range(300):
        tgt = (rng.uniform(300, 900), rng.uniform(0, 300))
        plan = nav.plan_click((rng.uniform(400, 700), rng.uniform(100, 250)), tgt, PLAYER, mask)
        if plan is not None:
            assert mask.allows(plan.x, plan.y) and not plan.probe
            n_ok += 1
    assert n_ok > 150


def test_plan_click_sin_salida_devuelve_none():
    nav = Navigator(CFG, ZONE, CameraModel(CFG), random.Random(0))
    full = ScreenMask([], np.full(SHAPE, 255, np.uint8))
    assert nav.plan_click((500, 150), (600, 150), PLAYER, full) is None


def test_plan_click_acota_distancia_y_apunta():
    nav = Navigator(CFG, ZONE, calibrated_camera(), random.Random(0))
    lo, hi = CFG["navigation"]["click_radius_px"]
    far = nav.plan_click((500, 150), (700, 150), PLAYER, open_mask())
    assert math.isclose(math.hypot(far.x - PLAYER[0], far.y - PLAYER[1]), hi, abs_tol=1)
    near = nav.plan_click((500, 150), (503, 150), PLAYER, open_mask())
    assert math.isclose(math.hypot(near.x - PLAYER[0], near.y - PLAYER[1]), CFG["navigation"]["min_segment_px"], abs_tol=1)
    assert near.expected_delta is not None and near.expected_delta[0] > 0


def test_exploration_target_con_margen():
    nav = Navigator(CFG, ZONE, CameraModel(CFG), random.Random(2))
    m = CFG["navigation"]["explore_margin"]
    for _ in range(300):
        assert ZONE.signed_distance(nav.exploration_target()) >= m - 1e-9


def test_return_target_esta_adentro():
    nav = Navigator(CFG, ZONE, CameraModel(CFG), random.Random(2))
    assert ZONE.status(nav.return_target((900, 300))) is ZoneStatus.INSIDE
