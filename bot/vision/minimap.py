"""Rumbo de la camara a partir del minimapa (arriba a la derecha).

El minimapa tiene el norte fijo y muestra, centrado en el personaje, un abanico (cono) mas claro hacia donde mira la
camara. Se miden, en rayos alrededor del centro, los V (brillo HSV) medios entre `r_min` y `r_max` px; el abanico es el
arco mas largo con exceso de brillo sobre la mediana. El rumbo es el centro de ese arco, en grados con el marco de la
imagen: atan2(dy, dx) con y hacia abajo (0 = este, 90 = sur).

Marco (medido con debug/run_20261121_205635, ver PROGRESS.md): ese marco es EL MISMO de las coordenadas del juego, sin
espejo ni giro (`vision.minimap.heading_offset_deg` = 0): W camina hacia el rumbo del cono. Un pase estricto
(`min_excess`, `rel_frac`, `width_deg`) y, si no lee, uno relajado (`relax_*`: cono tenue o parcialmente tapado).
Config: `vision.minimap`.
"""
from __future__ import annotations

import cv2
import numpy as np


def _rays(cfg: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(angulos en rad, radios en px, matriz de pasos) de los rayos de muestreo."""
    m = cfg["vision"]["minimap"]
    angs = np.radians(np.arange(0.0, 360.0, float(m["step_deg"])))
    rads = np.arange(float(m["r_min"]), float(m["r_max"]))
    return angs, rads, np.outer(np.cos(angs), rads)


def _ray_excess(img: np.ndarray, cfg: dict) -> np.ndarray | None:
    """Exceso de brillo de cada rayo sobre la mediana (0 donde no hay muestras validas); None si no se puede medir."""
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
    return np.nan_to_num(mean - np.nanmedian(mean))


def _longest_arc(exc: np.ndarray, min_excess: float, rel_frac: float, step: float,
                 width: tuple[float, float]) -> float | None:
    """Centro (grados) del arco mas largo con exceso > max(min_excess, rel_frac x percentil 95) y ancho en `width`."""
    thr = max(min_excess, rel_frac * float(np.percentile(exc, 95)))
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
    if not width[0] <= best_l * step <= width[1]:
        return None
    return ((best_s + best_l / 2.0 - 0.5) * step) % 360.0


def camera_heading(img: np.ndarray, cfg: dict) -> float | None:
    """Rumbo de la camara en grados [0, 360) (0 = este, 90 = sur) o None si no se ve un abanico creible."""
    m = cfg["vision"]["minimap"]
    exc = _ray_excess(img, cfg)
    if exc is None:
        return None
    step = float(m["step_deg"])
    lo, hi = m["width_deg"]
    h = _longest_arc(exc, float(m["min_excess"]), float(m["rel_frac"]), step, (float(lo), float(hi)))
    if h is not None:
        return h
    rlo, rhi = m["relax_width_deg"]
    return _longest_arc(exc, float(m["min_excess"]) * float(m["relax_factor"]), float(m["rel_frac"]) * float(m["relax_factor"]),
                        step, (float(rlo), float(rhi)))
