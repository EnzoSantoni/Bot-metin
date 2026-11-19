"""Captcha ("Bot Verify"): solo deteccion del panel, en cualquier posicion y con anclas tapadas."""
import copy
import pathlib

import cv2
import numpy as np
import pytest

from bot.config import DEFAULTS
from bot.vision.captcha import find_captcha
from bot.vision.screen_mask import anchor_rect

CFG = copy.deepcopy(DEFAULTS)
CC = CFG["vision"]["captcha"]
ROOT = pathlib.Path(__file__).resolve().parent.parent
ALL = sorted(p.stem for p in (ROOT / "samples").glob("*.png"))
PW, PH = CC["size"]          # 152 x 194


@pytest.fixture(scope="module")
def imgs():
    return {n: cv2.imread(str(ROOT / "samples" / f"{n}.png")) for n in ALL}


@pytest.fixture(scope="module")
def panel(imgs):
    info = find_captcha(imgs["captcha"], CFG, full=True)
    r = info.rect
    return imgs["captcha"][r.y:r.bottom, r.x:r.right].copy()


def paste(bg, panel, x, y):
    """Pega el panel con su esquina sup. izq. en (x, y); lo que sale de la imagen se recorta."""
    im = bg.copy()
    H, W = im.shape[:2]
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + PW, W), min(y + PH, H)
    im[y0:y1, x0:x1] = panel[y0 - y:y1 - y, x0 - x:x1 - x]
    return im


def cover(im, x, y, key):
    """Tapa el ancla `key` ('footer' o 'keypad') de un panel cuya esquina sup. izq. esta en (x, y)."""
    t = cv2.imread(str(ROOT / CC[key]["template"]))
    ox, oy = CC[key]["offset"]
    im[y + oy:y + oy + t.shape[0], x + ox:x + ox + t.shape[1]] = (90, 80, 70)


@pytest.mark.parametrize("full", [False, True])
def test_captcha_en_su_sample(imgs, full):
    info = find_captcha(imgs["captcha"], CFG, full=full)
    assert info is not None and info.score > 0.95
    assert (info.rect.x, info.rect.y, info.rect.w, info.rect.h) == (1536, 433, PW, PH)


@pytest.mark.parametrize("name", [n for n in ALL if n != "captcha"])
@pytest.mark.parametrize("full", [False, True])
def test_captcha_no_en_otras(imgs, name, full):
    assert find_captcha(imgs[name], CFG, full=full) is None


@pytest.mark.parametrize("pos", [(100, 100), (700, 400), (1650, 800), (30, 600), (1000, 60)])
def test_captcha_sintetico_en_otras_posiciones(imgs, panel, pos):
    info = find_captcha(paste(imgs["explorando_zona"], panel, *pos), CFG, full=True)
    assert info is not None and info.score > 0.95
    assert (info.rect.x, info.rect.y) == pos


@pytest.mark.parametrize("key", ["footer", "keypad"])
def test_captcha_con_un_ancla_tapada(imgs, panel, key):
    for pos in [(100, 100), (700, 400), (1650, 800)]:
        im = paste(imgs["metin_cerca_sin_seleccionar"], panel, *pos)
        cover(im, *pos, key)
        info = find_captcha(im, CFG, full=True)
        assert info is not None and (info.rect.x, info.rect.y) == pos, (key, pos)


def test_captcha_con_las_dos_anclas_tapadas_no_se_detecta(imgs, panel):
    """Documenta el limite: sin ninguna de las dos anclas visibles devuelve None (sin excepcion)."""
    im = paste(imgs["explorando_zona"], panel, 700, 400)
    cover(im, 700, 400, "footer")
    cover(im, 700, 400, "keypad")
    assert find_captcha(im, CFG, full=True) is None


@pytest.mark.parametrize("pos", [(600, -40), (600, 900), (-30, 300), (1800, 300), (1700, 950), (-100, -100)])
def test_captcha_parcialmente_fuera_no_falla(imgs, panel, pos):
    """Cortado por el borde: se detecta (rect recortado a la imagen) si queda alguna ancla entera; si no, None."""
    im = paste(imgs["explorando_zona"], panel, *pos)
    info = find_captcha(im, CFG, full=True)
    H, W = im.shape[:2]
    if info is not None:
        r = info.rect
        assert 0 <= r.x and 0 <= r.y and r.right <= W and r.bottom <= H
    # Medido: (600,-40) y (600,900) se detectan (ancla entera dentro); el resto da None.
    if pos in [(600, -40), (600, 900)]:
        assert info is not None


def test_captcha_con_imagenes_degeneradas():
    for shape in [(0, 0, 3), (1, 1, 3), (50, 50, 3), (194, 152, 3)]:
        assert find_captcha(np.zeros(shape, np.uint8), CFG, full=True) is None


@pytest.mark.parametrize("dx,dy", [(0, 0), (6, 4), (12, 12)])
def test_captcha_con_recorte_de_borde(imgs, dx, dy):
    """Las samples tienen tamanos distintos: el panel se encuentra igual recortando bordes."""
    info = find_captcha(np.ascontiguousarray(imgs["captcha"][dy:, dx:]), CFG, full=True)
    assert info is not None and (info.rect.x, info.rect.y) == (1536 - dx, 433 - dy)


@pytest.mark.parametrize("d", [(-50, 0), (50, 0), (0, -50), (0, 50), (50, 50), (-50, -50)])
def test_captcha_zona_fija_tolera_desplazamiento(imgs, panel, d):
    """Con el panel desplazado +-50 px respecto de la sample, la zona fija lo sigue encontrando."""
    pos = (1536 + d[0], 433 + d[1])
    bg = imgs["captcha"].copy()
    bg[433:433 + PH, 1536:1536 + PW] = (90, 80, 70)   # borra el panel original
    info = find_captcha(paste(bg, panel, *pos), CFG, full=False)
    assert info is not None and (info.rect.x, info.rect.y) == pos


def test_captcha_zona_fija_no_ve_panel_lejos(imgs, panel):
    im = paste(imgs["explorando_zona"], panel, 100, 300)
    assert find_captcha(im, CFG, full=False) is None
    info = find_captcha(im, CFG, full=True)
    assert info is not None and (info.rect.x, info.rect.y) == (100, 300)


def test_captcha_search_region_con_holgura(imgs):
    H, W = imgs["captcha"].shape[:2]
    r = anchor_rect(W, H, CC["search_region"])
    assert r.x <= 1536 - 60 and r.y <= 433 - 60 and r.right >= 1536 + PW + 60 and r.bottom >= 433 + PH + 60
