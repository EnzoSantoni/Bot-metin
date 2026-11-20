"""bot/ground.py (plano del piso) y la eleccion del metin mas cercano, con la captura real de tres metin."""
import copy
import logging
import math

import cv2
import pytest

import bot.input.base as base
from bot.bot import State
from bot.config import DEFAULTS
from bot.ground import Goal, ground_dist, rank_goals, to_ground
from bot.perception import perceive
from bot.vision.coords import Coords
from bot.vision.metin import MetinDetection
from bot.geometry import Rect
from test_bot_behavior import far_at, perc_far
from test_bot_states import INSIDE_XY, NEAR_EDGE_XY, OUT_XY, PLAYER, TOL_XY, Harness, metin, perc

SHAPE = (1057, 1858, 3)
SAMPLE = "samples/real_tres_metin_hover.png"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


@pytest.fixture
def cfg():
    return copy.deepcopy(DEFAULTS)


# ---------------------------------------------------------------- ground (puro)
def test_mas_abajo_es_mas_cerca(cfg):
    feet = (929.0, 600.0)
    assert ground_dist(feet, (929, 500), SHAPE, cfg) < ground_dist(feet, (929, 300), SHAPE, cfg)
    # a la misma distancia en pixeles, lo de abajo en la pantalla esta mas cerca del personaje que lo de arriba
    assert ground_dist(feet, (929, 700), SHAPE, cfg) < ground_dist(feet, (929, 500), SHAPE, cfg)


def test_mismo_v_mas_al_costado_es_mas_lejos(cfg):
    feet = (929.0, 600.0)
    assert ground_dist(feet, (1000, 400), SHAPE, cfg) < ground_dist(feet, (1400, 400), SHAPE, cfg)
    assert ground_dist(feet, (860, 400), SHAPE, cfg) < ground_dist(feet, (400, 400), SHAPE, cfg)


def test_por_encima_del_horizonte_se_acota(cfg):
    assert to_ground((929, 0), SHAPE, cfg) == to_ground((929, 50), SHAPE, cfg)   # ambos "muy lejos", sin dividir por <= 0
    assert math.isfinite(ground_dist((929, 600), (0, -500), SHAPE, cfg))


def test_rank_goals_ordena_y_es_estable(cfg):
    goals = [Goal("piedra", "piedra", (900, 200)), Goal("etiqueta", "a", (900, 500)), Goal("etiqueta", "b", (900, 500))]
    r = rank_goals(goals, (929, 560), SHAPE, cfg)
    assert [g.name for g, _ in r] == ["a", "b", "piedra"] and r[0][1] == r[1][1] < r[2][1]


# ---------------------------------------------------------------- captura real
@pytest.fixture(scope="module")
def real():
    img = cv2.imread(SAMPLE)
    if img is None:
        pytest.skip(f"falta {SAMPLE}")
    return img


def _goals_of(real, hor=None):
    cfg = copy.deepcopy(DEFAULTS)
    if hor is not None:
        cfg["vision"]["ground"]["horizon_frac"] = hor
    p = perceive(real, cfg)
    p.coords = Coords(INSIDE_XY[0], INSIDE_XY[1], 1.0)
    h = Harness(lambda h, n: p, cfg=_harness_cfg(cfg))
    h.bot.tracker.update(p.coords, h.clock())
    return h, p


def _harness_cfg(cfg):
    from test_bot_states import make_cfg
    out = make_cfg()
    out["vision"] = cfg["vision"]
    return out


@pytest.mark.parametrize("hor", [None, 0.10, 0.12, 0.14, 0.16, 0.19])
def test_captura_real_la_piedra_de_adelante_gana_a_la_etiqueta_de_atras(real, hor):
    """Pedido de Enzo (prueba de las 01:15): en esta captura tiene que ir a la piedra de adelante (1407, 374), que esta
    claramente mas cerca que la etiqueta de atras (1236, 265): su distancia de piso es < unlabeled_closer_ratio (0.5) x la
    de la etiqueta con cualquier horizonte razonable (0.10-0.19). Las piedras sin etiqueta van por cercania entre si."""
    h, p = _goals_of(real, hor)
    cands, _ = h.bot._label_view(p, 0.0)
    far, _ = h.bot._far_view(p, 0.0)
    ranked = h.bot._goals(cands, far, p)
    assert len(ranked) == 3
    label = next(e for e in ranked if e[0].kind == "etiqueta")
    stones = [e for e in ranked if e[0].kind == "piedra"]
    assert label[0].name == "metin_de_oscuridad"
    assert abs(stones[0][0].point[0] - 1407) <= 60 and abs(stones[0][0].point[1] - 374) <= 60
    assert stones[0][1] < stones[1][1]
    assert ranked[0][0].kind == "piedra"
    assert stones[0][1] < 0.5 * label[1]


def test_captura_real_pick_goal_va_a_la_piedra_de_adelante_con_hover(real, caplog, monkeypatch):
    caplog.set_level(logging.INFO)
    h, p = _goals_of(real)
    clicked = []
    monkeypatch.setattr(h.bot, "_click_attack", lambda point, gp, p, t: clicked.append(point) or True)
    out = h.bot._pick_goal(p, 0.0, "x")
    assert out is not None and out[0] is State.ACERCARSE and not clicked   # piedra sin etiqueta: primero el hover
    msg = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Elegido"))
    assert msg.startswith("Elegido piedra") and "metin_de_oscuridad" in msg and "tambien:" in msg


def test_captura_real_con_ratio_bajo_gana_la_etiqueta(real, monkeypatch):
    """Con un umbral mas exigente (0.4) la etiqueta de atras le gana a la piedra (la regla de prioridad funciona)."""
    h, p = _goals_of(real)
    h.bot.cfg["approach"]["unlabeled_closer_ratio"] = 0.4
    cands, _ = h.bot._label_view(p, 0.0)
    far, _ = h.bot._far_view(p, 0.0)
    assert h.bot._goals(cands, far, p)[0][0].kind == "etiqueta"


# ---------------------------------------------------------------- eleccion (unitarios)
def _lejos_en_pantalla(px):
    return metin(click=(PLAYER[0] + px, PLAYER[1]))


@pytest.mark.parametrize("xy", [INSIDE_XY, NEAR_EDGE_XY, TOL_XY])
def test_etiqueta_a_174_px_se_ataca_con_el_jugador_adentro_o_en_tolerancia(xy):
    """Caso real: metin con etiqueta pegado al personaje a 174 px se descartaba por la regla vieja de 160 px."""
    h = Harness(lambda h, n: perc(coords=xy, metins=[_lejos_en_pantalla(174)]))
    h.bot.step()
    assert State.ATACAR in h.states() and h.clicks() == 1


def test_etiqueta_a_174_px_no_se_ataca_con_el_jugador_afuera():
    h = Harness(lambda h, n: perc(coords=OUT_XY, metins=[_lejos_en_pantalla(174)]))
    h.bot.step()
    assert State.ATACAR not in h.states() and h.clicks() == 0


def test_elige_la_piedra_mas_cercana_aunque_haya_etiqueta_mas_lejos():
    near_stone = far_at(PLAYER[0] + 150, PLAYER[1] + 20)
    far_label = metin(click=(PLAYER[0] - 300, PLAYER[1] - 250))
    h = Harness(lambda h, n: perc_far(far=[near_stone], metins=[far_label]))
    h.bot.step()
    assert h.states()[0] is State.ACERCARSE and h.clicks() == 0


def test_elige_la_etiqueta_mas_cercana_aunque_haya_piedra_mas_lejos():
    far_stone = far_at(PLAYER[0] + 300, PLAYER[1] - 250)
    near_label = metin(click=(PLAYER[0] + 100, PLAYER[1] + 10))
    h = Harness(lambda h, n: perc_far(far=[far_stone], metins=[near_label]))
    h.bot.step()
    assert h.states()[0] is State.ATACAR and h.clicks() == 1


def test_piedra_debajo_de_una_etiqueta_de_otro_tipo_no_es_candidata():
    stone = far_at(1300, 548)
    otro = MetinDetection(Rect(1260, 470, 80, 10), "metin_de_avaricia", 1.0, False, (1300, 540), True)
    h = Harness(lambda h, n: perc_far(far=[stone], metins=[otro]))
    p = perc_far(far=[stone], metins=[otro])
    far, why = h.bot._far_view(p, 0.0)
    assert far == [] and any("tipo ignorado" in m for _, m in why)
    h.bot.step()
    assert State.ACERCARSE not in h.states()
