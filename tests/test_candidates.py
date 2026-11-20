"""Lista de candidatos con posicion en el mapa: proyeccion con rumbo y escala conocidos, fusion, vencimiento, mejor y escala."""
import math

import pytest

from bot import ground
from bot.candidates import CandidateMap, ScaleEstimator, floor_vec, world_pos
from bot.config import DEFAULTS

SHAPE = (1057, 1858, 3)


def approx(a, b, tol=1e-6):
    return math.dist(a, b) <= tol


def test_world_pos_adelante_y_derecha_segun_el_rumbo():
    # rumbo este (0): adelante = +x, derecha = +y (y hacia abajo)
    assert approx(world_pos((100, 100), 0.0, (0.0, 2.0), 10.0), (120, 100))
    assert approx(world_pos((100, 100), 0.0, (1.0, 0.0), 10.0), (100, 110))
    # rumbo sur (90): adelante = +y, derecha = -x
    assert approx(world_pos((100, 100), math.pi / 2, (0.0, 2.0), 10.0), (100, 120))
    assert approx(world_pos((100, 100), math.pi / 2, (1.0, 0.0), 10.0), (90, 100))
    # rumbo oeste (180): adelante = -x
    assert approx(world_pos((100, 100), math.pi, (0.0, 1.0), 5.0), (95, 100))


def test_floor_vec_de_un_punto_justo_arriba_del_personaje_va_hacia_adelante():
    player = (929, 548)
    x, z = floor_vec(player, (929.0, 300.0), SHAPE, DEFAULTS)
    assert abs(x) < 1e-9 and z > 0
    x2, z2 = floor_vec(player, (1100.0, 300.0), SHAPE, DEFAULTS)
    assert x2 > 0 and abs(z2 - z) < 1e-9


@pytest.mark.parametrize("heading_deg", [0, 45, 100, 200, 300])
def test_posicion_en_el_mapa_con_rumbo_y_escala_conocidos(heading_deg):
    """Se proyecta un punto conocido del mapa a pantalla (inversa del piso) y se recupera su posicion."""
    k, h = 13.0, math.radians(heading_deg)
    player_xy, pos = (929, 548), (500.0, 150.0)
    fwd, right = (math.cos(h), math.sin(h)), (-math.sin(h), math.cos(h))
    truth = (pos[0] + 50 * fwd[0] + 12 * right[0], pos[1] + 50 * fwd[1] + 12 * right[1])   # al frente y un poco a la derecha
    rel = (truth[0] - pos[0], truth[1] - pos[1])
    z, x = (rel[0] * fwd[0] + rel[1] * fwd[1]) / k, (rel[0] * right[0] + rel[1] * right[1]) / k
    fx, fz = ground.to_ground(ground.feet_of(player_xy, DEFAULTS), SHAPE, DEFAULTS)
    g = DEFAULTS["vision"]["ground"]
    f, hor, cx = g["focal_frac"] * SHAPE[1], g["horizon_frac"] * SHAPE[0], SHAPE[1] / 2
    dv = f / (fz + z)
    pt = (cx + (fx + x) * dv, hor + dv)
    got = world_pos(pos, h, floor_vec(player_xy, pt, SHAPE, DEFAULTS), k)
    assert approx(got, truth, 1e-6)


def test_escala_se_aprende_con_ema_y_rechaza_muestras_absurdas():
    s = ScaleEstimator(13.0, 0.5, (0.4, 2.5))
    assert s.learn(0.0, 3.0, 4.0) is None and s.learn(10.0, 0.0, 4.0) is None
    assert s.learn(500.0, 1.0, 4.0) is None            # 504 u por unidad: fuera de cotas
    assert s.k == 13.0 and s.samples == 0
    assert s.learn(35.0, 3.0, 4.0) == pytest.approx(13.0)
    assert s.learn(60.0, 3.0, 4.0) == pytest.approx(64 / 3.0)
    assert s.k == pytest.approx(0.5 * 13.0 + 0.5 * 64 / 3.0) and s.samples == 2


def cmap(**kw):
    a = dict(keep_s=100.0, merge_u=10.0, tie_u=4.0, stone_ratio=0.5)
    a.update(kw)
    return CandidateMap(**a)


def test_fusiona_los_cercanos_y_la_etiqueta_mejora_a_la_piedra():
    m = cmap()
    a = m.add((100, 100), "piedra", False, 0.0)
    b = m.add((104, 100), "metin_negro", True, 1.0)
    assert a is b and len(m) == 1 and b.label and b.kind == "metin_negro" and b.n == 2
    assert approx(b.xy, (102, 100))
    m.add((140, 100), "piedra", False, 1.0)
    assert len(m) == 2


def test_vence_a_los_keep_s_y_borra_cerca_de_un_roto():
    m = cmap()
    m.add((0, 0), "piedra", False, 0.0)
    m.add((200, 0), "piedra", False, 60.0)
    assert m.prune(120.0) == 1 and len(m) == 1
    assert m.remove_near((195, 0), 20.0) == 1 and len(m) == 0


def test_mejor_es_el_mas_cercano():
    m = cmap()
    m.add((50, 0), "metin_negro", True, 0.0)
    m.add((20, 0), "metin_negro", True, 0.0)
    m.add((90, 0), "metin_negro", True, 0.0)
    assert m.best((0, 0), (500, 500), 1.0).xy == (20, 0)


def test_empate_de_distancia_gana_el_mas_cerca_del_centro():
    m = cmap()
    m.add((60, 0), "metin_negro", True, 0.0)      # a 60 del jugador, lejos del centro
    m.add((-61, 0), "metin_negro", True, 0.0)     # a 61 (dentro de tie_u), pegado al centro
    assert m.best((0, 0), (-70, 0), 1.0).xy == (-61, 0)
    m2 = cmap(tie_u=0.5)                          # sin empate gana el mas cercano
    m2.add((60, 0), "metin_negro", True, 0.0)
    m2.add((-61, 0), "metin_negro", True, 0.0)
    assert m2.best((0, 0), (-70, 0), 1.0).xy == (60, 0)


def test_etiquetas_antes_que_piedras_salvo_piedra_mucho_mas_cerca():
    m = cmap()
    m.add((100, 0), "metin_negro", True, 0.0)
    m.add((70, 0), "piedra", False, 0.0)
    assert m.best((0, 0), (0, 0), 1.0).label                      # 70 no es < 0.5 x 100
    m.add((0, 30), "piedra", False, 0.0)
    assert not m.best((0, 0), (0, 0), 1.0).label                  # 30 < 50: la piedra gana


def test_solo_dentro_de_la_zona_y_vigentes():
    m = cmap()
    m.add((10, 0), "metin_negro", True, 0.0)
    m.add((50, 0), "metin_negro", True, 0.0)
    assert m.best((0, 0), (0, 0), 1.0, accepts=lambda xy: xy[0] > 20).xy == (50, 0)
    assert m.best((0, 0), (0, 0), 1.0, accepts=lambda xy: False) is None
    assert m.best((0, 0), (0, 0), 101.0) is None   # vencidos
