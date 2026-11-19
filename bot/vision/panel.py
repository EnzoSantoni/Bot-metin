"""Panel superior (arriba al centro): marco, nombre, barra de vida y numero de vida.

Todo se ubica relativo al marco del panel, que se busca por su borde (color exacto) dentro de una region
de busqueda holgada: el ancho del panel y su posicion varian entre capturas.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from bot.config import resolve_path
from bot.geometry import Rect
from bot.vision.digits import get_glyphs, read_text_line, text_mask
from bot.vision.metin import is_target_name, slugify
from bot.vision.screen_mask import anchor_rect

logger = logging.getLogger(__name__)

_HP_RE = re.compile(r"^(\d{1,3}(?:\.\d{3})*)/(\d{1,3}(?:\.\d{3})*)$")
_PAD = 3                       # parametro del algoritmo (no de la UI): margen (px) al deslizar la plantilla del nombre sobre la linea
_GOLD_GAP = 4                  # columnas vacias que separan dos tapas doradas de la barra
_templates_cache: dict[str, dict[str, np.ndarray]] = {}
_warned_missing: set[str] = set()


@dataclass(frozen=True)
class PanelInfo:
    """Lo que se lee del panel superior (coordenadas de cliente)."""
    present: bool = False
    rect: Rect | None = None
    kind: str | None = None            # "target_metin" | "other_metin" | "other" | None (sin panel)
    name: str | None = None            # slug de la plantilla que calzo
    name_score: float = 0.0
    hp_ratio: float | None = None      # 0..1 por la barra roja
    hp_current: int | None = None
    hp_max: int | None = None
    hp_text_score: float = 0.0
    bar_rect: Rect | None = None       # recipiente de la barra (interior), para debug
    number_rect: Rect | None = None
    name_rect: Rect | None = None


def reset_warnings() -> None:
    """Olvida los avisos ya emitidos (para tests)."""
    _warned_missing.clear()


def _runs(flags: np.ndarray) -> list[tuple[int, int]]:
    """Tramos consecutivos de True como (inicio, fin inclusive)."""
    padded = np.concatenate(([False], flags, [False]))
    d = np.diff(padded.astype(np.int8))
    return list(zip(np.nonzero(d == 1)[0].tolist(), (np.nonzero(d == -1)[0] - 1).tolist()))


def find_frame(img_bgr: np.ndarray, pcfg: dict) -> Rect | None:
    """Rectangulo del marco del panel (por su borde superior e inferior), o None si no hay panel."""
    ih, iw = img_bgr.shape[:2]
    if ih == 0 or iw == 0:
        return None
    region = anchor_rect(iw, ih, pcfg["search"]).clip(iw, ih)
    if region is None:
        return None
    sub = img_bgr[region.y:region.bottom, region.x:region.right]
    c = tuple(int(v) for v in pcfg["frame_color"])
    m = cv2.inRange(sub, c, c) > 0
    min_w = int(pcfg["frame_min_w"])
    lo_h, hi_h = pcfg["frame_h"]
    xtol = int(pcfg["frame_x_tol"])
    edges: list[tuple[int, int, int]] = []   # (y, x0, x1) de cada borde horizontal largo
    for y in np.nonzero(m.sum(axis=1) >= min_w)[0].tolist():
        long_runs = [r for r in _runs(m[y]) if r[1] - r[0] + 1 >= min_w]
        if long_runs:
            x0, x1 = max(long_runs, key=lambda r: r[1] - r[0])
            if not edges or y - edges[-1][0] > int(pcfg["frame_edge_merge"]):
                edges.append((y, x0, x1))
    for i, (ytop, x0, x1) in enumerate(edges):
        for ybot, bx0, bx1 in edges[i + 1:]:
            if lo_h <= ybot - ytop + 1 <= hi_h and abs(bx0 - x0) <= xtol and abs(bx1 - x1) <= xtol:
                return Rect(region.x + x0, region.y + ytop, x1 - x0 + 1, ybot - ytop + 1)
    return None


def read_bar(img_bgr: np.ndarray, frame: Rect, pcfg: dict) -> tuple[float, Rect] | None:
    """(proporcion de rojo 0..1, interior del recipiente) o None. El ancho sale de las tapas doradas."""
    b = pcfg["bar"]
    rr = frame.right - 1
    y0, y1 = frame.y + b["band"][0], frame.y + b["band"][1] + 1
    x0, x1 = rr + b["search_x"][0], rr + b["search_x"][1]
    ih, iw = img_bgr.shape[:2]
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, iw), min(y1, ih)
    if x1 <= x0 or y1 <= y0:
        return None
    band = img_bgr[y0:y1, x0:x1].astype(np.int16)
    bl, gr, rd = band[..., 0], band[..., 1], band[..., 2]
    d = int(b["cap_diff"])
    gold = ((rd >= gr + d) & (gr >= bl + d)).any(axis=0)
    # Une columnas doradas cercanas en tapas (huecos de hasta gold_gap).
    caps: list[list[int]] = []
    for s, e in _runs(gold):
        if caps and s - caps[-1][1] - 1 <= int(b["gold_gap"]):
            caps[-1][1] = e
        else:
            caps.append([s, e])
    if len(caps) < 2:
        return None
    inner0, inner1 = x0 + caps[0][1] + 1, x0 + caps[-1][0]
    width = inner1 - inner0
    if abs(width - int(b["width"])) > int(b["width_tol"]):
        return None
    inner = band[:, inner0 - x0:inner1 - x0]
    red = ((inner[..., 2] >= b["red_min_r"]) & (inner[..., 1] <= b["red_max_gb"])
           & (inner[..., 0] <= b["red_max_gb"]) & (inner[..., 2] - inner[..., 1] >= b["red_min_rg"]))
    filled = int(red.sum(axis=1).max())
    return min(1.0, filled / width), Rect(inner0, y0, width, y1 - y0)


def parse_hp(text: str) -> tuple[int, int] | None:
    """'18.131/80.250' -> (18131, 80250); None si el formato no es exactamente ese."""
    m = _HP_RE.match(text)
    if not m:
        return None
    return int(m.group(1).replace(".", "")), int(m.group(2).replace(".", ""))


def read_hp_number(img_bgr: np.ndarray, frame: Rect, pcfg: dict, glyphs, section: dict
                   ) -> tuple[tuple[int, int] | None, float, Rect | None]:
    """((actual, maxima) o None, confianza, region leida)."""
    n = pcfg["number_region"]
    rr = frame.right - 1
    ih, iw = img_bgr.shape[:2]
    rect = Rect(rr + n["dx"], frame.y + n["dy"], n["w"], n["h"]).clip(iw, ih)
    if rect is None:
        return None, 0.0, None
    read = read_text_line(img_bgr[rect.y:rect.bottom, rect.x:rect.right], glyphs, section)
    return parse_hp(read.text), read.min_score, rect


def load_name_templates(pcfg: dict) -> dict[str, np.ndarray]:
    """Plantillas {slug: mascara 0/255} del nombre entero, de templates/panel_names (con cache)."""
    d = resolve_path(pcfg["name_templates_dir"])
    key = str(d)
    if key not in _templates_cache:
        tpls: dict[str, np.ndarray] = {}
        for p in sorted(Path(d).glob("*.png")) if d.is_dir() else []:
            img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            if img is None:
                raise FileNotFoundError(f"No se pudo leer la plantilla {p}")
            tpls[p.stem] = img
        _templates_cache[key] = tpls
    return _templates_cache[key]


def warn_missing_targets(cfg: dict, templates: dict[str, np.ndarray]) -> None:
    """WARNING (una sola vez por nombre) si falta la plantilla del panel de un objetivo."""
    for name in cfg["target_metin_names"]:
        slug = slugify(name)
        if slug not in templates and slug not in _warned_missing:
            _warned_missing.add(slug)
            logger.warning("falta la plantilla del panel para %s; el bot no va a poder confirmar que lo "
                           "seleccionó (tools/make_panel_name_template.py)", name)


def name_region(frame: Rect, pcfg: dict, shape: tuple[int, ...]) -> Rect | None:
    """Linea del nombre dentro del marco, recortada a la imagen."""
    r = pcfg["name_region"]
    x0 = frame.x + r["x_from_left"]
    x1 = frame.right - 1 + r["x_to_right"]
    return Rect(x0, frame.y + r["dy"], x1 - x0, r["h"]).clip(shape[1], shape[0])


def match_name(mask: np.ndarray, template: np.ndarray) -> float:
    """Mejor coincidencia (0..1) de la plantilla deslizada sobre la mascara de la linea."""
    patch = cv2.copyMakeBorder(mask, _PAD, _PAD, _PAD, _PAD, cv2.BORDER_CONSTANT, value=0)
    extra_h = max(0, template.shape[0] - patch.shape[0])
    extra_w = max(0, template.shape[1] - patch.shape[1])
    if extra_h or extra_w:
        patch = cv2.copyMakeBorder(patch, 0, extra_h, 0, extra_w, cv2.BORDER_CONSTANT, value=0)
    res = cv2.matchTemplate(patch.astype(np.float32), template.astype(np.float32), cv2.TM_CCOEFF_NORMED)
    return float(np.clip(np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0).max(), 0.0, 1.0))


def classify_name(mask: np.ndarray, templates: dict[str, np.ndarray], min_score: float, min_margin: float
                  ) -> tuple[str | None, float]:
    """(slug o None, mejor score). Solo da nombre si pasa el umbral y le saca margen al segundo."""
    scores = sorted(((match_name(mask, t), n) for n, t in templates.items()), reverse=True)
    if not scores:
        return None, 0.0
    best, slug = scores[0]
    second = scores[1][0] if len(scores) > 1 else 0.0
    if best >= min_score and best - second >= min_margin:
        return slug, best
    return None, best


def kind_for(name: str | None, cfg: dict) -> str:
    """'target_metin' / 'other_metin' / 'other'. Ante la duda (sin nombre) no es objetivo."""
    if name is None:
        return "other"
    if is_target_name(name, cfg):
        return "target_metin"
    return "other_metin" if name.startswith("metin") else "other"


def read_panel(img_bgr: np.ndarray, cfg: dict) -> PanelInfo:
    """Lee el panel superior. Sin panel -> PanelInfo(present=False)."""
    pcfg = cfg["vision"]["panel"]
    frame = find_frame(img_bgr, pcfg)
    if frame is None:
        return PanelInfo()
    templates = load_name_templates(pcfg)
    warn_missing_targets(cfg, templates)

    name, name_score, nrect = None, 0.0, name_region(frame, pcfg, img_bgr.shape)
    if nrect is not None and templates:
        nmask = text_mask(img_bgr[nrect.y:nrect.bottom, nrect.x:nrect.right],
                          {"color": pcfg["name_color"], "color_tol": pcfg["name_color_tol"]})
        name, name_score = classify_name(nmask, templates, pcfg["name_min_score"], pcfg["name_min_margin"])

    bar = read_bar(img_bgr, frame, pcfg)
    ratio, bar_rect = (bar[0], bar[1]) if bar else (None, None)

    section = cfg["vision"]["glyphs"]["panel"]
    hp, text_score, num_rect = read_hp_number(img_bgr, frame, pcfg, get_glyphs(section), section)
    cur = mx = None
    if hp is not None:
        c, m = hp
        # Sin barra validada no hay forma de confirmar el numero: la barra es la fuente principal.
        coherent = ratio is not None and m > 0 and abs(c / m - ratio) <= pcfg["hp_ratio_tol"]
        if 0 <= c <= m and m > 0 and coherent and text_score >= section["min_score"]:
            cur, mx = c, m
        else:
            logger.debug("Numero de vida descartado: %s/%s (barra %s)", c, m, ratio)
    return PanelInfo(True, frame, kind_for(name, cfg), name, name_score, ratio, cur, mx,
                     text_score, bar_rect, num_rect, nrect)
