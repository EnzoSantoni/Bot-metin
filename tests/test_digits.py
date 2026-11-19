"""Tests puros de bot/vision/digits.py con imagenes sinteticas armadas con las propias plantillas."""
import numpy as np
import pytest

from bot.config import DEFAULTS, ConfigError, validate_config
from bot.vision.coords import parse_coords
from bot.vision.digits import GlyphSet, char_filename, peel_glyphs, read_text_line, segment_glyphs, text_mask
from bot.vision.panel import parse_hp

COORDS = DEFAULTS["vision"]["glyphs"]["coords"]
PANEL = DEFAULTS["vision"]["glyphs"]["panel"]


def compose(text: str, glyphs: GlyphSet, section: dict, gap: int = 1, overlap: bool = False) -> np.ndarray:
    """Imagen BGR con `text` dibujado con las plantillas; overlap=True pega los caracteres sin hueco."""
    color = np.array(section["color"], np.uint8)
    chars = [c for c in text if c != " "]
    h = 14
    cursor, placed = 2, []
    for c in chars:
        t = glyphs.chars[c]
        if c == ".":
            cursor += 1    # la fuente siempre deja aire alrededor del punto
        placed.append((cursor, t))
        cursor += t.shape[1] + (0 if overlap and c != "." else gap)
    img = np.full((h, cursor + 4, 3), 40, np.uint8)
    for x, t in placed:
        y = 2 + 7 - t.shape[0] if c == "." else (2 if t.shape[0] >= 9 else 3)   # el punto va abajo, como en el juego
        region = img[y:y + t.shape[0], x:x + t.shape[1]]
        region[t] = color
    return img


@pytest.fixture(scope="module")
def cg() -> GlyphSet:
    g = GlyphSet.load(COORDS["dir"])
    assert g.chars, "faltan plantillas en templates/glyphs/coords"
    return g


@pytest.fixture(scope="module")
def pg() -> GlyphSet:
    g = GlyphSet.load(PANEL["dir"])
    assert g.chars, "faltan plantillas en templates/glyphs/panel"
    return g


def test_char_filenames_are_safe():
    assert char_filename("(") == "lparen.png" and char_filename("/") == "slash.png"
    assert char_filename("7") == "7.png" and char_filename(".") == "dot.png" and char_filename(",") == "comma.png"


def test_glyph_sets_have_all_digits(cg, pg):
    for g in (cg, pg):
        assert all(d in g.chars for d in "0123456789")
    assert all(c in cg.chars for c in "(),")
    assert all(c in pg.chars for c in "./")


@pytest.mark.parametrize("text", ["(453, 161)", "(678, 114)", "(0, 5)", "(1234, 987)"])
def test_read_coords_synthetic(cg, text):
    r = read_text_line(compose(text, cg, COORDS), cg, COORDS)
    assert r.text == text.replace(" ", "") and r.min_score == pytest.approx(1.0)
    assert len(r.boxes) == len(r.text)


@pytest.mark.parametrize("text", ["12.345/80.250", "80.250/80.250", "524/2.692", "5.213/6.713"])
def test_read_hp_synthetic(pg, text):
    r = read_text_line(compose(text, pg, PANEL), pg, PANEL)
    assert r.text == text and r.min_score == pytest.approx(1.0)


def test_touching_chars_are_separated(pg):
    img = compose("18.131/80.250", pg, PANEL, overlap=True)
    r = read_text_line(img, pg, PANEL)
    assert r.text == "18.131/80.250"


def test_unknown_char_is_question_mark_with_low_score(cg):
    img = compose("(453, 161)", cg, COORDS)
    # un garabato del color del texto entre dos digitos
    img = np.concatenate([img, np.full((14, 8, 3), 40, np.uint8)], axis=1)
    img[4:11, -6:-4] = COORDS["color"]
    r = read_text_line(img, cg, COORDS)
    assert "?" in r.text and r.min_score < COORDS["min_score"]


def test_empty_line_reads_nothing(cg):
    r = read_text_line(np.full((12, 40, 3), 30, np.uint8), cg, COORDS)
    assert r.text == "" and r.min_score == 0.0


def test_text_mask_and_segmentation(cg):
    img = compose("(12,3)", cg, COORDS)
    comps = segment_glyphs(text_mask(img, COORDS))
    assert len(comps) == 6 and [c[0].x for c in comps] == sorted(c[0].x for c in comps)


def test_parsers_are_strict():
    assert parse_coords("(678,114)") == (678, 114) and parse_coords("(678, 114)") == (678, 114)
    for bad in ("(678,11?)", "678,114", "(678,)", "(1,2,3)", "(12345,1)", ""):
        assert parse_coords(bad) is None
    assert parse_hp("18.131/80.250") == (18131, 80250) and parse_hp("524/2.692") == (524, 2692)
    for bad in ("18131/80250", "18.13/80.250", "?8.131/80.250", "1.234", "1.234/"):
        assert parse_hp(bad) is None


def test_default_text_config_is_valid_and_rejects_bad_values():
    import copy
    validate_config(copy.deepcopy(DEFAULTS))
    bad = copy.deepcopy(DEFAULTS)
    bad["vision"]["panel"]["bar"]["band"] = [1]
    with pytest.raises(ConfigError):
        validate_config(bad)


# --- sin circularidad: textos que no estan en ninguna sample, caracteres pegados ---------------------------

@pytest.mark.parametrize("overlap", [False, True])
def test_every_digit_pair_touching_panel_font(pg, overlap):
    bad = []
    for a in "0123456789":
        for b in "0123456789":
            text = f"{a}{b}"
            r = read_text_line(compose(text, pg, PANEL, overlap=overlap), pg, PANEL)
            if r.text != text:
                bad.append((text, r.text))
    assert not bad, bad


@pytest.mark.parametrize("overlap", [False, True])
def test_every_digit_pair_touching_coords_font(cg, overlap):
    bad = []
    for a in "0123456789":
        for b in "0123456789":
            text = f"{a}{b}"
            r = read_text_line(compose(text, cg, COORDS, overlap=overlap), cg, COORDS)
            if r.text != text:
                bad.append((text, r.text))
    assert not bad, bad


def test_slash_next_to_every_digit_touching(pg):
    bad = []
    for d in "0123456789":
        for text in (f"{d}/{d}", f"{d}/", f"/{d}", f"{d}.{d}"):   # el punto de la fuente nunca toca al digito
            r = read_text_line(compose(text, pg, PANEL, overlap=True), pg, PANEL)
            if r.text != text:
                bad.append((text, r.text))
    assert not bad, bad


@pytest.mark.parametrize("text", ["97.531/86.420", "13.579/24.680", "4.567/8.901", "70.123/70.123", "1/2"])
@pytest.mark.parametrize("overlap", [False, True])
def test_unseen_panel_numbers(pg, text, overlap):
    r = read_text_line(compose(text, pg, PANEL, overlap=overlap), pg, PANEL)
    assert r.text == text


@pytest.mark.parametrize("text", ["(1029, 384)", "(56, 7)", "(3857, 1209)", "(0, 4000)", "(7, 90)"])
@pytest.mark.parametrize("overlap", [False, True])
def test_unseen_coords(cg, text, overlap):
    r = read_text_line(compose(text, cg, COORDS, overlap=overlap), cg, COORDS)
    assert r.text == text.replace(" ", "")


def test_gap_where_a_char_is_missing_gives_question_mark(pg):
    img = compose("12.345/67.890", pg, PANEL)
    r0 = read_text_line(img, pg, PANEL)
    b = r0.boxes[4]     # el '4'
    img[:, b.x:b.right + 1] = 40
    r = read_text_line(img, pg, PANEL)
    assert "?" in r.text and r.min_score == 0.0 and parse_hp(r.text) is None


# --- margen entre glifos parecidos, plantillas vacias ------------------------------------------------------

def _glyph_img(t: np.ndarray, section: dict) -> np.ndarray:
    img = np.full((t.shape[0] + 4, t.shape[1] + 4, 3), 40, np.uint8)
    img[2:2 + t.shape[0], 2:2 + t.shape[1]][t] = section["color"]
    return img


@pytest.mark.parametrize("section_name", ["coords", "panel"])
def test_eight_missing_pixels_is_never_another_digit(section_name):
    """Un 8 al que le faltan 1-3 pixeles (cualquiera) sale como 8 o '?', nunca como 6/9/0."""
    sec = DEFAULTS["vision"]["glyphs"][section_name]
    g = GlyphSet.load(sec["dir"])
    eight = g.chars["8"]
    ys, xs = np.nonzero(eight)
    rng = np.random.default_rng(0)
    wrong = []
    for k in (1, 2, 3):
        for _ in range(60):
            t = eight.copy()
            idx = rng.choice(len(xs), k, replace=False)
            t[ys[idx], xs[idx]] = False
            r = read_text_line(_glyph_img(t, sec), g, sec)
            if (set(r.text) & set("0123456789")) - {"8"}:   # fragmentos sueltos pueden dar "." o ","
                wrong.append((k, r.text, [(int(ys[i]), int(xs[i])) for i in idx]))
    # el unico caso inevitable: sacarle justo los pixeles que distinguen un 8 de un 9 da un 9 de verdad
    for k, text, removed in wrong:
        t = eight.copy()
        for y, x in removed:
            t[y, x] = False
        assert any(np.array_equal(t, other) for other in g.chars.values()), (k, text, removed)


def test_eight_with_two_pixels_less_is_question_mark(cg):
    t = cg.chars["8"].copy()
    ys, xs = np.nonzero(t)
    t[ys[0], xs[0]] = False    # esquina superior izquierda
    t[ys[-1], xs[-1]] = False  # esquina inferior derecha
    r = read_text_line(_glyph_img(t, COORDS), cg, COORDS)
    assert r.text == "?"


def test_accept_requires_margin(cg):
    ch, best, second = cg.classify_ranked(cg.chars["8"])
    assert ch == "8" and best == 1.0 and second <= 0.8
    assert cg.accept(cg.chars["8"], 0.9, 0.15)[0] == "8"
    assert cg.accept(cg.chars["8"], 0.9, 0.5)[0] == "?"


def test_empty_template_is_discarded(tmp_path, caplog):
    import cv2
    cv2.imwrite(str(tmp_path / "0.png"), np.full((7, 4), 255, np.uint8))
    cv2.imwrite(str(tmp_path / "1.png"), np.zeros((7, 3), np.uint8))     # sin pixeles de texto
    with caplog.at_level("WARNING"):
        g = GlyphSet.load(tmp_path)
    assert list(g.chars) == ["0"] and "sin pixeles" in caplog.text


def test_peel_with_empty_template_does_not_crash():
    g = GlyphSet({"0": np.ones((7, 4), bool), "1": np.zeros((0, 0), bool)})
    crop = np.ones((7, 9), bool)
    assert peel_glyphs(crop, g)    # antes: IndexError
