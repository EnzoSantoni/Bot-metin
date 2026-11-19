"""Imagenes degeneradas (0x0, 1x1, mas chicas que las plantillas): nada lanza, todo da vacio."""
import numpy as np
import pytest

from bot.vision.coords import read_coords
from bot.vision.metin import find_metins
from bot.vision.panel import read_panel
from bot.vision.screen_mask import build_screen_mask, find_ui_windows
from synth import make_cfg

CFG = make_cfg()
SHAPES = [(0, 0, 3), (1, 1, 3), (0, 10, 3), (10, 0, 3), (5, 7, 3), (40, 60, 3), (120, 90, 3)]


@pytest.mark.parametrize("shape", SHAPES)
def test_find_ui_windows(shape):
    assert find_ui_windows(np.zeros(shape, np.uint8), CFG) == []


@pytest.mark.parametrize("shape", SHAPES)
def test_build_screen_mask(shape):
    m = build_screen_mask(shape, CFG)
    assert not m.allows(0, 0) or shape[0] > 0


@pytest.mark.parametrize("shape", SHAPES)
def test_find_metins(shape):
    assert find_metins(np.zeros(shape, np.uint8), CFG) == []


@pytest.mark.parametrize("shape", SHAPES)
def test_read_panel(shape):
    p = read_panel(np.zeros(shape, np.uint8), CFG)
    assert not p.present and p.hp_current is None


@pytest.mark.parametrize("shape", SHAPES)
def test_read_coords(shape):
    assert read_coords(np.zeros(shape, np.uint8), CFG) is None
