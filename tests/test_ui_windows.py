"""Inventario y Mapa grande: deteccion por plantilla de la barra de titulo, en cualquier posicion."""
import copy

import cv2
import numpy as np
import pytest

from bot.config import DEFAULTS
from bot.geometry import Rect
from bot.vision.screen_mask import build_screen_mask, find_ui_windows

REAL = "real_metin_negro_inventario_mapa.png"
TOL = 3

# Esquinas superiores izquierdas del marco medidas sobre recortes ampliados (x, y).
MAPA_REAL = (1494, 62)
INV_REAL = (1555, 458)
# El Mapa grande tambien esta abierto en estas dos samples (verificado en recorte), corrido unos px.
MAPA_EN_OTRAS = {"items_en_piso.png": (1496, 58), "metin_oscuridad_combate_vida_parcial.png": (1492, 62)}
SIN_VENTANAS = ["captcha", "explorando_zona", "metin_avaricia_seleccionado", "metin_cerca_sin_seleccionar",
                "metin_muy_lejos", "metin_oscuridad_a_distancia", "metin_oscuridad_seleccionado_vida_llena",
                "muerte", "yang_en_piso"]


def _near(r: Rect, xy, tol=TOL):
    return abs(r.x - xy[0]) <= tol and abs(r.y - xy[1]) <= tol


def test_real_detecta_mapa_e_inventario_con_tamano_completo(load_sample):
    rects = find_ui_windows(load_sample(REAL), DEFAULTS)
    assert len(rects) == 2
    mapa, inv = rects
    assert _near(mapa, MAPA_REAL) and (mapa.w, mapa.h) == (214, 289)
    assert _near(inv, INV_REAL) and inv.w == 295
    assert inv.right <= 1858 and inv.bottom <= 1057  # recortado a la imagen


@pytest.mark.parametrize("name,xy", MAPA_EN_OTRAS.items())
def test_mapa_grande_abierto_en_otras_samples(load_sample, name, xy):
    rects = find_ui_windows(load_sample(name), DEFAULTS)
    assert len(rects) == 1 and _near(rects[0], xy)


@pytest.mark.parametrize("name", SIN_VENTANAS)
def test_samples_sin_ventanas_dan_vacio(load_sample, name):
    assert find_ui_windows(load_sample(name + ".png"), DEFAULTS) == []


def test_ventanas_en_otra_posicion(load_sample):
    img = load_sample(REAL)
    shifted = cv2.copyMakeBorder(img, 9, 0, 13, 0, cv2.BORDER_CONSTANT, value=(40, 30, 30))
    mapa, inv = find_ui_windows(shifted, DEFAULTS)
    assert _near(mapa, (MAPA_REAL[0] + 13, MAPA_REAL[1] + 9), 1)
    assert _near(inv, (INV_REAL[0] + 13, INV_REAL[1] + 9), 1)


def test_solo_una_ventana_si_se_tapa_la_otra(load_sample):
    img = load_sample(REAL).copy()
    img[440:1040, 1500:1858] = (30, 30, 30)  # borra el inventario
    rects = find_ui_windows(img, DEFAULTS)
    assert len(rects) == 1 and _near(rects[0], MAPA_REAL)


def test_imagen_negra_y_chica_no_falla():
    assert find_ui_windows(np.zeros((1000, 1800, 3), np.uint8), DEFAULTS) == []
    assert find_ui_windows(np.zeros((10, 10, 3), np.uint8), DEFAULTS) == []


def test_plantilla_faltante_da_error_claro():
    cfg = copy.deepcopy(DEFAULTS)
    cfg["vision"]["ui_windows"]["windows"] = [{"name": "x", "template": "templates/ui/no_existe.png", "size": [10, 10]}]
    with pytest.raises(FileNotFoundError, match="plantilla de ventana"):
        find_ui_windows(np.zeros((100, 100, 3), np.uint8), cfg)


def test_mascara_excluye_las_ventanas(load_sample):
    img = load_sample(REAL)
    m = build_screen_mask(img.shape, DEFAULTS, extra=find_ui_windows(img, DEFAULTS))
    assert [n for n, _ in m.zones].count("ui_window") == 2
    for p in [(1600, 200), (1700, 340), (1600, 700), (1840, 900), (1560, 470)]:
        assert not m.allows(*p), p
    assert m.allows(900, 500)  # centro del mapa de juego


def test_titulo_tapado_se_detecta_por_el_ancla_inferior(load_sample):
    img = load_sample(REAL).copy()
    img[440:490, 1540:1858] = (30, 30, 30)   # tapa el titulo del inventario
    img[50:100, 1480:1720] = (30, 30, 30)    # y el del Mapa grande
    mapa, inv = find_ui_windows(img, DEFAULTS)
    assert _near(mapa, MAPA_REAL) and _near(inv, INV_REAL)


def test_titulo_fuera_de_la_imagen_se_detecta(load_sample):
    img = load_sample(REAL)[100:, :]   # el titulo del Mapa grande queda arriba, fuera de la imagen
    rects = find_ui_windows(img, DEFAULTS)
    assert len(rects) == 2
    mapa, inv = rects
    assert mapa.x == MAPA_REAL[0] and mapa.y == 0 and abs(mapa.bottom - (MAPA_REAL[1] - 100 + 289)) <= TOL
    assert _near(inv, (INV_REAL[0], INV_REAL[1] - 100))


def test_no_duplica_si_calzan_los_dos_anclas(load_sample):
    assert len(find_ui_windows(load_sample(REAL), DEFAULTS)) == 2


# --- inventario compacto (sin panel de estadisticas), pagina III, real_inventario_abierto.png ---
REAL2 = "real_inventario_abierto.png"
INV_COMPACTO = (1685, 458)   # esquina del marco medida en recorte; ancho 165 (hasta el boton X en x=1850)


def test_inventario_compacto_y_mapa_detectados(load_sample):
    rects = find_ui_windows(load_sample(REAL2), DEFAULTS)
    assert len(rects) == 2
    mapa, inv = rects
    assert _near(mapa, MAPA_REAL) and (mapa.w, mapa.h) == (214, 289)
    assert _near(inv, INV_COMPACTO) and inv.w == 165
    assert inv.bottom == 1033   # 458 + 575 (llega hasta la barra de atajos)
    assert inv.contains(1700, 600) and inv.contains(1840, 1000) and not inv.contains(1840, 1040) and not inv.contains(1670, 600)


def test_inventario_compacto_en_otra_posicion(load_sample):
    shifted = cv2.copyMakeBorder(load_sample(REAL2), 7, 0, 11, 0, cv2.BORDER_CONSTANT, value=(40, 30, 30))
    inv = find_ui_windows(shifted, DEFAULTS)[-1]
    assert _near(inv, (INV_COMPACTO[0] + 11, INV_COMPACTO[1] + 7), 1) and inv.w == 165


def test_cada_variante_del_inventario_solo_se_detecta_en_su_sample(load_sample):
    # el grande no se confunde con el compacto (ni al reves): un solo inventario por captura
    assert len(find_ui_windows(load_sample(REAL), DEFAULTS)) == 2
    assert find_ui_windows(load_sample(REAL), DEFAULTS)[1].w == 295


def test_margen_de_seguridad_en_la_mascara(load_sample):
    sm = DEFAULTS["vision"]["ui_windows"]["safety_margin"]
    assert sm >= 20
    for name in (REAL, REAL2):
        img = load_sample(name)
        rects = find_ui_windows(img, DEFAULTS)
        m = build_screen_mask(img.shape, DEFAULTS, extra=rects)
        inv = rects[-1]
        y = inv.y + 100
        assert not m.allows(inv.x - sm + 1, y)    # dentro del margen
        assert m.allows(inv.x - sm - 1, y)        # justo afuera
        assert not m.allows(inv.x + 20, inv.y - sm + 1)
        assert m.allows(inv.x + 20, inv.y - sm - 1)
        mapa = rects[0]
        zonas = [r for n, r in m.zones if n == "ui_window"]
        assert Rect(mapa.x - sm, mapa.y - sm, mapa.w + 2 * sm, mapa.h + 2 * sm) in zonas
        assert Rect(inv.x - sm, inv.y - sm, inv.w + 2 * sm, inv.h + 2 * sm).clip(1858, 1057) in zonas
