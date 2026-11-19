import copy
import random

import pytest

from bot.config import DEFAULTS, ConfigError, validate_config
from bot.zone import FarmZone, ZoneStatus

POLY = [(439, 141), (753, 91), (702, 203), (436, 196)]


def _zone(tol=30, poly=None):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["farm_zone"] = {"polygon": [list(p) for p in (poly or POLY)], "outside_tolerance": tol}
    return FarmZone.from_config(cfg)


def test_metin_negro_inside():
    assert _zone().status((453, 161)) is ZoneStatus.INSIDE


def test_vertices_distancia_cero():
    z = _zone()
    for v in POLY:
        assert z.signed_distance(v) == 0


def test_tolerancia_y_afuera():
    z = _zone()
    # borde izquierdo casi vertical (x 436..439): a 20 y 50 unidades a la izquierda
    assert z.status((438 - 20, 168)) is ZoneStatus.TOLERANCE
    assert z.status((438 - 50, 168)) is ZoneStatus.OUTSIDE
    assert z.accepts((418, 168)) and not z.accepts((388, 168))


def test_tolerancia_cero():
    z = _zone(tol=0)
    assert z.accepts((453, 161))
    assert not z.accepts((430, 168))


def test_centroide_y_direccion():
    z = _zone()
    assert z.status(z.centroid) is ZoneStatus.INSIDE
    d = z.direction_to_center((300, 168))
    assert abs(d[0] ** 2 + d[1] ** 2 - 1) < 1e-9 and d[0] > 0
    assert z.direction_to_center(z.centroid) == (0.0, 0.0)


def test_centroide_de_area_no_promedio():
    tri = _zone(poly=[(0, 0), (6, 0), (0, 3)])
    assert tri.centroid == pytest.approx((2, 1))
    # trapecio: el promedio de vertices daria x=5.5, el centroide de area difiere
    trap = _zone(poly=[(0, 0), (10, 0), (8, 2), (0, 2)])
    assert trap.centroid[0] < 4.7 and trap.centroid[0] != pytest.approx(4.5)


def test_random_point_inside_con_margen():
    z, rng = _zone(), random.Random(1)
    for _ in range(200):
        p = z.random_point_inside(rng, margin=5)
        assert z.signed_distance(p) >= 5
    assert z.random_point_inside(rng, margin=1000) == z.interior_center


@pytest.mark.parametrize("zone", [
    {"polygon": [[0, 0], [1, 1]], "outside_tolerance": 1},
    {"polygon": [[0, 0], [10, 10], [10, 0], [0, 10]], "outside_tolerance": 1},   # bowtie / cruce
    {"polygon": [[0, 0], [5, 5], [10, 10]], "outside_tolerance": 1},             # area 0
    {"polygon": [[0, 0], [10, 0], [10, 10]], "outside_tolerance": -1},
    {"polygon": [[0, 0], [10, 0], [10, "a"]], "outside_tolerance": 1},
])
def test_poligonos_invalidos(zone):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["farm_zone"] = zone
    with pytest.raises(ConfigError):
        validate_config(cfg)


def _err(poly):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["farm_zone"] = {"polygon": [list(p) for p in poly], "outside_tolerance": 1}
    with pytest.raises(ConfigError) as e:
        validate_config(cfg)
    return str(e.value)


def test_vertice_repetido():
    assert "repetido" in _err([(0, 0), (10, 0), (10, 0), (10, 10)])
    assert "repetido" in _err([(0, 0), (10, 0), (10, 10), (0, 0)])   # el cierre repite el primero


def test_bowtie_dice_cruce():
    assert "cruza" in _err([(0, 0), (10, 10), (10, 0), (0, 10)])


def test_vertices_alineados_dicen_area_cero():
    assert "area 0" in _err([(0, 0), (5, 5), (10, 10)])


def test_espigas():
    assert "espiga" in _err([(0, 0), (10, 0), (10, 10), (0, 10), (0, 20), (0, 5)])
    assert "espiga" in _err([(0, 0), (10, 0), (5, 0), (5, 10)])


def test_aristas_colineales_solapadas():
    msg = _err([(0, 0), (10, 0), (10, 5), (15, 5), (15, 0), (5, 0), (5, -5), (0, -5)])
    assert "colineales solapadas" in msg


def test_aristas_que_se_tocan():
    assert "tocan" in _err([(0, 0), (10, 0), (10, 10), (5, 0), (5, -10), (0, -10)])


def test_centroide_area_cero_es_config_error():
    with pytest.raises(ConfigError):
        FarmZone(((0.0, 0.0), (5.0, 5.0), (10.0, 10.0)), 1.0)


def test_from_config_valida():
    cfg = copy.deepcopy(DEFAULTS)
    cfg["farm_zone"] = {"polygon": [[0, 0], [10, 10], [10, 0], [0, 10]], "outside_tolerance": 1}
    with pytest.raises(ConfigError):
        FarmZone.from_config(cfg)


def test_interior_center_cuadrado_y_triangulo_fino():
    sq = _zone(poly=[(0, 0), (100, 0), (100, 100), (0, 100)])
    assert sq.interior_center == pytest.approx((50, 50), abs=2)
    # triangulo fino: el centroide esta muy cerca del borde; el punto interior esta mas adentro
    tri = _zone(poly=[(0, 0), (200, 0), (0, 10)])
    assert tri.signed_distance(tri.interior_center) >= tri.signed_distance(tri.centroid) - 1e-6
    assert tri.signed_distance(tri.interior_center) > 3
    assert tri.status(tri.interior_center) is ZoneStatus.INSIDE


U = [(0, 0), (30, 0), (30, 100), (70, 100), (70, 0), (100, 0), (100, 130), (0, 130)]   # una U


def test_poligono_en_u():
    z = _zone(poly=U)
    assert z.signed_distance(z.centroid) < 0       # el centroide cae en el hueco de la U
    assert z.signed_distance(z.interior_center) > 10
    for pt in [(50, 60), (50, 10), (500, 500)]:
        d = z.direction_to_center(pt)
        assert abs(d[0] ** 2 + d[1] ** 2 - 1) < 1e-9
        ix, iy = z.interior_center
        assert d[0] * (ix - pt[0]) + d[1] * (iy - pt[1]) > 0
    assert z.random_point_inside(random.Random(1), margin=1000) == z.interior_center
