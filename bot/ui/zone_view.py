"""Logica pura (sin tkinter) de la pestana Zona: datos de la zona y del mapa de metin, escala a canvas, franja de
tolerancia, textos de los puntos y rastro de posiciones. El dibujo en si esta en `bot/ui/app.py`."""
from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from bot.config import resolve_path
from bot.profiles import build_config
from bot.spawns import Spot, SpawnMap

logger = logging.getLogger(__name__)

CANVAS_MARGIN_PX = 30        # margen entre el dibujo y el borde del canvas
TRAIL_LEN = 25               # ultimas posiciones del personaje que se dibujan como rastro
SPOT_HIT_RADIUS_PX = 8       # distancia maxima del mouse a un punto para mostrar su tooltip
MIN_SPAN_U = 1.0             # lado minimo (unidades de juego) para no dividir por cero


@dataclass
class ZoneData:
    """Lo que se dibuja: poligono, tolerancia y puntos del mapa de metin (con `error` si no se pudo leer algo)."""
    polygon: list[tuple[float, float]] = field(default_factory=list)
    tolerance: float = 0.0
    spots: list[Spot] = field(default_factory=list)
    error: str = ""


def load_zone_data(config_path: str | Path, servidor: str | None, personaje: str | None, zona: str | None,
                   profiles_root: str | Path | None = None) -> ZoneData:
    """Zona y mapa de metin efectivos para los perfiles elegidos (los de la config armada con `build_config`)."""
    try:
        cfg = build_config(config_path, servidor or None, personaje or None, zona or None,
                           profiles_root_dir=profiles_root, warn=False)
    except Exception as e:  # noqa: BLE001 - la pestana no debe romper la ventana
        return ZoneData(error=f"No se pudo leer la configuración: {e}")
    fz = cfg["farm_zone"]
    data = ZoneData(polygon=[(float(x), float(y)) for x, y in fz["polygon"]], tolerance=float(fz["outside_tolerance"]))
    sp = cfg["spawns"]
    if sp["enabled"]:
        data.spots = SpawnMap(resolve_path(sp["file"]), float(sp["merge_u"]), read_only=True).spots
    return data


@dataclass(frozen=True)
class Transform:
    """Escala y desplazamiento de unidades de juego a pixeles de canvas (y hacia abajo, igual que el juego)."""
    scale: float
    ox: float
    oy: float

    def apply(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.ox) * self.scale, (y - self.oy) * self.scale


def fit_transform(polygon: list[tuple[float, float]], tolerance: float, width: int, height: int,
                  margin: int = CANVAS_MARGIN_PX) -> Transform:
    """Escala uniforme para que el poligono mas su tolerancia entren centrados en el canvas con `margin` px de margen."""
    if not polygon:
        return Transform(1.0, 0.0, 0.0)
    xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
    x0, x1, y0, y1 = min(xs) - tolerance, max(xs) + tolerance, min(ys) - tolerance, max(ys) + tolerance
    spanx, spany = max(x1 - x0, MIN_SPAN_U), max(y1 - y0, MIN_SPAN_U)
    availx, availy = max(width - 2 * margin, 1), max(height - 2 * margin, 1)
    scale = min(availx / spanx, availy / spany)
    ox = x0 - (width / scale - spanx) / 2
    oy = y0 - (height / scale - spany) / 2
    return Transform(scale, ox, oy)


def polygon_to_canvas(polygon: list[tuple[float, float]], tr: Transform) -> list[tuple[float, float]]:
    return [tr.apply(x, y) for x, y in polygon]


def tolerance_outline(poly_px: list[tuple[float, float]], radius_px: float, width: int, height: int) -> list[list[tuple[int, int]]]:
    """Contornos (en px de canvas) de la franja de tolerancia: el poligono agrandado `radius_px`. Lista vacia si no hay."""
    if len(poly_px) < 3 or radius_px <= 0:
        return []
    pts = np.array([[round(x), round(y)] for x, y in poly_px], dtype=np.int32)
    mask = np.zeros((height, width), np.uint8)
    cv2.fillPoly(mask, [pts], 255)
    r = int(round(radius_px))
    band = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    contours, _ = cv2.findContours(band, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return [[(int(p[0][0]), int(p[0][1])) for p in c] for c in contours]


def ago_text(epoch_s: float, now: float | None = None) -> str:
    """'hace 5 min' / 'hace 2 h 10 min' / 'hace 3 d'; 'nunca' si no hay hora."""
    if epoch_s <= 0:
        return "nunca"
    delta = max(0, int((time.time() if now is None else now) - epoch_s))
    if delta < 60:
        return "hace menos de 1 min"
    mins = delta // 60
    if mins < 60:
        return f"hace {mins} min"
    hours, rem = divmod(mins, 60)
    if hours < 48:
        return f"hace {hours} h {rem} min" if rem else f"hace {hours} h"
    return f"hace {hours // 24} d"


def spot_tooltip(spot: Spot, now: float | None = None) -> str:
    """Texto del tooltip de un punto del mapa de metin."""
    veces = "vez" if spot.n == 1 else "veces"
    return (f"Punto ({spot.x:.0f}, {spot.y:.0f})\nSe rompió {spot.n} {veces}\n"
            f"Último metin roto: {ago_text(spot.last_broken, now)}")


def nearest_spot(spots_px: list[tuple[float, float]], x: float, y: float, radius: float = SPOT_HIT_RADIUS_PX) -> int | None:
    """Indice del punto (en px de canvas) mas cercano a (x, y) dentro de `radius`, o None."""
    best, best_d = None, radius
    for i, (sx, sy) in enumerate(spots_px):
        d = math.hypot(sx - x, sy - y)
        if d <= best_d:
            best, best_d = i, d
    return best


class Trail:
    """Rastro de las ultimas posiciones del personaje (solo agrega cuando la posicion cambio)."""

    def __init__(self, maxlen: int = TRAIL_LEN) -> None:
        self.points: deque[tuple[float, float]] = deque(maxlen=maxlen)

    def add(self, pos: tuple[float, float] | None) -> bool:
        """True si se agrego (posicion nueva)."""
        if pos is None or (self.points and self.points[-1] == pos):
            return False
        self.points.append(pos)
        return True

    def clear(self) -> None:
        self.points.clear()


ZONE_STATUS_TEXT = {"inside": "Dentro de la zona", "tolerance": "En la franja de tolerancia", "outside": "Fuera de la zona"}


def position_text(pos: tuple[float, float] | None, zone_status: str) -> str:
    """Texto de la posicion actual del personaje para la pestana Zona."""
    if pos is None:
        return "Posición del personaje: sin dato"
    return f"Posición del personaje: ({pos[0]:.0f}, {pos[1]:.0f}) - {ZONE_STATUS_TEXT.get(zone_status, 'zona desconocida')}"
