"""Deteccion de piedras de metin por ESTRUCTURA (piedra + aura + humo), independiente del tono.

Los metin de otros mapas pueden ser verdes, azules o violetas, pero siempre tienen la misma forma: una piedra compacta
rodeada de un brillo saturado (aura) con una columna de humo palido arriba, sola sobre el suelo. Aca no se usa el matiz
(H) para decidir: solo la saturacion y el brillo RELATIVOS al fondo local (mediana en una ventana grande), asi que
funciona sobre cualquier suelo y color. El matiz solo sirve como pista opcional (`hue_hints`) para subir el puntaje.

Combina, en este orden:
  1. nucleo+aura: blob de pixeles mucho mas saturados y brillantes que el fondo local;
  2. forma: tamano, proporcion y relleno coherentes (reusa los filtros de perspectiva del detector por color);
  3. aislamiento: el anillo alrededor se parece al fondo (una piedra esta sola; un monstruo/efecto, entre otros);
  4. humo: pixeles palidos mas claros que el fondo, arriba del blob y repartidos en varias filas (columna vertical);
  5. sin humo (p. ej. Metin Negro): se acepta con aura muy aislada y compacta, marcado `smoke=False` (mas dudoso).
"""
from __future__ import annotations

import copy
import logging
import math
from dataclasses import dataclass

import cv2
import numpy as np

from bot.geometry import Rect
from bot.vision.far_metin import FarMetin, _bad_shape, _excluded, _monster_above
from bot.vision.monsters import MonsterLabel
from bot.vision.screen_mask import ScreenMask

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StructFeatures:
    """Medidas de un candidato (para puntuar, depurar y calibrar)."""
    core_px: int        # pixeles del blob nucleo+aura
    fill: float         # core_px / (ancho*alto del rect)
    calm: float         # fraccion del anillo exterior parecida al fondo (aislamiento)
    smoke_px: int       # pixeles de humo arriba
    smoke_rows: int     # filas con humo (altura de la columna)
    glow: float         # fraccion del blob con saturacion muy alta (aura)
    hue: int            # matiz dominante del blob (0-179); solo informativo / para hue_hints


def local_background(hsv: np.ndarray, st: dict) -> tuple[np.ndarray, np.ndarray]:
    """Saturacion y brillo del fondo local (mediana en ventana grande sobre la imagen reducida), int16 a tamano completo."""
    sc = int(st["bg_scale"])
    small = cv2.resize(hsv, None, fx=1.0 / sc, fy=1.0 / sc, interpolation=cv2.INTER_AREA)
    k = int(st["bg_kernel"]) | 1
    med = cv2.medianBlur(small, k)
    big = cv2.resize(med, (hsv.shape[1], hsv.shape[0]), interpolation=cv2.INTER_LINEAR)
    return big[:, :, 1].astype(np.int16), big[:, :, 2].astype(np.int16)


def _core_mask(hsv: np.ndarray, d_s: np.ndarray, d_v: np.ndarray, st: dict) -> np.ndarray:
    """Nucleo+aura (0/255): mucho mas saturado y brillante que el fondo, de cualquier matiz."""
    m = (d_s >= st["core_ds"]) & (d_v >= st["core_dv"]) & (hsv[:, :, 1] >= st["core_s_min"])
    return cv2.morphologyEx(m.astype(np.uint8) * 255, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))


def _smoke(hsv: np.ndarray, d_v: np.ndarray, r: Rect, st: dict) -> tuple[int, int]:
    """(pixeles, filas) de humo: palido (poca saturacion) y mas claro que el fondo, arriba del rect y un poco dentro."""
    side = int(r.w * st["smoke_side_frac"]) + int(st["smoke_side_px"])
    y0 = max(0, r.y - int(r.h * st["smoke_height_frac"]))
    y1 = r.y + int(r.h * st["smoke_overlap_frac"])
    x0, x1 = max(0, r.x - side), min(hsv.shape[1], r.right + side)
    if y1 <= y0 or x1 <= x0:
        return 0, 0
    pale = (hsv[y0:y1, x0:x1, 1] <= st["smoke_s_max"]) & (d_v[y0:y1, x0:x1] >= st["smoke_dv"])
    pale = cv2.morphologyEx(pale.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    rows = int(np.count_nonzero(pale.sum(axis=1) >= st["smoke_row_px"]))
    return int(pale.sum()), rows


def _calm(d_s: np.ndarray, d_v: np.ndarray, r: Rect, st: dict) -> float:
    """Fraccion del anillo alrededor del rect que se parece al fondo (|dS| y |dV| chicos)."""
    mg = int(st["ring_margin"])
    h, w = d_s.shape
    y0, y1, x0, x1 = max(0, r.y - mg), min(h, r.bottom + mg), max(0, r.x - mg), min(w, r.right + mg)
    ring = np.ones((y1 - y0, x1 - x0), bool)
    ring[r.y - y0:r.y - y0 + r.h, r.x - x0:r.x - x0 + r.w] = False
    if not ring.any():
        return 0.0
    near = (np.abs(d_s[y0:y1, x0:x1]) < st["calm_d"]) & (np.abs(d_v[y0:y1, x0:x1]) < st["calm_d"])
    return float(near[ring].mean())


def _dominant_hue(hsv: np.ndarray, core: np.ndarray, r: Rect) -> int:
    """Matiz mas frecuente del blob (moda del histograma de H, solo pixeles del nucleo/aura)."""
    sel = core[r.y:r.bottom, r.x:r.right] > 0
    vals = hsv[r.y:r.bottom, r.x:r.right, 0][sel]
    if vals.size == 0:
        return 0
    return int(np.argmax(np.bincount(vals, minlength=180)))


def hue_matches(hue: int, hints: dict) -> bool:
    """True si `hue` cae en alguno de los rangos [h_min, h_max] de `hints` (rango envolvente si h_min > h_max)."""
    for lo, hi in hints.values():
        if (lo <= hue <= hi) if lo <= hi else (hue >= lo or hue <= hi):
            return True
    return False


def _features(hsv: np.ndarray, d_s: np.ndarray, d_v: np.ndarray, core: np.ndarray, r: Rect, n: int,
              st: dict) -> StructFeatures:
    spx, srows = _smoke(hsv, d_v, r, st)
    sel = core[r.y:r.bottom, r.x:r.right] > 0
    glow = float(((d_s[r.y:r.bottom, r.x:r.right] >= st["glow_ds"]) & sel).sum() / max(1, sel.sum()))
    return StructFeatures(n, n / (r.w * r.h), _calm(d_s, d_v, r, st), spx, srows, glow, _dominant_hue(hsv, core, r))


def _accept(f: StructFeatures, r: Rect, st: dict) -> bool | None:
    """True = con humo, False = sin humo (aura aislada), None = rechazado."""
    asp = r.h / r.w
    if not (st["min_aspect"] <= asp) or f.fill < st["min_fill"] or f.calm < st["min_calm"]:
        return None
    if f.smoke_px >= st["min_smoke_pixels"] and f.smoke_rows >= st["min_smoke_rows"]:
        return True
    if (st["nosmoke_enabled"] and f.core_px >= st["nosmoke_min_core"] and f.calm >= st["nosmoke_min_calm"]
            and f.fill >= st["nosmoke_min_fill"] and st["nosmoke_aspect"][0] <= asp <= st["nosmoke_aspect"][1]):
        return False
    return None


def _score(f: StructFeatures, has_smoke: bool, st: dict, hint: bool) -> float:
    """0..1: promedio ponderado de tamano del blob, humo, aura y aislamiento (+ bonus si el matiz coincide con una pista)."""
    w = st["weights"]
    parts = (w["core"] * min(1.0, f.core_px / st["core_ref"]),
             w["smoke"] * (min(1.0, f.smoke_px / st["smoke_ref"]) if has_smoke else 0.0),
             w["aura"] * min(1.0, f.glow / st["glow_ref"]),
             w["calm"] * f.calm)
    return min(1.0, sum(parts) / sum(w.values()) + (st["hue_bonus"] if hint else 0.0))


def find_structural_metins(img_bgr: np.ndarray, cfg: dict, mask: ScreenMask | None = None,
                           exclude: list[Rect] | None = None,
                           monsters: list[MonsterLabel] | None = None) -> list[FarMetin]:
    """Piedras sin etiqueta por estructura (blob saturado aislado + humo arriba), sin depender del color. Mismas reglas de
    exclusion (mascara, `exclude`, etiqueta de monstruo arriba, forma y tamano por perspectiva) que el detector por color."""
    h, w = img_bgr.shape[:2]
    if h == 0 or w == 0:
        return []
    m = cfg["vision"]["far_metin"]
    st = m["structure"]
    hints = m.get("hue_hints") or {}
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    bg_s, bg_v = local_background(hsv, st)
    d_s = hsv[:, :, 1].astype(np.int16) - bg_s
    d_v = hsv[:, :, 2].astype(np.int16) - bg_v
    core = _core_mask(hsv, d_s, d_v, st)
    kw, kh = st["group_kernel"]
    contours, _ = cv2.findContours(cv2.dilate(core, np.ones((kh, kw), np.uint8)),
                                   cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out: list[FarMetin] = []
    for c in contours:
        x, y, bw, bh = cv2.boundingRect(c)
        ys, xs = np.nonzero(core[y:y + bh, x:x + bw])
        if len(xs) < st["min_core_pixels"]:
            continue
        r = Rect(x + int(xs.min()), y + int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))
        if not (st["min_w"] <= r.w <= st["max_w"] and st["min_h"] <= r.h <= st["max_h"]):
            continue
        if mask is not None and mask.overlap_fraction(r) > st["max_zone_overlap"]:
            continue
        center = (r.x + r.w // 2, r.y + int(r.h * m["center_y_frac"]))
        if _monster_above(r, center, monsters, m) or _bad_shape(r, center, img_bgr.shape, cfg, m) is not None:
            continue
        if _excluded(r, center, exclude, m["exclude_overlap"]):
            continue
        f = _features(hsv, d_s, d_v, core, r, len(xs), st)
        kind = _accept(f, r, st)
        if kind is None:
            logger.debug("Estructura %s descartada: %s", center, f)
            continue
        hint = bool(hints) and hue_matches(f.hue, hints)
        score = _score(f, kind, st, hint)
        if score < st["min_score"]:
            continue
        allowed = mask.allows(*center) if mask is not None else (0 <= center[0] < w and 0 <= center[1] < h)
        out.append(FarMetin(r, center, round(score, 3), allowed, kind, f.hue))
    out.sort(key=lambda p: (p.rect.y, p.rect.x))
    return out


def measure_stone_hue(img_bgr: np.ndarray, cfg: dict, near: tuple[int, int], max_dist: float = 90.0) -> int | None:
    """Matiz dominante (0-179) de la piedra detectada por estructura mas cercana a `near` (a <= `max_dist` px de su centro
    o de su rect), o None. Ignora las pistas y las exclusiones: sirve para calibrar un metin nuevo (`calibrate.py`)."""
    c = copy.deepcopy(cfg)
    c["vision"]["far_metin"]["hue_hints"] = {}
    found = [f for f in find_structural_metins(img_bgr, c)
             if f.hue >= 0 and min(math.dist(f.center, near), math.dist(f.rect.center, near)) <= max_dist]
    if not found:
        return None
    return min(found, key=lambda f: math.dist(f.rect.center, near)).hue


def hue_range(hues: list[int], margin: int) -> list[int]:
    """[h_min, h_max] (circulo de 180, puede envolver) que cubre todos los `hues` con `margin` de holgura: centro =
    media circular, semiancho = mayor desvio + margin."""
    ang = np.array(hues, float) * (2 * math.pi / 180)
    center = (math.atan2(np.sin(ang).mean(), np.cos(ang).mean()) * 180 / (2 * math.pi)) % 180
    dev = max(min(abs(h - center), 180 - abs(h - center)) for h in hues)
    half = int(math.ceil(dev)) + int(margin)
    return [int(round(center - half)) % 180, int(round(center + half)) % 180]
