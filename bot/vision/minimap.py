"""Rumbo de la camara a partir del minimapa (arriba a la derecha).

El minimapa tiene el norte fijo y muestra, centrado en el personaje, un abanico (cono) mas claro hacia donde mira la
camara. Se miden, en rayos alrededor del centro, los V (brillo HSV) medios entre `r_min` y `r_max` px; el abanico es el
arco mas largo con exceso de brillo sobre la mediana. El rumbo es el centro de ese arco, en grados con el marco de la
imagen: atan2(dy, dx) con y hacia abajo (0 = este, 90 = sur), el mismo de las coordenadas del juego.
Config: `vision.minimap`.
"""
from __future__ import annotations

import math

import cv2
import numpy as np


def _rays(cfg: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(angulos en rad, radios en px, matriz de pasos) de los rayos de muestreo."""
    m = cfg["vision"]["minimap"]
    angs = np.radians(np.arange(0.0, 360.0, float(m["step_deg"])))
    rads = np.arange(float(m["r_min"]), float(m["r_max"]))
    return angs, rads, np.outer(np.cos(angs), rads)


def camera_heading(img: np.ndarray, cfg: dict) -> float | None:
    """Rumbo de la camara en grados [0, 360) (0 = este, 90 = sur) o None si no se ve un abanico creible."""
    m = cfg["vision"]["minimap"]
    h, w = img.shape[:2]
    cx, cy = w + float(m["center_dx"]), float(m["center_dy"])
    r_max = float(m["r_max"])
    x0, y0 = int(cx - r_max) - 1, int(cy - r_max) - 1
    x1, y1 = int(cx + r_max) + 2, int(cy + r_max) + 2
    if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
        return None
    crop = img[y0:y1, x0:x1]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    val = hsv[..., 2].astype(np.float32)
    red = (crop[..., 2] > 150) & (crop[..., 1] < 60)   # marcas rojas de monstruos y metin
    bad = red | (val < float(m["min_val"]))
    angs, rads, cos_r = _rays(cfg)
    xs = np.rint(cx - x0 + cos_r).astype(int)
    ys = np.rint(cy - y0 + np.outer(np.sin(angs), rads)).astype(int)
    ok = ~bad[ys, xs]
    cnt = ok.sum(axis=1)
    mean = np.where(cnt >= int(m["min_samples"]), (val[ys, xs] * ok).sum(axis=1) / np.maximum(cnt, 1), np.nan)
    if np.isnan(mean).all():
        return None
    exc = np.nan_to_num(mean - np.nanmedian(mean))
    thr = max(float(m["min_excess"]), float(m["rel_frac"]) * float(np.percentile(exc, 95)))
    mask = exc > thr
    n = len(mask)
    if not mask.any() or mask.all():
        return None
    best_s, best_l = 0, 0
    for s in range(n):
        if mask[s] and not mask[s - 1]:   # inicio de un arco
            ln = 0
            while ln < n and mask[(s + ln) % n]:
                ln += 1
            if ln > best_l:
                best_s, best_l = s, ln
    step = float(m["step_deg"])
    lo, hi = m["width_deg"]
    if not lo <= best_l * step <= hi:
        return None
    return ((best_s + best_l / 2.0 - 0.5) * step) % 360.0
