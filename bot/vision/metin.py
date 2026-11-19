"""Deteccion de metin por su etiqueta naranja y verificacion del nombre por plantilla."""
from __future__ import annotations

import logging
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from bot.config import resolve_path
from bot.geometry import Rect
from bot.vision.screen_mask import ScreenMask

logger = logging.getLogger(__name__)

_PAD = 3  # margen (px) con el que se desliza la plantilla sobre la etiqueta candidata


@dataclass(frozen=True)
class LabelCandidate:
    """Etiqueta naranja encontrada: rectangulo ajustado al texto y su mascara binaria (0/255)."""
    rect: Rect
    text_mask: np.ndarray


@dataclass(frozen=True)
class MetinDetection:
    label_rect: Rect
    name: str | None          # plantilla que mejor coincide (nombre de archivo sin extension)
    score: float
    is_target: bool
    click_point: tuple[int, int]
    allowed: bool             # el punto de clic esta permitido por la mascara


def slugify(name: str) -> str:
    """'Metin de Oscuridad' -> 'metin_de_oscuridad'."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def target_slugs(cfg: dict) -> set[str]:
    """Slugs de los nombres de metin objetivo."""
    return {slugify(n) for n in cfg["target_metin_names"]}


def is_target_name(name: str, cfg: dict) -> bool:
    """True si `name` (ej. leido del panel superior) es uno de los objetivos; ignora mayusculas y tildes."""
    return slugify(name) in target_slugs(cfg)


def orange_mask(img_bgr: np.ndarray, mcfg: dict) -> np.ndarray:
    """Mascara binaria (0/255) de los pixeles del naranja de las etiquetas de metin."""
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, tuple(mcfg["hsv_lo"]), tuple(mcfg["hsv_hi"]))


def find_label_candidates(img_bgr: np.ndarray, cfg: dict, mask: ScreenMask | None = None) -> list[LabelCandidate]:
    """Etiquetas naranjas (grupos de letras) con tamano de texto valido, fuera de zonas excluidas."""
    m = cfg["vision"]["metin"]
    orange = orange_mask(img_bgr, m)
    kw, kh = m["group_kernel"]
    grouped = cv2.dilate(orange, np.ones((kh, kw), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(grouped, connectivity=8)
    out: list[LabelCandidate] = []
    for k in range(1, n):
        x, y, w, h, _a = (int(v) for v in stats[k])
        piece = np.where(labels[y:y + h, x:x + w] == k, orange[y:y + h, x:x + w], 0).astype(np.uint8)
        ys, xs = np.nonzero(piece)
        if len(xs) < m["min_pixels"]:
            continue
        x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        rect = Rect(x + int(x0), y + int(y0), int(x1 - x0), int(y1 - y0))
        if not (m["min_text_w"] <= rect.w <= m["max_text_w"] and m["min_text_h"] <= rect.h <= m["max_text_h"]):
            continue
        if mask is not None and mask.overlap_fraction(rect) > m["max_zone_overlap"]:
            continue
        out.append(LabelCandidate(rect, piece[y0:y1, x0:x1].copy()))
    out.sort(key=lambda c: (c.rect.y, c.rect.x))
    return out


_templates_cache: dict[str, dict[str, np.ndarray]] = {}


def load_label_templates(cfg: dict) -> dict[str, np.ndarray]:
    """Plantillas {slug: mascara 0/255} de la carpeta configurada. Error claro si falta la del objetivo."""
    d = resolve_path(cfg["vision"]["metin"]["label_templates_dir"])
    key = str(d)
    if key not in _templates_cache:
        tpls: dict[str, np.ndarray] = {}
        for p in sorted(Path(d).glob("*.png")) if d.is_dir() else []:
            img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            if img is None:
                raise FileNotFoundError(f"No se pudo leer la plantilla {p}")
            tpls[p.stem] = img
        _templates_cache[key] = tpls
    tpls = _templates_cache[key]
    for name in cfg["target_metin_names"]:
        slug = slugify(name)
        if slug not in tpls:
            raise FileNotFoundError(
                f"Falta la plantilla del objetivo {name!r}: {d / (slug + '.png')}. "
                f'Generala con: tools/make_label_template.py --image <captura.png> --name "{name}"')
    return tpls


def match_score(text_mask: np.ndarray, template: np.ndarray) -> float:
    """Mejor coincidencia (0..1) de la plantilla deslizada sobre la etiqueta con un margen chico."""
    patch = cv2.copyMakeBorder(text_mask, _PAD, _PAD, _PAD, _PAD, cv2.BORDER_CONSTANT, value=0)
    extra_h = max(0, template.shape[0] - patch.shape[0])
    extra_w = max(0, template.shape[1] - patch.shape[1])
    if extra_h or extra_w:
        patch = cv2.copyMakeBorder(patch, 0, extra_h, 0, extra_w, cv2.BORDER_CONSTANT, value=0)
    res = cv2.matchTemplate(patch.astype(np.float32), template.astype(np.float32), cv2.TM_CCOEFF_NORMED)
    return float(np.clip(np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0).max(), 0.0, 1.0))


def classify_label(text_mask: np.ndarray, templates: dict[str, np.ndarray], target_slugs: set[str],
                   min_score: float, min_margin: float) -> tuple[str | None, float, bool]:
    """(mejor plantilla, score, is_target). Ante la duda no es objetivo."""
    scores = sorted(((match_score(text_mask, t), n) for n, t in templates.items()), reverse=True)
    if not scores:
        return None, 0.0, False
    best, name = scores[0]
    second = scores[1][0] if len(scores) > 1 else 0.0
    ok = name in target_slugs and best >= min_score and (best - second) >= min_margin
    return name, best, ok


def click_point_for(label: Rect, mcfg: dict) -> tuple[int, int]:
    """Punto de clic sobre la piedra: desplazamiento fijo desde el centro de la etiqueta."""
    cx, cy = label.center
    return cx + int(mcfg["click_dx"]), cy + int(mcfg["click_dy"])


def find_metins(img_bgr: np.ndarray, cfg: dict, mask: ScreenMask | None = None) -> list[MetinDetection]:
    """Todas las etiquetas naranjas encontradas (objetivo o no)."""
    h, w = img_bgr.shape[:2]
    if h == 0 or w == 0:
        return []
    m = cfg["vision"]["metin"]
    templates = load_label_templates(cfg)
    target = target_slugs(cfg)
    out: list[MetinDetection] = []
    for cand in find_label_candidates(img_bgr, cfg, mask):
        name, score, is_target = classify_label(cand.text_mask, templates, target,
                                                m["min_score"], m["min_margin"])
        cp = click_point_for(cand.rect, m)
        allowed = mask.allows(*cp) if mask is not None else (0 <= cp[0] < w and 0 <= cp[1] < h)
        out.append(MetinDetection(cand.rect, name, score, is_target, cp, allowed))
    return out


def player_point(shape: tuple[int, ...], cfg: dict) -> tuple[int, int]:
    """Posicion estimada del personaje: centro de la imagen + desplazamiento configurable."""
    dx, dy = cfg["vision"]["player_offset"]
    return shape[1] // 2 + int(dx), shape[0] // 2 + int(dy)


def choose_target(detections: list[MetinDetection], player_xy: tuple[int, int]) -> MetinDetection | None:
    """El metin objetivo permitido mas cercano al personaje."""
    ok = [d for d in detections if d.is_target and d.allowed]
    if not ok:
        return None
    return min(ok, key=lambda d: math.dist(d.click_point, player_xy))
