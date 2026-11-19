"""Zona de farmeo poligonal en coordenadas del juego (logica pura, sin capturas ni input)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

import cv2
import numpy as np

from bot.config import ConfigError, validate_farm_zone

_MAX_TRIES = 1000  # intentos de muestreo por rechazo antes de caer al punto interior
_RASTER_SIZE = 1000  # lado mayor (px) de la mascara con que se busca el punto interior
_CENTROID_MIN_DEPTH = 0.25  # el centroide sirve si esta a >= esta fraccion de la profundidad maxima del borde


class ZoneStatus(Enum):
    INSIDE = "inside"
    TOLERANCE = "tolerance"   # afuera pero a <= outside_tolerance del borde
    OUTSIDE = "outside"


@dataclass(frozen=True)
class FarmZone:
    polygon: tuple[tuple[float, float], ...]
    outside_tolerance: float
    interior_center: tuple[float, float] = field(init=False, compare=False)   # punto interior mas alejado del borde

    def __post_init__(self) -> None:
        _ = self.centroid   # ConfigError si el area es 0
        object.__setattr__(self, "interior_center", self._find_interior_center())

    @classmethod
    def from_config(cls, cfg: dict) -> "FarmZone":
        z = cfg["farm_zone"]
        validate_farm_zone(z)
        return cls(tuple((float(x), float(y)) for x, y in z["polygon"]), float(z["outside_tolerance"]))

    @property
    def _contour(self) -> np.ndarray:
        return np.array(self.polygon, dtype=np.float32).reshape(-1, 1, 2)

    def signed_distance(self, pt: tuple[float, float]) -> float:
        """Distancia al borde: positiva adentro, negativa afuera, 0 en el borde."""
        return float(cv2.pointPolygonTest(self._contour, (float(pt[0]), float(pt[1])), True))

    def status(self, pt: tuple[float, float]) -> ZoneStatus:
        d = self.signed_distance(pt)
        if d >= 0:
            return ZoneStatus.INSIDE
        return ZoneStatus.TOLERANCE if -d <= self.outside_tolerance else ZoneStatus.OUTSIDE

    def accepts(self, pt: tuple[float, float]) -> bool:
        """INSIDE o TOLERANCE."""
        return self.status(pt) is not ZoneStatus.OUTSIDE

    @property
    def centroid(self) -> tuple[float, float]:
        """Centroide de area (no el promedio de vertices)."""
        pts = self.polygon
        a = cx = cy = 0.0
        for i, (x0, y0) in enumerate(pts):
            x1, y1 = pts[(i + 1) % len(pts)]
            cr = x0 * y1 - x1 * y0
            a += cr
            cx += (x0 + x1) * cr
            cy += (y0 + y1) * cr
        if a == 0:
            raise ConfigError("farm_zone.polygon tiene area 0: no se puede calcular el centroide")
        return cx / (3 * a), cy / (3 * a)

    def _find_interior_center(self) -> tuple[float, float]:
        """Punto interior mas lejos del borde (maximo de la transformada de distancia del poligono rasterizado)."""
        xs = [p[0] for p in self.polygon]
        ys = [p[1] for p in self.polygon]
        x0, y0 = min(xs), min(ys)
        scale = _RASTER_SIZE / max(max(xs) - x0, max(ys) - y0, 1e-9)
        pad = 2
        w = int((max(xs) - x0) * scale) + 2 * pad + 1
        h = int((max(ys) - y0) * scale) + 2 * pad + 1
        pts = np.array([((x - x0) * scale + pad, (y - y0) * scale + pad) for x, y in self.polygon])
        mask = np.zeros((h, w), np.uint8)
        cv2.fillPoly(mask, [np.round(pts).astype(np.int32)], 255)
        dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
        if dist.max() <= 0:
            return self.centroid
        iy, ix = np.unravel_index(int(np.argmax(dist)), dist.shape)
        return (float(ix - pad) / scale + x0, float(iy - pad) / scale + y0)

    def direction_to_center(self, pt: tuple[float, float]) -> tuple[float, float]:
        """Vector unitario desde pt hacia el centroide, o hacia `interior_center` si el centroide cae
        afuera o muy pegado al borde (poligonos en U o muy finos). (0, 0) si coinciden."""
        cx, cy = self.centroid
        depth = self.signed_distance(self.interior_center)
        if self.signed_distance((cx, cy)) < _CENTROID_MIN_DEPTH * depth:
            cx, cy = self.interior_center
        dx, dy = cx - pt[0], cy - pt[1]
        n = math.hypot(dx, dy)
        return (0.0, 0.0) if n < 1e-9 else (dx / n, dy / n)

    def random_point_inside(self, rng, margin: float = 0) -> tuple[float, float]:
        """Punto aleatorio dentro, a >= margin del borde. `rng` es random.Random. Fallback: `interior_center`."""
        xs = [p[0] for p in self.polygon]
        ys = [p[1] for p in self.polygon]
        for _ in range(_MAX_TRIES):
            pt = (rng.uniform(min(xs), max(xs)), rng.uniform(min(ys), max(ys)))
            if self.signed_distance(pt) >= margin:
                return pt
        return self.interior_center
