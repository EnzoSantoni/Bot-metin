"""Punto de clic sobre la parte VISIBLE de una piedra de metin (tapada por bichos, arboles o estructuras).

Logica pura sobre una captura BGR: genera candidatos dentro del cuerpo de la piedra, descarta los que caen sobre la silueta de
un monstruo (la columna debajo de su etiqueta) o sobre la etiqueta del personaje, y ordena por cuanto color de piedra hay
alrededor (un tronco de arbol es gris, de baja saturacion). Config: `vision.click_point`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from bot.geometry import Rect
from bot.ground import depth_px
from bot.vision.far_metin import _core_mask
from bot.vision.monsters import MonsterLabel


@dataclass(frozen=True)
class ClickPoint:
    point: tuple[int, int]
    stone_frac: float          # fraccion de pixeles de color de piedra en el parche del punto (0..1)
    score: float


def stone_rect_near(img_bgr: np.ndarray, around: tuple[int, int], cfg: dict) -> Rect | None:
    """Rect del cuerpo de la piedra (nucleo de color de `vision.far_metin`) cerca de `around` (la base de su etiqueta):
    caja `vision.click_point.stone_search` = [medio ancho, arriba, abajo] px; el bloque mas grande. None si no hay."""
    half_w, up, down = cfg["vision"]["click_point"]["stone_search"]
    h, w = img_bgr.shape[:2]
    x0, y0 = max(0, around[0] - half_w), max(0, around[1] - up)
    x1, y1 = min(w, around[0] + half_w), min(h, around[1] + down)
    if x1 <= x0 or y1 <= y0:
        return None
    hsv = cv2.cvtColor(img_bgr[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    core = cv2.morphologyEx(_core_mask(hsv, cfg["vision"]["far_metin"]), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(core, connectivity=8)
    if n <= 1:
        return None
    k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, bw, bh, area = (int(v) for v in stats[k])
    if area < 20:
        return None
    return Rect(x + x0, y + y0, bw, bh)


def monster_column(label: Rect, shape: tuple[int, ...], cfg: dict) -> Rect:
    """Zona de pantalla que ocupa el monstruo de la etiqueta `label` (esta debajo de ella): la etiqueta mas una columna hacia
    abajo de `body_h_mult` x el alto esperado de la silueta (`body_h_k` x distancia de la etiqueta al horizonte)."""
    c = cfg["vision"]["click_point"]
    body_h = max(float(c["body_h_min_px"]), float(c["body_h_k"]) * depth_px(label.center[1], shape, cfg))
    half = body_h * float(c["body_w_frac"]) + float(c["col_margin_px"])
    x0 = min(label.x, int(label.center[0] - half))
    x1 = max(label.right, int(label.center[0] + half))
    return Rect(x0, label.y, x1 - x0, label.h + int(body_h * float(c["body_h_mult"])))


def _inside(pt: tuple[int, int], r: Rect, margin: int = 0) -> bool:
    return r.x - margin <= pt[0] < r.right + margin and r.y - margin <= pt[1] < r.bottom + margin


def stone_fraction(img_bgr: np.ndarray, pt: tuple[int, int], cfg: dict) -> float:
    """Fraccion de pixeles de color de piedra (rojo/naranja/rosado con saturacion >= `stone_s_min`) en el parche de radio
    `patch_r` alrededor de `pt`. Tronco, nieve y piso (grises o azulados) dan ~0."""
    c = cfg["vision"]["click_point"]
    r = int(c["patch_r"])
    h, w = img_bgr.shape[:2]
    x0, y0, x1, y1 = max(0, pt[0] - r), max(0, pt[1] - r), min(w, pt[0] + r + 1), min(h, pt[1] + r + 1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    hsv = cv2.cvtColor(img_bgr[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    hue = hsv[..., 0]
    warm = ((hue <= 20) | (hue >= 165)) & (hsv[..., 1] >= int(c["stone_s_min"])) & (hsv[..., 2] >= 60)
    return float(np.count_nonzero(warm)) / warm.size


def click_candidates(img_bgr: np.ndarray, rect: Rect, monsters: list[MonsterLabel], own_label: Rect | None,
                     cfg: dict) -> list[ClickPoint]:
    """Puntos de clic validos dentro de `rect` (cuerpo de la piedra), del mejor al peor: fuera de las columnas de los monstruos
    y de la etiqueta del personaje, con >= `min_stone_frac` de color de piedra. Puntaje = fraccion de piedra - cuanto se aleja
    del centro (en medios rects). Vacio si no queda ninguno libre."""
    c = cfg["vision"]["click_point"]
    cols = [monster_column(m.rect, img_bgr.shape, cfg) for m in monsters]
    own = [] if own_label is None else [own_label]
    out: list[ClickPoint] = []
    seen: set[tuple[int, int]] = set()
    for fx, fy in c["grid"]:
        pt = (rect.x + int(round(fx * (rect.w - 1))), rect.y + int(round(fy * (rect.h - 1))))
        if pt in seen:
            continue
        seen.add(pt)
        if any(_inside(pt, col) for col in cols) or any(_inside(pt, o, int(c["own_margin_px"])) for o in own):
            continue
        frac = stone_fraction(img_bgr, pt, cfg)
        if frac < float(c["min_stone_frac"]):
            continue
        off = math.hypot((fx - 0.5) * 2, (fy - 0.5) * 2)
        out.append(ClickPoint(pt, frac, frac - 0.2 * off))
    out.sort(key=lambda k: -k.score)
    return out


def point_is_free(img_bgr: np.ndarray, pt: tuple[int, int], monsters: list[MonsterLabel], own_label: Rect | None,
                  cfg: dict) -> bool:
    """`pt` ya es un buen punto de clic: fuera de columnas de monstruos y de la etiqueta propia, con color de piedra."""
    c = cfg["vision"]["click_point"]
    if any(_inside(pt, monster_column(m.rect, img_bgr.shape, cfg)) for m in monsters):
        return False
    if own_label is not None and _inside(pt, own_label, int(c["own_margin_px"])):
        return False
    return stone_fraction(img_bgr, pt, cfg) >= float(c["min_stone_frac"])
