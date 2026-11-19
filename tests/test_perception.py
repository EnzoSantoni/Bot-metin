"""perceive() contra samples: coincide con llamar a cada funcion por separado."""
import pytest

from bot.config import load_config
from bot.perception import perceive
from bot.vision.captcha import find_captcha
from bot.vision.coords import read_coords
from bot.vision.death import find_death
from bot.vision.items import choose_item, find_items
from bot.vision.metin import choose_target, find_metins, player_point
from bot.vision.panel import read_panel
from bot.vision.screen_mask import build_screen_mask, find_ui_windows

CFG = load_config("config.json")
SAMPLES = ["metin_oscuridad_seleccionado_vida_llena.png", "items_en_piso.png", "captcha.png", "muerte.png",
           "real_inventario_abierto.png", "explorando_zona.png"]


@pytest.mark.parametrize("name", SAMPLES)
def test_coincide_con_funciones_separadas(load_sample, name):
    img = load_sample(name)
    p = perceive(img, CFG, t=5.0)
    wins = find_ui_windows(img, CFG)
    mask = build_screen_mask(img.shape, CFG, extra=wins)
    player = player_point(img.shape, CFG)
    dets = find_metins(img, CFG, mask)
    items = find_items(img, CFG, mask)
    assert p.t == 5.0 and p.shape == img.shape and p.player_xy == player
    assert p.windows == wins
    assert (p.mask.mask == mask.mask).all()
    assert p.metins == dets and p.target == choose_target(dets, player)
    assert p.panel == read_panel(img, CFG)
    assert p.coords == read_coords(img, CFG)
    assert p.items == items and p.item_choice == choose_item(items, player)
    assert p.captcha == find_captcha(img, CFG)
    assert p.death == find_death(img, CFG)
    assert {"windows", "mask", "metins", "panel", "coords", "items", "captcha", "death", "total"} <= set(p.timings)


def test_parts_saltea(load_sample):
    img = load_sample("items_en_piso.png")
    p = perceive(img, CFG, parts={"coords"})
    assert p.coords is not None
    assert p.metins == [] and p.target is None and p.panel is None and p.items == [] and p.item_choice is None
    for k in ("metins", "panel", "items"):
        assert k not in p.timings
    assert "captcha" in p.timings and "death" in p.timings
    assert perceive(img, CFG, parts=set()).coords is None
    with pytest.raises(ValueError):
        perceive(img, CFG, parts={"nada"})


def test_full_captcha(load_sample, monkeypatch):
    import bot.perception as mod
    seen = []
    monkeypatch.setattr(mod, "find_captcha", lambda img, cfg, full=False: seen.append(full))
    img = load_sample("captcha.png")
    perceive(img, CFG)
    perceive(img, CFG, full_captcha=True)
    assert seen == [False, True]


def test_captcha_en_sample(load_sample):
    assert perceive(load_sample("captcha.png"), CFG, parts={"coords"}).captcha is not None
    assert perceive(load_sample("captcha.png"), CFG, full_captcha=True, parts=set()).captcha is not None
    assert perceive(load_sample("muerte.png"), CFG, parts=set()).death is not None
