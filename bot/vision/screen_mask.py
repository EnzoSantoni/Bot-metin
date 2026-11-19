"""Zonas de interfaz excluidas de la deteccion (coordenadas del area de cliente)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import cv2
import numpy as np

from bot.config import ANCHORS, resolve_path
from bot.geometry import Rect

logger = logging.getLogger(__name__)

# (fraccion horizontal, fraccion vertical) del punto de anclaje dentro de la imagen.
_ANCHOR_FRACTIONS: dict[str, tuple[float, float]] = dict(zip(ANCHORS, [
    (0.0, 0.0), (0.5, 0.0), (1.0, 0.0), (0.0, 0.5), (0.5, 0.5), (1.0, 0.5), (0.0, 1.0), (0.5, 1.0), (1.0, 1.0)]))


@dataclass
class ScreenMask:
    """Zonas excluidas ya recortadas a la imagen y su mascara (255 = excluido)."""
    zones: list[tuple[str, Rect]]
    mask: np.ndarray
    _shape: tuple[int, int] = field(default=(0, 0), repr=False)

    def allows(self, x: int, y: int) -> bool:
        """False fuera de la imagen o dentro de una zona."""
        h, w = self.mask.shape[:2]
        if not (0 <= x < w and 0 <= y < h):
            return False
        return self.mask[y, x] == 0

    def allows_rect(self, rect: Rect) -> bool:
        """True si el rectangulo entra en la imagen y no toca ninguna zona."""
        h, w = self.mask.shape[:2]
        if rect.w <= 0 or rect.h <= 0 or rect.x < 0 or rect.y < 0 or rect.right > w or rect.bottom > h:
            return False
        return not self.mask[rect.y:rect.bottom, rect.x:rect.right].any()

    def overlap_fraction(self, rect: Rect) -> float:
        """Fraccion del rectangulo (recortado a la imagen) que cae en zonas excluidas."""
        r = rect.clip(self.mask.shape[1], self.mask.shape[0])
        if r is None:
            return 1.0
        return float(np.count_nonzero(self.mask[r.y:r.bottom, r.x:r.right])) / (r.w * r.h)


def anchor_rect(width: int, height: int, zone: dict) -> Rect:
    """Rectangulo de la zona (sin recortar) para una imagen de width x height."""
    fx, fy = _ANCHOR_FRACTIONS[zone["anchor"]]
    ax, ay = round(width * fx), round(height * fy)
    return Rect(ax + int(zone["dx"]), ay + int(zone["dy"]), int(zone["w"]), int(zone["h"]))


_ui_tpl_cache: dict[tuple[str, float], tuple[np.ndarray, np.ndarray]] = {}


def load_ui_template(path: str, scale: float) -> tuple[np.ndarray, np.ndarray]:
    """Plantilla BGR completa y su version gris a `scale` (con cache)."""
    p = resolve_path(path)
    key = (str(p), scale)
    if key not in _ui_tpl_cache:
        tpl = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if tpl is None:
            raise FileNotFoundError(f"Falta la plantilla de ventana {p} (tools/make_ui_template.py)")
        small = cv2.resize(cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY), None, fx=scale, fy=scale,
                           interpolation=cv2.INTER_AREA)
        _ui_tpl_cache[key] = (tpl, small)
    return _ui_tpl_cache[key]


def _best(res: np.ndarray) -> tuple[float, tuple[int, int]]:
    res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
    _min, score, _loc_min, loc = cv2.minMaxLoc(res)
    return float(score), loc


def match_anchor(img: np.ndarray, gray_small: np.ndarray, template: str, ucfg: dict
                  ) -> tuple[float, int, int] | None:
    """(score, x, y) de la plantilla en `img` (esquina sup. izq.), o None si no calza.

    Busqueda gruesa en gris a escala reducida y refinada en BGR a resolucion completa.
    """
    scale = float(ucfg["coarse_scale"])
    tpl, tpl_small = load_ui_template(template, scale)
    th, tw = tpl.shape[:2]
    h, w = img.shape[:2]
    if h < th or w < tw or gray_small.shape[0] < tpl_small.shape[0] or gray_small.shape[1] < tpl_small.shape[1]:
        return None
    score, (cx, cy) = _best(cv2.matchTemplate(gray_small, tpl_small, cv2.TM_CCOEFF_NORMED))
    if score < ucfg["coarse_min_score"]:
        return None
    m = int(ucfg["refine_margin"])
    x0, y0 = max(0, round(cx / scale) - m), max(0, round(cy / scale) - m)
    x1, y1 = min(w, round(cx / scale) + tw + m), min(h, round(cy / scale) + th + m)
    score, (rx, ry) = _best(cv2.matchTemplate(img[y0:y1, x0:x1], tpl, cv2.TM_CCOEFF_NORMED))
    if score < ucfg["min_score"]:
        return None
    return score, x0 + rx, y0 + ry


def coarse_to_fine_match(img: np.ndarray, template: str, cfg_section: dict, region: Rect | None = None,
                         gray_small: np.ndarray | None = None) -> tuple[Rect | None, float]:
    """Busca `template` (ruta) en `img` (o en `region`, coords de cliente): gris grueso + refinado BGR.

    `cfg_section` aporta coarse_scale, coarse_min_score, refine_margin y min_score. Devuelve el rectangulo
    del calce en coords de `img` (tamano de la plantilla) y su score, o (None, 0.0). `gray_small` permite
    reutilizar el gris reducido de `img` (o de `region`) calculado afuera con el mismo coarse_scale.
    """
    ox = oy = 0
    if region is not None:
        r = region.clip(img.shape[1], img.shape[0])
        if r is None:
            return None, 0.0
        ox, oy = r.x, r.y
        img = img[r.y:r.bottom, r.x:r.right]
    scale = float(cfg_section["coarse_scale"])
    if min(img.shape[:2]) * scale < 1:   # imagen degenerada: el resize daria 0 px
        return None, 0.0
    if gray_small is None:
        gray_small = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), None, fx=scale, fy=scale,
                                interpolation=cv2.INTER_AREA)
    found = match_anchor(img, gray_small, template, cfg_section)
    if found is None:
        return None, 0.0
    th, tw = load_ui_template(template, scale)[0].shape[:2]
    return Rect(found[1] + ox, found[2] + oy, tw, th), found[0]


def find_ui_windows(img: np.ndarray, cfg: dict) -> list[Rect]:
    """Ventanas abiertas (inventario, Mapa grande) en cualquier posicion.

    Busca la barra de titulo de cada una y un segundo ancla (borde inferior del marco, con desplazamiento
    conocido); si calza cualquiera devuelve el rectangulo completo (tamano fijo de la config) recortado a
    la imagen. Una sola vez por ventana, aunque calcen los dos anclas (se prefiere el titulo). [] si no hay.
    """
    ucfg = cfg["vision"].get("ui_windows") or {}
    if not ucfg.get("windows"):
        return []
    h, w = img.shape[:2]
    scale = float(ucfg["coarse_scale"])
    if min(h, w) * scale < 1:   # imagen degenerada (0x0, 1x1): el resize daria 0 px
        return []
    gray_small = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), None, fx=scale, fy=scale,
                            interpolation=cv2.INTER_AREA)
    out: list[Rect] = []
    for win in ucfg["windows"]:
        # Primero la variante principal (titulo y, si no, ancla inferior); si no esta, las alternativas
        # ("alt": mismo marco con otro tamano, ej. inventario sin el panel de estadisticas).
        variants = [win] + list(win.get("alt") or [])
        for var in variants:
            rect, score = coarse_to_fine_match(img, var["template"], ucfg, gray_small=gray_small)
            origin = (rect.x, rect.y) if rect else None
            anchor2 = var.get("anchor2")
            if origin is None and anchor2:
                rect, score = coarse_to_fine_match(img, anchor2["template"], ucfg, gray_small=gray_small)
                if rect:
                    origin = (rect.x - int(anchor2["offset"][0]), rect.y - int(anchor2["offset"][1]))
            if origin is None:
                continue
            r = Rect(origin[0], origin[1], int(var["size"][0]), int(var["size"][1])).clip(w, h)
            if r is not None:
                logger.debug("Ventana %s en %s (score %.2f)", win["name"], r, score)
                out.append(r)
            break
    return out


def build_screen_mask(shape: tuple[int, ...], cfg: dict, extra: list[Rect] | None = None) -> ScreenMask:
    """Arma la mascara para una imagen de `shape` (alto, ancho, ...).

    Las ventanas detectadas (`extra`) se agrandan `vision.ui_windows.safety_margin` px por lado, asi ningun
    punto de clic queda pegado a su borde.
    """
    h, w = int(shape[0]), int(shape[1])
    named = [(z["name"], anchor_rect(w, h, z)) for z in cfg["vision"]["exclusion_zones"]]
    sm = int((cfg["vision"].get("ui_windows") or {}).get("safety_margin", 0))
    named += [("ui_window", Rect(r.x - sm, r.y - sm, r.w + 2 * sm, r.h + 2 * sm)) for r in (extra or [])]
    zones: list[tuple[str, Rect]] = []
    mask = np.zeros((h, w), np.uint8)
    for name, r in named:
        c = r.clip(w, h)
        if c is None:
            continue
        zones.append((name, c))
        mask[c.y:c.bottom, c.x:c.right] = 255
    return ScreenMask(zones=zones, mask=mask, _shape=(h, w))


# Alias privados (compatibilidad)
_load_ui_template = load_ui_template
_match_anchor = match_anchor
