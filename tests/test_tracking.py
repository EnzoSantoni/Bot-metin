import copy

from bot.config import load_config
from bot.tracking import CoordTracker
from bot.vision.coords import Coords
from sim_game import Box, SimGame, make_a0

CFG = load_config("config.json")


def c(x, y):
    return Coords(x, y, 1.0)


def test_primera_lectura_y_none():
    tr = CoordTracker(CFG)
    assert tr.update(None, 0) is None
    assert tr.update(c(100, 100), 1) == (100, 100)
    assert tr.update(None, 2) == (100, 100) and tr.last_good_t == 1


def test_salto_rechazado():
    tr = CoordTracker(CFG)
    tr.update(c(100, 100), 0)
    assert tr.update(c(500, 100), 0.5) == (100, 100)
    assert tr.update(c(105, 100), 1.0) == (105, 100)   # lectura normal despues: se acepta y se limpia
    assert tr.update(c(900, 900), 1.5) == (105, 100)


def test_movimiento_normal_aceptado():
    tr = CoordTracker(CFG)
    tr.update(c(100, 100), 0)
    for i in range(1, 10):
        assert tr.update(c(100 + 10 * i, 100), i) == (100 + 10 * i, 100)


def test_teletransporte_tras_k_lecturas_consistentes():
    k = CFG["coords_filter"]["accept_after"]
    tr = CoordTracker(CFG)
    tr.update(c(100, 100), 0)
    for i in range(k - 1):
        assert tr.update(c(600, 300), 1 + i * 0.1) == (100, 100)
    assert tr.update(c(601, 300), 1 + k * 0.1) == (601, 300)


def test_lecturas_inconsistentes_no_se_aceptan():
    tr = CoordTracker(CFG)
    tr.update(c(100, 100), 0)
    for i, x in enumerate([500, 700, 300, 900, 450, 650]):
        assert tr.update(c(x, 50), 1 + i * 0.1) == (100, 100)


def test_none_intercalados_con_salto():
    tr = CoordTracker(CFG)
    tr.update(c(100, 100), 0)
    assert tr.update(None, 0.2) == (100, 100)
    assert tr.update(c(700, 100), 0.4) == (100, 100)
    assert tr.update(None, 0.6) == (100, 100)
    assert tr.update(c(701, 100), 0.8) == (100, 100)
    assert tr.update(c(700, 101), 1.0) == (700, 101)   # 3 consistentes, los None no cuentan


def test_dt_grande_esta_acotado_por_max_dt_s():
    tr = CoordTracker(CFG)
    tr.update(c(100, 100), 0)
    tr.update(None, 5)
    tr.update(c(200, 100), 10)
    assert tr.last_known == (100, 100)   # 100 u en 10 s serian plausibles, pero dt se acota a max_dt_s
    assert tr.update(c(112, 100), 10.1) == (112, 100)


def test_trabado_y_no_trabado():
    tr = CoordTracker(CFG)
    tr.update(c(100, 100), 0)
    assert not tr.is_stuck(10)                       # nunca intento moverse
    tr.mark_moving(0)
    for i in range(1, 9):
        tr.update(c(100, 100), i * 0.5)
    assert tr.is_stuck(4.0)
    assert not tr.is_stuck(1.0)                     # ventana todavia no cubierta
    tr.mark_moving(4.0)
    assert not tr.is_stuck(4.5)
    for i in range(1, 9):
        tr.update(c(100 + 5 * i, 100), 4 + i * 0.5)
    assert not tr.is_stuck(8.0)


def test_stuck_ventana_parametro_y_sin_lecturas():
    tr = CoordTracker(CFG)
    tr.update(c(10, 10), 0)
    tr.mark_moving(0)
    tr.update(c(10, 10), 1)
    assert tr.is_stuck(2.0, window_s=1.5)
    assert not tr.is_stuck(20.0)   # sin lecturas recientes no hay evidencia


def test_stuck_con_none_intercalados():
    tr = CoordTracker(CFG)
    tr.update(c(50, 50), 0)
    tr.mark_moving(0)
    for i in range(1, 9):
        tr.update(None if i % 2 else c(50, 51), i * 0.5)
    assert tr.is_stuck(4.0)


def test_simulador_obstaculo_activa_stuck():
    sim = SimGame(make_a0(), 0.0, (100, 100), speed=10, obstacles=[Box(110, 90, 130, 110)])
    tr = CoordTracker(CFG)
    t = 0.0
    tr.update(c(*sim.coords()), t)
    sim.click((300, 0))   # +60 unidades en x: choca a los 10
    tr.mark_moving(t)
    stuck_at = None
    while t < 15:
        t += 0.25
        sim.advance(0.25)
        tr.update(c(*sim.coords()), t)
        if tr.is_stuck(t):
            stuck_at = t
            break
    assert sim.blocked and stuck_at is not None
    assert stuck_at >= 1.0 + CFG["stuck"]["window_s"] - 0.5   # recien empezo a chocar tras ~1 s


def test_simulador_sin_obstaculo_no_stuck():
    sim = SimGame(make_a0(), 0.0, (100, 100), speed=10)
    tr = CoordTracker(CFG)
    tr.update(c(*sim.coords()), 0.0)
    sim.click((1000, 0))
    tr.mark_moving(0.0)
    for i in range(1, 40):
        sim.advance(0.25)
        tr.update(c(*sim.coords()), i * 0.25)
        assert not tr.is_stuck(i * 0.25)
