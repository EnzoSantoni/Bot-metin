"""Deteccion de piedras de metin SIN etiqueta (media y larga distancia).

Una piedra se reconoce por dos cosas juntas: un nucleo rojo "calido" (rojo de matiz bajo y saturacion media,
distinto del rojo plano de los textos, del rojo oscuro de la sangre y del naranja de la piel de los
monstruos) y, justo encima, el humo (gris palido tirando a calido). No sabe el tipo de metin.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import cv2
import numpy as np

from bot.geometry import Rect
from bot.ground import depth_px
from bot.vision.monsters import MonsterLabel
from bot.vision.screen_mask import ScreenMask

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FarMetin:
    rect: Rect                 # cuerpo de la piedra (nucleo rojo), coords de cliente
    center: tuple[int, int]    # base de la piedra: punto al que caminar o clickear
    score: float               # 0..1 (tamano del nucleo y del humo)
    allowed: bool              # el centro esta permitido por la mascara
    smoke: bool = True         # lo detecto el criterio principal (humo); False = solo el cuerpo claro (sin humo, mas dudoso)


def _core_mask(hsv: np.ndarray, m: dict) -> np.ndarray:
    """Nucleo rojo de la piedra (0/255)."""
    s_lo, s_hi = m["core_s"]
    return cv2.inRange(hsv, (0, s_lo, m["core_v_min"]), (m["core_h_max"], s_hi, 255))


def _smoke_pixels(hsv: np.ndarray, r: Rect, m: dict) -> int:
    """Pixeles de humo (gris palido de matiz calido) arriba de la piedra y un poco dentro de su borde superior."""
    side = int(r.w * m["smoke_side_frac"]) + int(m["smoke_side_px"])
    y0 = max(0, r.y - int(r.h * m["smoke_height_frac"]))
    y1 = r.y + int(r.h * m["smoke_overlap_frac"])
    x0, x1 = max(0, r.x - side), r.right + side
    crop = hsv[y0:y1, x0:x1]
    if crop.size == 0:
        return 0
    sm = cv2.inRange(crop, tuple(m["smoke_hsv_lo"]), tuple(m["smoke_hsv_hi"]))
    return int(np.count_nonzero(cv2.morphologyEx(sm, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))))


def _bright_body(hsv: np.ndarray, r: Rect, m: dict) -> bool:
    """Piedra lejana SIN humo (p. ej. Metin Negro, anaranjada): cuerpo claro anaranjado sobre el nucleo rojo.

    Exige muchos pixeles claros de matiz naranja (la piel de los monstruos es mas oscura), que no pasen de
    `alt_bright_max_ratio` veces el nucleo (un efecto de habilidad es mucho mas grande) y que alrededor haya
    suelo oscuro azulado (una piedra esta sola en el terreno; un efecto de combate esta entre monstruos).
    """
    if not m.get("alt_enabled", True):
        return False
    h, w = hsv.shape[:2]
    x0, x1 = max(0, r.x - 6), min(w, r.right + 6)
    y0, y1 = max(0, r.y - r.h), min(h, r.bottom + 4)
    win = hsv[y0:y1, x0:x1]
    if win.size == 0:
        return False
    br = cv2.inRange(win, tuple(m["alt_bright_lo"]), tuple(m["alt_bright_hi"]))
    n_br = int(np.count_nonzero(cv2.morphologyEx(br, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))))
    core_px = int(np.count_nonzero(_core_mask(win, m)))   # nucleo rojo dentro de la ventana
    area = win.shape[0] * win.shape[1]
    if n_br < m["alt_min_bright_px"] or n_br / area < m["alt_min_bright_frac"] or n_br > m["alt_bright_max_ratio"] * core_px:
        return False
    px, py = int(r.w * 0.6) + 4, int(r.h * 0.6) + 4
    around = hsv[max(0, r.y - r.h - 4):min(h, r.bottom + py), max(0, r.x - px):min(w, r.right + px)]
    bg = int(np.count_nonzero(cv2.inRange(around, (95, 0, 0), (130, 120, 140))))
    return bg / (around.shape[0] * around.shape[1]) >= m["alt_min_ground_frac"]


def _excluded(r: Rect, center: tuple[int, int], exclude: list[Rect] | None, frac: float) -> bool:
    """True si el centro cae en una zona a ignorar o `r` se solapa con ella mas de `frac`."""
    for e in exclude or []:
        if e.contains(*center):
            return True
        inter = r.intersect(e)
        if inter is not None and inter.w * inter.h > frac * r.w * r.h:
            return True
    return False


def _monster_above(r: Rect, center: tuple[int, int], monsters: list[MonsterLabel] | None, m: dict) -> bool:
    """True si una etiqueta de monstruo queda justo arriba del candidato (a <= `monster_label_above_px` por encima de su
    base y con el centro a <= `monster_label_dx_px` en horizontal): es el cuerpo de ese monstruo, no una piedra."""
    for lb in monsters or []:
        up = center[1] - lb.rect.bottom
        if 0 <= up <= m["monster_label_above_px"] and abs(lb.center[0] - center[0]) <= m["monster_label_dx_px"]:
            return True
    return False


def _bad_shape(r: Rect, center: tuple[int, int], shape: tuple[int, ...], cfg: dict, m: dict) -> str | None:
    """Motivo por el que el cuerpo no parece una piedra: mucho mas alto que ancho (un personaje), o mucho mas chico de lo
    que mide una piedra a esa altura de pantalla (perspectiva: el tamano es proporcional a la distancia al horizonte)."""
    if r.h > m["max_aspect"] * r.w:
        return "alargado"
    if math.sqrt(r.w * r.h) < m["min_size_ratio"] * depth_px(center[1], shape, cfg):
        return "muy chico para esa altura"
    return None


def find_far_metins(img_bgr: np.ndarray, cfg: dict, mask: ScreenMask | None = None,
                    exclude: list[Rect] | None = None, monsters: list[MonsterLabel] | None = None) -> list[FarMetin]:
    """Piedras sin etiqueta: nucleo rojo con humo encima, fuera de zonas excluidas, ventanas y `exclude`. Con `monsters`
    descarta los cuerpos con etiqueta de monstruo arriba; ademas filtra por forma y tamano esperado (perspectiva)."""
    h, w = img_bgr.shape[:2]
    if h == 0 or w == 0:
        return []
    m = cfg["vision"]["far_metin"]
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    core = _core_mask(hsv, m)
    kw, kh = m["group_kernel"]
    contours, _ = cv2.findContours(cv2.dilate(core, np.ones((kh, kw), np.uint8)),
                                   cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out: list[FarMetin] = []
    for c in contours:
        x, y, bw, bh = cv2.boundingRect(c)
        piece = cv2.morphologyEx(core[y:y + bh, x:x + bw], cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        ys, xs = np.nonzero(piece)   # sin pixeles sueltos
        if len(xs) < max(1, m["min_core_pixels"]):
            continue
        r = Rect(x + int(xs.min()), y + int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))
        if not (m["min_w"] <= r.w <= m["max_w"] and m["min_h"] <= r.h <= m["max_h"]):
            continue
        if mask is not None and mask.overlap_fraction(r) > m["max_zone_overlap"]:
            continue
        center = (r.x + r.w // 2, r.y + int(r.h * m["center_y_frac"]))
        if _monster_above(r, center, monsters, m):
            logger.debug("Candidato %s descartado: etiqueta de monstruo arriba", center)
            continue
        if (why := _bad_shape(r, center, img_bgr.shape, cfg, m)) is not None:
            logger.debug("Candidato %s descartado: %s (%dx%d)", center, why, r.w, r.h)
            continue
        smoke_px = _smoke_pixels(hsv, r, m)
        has_smoke = smoke_px >= m["min_smoke_pixels"]
        if not has_smoke and not _bright_body(hsv, r, m):
            continue
        if _excluded(r, center, exclude, m["exclude_overlap"]):
            continue
        score = 0.5 * min(1.0, len(xs) / m["core_ref"]) + 0.5 * min(1.0, max(smoke_px, m["min_smoke_pixels"] // 2) / m["smoke_ref"])
        allowed = mask.allows(*center) if mask is not None else (0 <= center[0] < w and 0 <= center[1] < h)
        out.append(FarMetin(r, center, round(score, 3), allowed, has_smoke))
    out.sort(key=lambda f: (f.rect.y, f.rect.x))
    return out
