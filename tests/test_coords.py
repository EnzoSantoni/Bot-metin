"""Coordenadas del personaje: verdad de referencia leida a ojo en recortes ampliados de cada sample."""
import numpy as np
import pytest

from bot.vision.coords import read_coords
from bot.vision.digits import get_glyphs, read_text_line
from synth import COORDS_DY, draw_text, fill_rows, make_cfg, shifted

CFG = make_cfg()
SEC = CFG["vision"]["glyphs"]["coords"]
NEGRO = "real_metin_negro_seleccionado.png"

EXPECTED = {
    "captcha": (709, 283),
    "explorando_zona": (709, 283),
    "items_en_piso": (680, 108),
    "metin_avaricia_seleccionado": (373, 250),
    "metin_cerca_sin_seleccionar": (603, 121),
    "metin_muy_lejos": (365, 214),
    "metin_oscuridad_a_distancia": (741, 91),
    "metin_oscuridad_combate_vida_parcial": (680, 108),
    "metin_oscuridad_seleccionado_vida_llena": (615, 126),
    "muerte": (694, 127),
    "real_metin_negro_inventario_mapa": (453, 161),
    "real_inventario_abierto": (453, 169),
    "real_rodeado": (688, 194),
    "real_metin_lejos_1": (704, 193),
    "real_metin_negro_seleccionado": (463, 165),
    "yang_en_piso": (740, 113),
}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_coords_on_samples(load_sample, name):
    c = read_coords(load_sample(name + ".png"), CFG)
    assert c is not None and (c.x, c.y) == EXPECTED[name] and c.score >= 0.8


@pytest.mark.parametrize("shift", [(3, 0, 0, 0), (0, 5, 2, 4), (6, 6, 6, 6), (-4, -3, 0, 5), (0, 0, 0, 12)])
def test_coords_position_robust(load_sample, shift):
    img = shifted(load_sample(NEGRO), *shift)
    c = read_coords(img, CFG)
    assert c is not None and (c.x, c.y) == (463, 165)


def _with_text(img, text, gap=1):
    """Sample con la linea de coordenadas reemplazada por `text` dibujado con plantillas."""
    img = img.copy()
    r = read_coords(img, CFG).rect
    fill_rows(img, r.x - 3, r.y - 2, r.right + 12, r.bottom + 2, r.x - 4)
    draw_text(img, text, get_glyphs(SEC), SEC["color"], r.x, r.y, COORDS_DY, gap)
    return img


@pytest.mark.parametrize("text,expected", [("(1029, 384)", (1029, 384)), ("(56, 7)", (56, 7)),
                                           ("(0, 0)", (0, 0)), ("(3999, 2058)", (3999, 2058)),
                                           ("(1234, 567)", (1234, 567)), ("(890, 1)", (890, 1))])
def test_coords_synthetic_all_digits(load_sample, text, expected):
    c = read_coords(_with_text(load_sample(NEGRO), text), CFG)
    assert c is not None and (c.x, c.y) == expected


@pytest.mark.parametrize("text", ["(4001, 100)", "(100, 4500)", "(9999, 9999)"])
def test_coords_out_of_range_is_none(load_sample, text):
    assert read_coords(_with_text(load_sample(NEGRO), text), CFG) is None


def _digit_boxes(img, r):
    return read_text_line(img[r.y:r.bottom, r.x:r.right], get_glyphs(SEC), SEC).boxes


@pytest.mark.parametrize("index", [1, 2, 3, 5, 6, 7])   # cada digito de "(463,165)"
def test_coords_erased_digit_never_gives_wrong_number(load_sample, index):
    img = load_sample(NEGRO).copy()
    r = read_coords(img, CFG).rect
    b = _digit_boxes(img, r)[index]
    fill_rows(img, r.x + b.x, r.y - 1, r.x + b.right, r.bottom + 1, r.x - 4)
    assert read_coords(img, CFG) is None


@pytest.mark.parametrize("index", [1, 3, 5, 7])
def test_coords_partially_covered_digit_is_none_or_correct(load_sample, index):
    img = load_sample(NEGRO).copy()
    r = read_coords(img, CFG).rect
    b = _digit_boxes(img, r)[index]
    fill_rows(img, r.x + b.x, r.y + b.y + 3, r.x + b.right, r.y + b.bottom, r.x - 4)   # mitad inferior
    c = read_coords(img, CFG)
    assert c is None or (c.x, c.y) == (463, 165)


def test_coords_noise_over_digit_never_wrong(load_sample):
    img = load_sample(NEGRO).copy()
    r = read_coords(img, CFG).rect
    rng = np.random.default_rng(1)
    b = _digit_boxes(img, r)[2]
    for _ in range(6):
        img[r.y + int(rng.integers(0, r.h)), r.x + b.x + int(rng.integers(0, b.w))] = SEC["color"]
    img[r.y + 4, r.x + b.x:r.x + b.right] = SEC["color"]
    c = read_coords(img, CFG)
    assert c is None or (c.x, c.y) == (463, 165)


def test_no_coords_on_blank(load_sample):
    img = load_sample(NEGRO).copy()
    r = read_coords(img, CFG).rect
    fill_rows(img, r.x - 3, r.y - 2, r.right + 12, r.bottom + 2, r.x - 4)
    assert read_coords(img, CFG) is None
    assert read_coords(np.zeros((300, 400, 3), np.uint8), CFG) is None


@pytest.mark.parametrize("index", [1, 2, 3, 5, 6, 7])
@pytest.mark.parametrize("part", ["top", "bottom", "left", "right", "corner_tl", "corner_br"])
def test_coords_partial_cover_never_other_value(load_sample, index, part):
    """Tapar parte de un digito: None o el valor correcto, nunca otro."""
    img = load_sample(NEGRO).copy()
    r = read_coords(img, CFG).rect
    b = _digit_boxes(img, r)[index]
    x0, x1, y0, y1 = r.x + b.x, r.x + b.right, r.y + b.y, r.y + b.bottom
    area = {"top": (x0, x1, y0, y0 + 2), "bottom": (x0, x1, y1 - 2, y1),
            "left": (x0, x0 + 1, y0, y1), "right": (x1 - 1, x1, y0, y1),
            "corner_tl": (x0, x0 + 2, y0, y0 + 1), "corner_br": (x1 - 2, x1, y1 - 1, y1)}[part]
    fill_rows(img, area[0], area[2], area[1], area[3], r.x - 4)
    c = read_coords(img, CFG)
    assert c is None or (c.x, c.y) == (463, 165)


def test_coords_eight_missing_pixels_never_wrong(load_sample):
    """Un '8' dibujado con 2 pixeles menos no puede leerse como otro numero."""
    g = get_glyphs(SEC)
    eight = g.chars["8"]
    ys, xs = np.nonzero(eight)
    for a in range(0, len(xs), 3):
        for b in range(a + 1, len(xs), 4):
            glyphs8 = eight.copy()
            glyphs8[ys[a], xs[a]] = False
            glyphs8[ys[b], xs[b]] = False
            img = _with_text(load_sample(NEGRO), "(8, 5)")
            r = read_coords(img, CFG).rect
            # reemplaza el primer 8 por la version recortada
            bx = _digit_boxes(img, r)[1]
            fill_rows(img, r.x + bx.x, r.y - 1, r.x + bx.right, r.bottom + 1, r.x - 4)
            region = img[r.y + bx.y:r.y + bx.y + 7, r.x + bx.x:r.x + bx.x + 4]
            region[glyphs8] = SEC["color"]
            c = read_coords(img, CFG)
            if c is not None:
                assert (c.x, c.y) in {(8, 5), (9, 5), (6, 5)} and any(
                    np.array_equal(glyphs8, o) for o in g.chars.values()), (a, b, c)
