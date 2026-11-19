"""Humo: captcha y muerte se detectan en su sample y no en otra."""
from bot.config import DEFAULTS
from bot.vision.captcha import find_captcha
from bot.vision.death import find_death
import copy

CFG = copy.deepcopy(DEFAULTS)


def test_captcha_en_su_sample(load_sample):
    info = find_captcha(load_sample("captcha.png"), CFG)
    assert info is not None and info.score > 0.95
    assert (info.rect.x, info.rect.y) == (1536, 433)


def test_captcha_no_en_otras(load_sample):
    for n in ("muerte.png", "items_en_piso.png", "real_inventario_abierto.png"):
        assert find_captcha(load_sample(n), CFG) is None


def test_muerte_en_su_sample(load_sample):
    info = find_death(load_sample("muerte.png"), CFG)
    assert info is not None and info.here_rect and info.city_rect
    assert (info.here_rect.x, info.here_rect.y) == (59, 83)


def test_muerte_no_en_otras(load_sample):
    for n in ("captcha.png", "metin_avaricia_seleccionado.png", "yang_en_piso.png"):
        assert find_death(load_sample(n), CFG) is None
