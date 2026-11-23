"""Mapa de metin: puntos donde se rompio un metin, persistidos entre sesiones.

Cada punto es la posicion del personaje (coordenadas del juego) al romper un metin, con la hora (epoch, segundos) de
la ultima rotura y de la ultima visita, y el tiempo de reaparicion medido (`respawn_s`). `next_target` elige el punto
elegible mas cercano: ya deberia tener metin (paso su tiempo de reaparicion desde la ultima rotura) y no se visito hace poco;
elegir siempre el mas cercano arma solo el recorrido. Con `path=None` no lee ni escribe nada.

Las horas de sesiones viejas (anteriores a la carga del mapa) no cuentan: un metin roto en otra sesion ya reaparecio y una
visita de otra sesion no hace esperar. Hora 0 (`last_broken` / `last_visit` en 0) = DESCONOCIDA (punto cargado sin hora).

Reaparicion: al romper un metin en un punto que ya se habia roto en ESTA sesion, (hora en que se encontro el metin nuevo -
hora de la rotura anterior) es una cota de lo que tarda en reaparecer; el punto guarda la menor (`respawn_s`). Los puntos sin
medicion usan la mediana de los medidos o, sin ninguna, `spawns.respawn_s`.
"""
from __future__ import annotations

import json
import logging
import math
import os
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from bot.config import DEFAULTS, resolve_path
from bot.fileutil import atomic_write_text

logger = logging.getLogger(__name__)

FORMAT_VERSION = 2   # v2 agrega "mapa" (nombre); la v1 se lee igual
LEGACY_FILE = "mapa_metin.json"   # default anterior (junto al archivo nuevo)
Xy = tuple[float, float]


@dataclass
class Spot:
    """Punto conocido de aparicion de metin."""
    x: float
    y: float
    n: int = 1                 # veces que se rompio un metin aca
    last_broken: float = 0.0   # epoch s (0 = desconocida)
    last_visit: float = 0.0    # epoch s (0 = desconocida)
    respawn_s: float = 0.0     # reaparicion medida (la menor cota vista; 0 = sin medir)

    @property
    def xy(self) -> Xy:
        return (self.x, self.y)

    def ages(self, now: float, session_start: float = 0.0) -> str:
        """Texto 'roto hace X min, visto hace Y min'; las horas de otra sesion (antes de `session_start`) no se muestran."""
        def one(ts: float) -> str:
            if ts <= 0:
                return "? (hora desconocida)"
            if ts < session_start:
                return "en otra sesion"
            return f"{max(0.0, now - ts) / 60:.1f} min"
        return f"roto hace {one(self.last_broken)}, visto hace {one(self.last_visit)}"


def migrate_legacy_map(path: Path) -> bool:
    """Si `path` no existe y hay un `mapa_metin.json` viejo al lado, lo copia y deja el viejo como .migrado."""
    legacy = path.parent / LEGACY_FILE
    if path.exists() or not legacy.is_file():
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, legacy.read_text(encoding="utf-8"))
        os.replace(legacy, legacy.with_name(legacy.name + ".migrado"))
    except OSError as e:
        logger.warning("Mapa de metin: no se pudo migrar %s a %s: %s", legacy, path, e)
        return False
    logger.info("Mapa de metin: migro data/%s a data/%s", LEGACY_FILE, path.name)
    return True


def spawn_map_from_config(sp: dict, *, read_only: bool = False) -> SpawnMap:
    """Arma el SpawnMap desde cfg["spawns"]. Migra el archivo viejo solo si `file` es el default (no explicito)."""
    path = resolve_path(sp["file"])
    if sp["file"] == DEFAULTS["spawns"]["file"]:
        migrate_legacy_map(path)
    return SpawnMap(path, float(sp["merge_u"]), respawn_s=float(sp["respawn_s"]), revisit_s=float(sp["revisit_s"]),
                    read_only=read_only, map_name=sp["map_name"])


class SpawnMap:
    """Puntos conocidos + elegibilidad por reaparicion y revisita. `respawn_s` / `revisit_s` por atributo."""

    def __init__(self, path: Path | None, merge_u: float, *, respawn_s: float = 240.0, revisit_s: float = 120.0,
                 clock_wall: Callable[[], float] = time.time, read_only: bool = False,
                 map_name: str = "Ciudad 1") -> None:
        self.map_name = map_name
        self.path = Path(path) if path is not None else None
        self.merge_u, self.respawn_s, self.revisit_s = float(merge_u), float(respawn_s), float(revisit_s)
        self.now = clock_wall
        self.read_only = read_only   # lee el archivo pero nunca lo escribe (--observar)
        self.spots: list[Spot] = []
        self._broken_file = False   # el archivo existente era ilegible: se respalda como .bak antes del primer guardado
        self.session_start = self.now()   # horas anteriores = de otra sesion (no cuentan para esperar)
        self.last_sample: float | None = None   # ultima medicion de reaparicion (record_broken)
        self._load()

    def __len__(self) -> int:
        return len(self.spots)

    # ------------------------------------------------------------------ persistencia
    def _load(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            saved = data.get("mapa") if isinstance(data, dict) else None   # None: archivo version 1
            self.spots = [Spot(float(d["x"]), float(d["y"]), int(d.get("n", 1)), float(d.get("last_broken", 0.0)),
                               float(d.get("last_visit", 0.0)), float(d.get("respawn_s", 0.0))) for d in data["spots"]]
        except (OSError, ValueError, KeyError, TypeError) as e:
            logger.warning("Mapa de metin: no se pudo leer %s (%s): empiezo vacio", self.path, e)
            self.spots, self._broken_file = [], True
            return
        if saved is not None and saved != self.map_name:
            logger.warning("Mapa de metin (%s): el archivo %s es del mapa %r", self.map_name, self.path, saved)
        logger.info("Mapa de metin (%s): %d puntos cargados de %s", self.map_name, len(self.spots), self.path)

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
            data = {"version": FORMAT_VERSION, "mapa": self.map_name,
                    "spots": [{"x": round(s.x, 2), "y": round(s.y, 2), "n": s.n, "last_broken": round(s.last_broken, 1),
                               "last_visit": round(s.last_visit, 1), "respawn_s": round(s.respawn_s, 1)}
                              for s in self.spots]}
            atomic_write_text(self.path, json.dumps(data, indent=2))
        except OSError as e:
            logger.warning("Mapa de metin: no se pudo guardar %s: %s", self.path, e)

    # ------------------------------------------------------------------ puntos
    def record_broken(self, pos: Xy, found: float | None = None) -> Spot:
        """Un metin se rompio con el personaje en `pos`: une con un punto a <= merge_u (promedio ponderado) o crea uno.
        `found` (epoch s): cuando se encontro ese metin. Si el punto ya se habia roto en esta sesion, (found - rotura anterior)
        es una medicion de la reaparicion (el punto guarda la menor; queda tambien en `last_sample`, si no None)."""
        t = self.now()
        near = [s for s in self.spots if math.dist(s.xy, pos) <= self.merge_u]
        sample = self.last_sample = None
        if near:
            s = min(near, key=lambda s: math.dist(s.xy, pos))
            if found is not None and s.last_broken >= self.session_start and s.last_broken > 0 and found > s.last_broken:
                sample = self.last_sample = found - s.last_broken
                s.respawn_s = sample if s.respawn_s <= 0 else min(s.respawn_s, sample)
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

    def respawn_of(self, s: Spot) -> float:
        """Reaparicion esperada de `s`: la medida; si no tiene, la mediana de los medidos; si no hay, `respawn_s`."""
        if s.respawn_s > 0:
            return s.respawn_s
        known = [q.respawn_s for q in self.spots if q.respawn_s > 0]
        return statistics.median(known) if known else self.respawn_s

    def _wait(self, s: Spot, now: float) -> float:
        """Segundos hasta que `s` quede elegible (0 = ya). Una hora desconocida o de otra sesion no hace esperar. `revisit_s`
        cuenta solo para una visita SIN metin (posterior a la ultima rotura); tras romperlo manda la reaparicion."""
        w_broken = s.last_broken + self.respawn_of(s) - now if s.last_broken >= max(self.session_start, 1e-9) else 0.0
        visited = s.last_visit >= max(self.session_start, 1e-9) and s.last_visit > s.last_broken
        w_visit = s.last_visit + self.revisit_s - now if visited else 0.0
        return max(w_broken, w_visit, 0.0)

    def ready_in(self, s: Spot) -> float:
        """Segundos hasta que `s` deberia tener metin y se puede volver (0 = ya)."""
        return self._wait(s, self.now())

    def _eligible(self, s: Spot, now: float) -> bool:
        return self._wait(s, now) <= 0.0

    def next_target(self, pos: Xy, accepts: Callable[[Xy], bool]) -> Spot | None:
        """El punto mas cercano a `pos` dentro de la zona (`accepts`) que ya reaparecio y no se visito hace poco."""
        now = self.now()
        ok = [s for s in self.spots if accepts(s.xy) and self._eligible(s, now)]
        return min(ok, key=lambda s: math.dist(s.xy, pos)) if ok else None

    def seconds_until_ready(self, accepts: Callable[[Xy], bool]) -> float | None:
        """Segundos hasta que el primer punto de la zona quede elegible (None si no hay puntos)."""
        now = self.now()
        waits = [self._wait(s, now) for s in self.spots if accepts(s.xy)]
        return min(waits) if waits else None
