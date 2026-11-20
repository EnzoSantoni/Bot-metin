"""Revivir al morir (Fase 14): configuracion y utilidades puras. El input lo manda `Bot._do_revivir`.

Coordenadas siempre de cliente. Sin capturas ni input propios.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from bot.config import resolve_path
from bot.geometry import Rect
from bot.vision.death import DeathInfo
from bot.vision.screen_mask import find_ui_windows

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReviveCfg:
    """Seccion `revive` (perfil de personaje)."""
    enabled: bool
    button_wait_s: float
    confirm_wait_s: float
    inventory_wait_s: float
    button_tries: int
    max_revives: int
    after_revive_wait_s: float
    recast_skills: bool
    inventory_key: str
    horse_item_template: str
    horse_item_match: float
    horse_item_click: str
    horse_key: str
    horse_wait_s: float
    mount_after: bool
    here_above_city_px: int

    @classmethod
    def from_config(cls, cfg: dict) -> "ReviveCfg":
        r = cfg["revive"]
        return cls(bool(r["auto_revive"]), float(r["button_wait_s"]), float(r["confirm_wait_s"]), float(r["inventory_wait_s"]), int(r["button_tries"]),
                   int(r["max_revives"]), float(r["after_revive_wait_s"]), bool(r["recast_skills"]),
                   str(r["inventory_key"]).strip().lower(), str(r["horse_item_template"]).strip(),
                   float(r["horse_item_match"]), str(r["horse_item_click"]).strip().lower(),
                   str(r["horse_key"]).strip().lower(), float(r["horse_wait_s"]), bool(r["mount_after"]),
                   int(r["here_above_city_px"]))


class RectMask:
    """Mascara (duck typing de `ScreenMask`) que solo permite los puntos de un rectangulo: el clic de revivir y el del
    objeto del caballo caen sobre ventanas de la interfaz que la mascara normal bloquea, asi que se habilitan solo ellos."""

    def __init__(self, rect: Rect) -> None:
        self.rect = rect

    def allows(self, x: int, y: int) -> bool:
        return self.rect.contains(x, y)


def here_button(info: DeathInfo, cfg: ReviveCfg) -> Rect | None:
    """Rectangulo de "Volver a Empezar Aqui": el detectado o, si solo calzo "Ciudad", el de arriba (mismo x, ancho y alto)."""
    if info.here_rect is not None:
        return info.here_rect
    c = info.city_rect
    if c is None:
        return None
    return Rect(c.x, max(0, c.y - cfg.here_above_city_px), c.w, c.h)


def find_icon(img: np.ndarray, template: str, threshold: float, cfg: dict) -> Rect | None:
    """Icono por plantilla (BGR, `cv2.matchTemplate`). Busca primero dentro de las ventanas de la interfaz detectadas
    (el inventario) y, si no, en toda la imagen. None si no calza (score < threshold) o falta la plantilla."""
    path = resolve_path(template)
    tpl = cv2.imread(str(path), cv2.IMREAD_COLOR) if Path(path).is_file() else None
    if tpl is None:
        logger.warning("No se pudo leer la plantilla del objeto del caballo: %s", path)
        return None
    th, tw = tpl.shape[:2]
    regions = list(find_ui_windows(img, cfg)) + [Rect(0, 0, img.shape[1], img.shape[0])]
    for region in regions:
        r = region.clip(img.shape[1], img.shape[0])
        if r is None or r.w < tw or r.h < th:
            continue
        res = cv2.matchTemplate(img[r.y:r.bottom, r.x:r.right], tpl, cv2.TM_CCOEFF_NORMED)
        res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
        _mn, score, _lmn, loc = cv2.minMaxLoc(res)
        if score >= threshold:
            logger.debug("Objeto del caballo en %s (score %.2f)", (r.x + loc[0], r.y + loc[1]), score)
            return Rect(r.x + loc[0], r.y + loc[1], tw, th)
    return None
