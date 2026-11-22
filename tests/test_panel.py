"""Panel superior: nombre, barra y numero de vida. Verdad de referencia leida en recortes ampliados."""
import numpy as np
import pytest

from bot.vision.digits import get_glyphs, read_text_line
from bot.vision.panel import read_bar, read_panel, reset_warnings
from synth import (PANEL_DY, draw_text, empty_bar_profile, fill_rows, make_cfg, set_bar_ratio,
                   set_panel_hp_text, shifted)

CFG = make_cfg()
GSEC = CFG["vision"]["glyphs"]["panel"]
NEGRO = "real_metin_negro_seleccionado"
COMBATE = "metin_oscuridad_combate_vida_parcial"

# sample -> (kind, nombre, vida actual, vida maxima)
WITH_PANEL = {
    "captcha": ("other", "se_rang", 524, 2692),
    "metin_avaricia_seleccionado": ("other_metin", "metin_de_avaricia", 47400, 47400),
    COMBATE: ("target_metin", "metin_de_oscuridad", 18131, 80250),
    "metin_oscuridad_seleccionado_vida_llena": ("target_metin", "metin_de_oscuridad", 80250, 80250),
    NEGRO: ("target_metin", "metin_negro", 63450, 63450),
    "yang_en_piso": ("other", "farfarfa", 5213, 6713),     # el propio personaje
}
NO_PANEL = ["explorando_zona", "items_en_piso", "metin_cerca_sin_seleccionar", "metin_muy_lejos",
            "metin_oscuridad_a_distancia", "muerte", "real_metin_negro_inventario_mapa",
            "real_inventario_abierto", "real_rodeado", "real_metin_lejos_1", "real_tres_metin_hover",
            "cam_01_plano_normal", "cam_02_plano_tope_horizontal", "cam_03_plano_tope_arriba", "cam_04_plano_normal_otra_vez", "cam_08_montana_camara_subida", "cam_09_montana_despues_de_alejarse", "cam_10_plano_zoom_tope_lejos"]


@pytest.fixture(autouse=True)
def _fresh_warnings():
    reset_warnings()
    yield
    reset_warnings()


# Capturas con un MONSTRUO seleccionado (panel "otra cosa", con su vida): (vida actual, maxima)
OTHER_PANEL = {"cam_05_subida_normal": (490, 490), "cam_06_subida_tope_horizontal": (490, 490),
               "cam_07_subida_tope_arriba": (490, 490), "cam_11_plano_zoom_tope_cerca": (1129, 1129)}


def test_every_sample_is_classified():
    import pathlib
    names = {p.stem for p in (pathlib.Path(__file__).parent.parent / "samples").glob("*.png")}
    assert names == set(WITH_PANEL) | set(NO_PANEL) | set(OTHER_PANEL)


@pytest.mark.parametrize("name", sorted(OTHER_PANEL))
def test_monster_panel_on_samples(load_sample, name):
    p = read_panel(load_sample(name + ".png"), CFG)
    assert p.present and p.kind == "other" and p.name is None
    assert (p.hp_current, p.hp_max) == OTHER_PANEL[name]


@pytest.mark.parametrize("name", sorted(WITH_PANEL))
def test_panel_on_samples(load_sample, name):
    kind, slug, cur, mx = WITH_PANEL[name]
    p = read_panel(load_sample(name + ".png"), CFG)
    assert p.present and p.rect is not None
    assert (p.kind, p.name) == (kind, slug)
    assert (p.hp_current, p.hp_max) == (cur, mx)
    assert p.hp_ratio == pytest.approx(cur / mx, abs=0.05)
    assert p.name_score >= 0.85 and p.hp_text_score >= 0.8


@pytest.mark.parametrize("name", NO_PANEL)
def test_no_panel_on_samples(load_sample, name):
    p = read_panel(load_sample(name + ".png"), CFG)
    assert not p.present and p.kind is None and p.hp_current is None and p.hp_ratio is None


def test_no_panel_on_blank_images():
    for shape in ((1057, 1858, 3), (300, 400, 3)):
        assert not read_panel(np.zeros(shape, np.uint8), CFG).present


def test_only_selected_target_names_are_target_metin(load_sample):
    for name, (kind, *_rest) in WITH_PANEL.items():
        if kind != "target_metin":
            assert read_panel(load_sample(name + ".png"), CFG).kind != "target_metin"


def test_name_filter_follows_config(load_sample):
    cfg = make_cfg(["Metin de Avaricia"])
    kinds = {n: read_panel(load_sample(n + ".png"), cfg).kind for n in WITH_PANEL}
    assert kinds["metin_avaricia_seleccionado"] == "target_metin"
    for n in (COMBATE, "metin_oscuridad_seleccionado_vida_llena", NEGRO):
        assert kinds[n] == "other_metin"
    assert kinds["captcha"] == "other" and kinds["yang_en_piso"] == "other"
    cfg = make_cfg(["Metin Negro"])
    assert read_panel(load_sample(NEGRO + ".png"), cfg).kind == "target_metin"
    assert read_panel(load_sample(COMBATE + ".png"), cfg).kind == "other_metin"


@pytest.mark.parametrize("name", sorted(WITH_PANEL))
@pytest.mark.parametrize("shift", [(3, 0, 0, 0), (0, 5, 2, 4), (6, 6, 6, 6), (-4, -3, 0, 5), (0, 9, 0, 0)])
def test_panel_position_robust(load_sample, name, shift):
    kind, slug, cur, mx = WITH_PANEL[name]
    p = read_panel(shifted(load_sample(name + ".png"), *shift), CFG)
    assert (p.kind, p.name, p.hp_current, p.hp_max) == (kind, slug, cur, mx)


@pytest.mark.parametrize("name", NO_PANEL)
def test_no_panel_position_robust(load_sample, name):
    assert not read_panel(shifted(load_sample(name + ".png"), 4, 5, 2, 6), CFG).present


# --- casos sinteticos sobre samples reales ---------------------------------------------------------------

def test_unknown_name_erased_is_other(load_sample):
    img = load_sample(NEGRO + ".png").copy()
    nr = read_panel(img, CFG).name_rect
    fill_rows(img, nr.x, nr.y - 2, nr.right, nr.bottom + 2, nr.x - 3)
    p = read_panel(img, CFG)
    assert p.present and p.kind == "other" and p.name is None
    assert (p.hp_current, p.hp_max) == (63450, 63450)


def test_unknown_name_other_text_is_other(load_sample):
    img = load_sample(NEGRO + ".png").copy()
    nr = read_panel(img, CFG).name_rect
    fill_rows(img, nr.x, nr.y - 2, nr.right, nr.bottom + 2, nr.x - 3)
    draw_text(img, "12.345/6.789", get_glyphs(GSEC), GSEC["color"], nr.x, nr.y + 3, PANEL_DY)
    p = read_panel(img, CFG)
    assert p.present and p.kind == "other" and p.name is None


def test_unreadable_number_keeps_bar_ratio(load_sample):
    img = load_sample(COMBATE + ".png").copy()
    nr = read_panel(img, CFG).number_rect
    fill_rows(img, nr.x + 30, nr.y, nr.x + 60, nr.bottom, nr.x)      # tapa el medio del numero
    p = read_panel(img, CFG)
    assert p.present and p.kind == "target_metin"
    assert p.hp_current is None and p.hp_max is None
    assert p.hp_ratio == pytest.approx(18131 / 80250, abs=0.03)


def test_noise_over_number_is_none(load_sample):
    img = load_sample(COMBATE + ".png").copy()
    nr = read_panel(img, CFG).number_rect
    rng = np.random.default_rng(3)
    for _ in range(25):
        img[nr.y + int(rng.integers(0, nr.h)), nr.x + int(rng.integers(30, 90))] = GSEC["color"]
    p = read_panel(img, CFG)
    assert p.hp_ratio == pytest.approx(0.23, abs=0.03)
    assert p.hp_current is None or (p.hp_current, p.hp_max) == (18131, 80250)


@pytest.mark.parametrize("col", range(0, 7))   # tapar cada tramo del numero por completo o a medias
def test_covered_digit_never_gives_wrong_number(load_sample, col):
    img = load_sample(COMBATE + ".png").copy()
    nr = read_panel(img, CFG).number_rect
    x0 = nr.x + 36 + col * 8
    fill_rows(img, x0, nr.y, x0 + 4, nr.bottom, nr.x)
    p = read_panel(img, CFG)
    assert p.hp_current is None or (p.hp_current, p.hp_max) == (18131, 80250)


@pytest.fixture(scope="module")
def empty_profile():
    import cv2
    from pathlib import Path
    img = cv2.imread(str(Path(__file__).resolve().parent.parent / "samples" / (COMBATE + ".png")))
    return empty_bar_profile(img, read_panel(img, CFG).bar_rect)


def test_metin_negro_partial_life(load_sample, empty_profile):
    img = load_sample(NEGRO + ".png").copy()
    p0 = set_panel_hp_text(img, "21.300/63.450", CFG)
    set_bar_ratio(img, p0.bar_rect, empty_profile, 21300 / 63450)
    p = read_panel(img, CFG)
    assert p.kind == "target_metin" and p.name == "metin_negro"
    assert (p.hp_current, p.hp_max) == (21300, 63450)
    assert p.hp_ratio == pytest.approx(0.34, abs=0.03)


@pytest.mark.parametrize("text,ratio", [("1.000/63.450", 1.0), ("63.450/63.450", 0.2), ("80.250/63.450", 1.0),
                                        ("0/0", 1.0)])
def test_number_incoherent_with_bar_is_rejected(load_sample, empty_profile, text, ratio):
    img = load_sample(NEGRO + ".png").copy()
    p0 = set_panel_hp_text(img, text, CFG)
    set_bar_ratio(img, p0.bar_rect, empty_profile, ratio)
    p = read_panel(img, CFG)
    assert p.present and p.hp_current is None and p.hp_max is None
    assert p.hp_ratio == pytest.approx(ratio, abs=0.04)


def test_empty_bar_zero_life(load_sample, empty_profile):
    img = load_sample(NEGRO + ".png").copy()
    p0 = set_panel_hp_text(img, "0/63.450", CFG)
    set_bar_ratio(img, p0.bar_rect, empty_profile, 0.0)
    p = read_panel(img, CFG)
    assert p.hp_ratio == pytest.approx(0.0, abs=0.02) and (p.hp_current, p.hp_max) == (0, 63450)


@pytest.mark.parametrize("text", ["12.345/98.765", "30.870/91.234", "7.081/9.999", "64.920/78.301",
                                  "1/1", "999/1.000", "56.789/65.432"])
def test_panel_numbers_not_in_samples(load_sample, empty_profile, text):
    cur = int(text.split("/")[0].replace(".", ""))
    mx = int(text.split("/")[1].replace(".", ""))
    for gap in (1, 0):
        img = load_sample(NEGRO + ".png").copy()
        p0 = set_panel_hp_text(img, text, CFG, gap=gap)
        set_bar_ratio(img, p0.bar_rect, empty_profile, cur / mx)
        p = read_panel(img, CFG)
        assert (p.hp_current, p.hp_max) == (cur, mx), (text, gap, p)


def test_no_bar_means_no_number(load_sample):
    """Sin ratio de barra validado el numero no se acepta (antes: 8131 con score 1.00 al tapar el '1')."""
    img = load_sample(COMBATE + ".png").copy()
    p0 = read_panel(img, CFG)
    br, nr = p0.bar_rect, p0.number_rect
    # borra las filas de la barra (tapas incluidas) para que read_bar no encuentre nada
    fill_rows(img, br.x - 16, br.y, br.right + 16, br.bottom, p0.rect.x + 2)
    assert read_bar(img, p0.rect, CFG["vision"]["panel"]) is None
    # y tapa el '1' inicial de "18.131"
    first = read_text_line(img[nr.y:nr.bottom, nr.x:nr.right], get_glyphs(GSEC), GSEC).boxes[0]
    fill_rows(img, nr.x + first.x, nr.y, nr.x + first.right, nr.bottom, nr.x)
    p = read_panel(img, CFG)
    assert p.present and p.hp_ratio is None
    assert p.hp_current is None and p.hp_max is None


def test_no_bar_with_intact_number_is_still_none(load_sample):
    img = load_sample(COMBATE + ".png").copy()
    p0 = read_panel(img, CFG)
    br = p0.bar_rect
    fill_rows(img, br.x - 16, br.y, br.right + 16, br.bottom, p0.rect.x + 2)
    p = read_panel(img, CFG)
    assert p.hp_ratio is None and p.hp_current is None
