"""Lectura de las coordenadas del personaje, "(x, y)", debajo del minimapa."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import numpy as np

from bot.geometry import Rect
from bot.vision.digits import GlyphSet, get_glyphs, read_text_line, text_mask  # noqa: F401  (get_glyphs: compat)
from bot.vision.screen_mask import anchor_rect

logger = logging.getLogger(__name__)

_COORDS_RE = re.compile(r"^\((\d{1,4}),(\d{1,4})\)$")


@dataclass(frozen=True)
class Coords:
    x: int
    y: int
    score: float
    rect: Rect | None = None   # linea de texto en coordenadas de cliente


def parse_coords(text: str) -> tuple[int, int] | None:
    """'(678,114)' -> (678, 114); None si el texto no tiene exactamente ese formato (tolera espacios)."""
    m = _COORDS_RE.match(text.replace(" ", ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def find_text_lines(mask: np.ndarray, line_w: tuple[int, int], line_h: tuple[int, int]) -> list[Rect]:
    """Lineas de texto de una mascara 0/255: bandas de filas con pixeles, con alto y ancho dentro de rango."""
    rows = mask.any(axis=1)
    out: list[Rect] = []
    y = 0
    n = len(rows)
    while y < n:
        if not rows[y]:
            y += 1
            continue
        y0 = y
        while y < n and rows[y]:
            y += 1
        h = y - y0
        if not (line_h[0] <= h <= line_h[1]):
            continue
        cols = np.nonzero(mask[y0:y].any(axis=0))[0]
        w = int(cols[-1] - cols[0] + 1)
        if line_w[0] <= w <= line_w[1]:
            out.append(Rect(int(cols[0]), y0, w, h))
    return out


def read_coords(img_bgr: np.ndarray, cfg: dict) -> Coords | None:
    """Coordenadas "(x, y)" del personaje, o None si no se leen con confianza suficiente."""
    vc = cfg["vision"]["coords"]
    section = cfg["vision"]["glyphs"]["coords"]
    glyphs = get_glyphs(section)
    ih, iw = img_bgr.shape[:2]
    region = anchor_rect(iw, ih, vc["search"]).clip(iw, ih)
    if region is None:
        return None
    sub = img_bgr[region.y:region.bottom, region.x:region.right]
    for line in find_text_lines(text_mask(sub, section), vc["line_w"], vc["line_h"]):
        read = read_text_line(sub[line.y:line.bottom, line.x:line.right], glyphs, section)
        parsed = parse_coords(read.text)
        if parsed is None or read.min_score < vc["min_score"]:
            continue
        lo, hi = vc["range"]
        if not (lo <= parsed[0] <= hi and lo <= parsed[1] <= hi):
            continue
        return Coords(parsed[0], parsed[1], read.min_score,
                      Rect(region.x + line.x, region.y + line.y, line.w, line.h))
    return None
