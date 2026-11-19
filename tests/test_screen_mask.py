"""Tests puros del calculo de zonas excluidas (sin samples)."""
from bot.config import DEFAULTS
from bot.geometry import Rect
from bot.vision.screen_mask import anchor_rect, build_screen_mask, find_ui_windows

import copy
import numpy as np
import pytest


def _cfg(zones):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["vision"]["exclusion_zones"] = zones
    return cfg


def _z(anchor, dx, dy, w, h, name="z"):
    return {"name": name, "anchor": anchor, "dx": dx, "dy": dy, "w": w, "h": h}


def test_anclas_en_imagenes_de_distinto_tamano():
    z = _z("bottom-right", -100, -50, 80, 30)
    assert anchor_rect(1000, 600, z) == Rect(900, 550, 80, 30)
    assert anchor_rect(1010, 640, z) == Rect(910, 590, 80, 30)
    assert anchor_rect(1000, 600, _z("center", -10, -10, 20, 20)) == Rect(490, 290, 20, 20)
    assert anchor_rect(1000, 600, _z("top-center", 0, 5, 10, 10)) == Rect(500, 5, 10, 10)


def test_allows_en_bordes_de_zona_e_imagen():
    m = build_screen_mask((100, 200, 3), _cfg([_z("top-left", 10, 10, 20, 20)]))
    assert not m.allows(10, 10) and not m.allows(29, 29)
    assert m.allows(9, 10) and m.allows(30, 29) and m.allows(10, 30)
    assert not m.allows(-1, 5) and not m.allows(5, -1)
    assert not m.allows(200, 5) and not m.allows(5, 100)
    assert m.allows(199, 99)


def test_zona_que_sale_de_la_imagen_se_recorta():
    m = build_screen_mask((100, 200), _cfg([_z("bottom-right", -30, -30, 4000, 4000),
                                            _z("top-left", 500, 500, 10, 10, "afuera")]))
    assert m.zones == [("z", Rect(170, 70, 30, 30))]
    assert m.mask.shape == (100, 200) and m.mask.dtype == np.uint8
    assert int((m.mask > 0).sum()) == 900


def test_allows_rect_y_extra():
    m = build_screen_mask((100, 200), _cfg([_z("top-left", 0, 0, 50, 50)]), extra=[Rect(100, 60, 30, 30)])
    assert [n for n, _ in m.zones] == ["z", "ui_window"]
    assert m.allows_rect(Rect(60, 0, 20, 20))
    assert not m.allows_rect(Rect(40, 40, 20, 20))
    assert not m.allows_rect(Rect(110, 70, 5, 5))
    assert not m.allows_rect(Rect(190, 90, 20, 20))  # sale de la imagen


def test_find_ui_windows_en_imagen_vacia():
    assert find_ui_windows(np.zeros((10, 10, 3), np.uint8), DEFAULTS) == []


# Puntos de referencia relativos a los bordes (dx desde el centro horizontal / vertical, o desde el borde
# derecho / inferior), medidos sobre recortes: texto del chat, log de exp, iconos de buffs, iconos de
# misiones, minimapa, coordenadas/FPS, panel superior (y 8..52, +-215 px del centro).
def _puntos(w, h):
    return {
        "chat": (w // 2 - 100, h - 60), "exp_log": (w // 2 + 340, h - 100), "buffs": (100, 25),
        "quests": (35, h // 2 - 180), "top_right": (w - 80, 60), "top_right_coords": (w - 80, 200),
        "top_center": (w // 2, 30), "top_center_borde": (w // 2 + 200, 45),
    }


@pytest.mark.parametrize("name", [
    "captcha", "explorando_zona", "items_en_piso", "metin_avaricia_seleccionado", "metin_cerca_sin_seleccionar",
    "metin_muy_lejos", "metin_oscuridad_a_distancia", "metin_oscuridad_combate_vida_parcial",
    "metin_oscuridad_seleccionado_vida_llena", "muerte", "real_metin_negro_inventario_mapa", "yang_en_piso"])
def test_zonas_estaticas_cubren_la_interfaz_en_todas_las_samples(load_sample, name):
    img = load_sample(name + ".png")
    h, w = img.shape[:2]
    m = build_screen_mask(img.shape, DEFAULTS, extra=find_ui_windows(img, DEFAULTS))
    for zone, (x, y) in _puntos(w, h).items():
        assert not m.allows(x, y), (zone, x, y)
    assert m.allows(w // 2, h // 2)  # el centro del campo de juego queda libre


def test_zonas_por_defecto_validas():
    from bot.config import validate_config
    cfg = copy.deepcopy(DEFAULTS)
    cfg.update(window_title="x")
    validate_config(cfg)
    m = build_screen_mask((1048, 1855, 3), cfg)
    assert len(m.zones) == len(DEFAULTS["vision"]["exclusion_zones"])
