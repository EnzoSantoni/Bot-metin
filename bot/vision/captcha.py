"""Deteccion del panel "Bot Verify" (solo detectar: el bot no lo resuelve ni lee sus digitos)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

from bot.geometry import Rect
from bot.vision.screen_mask import anchor_rect, coarse_to_fine_match, load_ui_template

logger = logging.getLogger(__name__)


@dataclass
class CaptchaInfo:
    """Panel del captcha: rectangulo completo (coords de cliente) y score del ancla que calzo."""
    rect: Rect
    score: float


def find_captcha(img_bgr: np.ndarray, cfg: dict, full: bool = False) -> CaptchaInfo | None:
    """Busca el panel con dos anclas (pie y teclado); calza cualquiera.

    full=False: solo dentro de `search_region` (rapido). full=True: en toda la imagen (respaldo).
    Si calzan las dos se devuelve la de mayor score. None si no esta.
    """
    ccfg = cfg["vision"]["captcha"]
    h, w = img_bgr.shape[:2]
    if h == 0 or w == 0:
        return None
    region = None if full else anchor_rect(w, h, ccfg["search_region"])
    best: tuple[float, int, int] | None = None
    for key in ("footer", "keypad"):
        anchor = ccfg[key]
        # Cada ancla puede tener su propia escala gruesa (opcional).
        scale = float(anchor.get("coarse_scale", ccfg["coarse_scale"]))
        found, score = coarse_to_fine_match(img_bgr, anchor["template"], {**ccfg, "coarse_scale": scale},
                                           region=region)
        if found is None:
            continue
        cand = (score, found.x - int(anchor["offset"][0]), found.y - int(anchor["offset"][1]))
        if best is None or cand[0] > best[0]:
            best = cand
    if best is None:
        return find_phrase_captcha(img_bgr, cfg)
    rect = Rect(best[1], best[2], int(ccfg["size"][0]), int(ccfg["size"][1])).clip(w, h)
    if rect is None:
        return None
    logger.debug("Captcha en %s (score %.2f)", rect, best[0])
    return CaptchaInfo(rect=rect, score=best[0])


def _best_match(img: np.ndarray, template: str, region: Rect) -> tuple[float, Rect | None]:
    """Mejor calce (score, rect en coords de `img`) de `template` dentro de `region` (a resolucion completa)."""
    r = region.clip(img.shape[1], img.shape[0])
    tpl = load_ui_template(template, 1.0)[0]
    if r is None or r.h < tpl.shape[0] or r.w < tpl.shape[1]:
        return 0.0, None
    res = cv2.matchTemplate(img[r.y:r.bottom, r.x:r.right], tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    return float(score), Rect(r.x + loc[0], r.y + loc[1], tpl.shape[1], tpl.shape[0])


def find_phrase_captcha(img_bgr: np.ndarray, cfg: dict) -> CaptchaInfo | None:
    """Captcha de la frase dorada (arriba al centro): la barra con manijas del cartel Y el texto fijo "la siguiente frase es:"
    dentro de su cuerpo (los anuncios del servidor usan el mismo marco pero otro texto). None si no esta."""
    fc = cfg["vision"]["captcha"]["frase"]
    h, w = img_bgr.shape[:2]
    score, bar = _best_match(img_bgr, fc["frame"]["template"], anchor_rect(w, h, fc["search_region"]))
    if bar is None or score < float(fc["frame"]["min_score"]):
        return None
    rect = Rect(bar.x, bar.y, int(fc["size"][0]), int(fc["size"][1])).clip(w, h)
    if rect is None:
        return None
    t_score, _ = _best_match(img_bgr, fc["text"]["template"], rect)
    if t_score < float(fc["text"]["min_score"]):
        return None
    logger.debug("Captcha de la frase en %s (marco %.2f, texto %.2f)", rect, score, t_score)
    return CaptchaInfo(rect=rect, score=min(score, t_score))
