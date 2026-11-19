"""Pruebas minimas de bot/vision/items.py (las pruebas contra samples las agrega el vision-tester)."""
from bot.config import DEFAULTS
from bot.geometry import Rect
from bot.vision.items import ItemLabel, choose_item, find_items

import copy
import numpy as np


def _it(x, kind="item", allowed=True):
    return ItemLabel(Rect(x, 0, 10, 10), kind, False, (x, 0), allowed, 0.0)


def test_imagen_vacia():
    cfg = copy.deepcopy(DEFAULTS)
    assert find_items(np.zeros((0, 0, 3), np.uint8), cfg) == []
    assert find_items(np.full((200, 300, 3), 80, np.uint8), cfg) == []


def test_choose_item_ignora_yang_y_no_permitidos():
    items = [_it(5, "yang"), _it(10, allowed=False), _it(50), _it(30)]
    assert choose_item(items, (0, 0)).click_point == (30, 0)
    assert choose_item([_it(5, "yang")], (0, 0)) is None


def test_find_items_en_samples(load_sample):
    cfg = copy.deepcopy(DEFAULTS)
    assert {i.kind for i in find_items(load_sample("yang_en_piso.png"), cfg)} == {"yang"}
    kinds = [i.kind for i in find_items(load_sample("items_en_piso.png"), cfg)]
    assert kinds and set(kinds) == {"item"}
