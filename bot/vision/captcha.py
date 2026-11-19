"""Deteccion del panel "Bot Verify" (solo detectar: el bot no lo resuelve ni lee sus digitos)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from bot.geometry import Rect
from bot.vision.screen_mask import anchor_rect, coarse_to_fine_match

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
        return None
    rect = Rect(best[1], best[2], int(ccfg["size"][0]), int(ccfg["size"][1])).clip(w, h)
    if rect is None:
        return None
    logger.debug("Captcha en %s (score %.2f)", rect, best[0])
    return CaptchaInfo(rect=rect, score=best[0])
