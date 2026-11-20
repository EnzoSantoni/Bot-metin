"""Rumbo de la camara desde el cono del minimapa: recortes reales (debug/run_20261120_121005 y samples) y un cono sintetico."""
from pathlib import Path

import cv2
import numpy as np
import pytest

from bot.config import DEFAULTS
from bot.vision.minimap import camera_heading

DATA = Path(__file__).parent / "data" / "minimap"
W, H = 1858, 1057
# (archivo, rumbo esperado en grados): medido y comprobado a ojo contra el abanico del recorte (0 = este, 90 = sur)
REAL = [("run1210_0001.png", 154), ("run1210_0003.png", 199), ("run1210_0008.png", 344), ("run1210_0020.png", 156),
        ("run1210_0044.png", 141), ("run1210_0047.png", 35), ("run1210_0052.png", 255), ("real_tres_metin_hover.png", 165)]


def canvas(crop: np.ndarray, w: int = W) -> np.ndarray:
    """El recorte de 150x150 pegado arriba a la derecha de una imagen del tamano del cliente."""
    img = np.zeros((H, w, 3), np.uint8)
    img[0:150, w - 150:w] = crop
    return img


def diff(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


@pytest.mark.parametrize("name,expected", REAL)
def test_rumbo_en_capturas_reales(name, expected):
    crop = cv2.imread(str(DATA / name))
    assert crop is not None
    h = camera_heading(canvas(crop), DEFAULTS)
    assert h is not None and diff(h, expected) <= 6.0


def test_ancho_de_ventana_distinto_usa_el_ancla_de_la_derecha():
    crop = cv2.imread(str(DATA / "run1210_0020.png"))
    a, b = camera_heading(canvas(crop), DEFAULTS), camera_heading(canvas(crop, 1862), DEFAULTS)
    assert a is not None and b is not None and diff(a, b) < 1.0


def test_estable_con_ruido():
    crop = cv2.imread(str(DATA / "run1210_0020.png"))
    noisy = np.clip(crop.astype(int) + np.random.default_rng(1).integers(-4, 5, crop.shape), 0, 255).astype(np.uint8)
    assert diff(camera_heading(canvas(noisy), DEFAULTS), camera_heading(canvas(crop), DEFAULTS)) <= 3.0


def test_sin_cono_devuelve_none():
    flat = np.full((150, 150, 3), (134, 106, 73), np.uint8)   # minimapa liso, sin abanico
    assert camera_heading(canvas(flat), DEFAULTS) is None
    assert camera_heading(np.zeros((H, W, 3), np.uint8), DEFAULTS) is None


def test_imagen_demasiado_chica_devuelve_none():
    assert camera_heading(np.zeros((40, 40, 3), np.uint8), DEFAULTS) is None


@pytest.mark.parametrize("deg", list(range(0, 360, 30)) + [17, 123, 301])
def test_cono_sintetico_precision(deg):
    """Un abanico de 40 grados sobre un fondo con textura: el rumbo sale a <= 3 grados."""
    m = DEFAULTS["vision"]["minimap"]
    rng = np.random.default_rng(5)
    crop = np.full((150, 150, 3), (134, 106, 73), np.int16) + rng.integers(-6, 7, (150, 150, 1))
    cx, cy = 150 + m["center_dx"], m["center_dy"]
    yy, xx = np.mgrid[0:150, 0:150]
    ang = np.degrees(np.arctan2(yy - cy, xx - cx)) % 360
    r = np.hypot(xx - cx, yy - cy)
    inside = (np.abs((ang - deg + 180) % 360 - 180) <= 20) & (r > 6) & (r < 60)
    crop[inside] = (190, 142, 101)
    h = camera_heading(canvas(np.clip(crop, 0, 255).astype(np.uint8)), DEFAULTS)
    assert h is not None and diff(h, deg) <= 3.0
