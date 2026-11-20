"""Mapa de metin: puntos donde se rompio un metin, persistidos entre sesiones.

Cada punto es la posicion del personaje (coordenadas del juego) al romper un metin, con la hora (epoch, segundos) de
la ultima rotura y de la ultima visita. `next_target` elige el punto elegible mas cercano: ya reaparecio y no se
visito hace poco; elegir siempre el mas cercano arma solo el recorrido. Con `path=None` no lee ni escribe nada.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from bot.fileutil import atomic_write_text

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
Xy = tuple[float, float]


@dataclass
class Spot:
    """Punto conocido de aparicion de metin."""
    x: float
    y: float
    n: int = 1                 # veces que se rompio un metin aca
    last_broken: float = 0.0   # epoch s
    last_visit: float = 0.0    # epoch s

    @property
    def xy(self) -> Xy:
        return (self.x, self.y)


class SpawnMap:
    """Puntos conocidos + elegibilidad por reaparicion y revisita. `respawn_s` / `revisit_s` por atributo."""

    def __init__(self, path: Path | None, merge_u: float, *, respawn_s: float = 240.0, revisit_s: float = 120.0,
                 clock_wall: Callable[[], float] = time.time, read_only: bool = False) -> None:
        self.path = Path(path) if path is not None else None
        self.merge_u, self.respawn_s, self.revisit_s = float(merge_u), float(respawn_s), float(revisit_s)
        self.now = clock_wall
        self.read_only = read_only   # lee el archivo pero nunca lo escribe (--observar)
        self.spots: list[Spot] = []
        self._broken_file = False   # el archivo existente era ilegible: se respalda como .bak antes del primer guardado
        self._load()

    def __len__(self) -> int:
        return len(self.spots)

    # ------------------------------------------------------------------ persistencia
    def _load(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.spots = [Spot(float(d["x"]), float(d["y"]), int(d.get("n", 1)), float(d.get("last_broken", 0.0)),
                               float(d.get("last_visit", 0.0))) for d in data["spots"]]
        except (OSError, ValueError, KeyError, TypeError) as e:
            logger.warning("Mapa de metin: no se pudo leer %s (%s): empiezo vacio", self.path, e)
            self.spots, self._broken_file = [], True
            return
        logger.info("Mapa de metin: %d puntos cargados de %s", len(self.spots), self.path)

    def save(self) -> None:
        """Guardado atomico (temporal + os.replace). Un archivo ilegible se renombra a .bak antes."""
        if self.path is None or self.read_only:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self._broken_file:
                bak = self.path.with_name(self.path.name + ".bak")
                os.replace(self.path, bak)
                logger.warning("Mapa de metin: archivo ilegible respaldado en %s", bak)
                self._broken_file = False
            data = {"version": FORMAT_VERSION,
                    "spots": [{"x": round(s.x, 2), "y": round(s.y, 2), "n": s.n, "last_broken": round(s.last_broken, 1),
                               "last_visit": round(s.last_visit, 1)} for s in self.spots]}
            atomic_write_text(self.path, json.dumps(data, indent=2))
        except OSError as e:
            logger.warning("Mapa de metin: no se pudo guardar %s: %s", self.path, e)

    # ------------------------------------------------------------------ puntos
    def record_broken(self, pos: Xy) -> Spot:
        """Un metin se rompio con el personaje en `pos`: une con un punto a <= merge_u (promedio ponderado) o crea uno."""
        t = self.now()
        near = [s for s in self.spots if math.dist(s.xy, pos) <= self.merge_u]
        if near:
            s = min(near, key=lambda s: math.dist(s.xy, pos))
            s.x, s.y = (s.x * s.n + pos[0]) / (s.n + 1), (s.y * s.n + pos[1]) / (s.n + 1)
            s.n += 1
        else:
            s = Spot(float(pos[0]), float(pos[1]))
            self.spots.append(s)
        s.last_broken = s.last_visit = t
        self.save()
        return s

    def mark_visited(self, spot: Spot) -> None:
        spot.last_visit = self.now()
        self.save()

    def _eligible(self, s: Spot, now: float) -> bool:
        return now - s.last_broken >= self.respawn_s and now - s.last_visit >= self.revisit_s

    def next_target(self, pos: Xy, accepts: Callable[[Xy], bool]) -> Spot | None:
        """El punto mas cercano a `pos` dentro de la zona (`accepts`) que ya reaparecio y no se visito hace poco."""
        now = self.now()
        ok = [s for s in self.spots if accepts(s.xy) and self._eligible(s, now)]
        return min(ok, key=lambda s: math.dist(s.xy, pos)) if ok else None

    def seconds_until_ready(self, accepts: Callable[[Xy], bool]) -> float | None:
        """Segundos hasta que el primer punto de la zona quede elegible (None si no hay puntos)."""
        now = self.now()
        waits = [max(s.last_broken + self.respawn_s - now, s.last_visit + self.revisit_s - now, 0.0)
                 for s in self.spots if accepts(s.xy)]
        return min(waits) if waits else None
