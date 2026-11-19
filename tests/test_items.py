"""Objetos en el piso: verdad de referencia sacada de recortes de las samples (ver debug/crops)."""
import copy
import math
import pathlib
import time

import cv2
import numpy as np
import pytest

from bot.config import DEFAULTS
from bot.geometry import Rect
from bot.vision.items import ItemLabel, choose_item, find_boxes, find_items
from bot.vision.screen_mask import build_screen_mask, find_ui_windows
from tests._synth import draw_box, glyph_runs, hcat, text_masks

CFG = copy.deepcopy(DEFAULTS)
ICFG = CFG["vision"]["items"]

ALL = ["captcha", "explorando_zona", "items_en_piso", "metin_avaricia_seleccionado",
       "metin_cerca_sin_seleccionar", "metin_muy_lejos", "metin_oscuridad_a_distancia",
       "metin_oscuridad_combate_vida_parcial", "metin_oscuridad_seleccionado_vida_llena", "muerte",
       "real_inventario_abierto", "real_metin_negro_inventario_mapa", "real_metin_negro_seleccionado",
       "yang_en_piso"]

# (x, y, w, h, reservado_para_Enzo)  -- leidos a ojo de los recortes
GT_ITEMS = {
    "items_en_piso": [(902, 561, 107, 31, True), (811, 616, 83, 31, True), (942, 616, 135, 31, True),
                      (869, 649, 131, 31, True), (1551, 755, 89, 15, False)],   # el ultimo: "Uniforme Negro+" publico
    "metin_oscuridad_combate_vida_parcial": [(807, 567, 83, 31, True), (1547, 759, 89, 31, True)],
}
GT_YANG = [(815, 640), (768, 643), (898, 645), (739, 685), (849, 697), (737, 708), (825, 714),
           (862, 714), (726, 725), (772, 735)]   # esquina sup. izq. de cada "Yang" en yang_en_piso


def run(img, mask=True):
    m = build_screen_mask(img.shape, CFG, extra=find_ui_windows(img, CFG)) if mask else None
    return find_items(img, CFG, m)


def close(r: Rect, x, y, w, h, tol=2):
    return abs(r.x - x) <= tol and abs(r.y - y) <= tol and abs(r.w - w) <= tol and abs(r.h - h) <= tol


@pytest.fixture(scope="module")
def imgs():
    root = pathlib.Path(__file__).resolve().parent.parent / "samples"
    return {n: cv2.imread(str(root / f"{n}.png")) for n in ALL}


# --------------------------------------------------------------------------- samples
@pytest.mark.parametrize("name", [n for n in ALL if n not in GT_ITEMS and n != "yang_en_piso"])
def test_sin_objetos(imgs, name):
    """Chat, inventario, Mapa grande, paneles, nombres de monstruos y metin no generan objetos."""
    for use_mask in (True, False):
        assert run(imgs[name], use_mask) == []


@pytest.mark.parametrize("name", list(GT_ITEMS))
def test_objetos_samples(imgs, name):
    items = run(imgs[name])
    gt = GT_ITEMS[name]
    assert len(items) == len(gt)
    for it, (x, y, w, h, mine) in zip(items, gt):
        assert it.kind == "item" and close(it.rect, x, y, w, h), (it.rect, (x, y, w, h))
        assert it.reserved == mine and it.reserved_other is False
        assert it.allowed
        if mine:
            assert it.owner_score >= 0.99     # el amarillo propio da 1.0 (fuente exacta)
        assert it.score < 0.85                # lejos de "Yang" (medido: 0.54-0.73)


def test_yang_en_piso(imgs):
    items = run(imgs["yang_en_piso"])
    assert len(items) == 10 and all(i.kind == "yang" and i.score >= 0.99 for i in items)
    got = sorted((i.rect.x, i.rect.y) for i in items)
    for (gx, gy), (x, y) in zip(sorted(GT_YANG), got):
        assert abs(gx - x) <= 2 and abs(gy - y) <= 2
    assert choose_item(items, (900, 700)) is None


# --------------------------------------------------------------------------- yang sintetico
@pytest.fixture(scope="module")
def parts(imgs):
    ip = imgs["items_en_piso"]
    bx = {b.x: b for b in find_boxes(ip, ICFG)}
    w_uni, y_uni = text_masks(ip, bx[811])
    w_cha, _ = text_masks(ip, bx[869])
    w_gue, _ = text_masks(ip, bx[942])
    yg = imgs["yang_en_piso"]
    w_yang, _ = text_masks(yg, find_boxes(yg, ICFG)[0])
    return {"uni": w_uni, "yellow": y_uni, "d2": glyph_runs(w_cha)[-1], "d0": glyph_runs(w_gue)[-1],
            "yang": w_yang, "cha": w_cha, "gue": w_gue}


def test_yang_con_numero_delante(imgs, parts):
    bg = imgs["explorando_zona"].copy()
    num = hcat([parts["d2"], parts["d0"]])
    gap = np.zeros((8, 5), np.uint8)
    draw_box(bg, 300, 300, hcat([num, gap, parts["yang"]]))                    # "20 Yang"
    draw_box(bg, 300, 400, hcat([parts["d2"], gap, parts["yang"]]))            # "2 Yang"
    items = find_items(bg, CFG)
    assert len(items) == 2 and all(i.kind == "yang" for i in items)
    assert choose_item(items, (300, 300)) is None


def test_item_con_texto_parecido_no_es_yang(imgs, parts):
    bg = imgs["explorando_zona"].copy()
    draw_box(bg, 300, 300, parts["uni"])
    (it,) = find_items(bg, CFG)
    assert it.kind == "item" and not it.reserved and not it.reserved_other


# --------------------------------------------------------------------------- reservado para otro
def test_reservado_propio_sintetico(imgs, parts):
    bg = imgs["explorando_zona"].copy()
    draw_box(bg, 300, 300, parts["uni"], parts["yellow"])
    (it,) = find_items(bg, CFG)
    assert it.reserved and not it.reserved_other and it.owner_score >= 0.99


def test_poco_amarillo_cuenta_como_de_otro(parts, imgs):
    """Con 10 px amarillos que no calzan con el dueno propio: reserved_other (ante la duda, de otro)."""
    y = np.zeros_like(parts["yellow"])
    ys, xs = np.nonzero(parts["yellow"])
    y[ys[:10], xs[:10]] = 255
    bg = imgs["explorando_zona"].copy()
    draw_box(bg, 300, 300, parts["uni"], y)
    (it,) = find_items(bg, CFG)
    assert it.reserved_other and not it.reserved
    assert choose_item([it], (0, 0)) is None


def test_publico_sin_amarillo_sigue_publico(imgs, parts):
    bg = imgs["explorando_zona"].copy()
    draw_box(bg, 300, 300, parts["uni"], np.zeros_like(parts["yellow"]))   # dos lineas, sin amarillo
    (it,) = find_items(bg, CFG)
    assert not it.reserved and not it.reserved_other and it.allowed


def test_reservado_propio_tolera_pixeles_perdidos(imgs, parts):
    rng = np.random.default_rng(1)
    y = parts["yellow"].copy()
    ys, xs = np.nonzero(y)
    k = rng.choice(len(xs), 4, replace=False)
    y[ys[k], xs[k]] = 0
    bg = imgs["explorando_zona"].copy()
    draw_box(bg, 300, 300, parts["uni"], y)
    (it,) = find_items(bg, CFG)
    assert it.reserved and it.owner_score >= ICFG["owner_min_score"]


def _letra_cambiada(p):
    y = p["yellow"].copy()
    y[:, 15:20] = 0       # una letra del medio borrada
    return y


@pytest.mark.parametrize("label,make", [
    ("otro_nombre_Piedra", lambda p: p["gue"][:, :43]),
    ("otro_nombre_Contra", lambda p: p["cha"][:, 44:90]),
    ("otro_nombre_largo", lambda p: p["gue"]),
    ("espejado", lambda p: p["yellow"][:, ::-1]),
    ("sin_la_s", lambda p: p["yellow"][:, :-8]),
    ("letra_borrada", _letra_cambiada),
])
def test_reservado_para_otro(imgs, parts, label, make):
    """Otro texto amarillo -> reserved_other, no reserved, y choose_item lo saltea."""
    bg = imgs["explorando_zona"].copy()
    draw_box(bg, 300, 300, parts["uni"], make(parts))
    (it,) = find_items(bg, CFG)
    assert it.kind == "item" and it.reserved_other and not it.reserved, (label, it.owner_score)
    assert it.owner_score < ICFG["owner_min_score"] - 0.04   # margen holgado
    assert choose_item([it], (0, 0)) is None


def test_reservado_para_otro_sobre_items_en_piso(imgs, parts):
    """Se reemplaza el amarillo de un recuadro real: el resto sigue igual y el elegido cambia."""
    img = imgs["items_en_piso"].copy()
    player = (925, 525)
    antes = choose_item(run(img), player)
    r = antes.rect
    inner = img[r.y + 1:r.bottom - 1, r.x + 1:r.right - 1]
    upper = inner[:-13]
    mask = cv2.inRange(upper, (0, 230, 230), (30, 255, 255))
    upper[mask > 0] = upper[mask == 0].mean(axis=0).astype(np.uint8)
    word = parts["gue"][:, :43]
    sub = inner[4:4 + word.shape[0], 4:4 + word.shape[1]]
    sub[word > 0] = (0, 255, 255)
    items = run(img)
    assert len(items) == 5
    mod = [i for i in items if close(i.rect, r.x, r.y, r.w, r.h)][0]
    assert mod.reserved_other and not mod.reserved
    assert sum(i.reserved for i in items) == 3
    despues = choose_item(items, player)
    assert despues is not None and despues.click_point != antes.click_point


# --------------------------------------------------------------------------- robustez
@pytest.mark.parametrize("dx,dy", [(0, 0), (5, 3), (12, 12), (0, 12), (12, 0), (7, 9)])
def test_desplazamiento(imgs, dx, dy):
    """Samples de distinto tamano: recortar por arriba/izquierda o por abajo/derecha no altera nada."""
    for name, gt in GT_ITEMS.items():
        img = imgs[name]
        for crop, (ox, oy) in ((img[dy:, dx:], (dx, dy)), (img[:img.shape[0] - dy, :img.shape[1] - dx], (0, 0))):
            items = run(np.ascontiguousarray(crop))
            assert len(items) == len(gt)
            for it, (x, y, w, h, mine) in zip(items, gt):
                assert close(it.rect, x - ox, y - oy, w, h) and it.reserved == mine
    ys = run(np.ascontiguousarray(imgs["yang_en_piso"][dy:, dx:]))
    assert len(ys) == 10 and all(i.kind == "yang" for i in ys)


def test_lado_tapado_parcialmente(imgs):
    """Se tapa la mitad de un borde lateral: sigue habiendo recuadro."""
    img = imgs["items_en_piso"].copy()
    x, y, w, h = 811, 616, 83, 31
    img[y:y + h // 2, x:x + 2] = (120, 90, 80)           # mitad superior del borde izquierdo
    items = run(img)
    assert len(items) == 5 and any(close(i.rect, x, y, w, h) for i in items)


def test_recuadros_apilados_pegados(imgs, parts):
    """Recuadros uno justo debajo del otro (1 px de separacion) se ven como recuadros distintos."""
    bg = imgs["explorando_zona"].copy()
    draw_box(bg, 300, 300, parts["uni"], parts["yellow"])
    draw_box(bg, 300, 300 + 32, parts["uni"], parts["yellow"])
    draw_box(bg, 300, 300 + 64, parts["uni"])
    items = find_items(bg, CFG)
    assert sorted(i.rect.y for i in items) == [300, 332, 364]
    assert sorted(i.rect.h for i in items) == [15, 31, 31]


def test_recuadro_en_borde_de_imagen(imgs, parts):
    """Un recuadro cortado por el borde de la imagen no genera excepcion (puede no detectarse)."""
    bg = imgs["explorando_zona"].copy()
    draw_box(bg, 300, 300, parts["uni"], parts["yellow"])
    for crop in (bg[:, 330:], bg[:315, :], bg[:, :350], bg[310:, :]):
        find_items(np.ascontiguousarray(crop), CFG)


# --------------------------------------------------------------------------- choose_item
def _it(x, y=0, kind="item", allowed=True, other=False, mine=False):
    return ItemLabel(Rect(x, y, 10, 10), kind, mine, (x, y), allowed, 0.0, other)


def test_choose_item_mas_cercano_entre_permitidos():
    items = [_it(5, kind="yang"), _it(10, other=True), _it(15, allowed=False), _it(60), _it(40, mine=True)]
    assert choose_item(items, (0, 0)).click_point == (40, 0)
    assert choose_item([_it(5, kind="yang"), _it(10, other=True), _it(15, allowed=False)], (0, 0)) is None
    assert choose_item([], (0, 0)) is None


def test_choose_item_en_samples(imgs):
    items = run(imgs["items_en_piso"])
    player = (925, 525)
    best = choose_item(items, player)
    expected = min(items, key=lambda i: math.dist(i.click_point, player))
    assert best == expected and best.rect.x == 902      # "Piedra Grand Master"


def test_objeto_bajo_inventario_no_permitido(imgs, parts):
    """Un recuadro pegado dentro de la ventana de inventario se detecta pero allowed=False y no se elige."""
    img = imgs["real_inventario_abierto"].copy()
    win = find_ui_windows(img, CFG)
    assert win
    r = win[0]
    draw_box(img, r.x + 40, r.y + 200, parts["uni"], parts["yellow"])
    items = run(img)
    inv = [i for i in items if r.x <= i.click_point[0] <= r.right and r.y <= i.click_point[1] <= r.bottom]
    assert all(not i.allowed for i in inv)
    assert choose_item(items, (r.x + 50, r.y + 210)) is None


def test_objeto_pegado_a_ventana_no_permitido(imgs, parts):
    """Recuadro fuera de la ventana pero dentro de su margen: el centro estaba libre, el recuadro no."""
    img = imgs["real_inventario_abierto"].copy()
    r = find_ui_windows(img, CFG)[0]
    m = build_screen_mask(img.shape, CFG, extra=[r])
    draw_box(img, r.x - 90, r.y + 200, parts["uni"], parts["yellow"])   # termina justo en el borde de la ventana
    (it,) = find_items(img, CFG, m)
    assert m.allows(*it.click_point) and not it.allowed
    assert choose_item([it], it.click_point) is None


# --------------------------------------------------------------------------- rendimiento
def test_rendimiento_peor_caso_bordes():
    """Rayas negras de 1 px cortadas cada 25 px (miles de bordes): tope max_edges, rapido y sin objetos."""
    img = np.full((1057, 1858, 3), 128, np.uint8)
    img[::2] = 0
    img[:, ::25] = 128
    t0 = time.perf_counter()
    assert find_boxes(img, ICFG) == []
    assert time.perf_counter() - t0 < 0.5
    # sin tope tambien debe ser aceptable (O(E*k))
    icfg = dict(ICFG, max_edges=10**9)
    img2 = np.full((1057, 1858, 3), 128, np.uint8)
    img2[::15] = 0
    img2[:, ::30] = 128
    t0 = time.perf_counter()
    find_boxes(img2, icfg)
    assert time.perf_counter() - t0 < 1.5


def test_tiempo_ciclo_completo(imgs):
    """Ciclo de deteccion completo (mascara, metin, panel, coords, objetos, captcha, muerte) bajo 150 ms (laxo)."""
    from bot.vision.captcha import find_captcha
    from bot.vision.coords import read_coords
    from bot.vision.death import find_death
    from bot.vision.metin import find_metins
    from bot.vision.panel import read_panel

    img = imgs["items_en_piso"]

    def ciclo():
        m = build_screen_mask(img.shape, CFG, extra=find_ui_windows(img, CFG))
        find_metins(img, CFG, m)
        read_panel(img, CFG)
        read_coords(img, CFG)
        find_items(img, CFG, m)
        find_captcha(img, CFG)
        find_death(img, CFG)

    ciclo()   # calienta caches de plantillas
    tiempos = []
    for _ in range(3):
        t0 = time.perf_counter()
        ciclo()
        tiempos.append((time.perf_counter() - t0) * 1000)
    assert min(tiempos) < 150, tiempos
