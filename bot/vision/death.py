"""Deteccion de la pantalla de muerte (botones "Volver a Empezar Aqui" / "...en la Ciudad")."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from bot.geometry import Rect
from bot.vision.screen_mask import coarse_to_fine_match

logger = logging.getLogger(__name__)

@dataclass
class DeathInfo:
    """Botones encontrados (coords de cliente; None si ese no calzo) y el mejor score."""
    here_rect: Rect | None
    city_rect: Rect | None
    score: float


def find_death(img_bgr: np.ndarray, cfg: dict) -> DeathInfo | None:
    """Pantalla de muerte si calza cualquiera de los dos botones en la region de arriba a la izquierda.

    Busqueda gruesa en gris a `coarse_scale` y refinada a resolucion completa (como las ventanas).
    """
    dcfg = cfg["vision"]["death"]
    region = Rect(0, 0, int(dcfg["search"]["w"]), int(dcfg["search"]["h"]))
    rects: dict[str, Rect | None] = {}
    scores: list[float] = []
    for key in ("here_template", "city_template"):
        rects[key], score = coarse_to_fine_match(img_bgr, dcfg[key], dcfg, region=region)
        if rects[key] is not None:
            scores.append(score)
    if not scores:
        return None
    logger.debug("Muerte: %s score=%.2f", rects, max(scores))
    return DeathInfo(here_rect=rects["here_template"], city_rect=rects["city_template"], score=max(scores))
