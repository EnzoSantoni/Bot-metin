"""Etiquetas de monstruos: nombre en rojo plano con "Lv XX" en verde plano pegado a la izquierda."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import cv2
import numpy as np

from bot.geometry import Rect
from bot.vision.screen_mask import ScreenMask

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MonsterLabel:
    rect: Rect                 # "Lv XX" + nombre, coords de cliente
    center: tuple[int, int]    # centro de la etiqueta (el monstruo queda debajo)


def _color_mask(img_bgr: np.ndarray, bgr: list[int], tol: int) -> np.ndarray:
    """Pixeles que coinciden con un color plano (0/255), con tolerancia por canal."""
    lo = np.clip(np.array(bgr) - tol, 0, 255).astype(np.uint8)
    hi = np.clip(np.array(bgr) + tol, 0, 255).astype(np.uint8)
    return cv2.inRange(img_bgr, lo, hi)


def _groups(binary: np.ndarray, kernel: list[int], min_pixels: int) -> list[Rect]:
    """Rectangulos ajustados de los grupos de pixeles (letras unidas por dilatacion)."""
    kw, kh = kernel
    contours, _ = cv2.findContours(cv2.dilate(binary, np.ones((kh, kw), np.uint8)),
                                   cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out: list[Rect] = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        ys, xs = np.nonzero(binary[y:y + h, x:x + w])
        if len(xs) < min_pixels:
            continue
        out.append(Rect(x + int(xs.min()), y + int(ys.min()), int(xs.max() - xs.min() + 1),
                        int(ys.max() - ys.min() + 1)))
    return out


def _red_run_end(red_cols: np.ndarray, x_start: int, max_gap: int) -> int:
    """Columna (exclusiva) donde termina el texto rojo que empieza en x_start; tolera huecos de max_gap px."""
    end, gap = x_start, 0
    for x in range(x_start, len(red_cols)):
        if red_cols[x]:
            end, gap = x + 1, 0
        else:
            gap += 1
            if gap > max_gap:
                break
    return end


def find_monsters(img_bgr: np.ndarray, cfg: dict, mask: ScreenMask | None = None) -> list[MonsterLabel]:
    """Etiquetas de monstruo: bloque verde "Lv" con texto rojo pegado a su derecha, a la misma altura.

    Cada bloque verde cuenta una vez; el rojo se mira por pixeles a la derecha del verde (las etiquetas
    apiladas de monstruos pegados se tapan entre si y sus nombres rojos se funden).
    """
    h, w = img_bgr.shape[:2]
    if h == 0 or w == 0:
        return []
    m = cfg["vision"]["monsters"]
    red = _color_mask(img_bgr, m["red_bgr"], m["color_tol"]) > 0
    green = _groups(_color_mask(img_bgr, m["green_bgr"], m["color_tol"]), m["green_kernel"], m["min_green_pixels"])
    out: list[MonsterLabel] = []
    for g in green:
        if not (m["min_green_w"] <= g.w <= m["max_green_w"] and m["min_green_h"] <= g.h <= m["max_green_h"]):
            continue
        x0 = g.right
        x1 = min(w, x0 + m["max_gap_px"] + m["probe_w"])
        band = red[g.y:g.bottom, x0:x1]
        if band.size == 0 or int(band.sum()) < max(1, m["min_red_pixels"]):
            continue
        first = int(np.nonzero(band.any(axis=0))[0][0])
        if first > m["max_gap_px"]:
            continue
        cols = red[g.y:g.bottom, :].any(axis=0)
        end = min(_red_run_end(cols, x0 + first, m["word_gap_px"]), g.x + m["max_label_w"])
        rect = Rect(g.x, g.y, end - g.x, g.h)
        if mask is not None and mask.overlap_fraction(rect) > m["max_zone_overlap"]:
            continue
        out.append(MonsterLabel(rect, rect.center))
    out.sort(key=lambda lb: (lb.rect.y, lb.rect.x))
    return out


def monsters_near(monsters: list[MonsterLabel], player_xy: tuple[int, int],
                  radius_px: float | None = None, cfg: dict | None = None) -> list[MonsterLabel]:
    """Etiquetas cuyo centro queda a menos de `radius_px` del personaje (por defecto `monsters.near_radius_px`)."""
    if radius_px is None:
        if cfg is None:
            raise ValueError("monsters_near necesita radius_px o cfg")
        radius_px = float(cfg["vision"]["monsters"]["near_radius_px"])
    return [mo for mo in monsters if math.dist(mo.center, player_xy) <= radius_px]


def count_near(monsters: list[MonsterLabel], player_xy: tuple[int, int],
               radius_px: float | None = None, cfg: dict | None = None) -> int:
    """Cantidad de monstruos cerca del personaje."""
    return len(monsters_near(monsters, player_xy, radius_px, cfg))
