"""Pantalla de muerte: botones "Aqui" y "Ciudad" (177x22) arriba a la izquierda."""
import copy
import pathlib

import cv2
import numpy as np
import pytest

from bot.config import DEFAULTS
from bot.vision.death import find_death

CFG = copy.deepcopy(DEFAULTS)
ROOT = pathlib.Path(__file__).resolve().parent.parent
ALL = sorted(p.stem for p in (ROOT / "samples").glob("*.png"))


@pytest.fixture(scope="module")
def imgs():
    return {n: cv2.imread(str(ROOT / "samples" / f"{n}.png")) for n in ALL}


def test_muerte_en_su_sample(imgs):
    info = find_death(imgs["muerte"], CFG)
    assert info is not None and info.score > 0.95
    assert (info.here_rect.x, info.here_rect.y, info.here_rect.w, info.here_rect.h) == (59, 83, 177, 22)
    assert (info.city_rect.x, info.city_rect.y, info.city_rect.w, info.city_rect.h) == (59, 113, 177, 22)


@pytest.mark.parametrize("name", [n for n in ALL if n != "muerte"])
def test_muerte_no_en_otras(imgs, name):
    assert find_death(imgs[name], CFG) is None


@pytest.mark.parametrize("which", ["here_rect", "city_rect"])
def test_muerte_con_un_boton_tapado(imgs, which):
    img = imgs["muerte"].copy()
    info = find_death(img, CFG)
    r = getattr(info, which)
    img[r.y:r.bottom, r.x:r.right] = (90, 80, 70)
    got = find_death(img, CFG)
    assert got is not None and got.score > 0.95
    assert getattr(got, which) is None
    other = "city_rect" if which == "here_rect" else "here_rect"
    assert getattr(got, other) is not None


def test_muerte_con_los_dos_botones_tapados(imgs):
    img = imgs["muerte"].copy()
    info = find_death(img, CFG)
    for r in (info.here_rect, info.city_rect):
        img[r.y:r.bottom, r.x:r.right] = (90, 80, 70)
    assert find_death(img, CFG) is None


@pytest.mark.parametrize("dx,dy", [(0, 0), (6, 4), (12, 12)])
def test_muerte_con_recorte_de_borde(imgs, dx, dy):
    info = find_death(np.ascontiguousarray(imgs["muerte"][dy:, dx:]), CFG)
    assert info is not None and (info.here_rect.x, info.here_rect.y) == (59 - dx, 83 - dy)


def test_muerte_con_imagenes_degeneradas():
    for shape in [(0, 0, 3), (1, 1, 3), (50, 50, 3), (300, 200, 3)]:
        assert find_death(np.zeros(shape, np.uint8), CFG) is None
