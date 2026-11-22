"""Menu de opciones del juego (el que abre Escape sin nada seleccionado): ventana centrada con botones. Se reconoce por la
plantilla del boton "Cancelar" (el de abajo de todo), que lo cierra al hacerle clic."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np

from bot.config import resolve_path
from bot.geometry import Rect

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GameMenu:
    cancel_rect: Rect                 # boton "Cancelar" (coords de cliente)
    cancel_point: tuple[int, int]     # centro del boton: el clic que cierra el menu
    score: float                      # 0..1


@lru_cache(maxsize=4)
def _load_template(path: str) -> np.ndarray | None:
    img = cv2.imread(str(resolve_path(path)), cv2.IMREAD_GRAYSCALE)
    if img is None:
        logger.warning("No se pudo leer la plantilla del menu de opciones: %s", path)
    return img


def find_game_menu(img_bgr: np.ndarray, cfg: dict) -> GameMenu | None:
    """Menu de opciones del juego abierto, o None. Busca el boton "Cancelar" en la zona central (`vision.game_menu.search`,
    fracciones [x0, y0, x1, y1] de la imagen) con score >= `min_score`. Apagado con `enabled: false`."""
    m = cfg["vision"]["game_menu"]
    if not m["enabled"]:
        return None
    tpl = _load_template(m["template"])
    if tpl is None:
        return None
    h, w = img_bgr.shape[:2]
    fx0, fy0, fx1, fy1 = m["search"]
    x0, y0, x1, y1 = int(w * fx0), int(h * fy0), int(w * fx1), int(h * fy1)
    region = img_bgr[y0:y1, x0:x1]
    if region.shape[0] < tpl.shape[0] or region.shape[1] < tpl.shape[1]:
        return None
    res = cv2.matchTemplate(cv2.cvtColor(region, cv2.COLOR_BGR2GRAY), tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    if score < float(m["min_score"]):
        return None
    th, tw = tpl.shape[:2]
    rect = Rect(loc[0] + x0, loc[1] + y0, tw, th)
    return GameMenu(rect, rect.center, float(score))
