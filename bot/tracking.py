"""Filtro de coordenadas del juego y deteccion de "no me muevo" (logica pura)."""
from __future__ import annotations

import logging
import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from bot.vision.coords import Coords

logger = logging.getLogger(__name__)


@dataclass
class CoordStats:
    """Conteo de lecturas de coordenadas de la sesion."""
    total: int = 0       # lecturas intentadas (una por ciclo)
    failed: int = 0      # sin lectura (el texto no se leyo)
    low_conf: int = 0    # descartadas por confianza baja
    jump: int = 0        # descartadas por salto imposible

    def line(self) -> str:
        pct = 100.0 * self.failed / self.total if self.total else 0.0
        return (f"Coordenadas: {self.total} lecturas, {self.failed} fallidas ({pct:.1f}%), "
                f"{self.low_conf} descartadas por confianza, {self.jump} por salto")


class CoordTracker:
    """Posicion filtrada: descarta lecturas de confianza baja y saltos imposibles (segun la velocidad medida) y acepta
    un teletransporte si se confirma. `position` es None si nunca hubo una lectura buena o la ultima es vieja."""

    OK, NO_READ, LOW_CONF, JUMP = "ok", "sin_lectura", "confianza", "salto"   # resultado de la ultima `update`

    def __init__(self, cfg: dict, clock: Callable[[], float] | None = None) -> None:
        f = cfg["coords_filter"]
        self.max_speed = float(f["max_speed"])
        self.slack = float(f["slack"])
        self.accept_after = int(f["accept_after"])
        self.consistency_dist = float(f["consistency_dist"])
        self.max_dt = float(f["max_dt_s"])
        self.min_score = float(f["min_score"])
        self.speed_factor = float(f["speed_factor"])
        self.min_speed_limit = float(f["min_speed_limit"])
        self.no_read_warn_s = float(f["no_read_warn_s"])
        self.stale_s = float(f["stale_s"])
        self.report_every_s = float(f["report_every_min"]) * 60.0
        self.speed_min_samples = int(f["speed_min_samples"])
        self.speed_pct = float(f["speed_percentile"])
        self.speed_min_dt = float(f["speed_min_dt_s"])
        self.speed_move_min = float(f["speed_move_min"])
        self.stuck_window_s = float(cfg["stuck"]["window_s"])
        self.stuck_min_move = float(cfg["stuck"]["min_move"])
        self._clock = clock
        self._pos: tuple[int, int] | None = None
        self.last_good_t: float | None = None    # None tambien tras `forget_reference`
        self._good_t: float | None = None        # ultima lectura buena (no se borra)
        self._now = 0.0
        self._first_t: float | None = None
        self._pending: list[tuple[int, int]] = []
        self._hist: deque[tuple[float, tuple[int, int]]] = deque(maxlen=int(f["history"]))
        self._speeds: deque[float] = deque(maxlen=int(f["speed_window"]))
        self._moving_since: float | None = None
        self.stats = CoordStats()
        self.last_outcome = self.OK
        self._warned = False
        self._last_report: float | None = None

    @property
    def position(self) -> tuple[int, int] | None:
        """Ultima posicion buena, o None si no hay o tiene mas de `stale_s` segundos (zona desconocida)."""
        if self._pos is None or self._good_t is None:
            return None
        now = self._clock() if self._clock is not None else self._now
        return self._pos if now - self._good_t <= self.stale_s else None

    @property
    def last_known(self) -> tuple[int, int] | None:
        """Ultima posicion buena sin importar su antiguedad."""
        return self._pos

    @property
    def measured_speed(self) -> float | None:
        """Velocidad tipica medida (percentil de las ultimas velocidades en movimiento); None sin muestras suficientes."""
        if len(self._speeds) < self.speed_min_samples:
            return None
        return float(np.percentile(list(self._speeds), self.speed_pct))

    def speed_limit(self) -> float:
        """Velocidad maxima creible (unidades/s): la medida por `speed_factor`, entre `min_speed_limit` y `max_speed`."""
        v = self.measured_speed
        if v is None:
            return self.max_speed
        return min(self.max_speed, max(v * self.speed_factor, self.min_speed_limit))

    def confident(self, coords: Coords | None) -> Coords | None:
        """La lectura si su confianza alcanza `min_score` (para lecturas sueltas fuera de `update`), si no None."""
        return coords if coords is not None and coords.score >= self.min_score else None

    def update(self, coords: Coords | None, t: float) -> tuple[int, int] | None:
        """Procesa una lectura (o None) y devuelve la posicion filtrada (None si no hay una buena reciente)."""
        self._now = t
        if self._first_t is None:
            self._first_t = t
        self.stats.total += 1
        self.last_outcome = self._classify(coords, t)
        self._housekeeping(t)
        return self.position

    def _classify(self, coords: Coords | None, t: float) -> str:
        if coords is None:
            self.stats.failed += 1
            return self.NO_READ
        if coords.score < self.min_score:
            self.stats.low_conf += 1
            logger.debug("Lectura descartada por confianza: %.2f < %.2f", coords.score, self.min_score)
            return self.LOW_CONF
        pt = (coords.x, coords.y)
        if self._pos is None or self.last_good_t is None:
            self._accept(pt, t, reset=True)
            return self.OK
        real_dt = max(0.0, t - self.last_good_t)
        dt = min(real_dt, self.max_dt)   # una pausa en las lecturas no habilita saltos grandes
        dist = math.dist(pt, self._pos)
        if dist <= self.speed_limit() * dt + self.slack:
            if self.speed_min_dt <= real_dt <= self.max_dt and dist / real_dt >= self.speed_move_min:
                self._speeds.append(dist / real_dt)
            self._accept(pt, t)
            return self.OK
        self.stats.jump += 1
        if self._pending and math.dist(pt, self._pending[-1]) > self.consistency_dist:
            self._pending = []
        self._pending.append(pt)
        if len(self._pending) >= self.accept_after:
            logger.warning("Teletransporte aceptado: %s -> %s", self._pos, pt)
            self._accept(pt, t, reset=True)
            self._speeds.clear()   # la velocidad medida ya no vale (otra montura, otro mapa): se vuelve a medir
            return self.OK
        logger.debug("Salto rechazado: %s -> %s (dt=%.2f)", self._pos, pt, dt)
        return self.JUMP

    def _housekeeping(self, t: float) -> None:
        """Aviso de lecturas ausentes (una vez por episodio) y resumen periodico en el log."""
        if self.last_outcome == self.OK:
            if self._warned:
                logger.info("Coordenadas: vuelve la lectura buena")
                self._warned = False
        else:
            ref = self._good_t if self._good_t is not None else self._first_t
            if not self._warned and ref is not None and t - ref >= self.no_read_warn_s:
                logger.warning("Coordenadas: %.0f s seguidos sin una lectura buena (ultima: %s)", t - ref, self._pos)
                self._warned = True
        if self._last_report is None:
            self._last_report = t
        elif self.report_every_s > 0 and t - self._last_report >= self.report_every_s:
            self._last_report = t
            logger.info(self.stats.line())

    def _accept(self, pt: tuple[int, int], t: float, reset: bool = False) -> None:
        self._pos, self.last_good_t, self._good_t, self._pending = pt, t, t, []
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
        if self._pos is not None:
            self._hist.append((t, self._pos))

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
