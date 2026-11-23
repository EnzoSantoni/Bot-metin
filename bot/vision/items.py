"""Deteccion de objetos en el piso (recuadro oscuro con nombre) y distincion de yang (no se recoge)."""
from __future__ import annotations

import bisect
import logging
import math
from dataclasses import dataclass

import cv2
import numpy as np

from bot.config import resolve_path
from bot.geometry import Rect
from bot.vision.screen_mask import ScreenMask

logger = logging.getLogger(__name__)

_PAD = 3  # margen (px) al deslizar la plantilla sobre la linea de texto
# Imagen menor que esto no puede contener ni el recuadro mas chico (borde + 1 linea de texto): se ignora.
_MIN_IMG_W, _MIN_IMG_H = 30, 20


@dataclass(frozen=True)
class ItemLabel:
    """Recuadro de un objeto en el piso (coordenadas de cliente)."""
    rect: Rect
    kind: str                 # "item" o "yang"
    reserved: bool            # reservado para el jugador: amarillo que calza con la plantilla "Farfarfa's"
    click_point: tuple[int, int]
    allowed: bool             # el recuadro entero esta fuera de zonas excluidas y dentro de la imagen
    score: float              # coincidencia (0..1) con la palabra "Yang"; alta = yang
    reserved_other: bool = False  # amarillo que NO calza con el dueno propio: reservado para otro (no se recoge)
    owner_score: float = 0.0  # coincidencia (0..1) del amarillo con la plantilla del dueno (0 si no hay amarillo)


_yang_cache: dict[str, np.ndarray] = {}


def load_yang_template(cfg: dict) -> np.ndarray:
    """Mascara 0/255 de la palabra "Yang" (con cache)."""
    p = resolve_path(cfg["vision"]["items"]["yang_template"])
    key = str(p)
    if key not in _yang_cache:
        tpl = cv2.imread(key, cv2.IMREAD_GRAYSCALE)
        if tpl is None:
            raise FileNotFoundError(f"Falta la plantilla de Yang {p}")
        _yang_cache[key] = tpl
    return _yang_cache[key]


_owner_cache: dict[str, np.ndarray] = {}


def load_owner_template(cfg: dict) -> np.ndarray:
    """Mascara 0/255 del nombre del dueno ("Farfarfa's") en amarillo (con cache)."""
    p = resolve_path(cfg["vision"]["items"]["owner_template"])
    key = str(p)
    if key not in _owner_cache:
        tpl = cv2.imread(key, cv2.IMREAD_GRAYSCALE)
        if tpl is None:
            raise FileNotFoundError(f"Falta la plantilla del dueno {p}")
        _owner_cache[key] = tpl
    return _owner_cache[key]


def owner_score(yellow_mask: np.ndarray, template: np.ndarray, size_tol: int = 2) -> float:
    """Coincidencia (0..1) de la mascara amarilla recortada a su caja con la plantilla del dueno.

    Si el tamano de la caja difiere mas de `size_tol` px de la plantilla, 0 (otro nombre).
    """
    ys, xs = np.nonzero(yellow_mask)
    if len(xs) == 0:
        return 0.0
    crop = yellow_mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    if (abs(crop.shape[1] - template.shape[1]) > size_tol
            or abs(crop.shape[0] - template.shape[0]) > size_tol):
        return 0.0
    return yang_score(crop, template)


def _edge_runs(img_bgr: np.ndarray, icfg: dict) -> list[tuple[int, int, int]] | None:
    """Bordes horizontales negros (x, y, ancho) de al menos `min_edge_run` px; None si hay mas de `max_edges`."""
    b, g, r = (int(v) for v in icfg["border_color"])
    black = cv2.inRange(img_bgr, (b, g, r), (b, g, r))
    run = int(icfg["min_edge_run"])
    eroded = cv2.erode(black, np.ones((1, run), np.uint8))
    # Corte barato antes del analisis de componentes (caro con decenas de miles de tramos): imagen anomala.
    n_runs = int(np.count_nonzero(eroded[:, 1:] > eroded[:, :-1]) + np.count_nonzero(eroded[:, 0]))
    if n_runs > int(icfg["max_edges"]):
        return None
    n, _labels, stats, _ = cv2.connectedComponentsWithStats(eroded, connectivity=8)
    half = run // 2
    return [(int(stats[k][0]) - half, int(stats[k][1]), int(stats[k][2]) + 2 * half)
            for k in range(1, n) if stats[k][3] == 1]


def find_boxes(img_bgr: np.ndarray, icfg: dict) -> list[Rect]:
    """Recuadros (borde incluido): par de bordes negros alineados a una distancia valida y lados negros.

    Tolera un borde recortado (tapado) o alargado (por un pixel negro del fondo): los bordes de arriba y
    de abajo deben coincidir en x y ancho dentro de `edge_x_tol`, y entre los extremos posibles se elige
    el que tenga mas lados negros. La altura (distancia entre bordes) debe ser una de `box_dy_allowed`
    (1 o 2 lineas) +-`box_dy_tol`, para no armar recuadros con el borde de uno y el fondo de otro.
    Si hay mas de `max_edges` bordes candidatos (imagen anomala) avisa y devuelve [].
    """
    h, w = img_bgr.shape[:2]
    runs = _edge_runs(img_bgr, icfg)
    edges = sorted((e for e in runs or [] if icfg["box_w"][0] <= e[2] <= icfg["box_w"][1]),
                   key=lambda e: (e[1], e[0]))
    if runs is None or len(edges) > int(icfg["max_edges"]):
        logger.warning("Demasiados bordes candidatos (> max_edges=%d): se omiten los objetos de este ciclo",
                       icfg["max_edges"])
        return []
    black = np.array([int(v) for v in icfg["border_color"]], np.uint8)
    dy_lo, dy_hi = icfg["box_dy"]
    allowed = [int(v) for v in icfg["box_dy_allowed"]]
    dy_tol, x_tol = int(icfg["box_dy_tol"]), int(icfg["edge_x_tol"])
    valid_d = sorted({d for a in allowed for d in range(a - dy_tol, a + dy_tol + 1) if dy_lo <= d <= dy_hi})
    rows: dict[int, list[int]] = {}      # y -> indices en `edges` (ordenados por x)
    for i, e in enumerate(edges):
        rows.setdefault(e[1], []).append(i)
    row_x = {y: [edges[i][0] for i in idx] for y, idx in rows.items()}
    found: dict[tuple[int, int, int, int], float] = {}
    for x1, y1, w1 in edges:
        cands: list[int] = []
        for d in valid_d:
            y2 = y1 + d
            if y2 >= h or y2 not in rows:
                continue
            xs = row_x[y2]
            lo = bisect.bisect_left(xs, x1 - x_tol)
            hi = bisect.bisect_right(xs, x1 + x_tol)
            cands.extend(rows[y2][lo:hi])
        best: tuple[float, Rect] | None = None
        for i in cands:               # ya en orden (y, x): mismo desempate que el barrido completo
            x2, y2, w2 = edges[i]
            if abs((x1 + w1) - (x2 + w2)) > x_tol:
                continue
            d = y2 - y1
            for xl in {x1, x2}:
                for xr in {x1 + w1, x2 + w2}:
                    if xl < 0 or xr > w or xr - xl < icfg["box_w"][0]:
                        continue
                    side = img_bgr[y1:y2 + 1, [xl, xr - 1]]
                    frac = float((side == black).all(axis=2).mean())
                    if frac >= icfg["border_min_frac"] and (best is None or frac > best[0]):
                        best = (frac, Rect(xl, y1, xr - xl, d + 1))
        if best is not None:
            r = best[1]
            found[(r.x, r.y, r.w, r.h)] = best[0]
    return [Rect(*k) for k in found]


def _white(img: np.ndarray, icfg: dict) -> np.ndarray:
    return ((img >= int(icfg["white_min"])).all(axis=2)).astype(np.uint8) * 255


def yang_score(line_mask: np.ndarray, template: np.ndarray) -> float:
    """Mejor coincidencia (0..1) de la plantilla deslizada sobre la mascara blanca de la linea de texto."""
    patch = cv2.copyMakeBorder(line_mask, _PAD, _PAD, _PAD, _PAD, cv2.BORDER_CONSTANT, value=0)
    eh, ew = max(0, template.shape[0] - patch.shape[0]), max(0, template.shape[1] - patch.shape[1])
    if eh or ew:
        patch = cv2.copyMakeBorder(patch, 0, eh, 0, ew, cv2.BORDER_CONSTANT, value=0)
    res = cv2.matchTemplate(patch.astype(np.float32), template.astype(np.float32), cv2.TM_CCOEFF_NORMED)
    return float(np.clip(np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0).max(), 0.0, 1.0))


def find_items(img_bgr: np.ndarray, cfg: dict, mask: ScreenMask | None = None) -> list[ItemLabel]:
    """Objetos en el piso (kind "item") y yang (kind "yang"), ordenados por (y, x)."""
    h, w = img_bgr.shape[:2]
    if h < _MIN_IMG_H or w < _MIN_IMG_W:
        return []
    icfg = cfg["vision"]["items"]
    template = load_yang_template(cfg)
    rows = int(icfg["text_rows"])
    out: list[ItemLabel] = []
    for rect in find_boxes(img_bgr, icfg):
        if mask is not None and mask.overlap_fraction(rect) > icfg["max_zone_overlap"]:
            continue
        inner = img_bgr[rect.y + 1:rect.bottom - 1, rect.x + 1:rect.right - 1]
        top = max(0, inner.shape[0] - rows)
        line = _white(inner[top:], icfg)
        if np.count_nonzero(line) < icfg["min_white_px"]:
            continue
        ys, xs = np.nonzero(line)
        score = yang_score(line[:, xs.min():xs.max() + 1], template)
        kind = "yang" if score >= icfg["yang_min_score"] else "item"
        reserved = reserved_other = False
        o_score = 0.0
        if top > 0:
            upper = cv2.inRange(inner[:top], tuple(icfg["yellow_lo"]), tuple(icfg["yellow_hi"]))
            if int(np.count_nonzero(upper)) > icfg["yellow_noise_px"]:
                o_score = owner_score(upper, load_owner_template(cfg), int(icfg["owner_size_tol"]))
                # Ante la duda (amarillo que no calza con el dueno propio) se trata como de otro:
                # saltear es mas barato que trabarse. Solo es publico si practicamente no hay amarillo.
                reserved = o_score >= icfg["owner_min_score"]
                reserved_other = not reserved
        cp = rect.center
        # Conservador: el recuadro entero debe estar fuera de zonas y ventanas (con su margen).
        allowed = mask.allows_rect(rect) if mask is not None else (rect.x >= 0 and rect.y >= 0
                                                                    and rect.right <= w and rect.bottom <= h)
        out.append(ItemLabel(rect, kind, reserved, cp, allowed, score, reserved_other, o_score))
    out.sort(key=lambda i: (i.rect.y, i.rect.x))
    return out


def choose_item(items: list[ItemLabel], player_xy: tuple[int, int]) -> ItemLabel | None:
    """El objeto (no yang, no reservado para otro) permitido mas cercano al personaje."""
    ok = [i for i in items if i.kind == "item" and i.allowed and not i.reserved_other]
    if not ok:
        return None
    return min(ok, key=lambda i: math.dist(i.click_point, player_xy))


def label_signature(img: np.ndarray | None, rect: Rect, min_px: int = 15) -> np.ndarray | None:
    """Firma de un recuadro de objeto para reconocerlo aunque se corra en pantalla: la mascara de su texto BLANCO (el nombre
    del objeto; sin el amarillo del dueno, que desaparece cuando vence la reserva) recortada a la caja de ese texto. None si
    casi no hay texto blanco (imagen negra, recuadro tapado)."""
    if img is None or img.ndim != 3:
        return None
    x0, y0 = max(0, rect.x), max(0, rect.y)
    x1, y1 = min(img.shape[1], rect.right), min(img.shape[0], rect.bottom)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    roi = img[y0:y1, x0:x1].astype(np.int16)
    lo, hi = roi.min(axis=2), roi.max(axis=2)
    m = (lo >= 170) & (hi - lo <= 45)
    if int(m.sum()) < min_px:
        return None
    ys, xs = np.nonzero(m)
    return m[ys.min():ys.max() + 1, xs.min():xs.max() + 1].copy()


def same_label(a: np.ndarray | None, b: np.ndarray | None, min_iou: float = 0.7, shift: int = 2) -> bool:
    """Dos firmas (`label_signature`) son el mismo texto: tamano parecido (+-`shift` px) y coincidencia (IoU) >= `min_iou`
    con un corrimiento de hasta `shift` px."""
    if a is None or b is None:
        return False
    if abs(a.shape[0] - b.shape[0]) > shift or abs(a.shape[1] - b.shape[1]) > shift + 1:
        return False
    h, w = max(a.shape[0], b.shape[0]) + 2 * shift, max(a.shape[1], b.shape[1]) + 2 * shift
    pa = np.zeros((h, w), bool)
    pa[shift:shift + a.shape[0], shift:shift + a.shape[1]] = a
    best = 0.0
    for dy in range(0, 2 * shift + 1):
        for dx in range(0, 2 * shift + 1):
            pb = np.zeros((h, w), bool)
            pb[dy:dy + b.shape[0], dx:dx + b.shape[1]] = b
            union = int((pa | pb).sum())
            if union:
                best = max(best, int((pa & pb).sum()) / union)
    return best >= min_iou

