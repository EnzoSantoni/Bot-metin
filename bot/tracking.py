"""Filtro de coordenadas del juego y deteccion de "no me muevo" (logica pura)."""
from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field

from bot.vision.coords import Coords

logger = logging.getLogger(__name__)


class CoordTracker:
    """Posicion filtrada: descarta saltos imposibles y acepta un teletransporte si se confirma."""

    def __init__(self, cfg: dict) -> None:
        f = cfg["coords_filter"]
        self.max_speed = float(f["max_speed"])
        self.slack = float(f["slack"])
        self.accept_after = int(f["accept_after"])
        self.consistency_dist = float(f["consistency_dist"])
        self.max_dt = float(f["max_dt_s"])
        self.stuck_window_s = float(cfg["stuck"]["window_s"])
        self.stuck_min_move = float(cfg["stuck"]["min_move"])
        self.position: tuple[int, int] | None = None
        self.last_good_t: float | None = None
        self._pending: list[tuple[int, int]] = []
        self._hist: deque[tuple[float, tuple[int, int]]] = deque(maxlen=int(f["history"]))
        self._moving_since: float | None = None

    def update(self, coords: Coords | None, t: float) -> tuple[int, int] | None:
        """Procesa una lectura (o None) y devuelve la posicion filtrada (None si nunca hubo una buena)."""
        if coords is None:
            return self.position
        pt = (coords.x, coords.y)
        if self.position is None or self.last_good_t is None:
            self._accept(pt, t, reset=True)
            return self.position
        dt = min(max(0.0, t - self.last_good_t), self.max_dt)   # una pausa en las lecturas no habilita saltos grandes
        if math.dist(pt, self.position) <= self.max_speed * dt + self.slack:
            self._accept(pt, t)
            return self.position
        if self._pending and math.dist(pt, self._pending[-1]) > self.consistency_dist:
            self._pending = []
        self._pending.append(pt)
        if len(self._pending) >= self.accept_after:
            logger.warning("Teletransporte aceptado: %s -> %s", self.position, pt)
            self._accept(pt, t, reset=True)
        else:
            logger.debug("Salto rechazado: %s -> %s (dt=%.2f)", self.position, pt, dt)
        return self.position

    def _accept(self, pt: tuple[int, int], t: float, reset: bool = False) -> None:
        self.position, self.last_good_t, self._pending = pt, t, []
        if reset:
            self._hist.clear()
            if self._moving_since is not None:
                self._moving_since = t
        self._hist.append((t, pt))

    def forget_reference(self) -> None:
        """El bot se movio durante una accion bloqueante larga (por ejemplo ESPACIO + W, ~3 s sin leer coordenadas): la
        proxima lectura se acepta sin filtro de velocidad (el filtro supone un dt acotado)."""
        self.last_good_t = None
        self._pending = []

    def mark_moving(self, t: float) -> None:
        """El bot acaba de intentar moverse (por ejemplo, un clic de caminata): reinicia la ventana de trabado."""
        self._moving_since = t
        self._hist.clear()
        if self.position is not None:
            self._hist.append((t, self.position))

    def is_stuck(self, t: float, window_s: float | None = None) -> bool:
        """True si en los ultimos window_s s el desplazamiento maximo fue < min_move (solo tras mark_moving)."""
        w = self.stuck_window_s if window_s is None else window_s
        if self._moving_since is None or t - self._moving_since < w:
            return False
        if self.last_good_t is None or self.last_good_t < t - w:
            return False   # sin lecturas dentro de la ventana: no hay evidencia
        start = t - w
        pts = [p for ts, p in self._hist if ts >= start]
        older = [p for ts, p in self._hist if ts < start]
        if older:
            pts.append(older[-1])   # posicion al empezar la ventana
        if len(pts) < 2:
            return False
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return math.hypot(max(xs) - min(xs), max(ys) - min(ys)) < self.stuck_min_move


# ---------------------------------------------------------------------------------------------------------------
# Episodios de traba
# ---------------------------------------------------------------------------------------------------------------
STRATEGIES = ("ESPACIO", "MIRAR", "ATRAS")   # orden de rotacion


def angle_sep(a: float, b: float) -> float:
    """Diferencia absoluta entre dos angulos (rad), en [0, pi]."""
    return abs((a - b + math.pi) % (2 * math.pi) - math.pi)


@dataclass
class TriedDir:
    """Direccion probada en un episodio: angulo de pantalla en el marco de camara `frame` y, si la camara estaba
    lista, el angulo en el juego (que no depende de como gire la camara)."""
    frame: int
    screen: float
    game: float | None = None


@dataclass
class StuckEpisode:
    """Una traba y todo lo que se probo para salir de ella. Vive entre estados (VOLVER, EXPLORAR, ATACAR, ACERCARSE...)
    y se cierra solo con progreso real (ver `Bot._close_episode`)."""
    n: int
    origin: tuple[int, int]
    start_t: float
    last_t: float
    attempt: int = 0
    last_strategy: str | None = None
    strategies: list[str] = field(default_factory=list)
    dirs: list[TriedDir] = field(default_factory=list)   # puede heredar las del episodio anterior si fue reciente
    # Para decidir si hubo progreso real al cerrar el episodio (ver `Bot._episode_progress`):
    state: str = ""                       # estado en el que se detecto la traba
    origin_sd: float | None = None        # distancia firmada a la zona en el origen
    metric0: float | None = None          # medida de cercania en VOLVER / CENTRO (mayor = mejor)
    goal: tuple[float, float] | None = None   # objetivo (posicion en el juego) en ATACAR / ACERCARSE
    goal_dist0: float | None = None       # distancia al objetivo en el origen

    def space_failures(self) -> int:
        """ESPACIO seguidos (los ultimos intentos del episodio): cada uno fallo porque hubo otro intento despues."""
        n = 0
        for s in reversed(self.strategies):
            if s != "ESPACIO":
                break
            n += 1
        return n

    def next_strategy(self, monsters_close: bool, space_fail_max: int = 2) -> str:
        """Con monstruos cerca, ESPACIO (regla A) hasta que falle `space_fail_max` veces seguidas; si no hay monstruos, o
        ESPACIO ya fallo, rotan MIRAR y ATRAS sin repetir la misma dos veces seguidas."""
        if monsters_close and self.space_failures() < space_fail_max:
            return "ESPACIO"
        rot = [s for s in STRATEGIES if s != "ESPACIO"]
        last = next((s for s in reversed(self.strategies) if s != "ESPACIO"), self.last_strategy)
        return rot[(rot.index(last) + 1) % len(rot)] if last in rot else rot[0]

    def start_attempt(self, strategy: str, t: float) -> None:
        self.attempt += 1
        self.last_strategy = strategy
        self.strategies.append(strategy)
        self.last_t = t

    def add_dir(self, frame: int, screen: float, game: float | None) -> TriedDir:
        d = TriedDir(frame, screen, game)
        self.dirs.append(d)
        return d

    def separation(self, frame: int, screen: float, game: float | None) -> float:
        """Menor separacion (rad) entre una direccion candidata y las ya probadas. Mismo marco de camara: angulos de
        pantalla; marcos distintos con angulo de juego en ambas: angulos de juego; si no se puede comparar bien, se
        compara el de pantalla (conservador: tampoco se repiten clics alrededor del mismo punto de la pantalla).
        pi si no hay ninguna."""
        best = math.pi
        for d in self.dirs:
            if d.frame != frame and game is not None and d.game is not None:
                best = min(best, angle_sep(game, d.game))
            else:
                best = min(best, angle_sep(screen, d.screen))
        return best
