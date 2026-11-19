"""Utilidades para armar imagenes sinteticas de prueba (recuadros de objetos, paneles pegados)."""
from __future__ import annotations

import cv2
import numpy as np

YELLOW = (0, 255, 255)


def text_masks(img: np.ndarray, rect, rows: int = 13) -> tuple[np.ndarray, np.ndarray | None]:
    """(mascara blanca recortada del nombre, mascara amarilla recortada o None) de un recuadro de la imagen."""
    inner = img[rect.y + 1:rect.bottom - 1, rect.x + 1:rect.right - 1]
    white = ((inner[-rows:] >= 250).all(axis=2)).astype(np.uint8) * 255
    ys, xs = np.nonzero(white)
    white = white[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    yel = None
    if inner.shape[0] > rows:
        m = cv2.inRange(inner[:-rows], (0, 230, 230), (30, 255, 255))
        if m.any():
            ys, xs = np.nonzero(m)
            yel = m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    return white, yel


def glyph_runs(mask: np.ndarray) -> list[np.ndarray]:
    """Separa una mascara en glifos por columnas vacias."""
    cols = mask.any(axis=0)
    out, start = [], None
    for i, c in enumerate(list(cols) + [False]):
        if c and start is None:
            start = i
        elif not c and start is not None:
            out.append(mask[:, start:i])
            start = None
    return out


def hcat(masks: list[np.ndarray], gap: int = 2) -> np.ndarray:
    """Une mascaras horizontalmente, alineadas abajo, con `gap` columnas vacias."""
    h = max(m.shape[0] for m in masks)
    parts = []
    for k, m in enumerate(masks):
        if k:
            parts.append(np.zeros((h, gap), np.uint8))
        parts.append(np.vstack([np.zeros((h - m.shape[0], m.shape[1]), np.uint8), m]))
    return np.hstack(parts)


def draw_box(img: np.ndarray, x: int, y: int, white: np.ndarray, yellow: np.ndarray | None = None,
             pad: int = 6, bg=(70, 60, 50)) -> tuple[int, int, int, int]:
    """Dibuja (in situ) un recuadro de objeto: borde negro de 1 px, fondo oscuro, nombre blanco y
    opcionalmente una linea amarilla arriba. Devuelve (x, y, w, h)."""
    tw = max(white.shape[1], yellow.shape[1] if yellow is not None else 0)
    w = tw + 2 * pad + 2
    h = 31 if yellow is not None else 15
    roi = img[y:y + h, x:x + w]
    roi[:] = (roi * 0.35 + np.array(bg) * 0.65).astype(np.uint8)
    cv2.rectangle(img, (x, y), (x + w - 1, y + h - 1), (0, 0, 0), 1)
    wy = y + h - 1 - 2 - white.shape[0] + 1 - 2   # linea de abajo, centrada en las 13 filas inferiores
    wx = x + 1 + (w - 2 - white.shape[1]) // 2
    sub = img[wy:wy + white.shape[0], wx:wx + white.shape[1]]
    sub[white > 0] = (255, 255, 255)
    if yellow is not None:
        yx = x + 1 + (w - 2 - yellow.shape[1]) // 2
        sub = img[y + 4:y + 4 + yellow.shape[0], yx:yx + yellow.shape[1]]
        sub[yellow > 0] = YELLOW
    return x, y, w, h
