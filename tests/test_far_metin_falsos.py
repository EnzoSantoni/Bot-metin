"""Falsos positivos de piedras lejanas: cuerpos de monstruos (arqueras, soldados) de la prueba real del 2026-11-20.

Los casos salen de capturas del modo debug (JPG con dibujos encima: circulos magenta, lineas, textos). Se borran los
dibujos con inpaint antes de analizar. El JPG degrada el nucleo rojo (menos pixeles que en el PNG original), por eso estos
tests bajan `min_core_pixels` / `min_smoke_pixels`; los filtros nuevos (etiqueta de monstruo arriba, forma y tamano
esperado) son los que se prueban. Las etiquetas de monstruos se anotaron a mano sobre la imagen (el JPG las destruye).
"""
import copy
from pathlib import Path

import cv2
import numpy as np
import pytest

from bot.config import load_config
from bot.geometry import Rect
from bot.vision.far_metin import find_far_metins
from bot.vision.monsters import MonsterLabel

DATA = Path(__file__).resolve().parent / "data"
LEFT = "debug_falsas_piedras_izquierda_y_real_lejana.jpg"
BOTTOM = "debug_falsas_piedras_arqueras_abajo.jpg"


def _labels(*rects: tuple[int, int, int, int]) -> list[MonsterLabel]:
    return [MonsterLabel(Rect(*r), Rect(*r).center) for r in rects]


# Etiquetas "Lv NN nombre" de los monstruos (x, y, ancho, alto), leidas a mano de cada imagen.
MONSTERS = {
    LEFT: _labels((128, 386, 140, 10), (118, 444, 163, 10), (0, 454, 95, 10), (73, 468, 163, 10), (209, 514, 143, 10),
                  (73, 556, 163, 10), (0, 500, 95, 10)),
    BOTTOM: _labels((667, 715, 161, 10), (810, 773, 161, 10), (1277, 735, 164, 10)),
}


def clean_debug_drawings(img: np.ndarray) -> np.ndarray:
    """Borra los dibujos del modo debug (colores puros saturados y el magenta de las piedras lejanas) con inpaint."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (0, 235, 235), (180, 255, 255)) | cv2.inRange(hsv, (140, 140, 150), (165, 255, 255))
    return cv2.inpaint(img, cv2.dilate(m, np.ones((3, 3), np.uint8)), 2, cv2.INPAINT_TELEA)


@pytest.fixture(scope="module")
def cfg():
    c = copy.deepcopy(load_config())
    c["vision"]["far_metin"].update(mode="color", min_core_pixels=25, min_smoke_pixels=20)   # el JPG degrada el nucleo
    return c


@pytest.fixture(scope="module")
def images():
    out = {}
    for name in (LEFT, BOTTOM):
        raw = cv2.imread(str(DATA / name), cv2.IMREAD_COLOR)
        assert raw is not None, name
        out[name] = clean_debug_drawings(raw)
    return out


def _near(found, x, y, tol=25):
    return [f for f in found if abs(f.center[0] - x) <= tol and abs(f.center[1] - y) <= tol]


def _no_filters(cfg):
    c = copy.deepcopy(cfg)
    c["vision"]["far_metin"].update(min_size_ratio=0.0, max_aspect=100.0)
    return c


def test_sin_filtros_los_cuerpos_de_monstruos_se_toman_por_piedras(images, cfg):
    """Control: sin los filtros nuevos el detector si produce los falsos (si no, los tests de abajo no probarian nada)."""
    left = find_far_metins(images[LEFT], _no_filters(cfg))
    assert _near(left, 32, 600, 40), [f.center for f in left]
    bottom = find_far_metins(images[BOTTOM], _no_filters(cfg))
    assert _near(bottom, 766, 866), [f.center for f in bottom]   # (1367, 856) queda bajo el circulo magenta del dibujo


def test_cuerpos_de_monstruos_de_la_izquierda_no_son_metin(images, cfg):
    found = find_far_metins(images[LEFT], cfg, monsters=MONSTERS[LEFT])
    for x, y in ((162, 642), (37, 567), (32, 600)):
        assert not _near(found, x, y, 40), (x, y, [f.center for f in found])


def test_cuerpos_de_arqueras_abajo_no_son_metin(images, cfg):
    found = find_far_metins(images[BOTTOM], cfg, monsters=MONSTERS[BOTTOM])
    for x, y in ((1368, 860), (1467, 813), (766, 866)):
        assert not _near(found, x, y, 40), (x, y, [f.center for f in found])


def test_filtro_de_tamano_solo_sin_etiquetas(images, cfg):
    """Aunque no se pasen etiquetas de monstruos, el tamano esperado por perspectiva descarta los cuerpos de abajo."""
    found = find_far_metins(images[BOTTOM], cfg)
    assert not _near(found, 1367, 856) and not _near(found, 766, 866), [f.center for f in found]


def test_la_piedra_real_lejana_y_el_metin_con_etiqueta_se_siguen_detectando(images, cfg):
    found = find_far_metins(images[LEFT], cfg, monsters=MONSTERS[LEFT])
    real = _near(found, 373, 295)
    assert len(real) == 1 and real[0].allowed, [f.center for f in found]
    assert 15 <= real[0].rect.w <= 30
    labeled = _near(found, 1545, 405)   # la piedra bajo la etiqueta de metin_de_oscuridad (1542, 400)
    assert len(labeled) == 1, [f.center for f in found]


def test_la_piedra_real_no_depende_del_borrado_de_dibujos(images, cfg):
    """El borrado no inventa ni pierde la piedra: con la etiqueta de un monstruo lejos, sigue estando."""
    far_label = _labels((900, 700, 160, 10))
    assert _near(find_far_metins(images[LEFT], cfg, monsters=far_label), 373, 295)


# ---- filtros sueltos (imagen sintetica con una piedra: nucleo rojo y humo arriba) ----
def _stone_image(cx: int, base_y: int, size: int, shape=(1057, 1858)) -> np.ndarray:
    img = np.full((*shape, 3), (60, 45, 40), np.uint8)   # suelo oscuro azulado (BGR)
    x0, y0 = cx - size // 2, base_y - size
    cv2.rectangle(img, (x0, y0), (x0 + size, base_y), (45, 60, 190), -1)                       # nucleo rojo (H~5)
    cv2.rectangle(img, (x0 - 2, y0 - size), (x0 + size + 2, y0 - 1), (150, 175, 185), -1)      # humo gris calido
    return img


def _synthetic_cfg(cfg):
    c = copy.deepcopy(cfg)
    c["vision"]["far_metin"]["min_core_pixels"] = 20
    return c


def test_sintetica_una_piedra_normal_se_detecta(cfg):
    c = _synthetic_cfg(cfg)
    found = find_far_metins(_stone_image(900, 500, 40), c)
    assert len(found) == 1


def test_sintetica_etiqueta_de_monstruo_arriba_descarta(cfg):
    c = _synthetic_cfg(cfg)
    img = _stone_image(900, 500, 40)
    above = _labels((820, 400, 160, 10))        # 90 px arriba de la base, centrada
    assert find_far_metins(img, c, monsters=above) == []
    aside = _labels((300, 400, 160, 10))        # a un costado: no es de esa piedra
    assert len(find_far_metins(img, c, monsters=aside)) == 1
    too_high = _labels((820, 200, 160, 10))     # a mas de monster_label_above_px
    assert len(find_far_metins(img, c, monsters=too_high)) == 1
    below = _labels((820, 560, 160, 10))        # debajo de la base: no cuenta
    assert len(find_far_metins(img, c, monsters=below)) == 1


def test_sintetica_tamano_segun_la_altura(cfg):
    c = _synthetic_cfg(cfg)
    assert len(find_far_metins(_stone_image(900, 900, 40), c)) == 0     # abajo del todo un metin real mediria ~100 px
    assert len(find_far_metins(_stone_image(900, 900, 100), c)) == 1
    assert len(find_far_metins(_stone_image(900, 260, 20), c)) == 1     # arriba, una piedra chica es normal


def test_sintetica_forma_alargada_se_descarta(cfg):
    c = _synthetic_cfg(cfg)
    img = np.full((1057, 1858, 3), (60, 45, 40), np.uint8)
    cv2.rectangle(img, (880, 400), (910, 500), (45, 60, 190), -1)              # 30 x 100: un personaje de pie
    cv2.rectangle(img, (878, 350), (912, 399), (150, 175, 185), -1)
    assert find_far_metins(img, c) == []
