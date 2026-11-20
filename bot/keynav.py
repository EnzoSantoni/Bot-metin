"""Navegacion SOLO con teclado (logica pura, sin input): hacia donde camina W (`cam_h`), combinaciones de teclas de
movimiento, velocidades y giro de la vista con Q/E.

W/A/S/D caminan relativo a la camara. `cam_h` (en `heading`) es el angulo, en coordenadas del juego (atan2(dy, dx)), hacia
donde camina W; sale del desplazamiento medido en un tramo menos el desvio de la combinacion usada. El juego no esta
espejado respecto de la pantalla (x a la derecha, y hacia abajo en ambos), asi que un aumento del angulo en el juego
equivale a girar hacia la derecha en pantalla: D = +90, A = -90 (`side` = +1). Si A/D resultan al reves (`side` = -1) se
aprende solo. Q/E ya no se usan para caminar: solo para mirar; despues de un giro `cam_h` queda "estimado" y el primer
tramo lo vuelve a medir. El angulo de pantalla de un vector v respecto del frente es `screen_angle(v) = atan2(vx, -vy)`.
"""
from __future__ import annotations

import logging
import math

from bot import ground
from bot.navigation import _wrap

logger = logging.getLogger(__name__)

Vec = tuple[float, float]
MIN_ORDERED = math.radians(15.0)    # giro ordenado minimo para aprender de el
MIN_REAL = math.radians(10.0)       # giro real minimo para aprender de el
MIN_RATE_TURN = math.radians(30.0)  # giros menores no sirven para medir la velocidad de giro (el ruido pesa demasiado)
MAX_LEARN_TURN = math.radians(110.0)   # giros mayores son ambiguos (con giros reales de hasta 1.5x el signo se confunde)
RATE_FACTORS = (0.25, 4.0)          # cotas de lo aprendido respecto del valor inicial
SWEEP_MARGIN = math.radians(10.0)   # giro acumulado por encima de max_turn (varias ordenes seguidas) = barrido
EMA = 0.5
REJECT = math.radians(60.0)         # una muestra de cam_h a mas de esto de un cam_h medido se rechaza
FLIP_FIT = math.radians(35.0)       # con A/D invertidos la muestra debe caer a menos de esto de cam_h para votar la inversion
VOTES = 2                           # muestras seguidas consistentes para concluir una inversion (A/D o Q/E)
OTHER_KEY = {"camera_left": "camera_right", "camera_right": "camera_left"}

# 8 direcciones: combinacion -> acciones de `controls` y desvio (grados, con side = +1) respecto de donde camina W.
COMBOS = ("W", "W+D", "D", "S+D", "S", "S+A", "A", "W+A")
COMBO_ACTIONS = {"W": ["move_up"], "W+D": ["move_up", "move_right"], "D": ["move_right"],
                 "S+D": ["move_down", "move_right"], "S": ["move_down"], "S+A": ["move_down", "move_left"],
                 "A": ["move_left"], "W+A": ["move_up", "move_left"]}
COMBO_BASE_DEG = {"W": 0.0, "W+D": 45.0, "D": 90.0, "S+D": 135.0, "S": 180.0, "S+A": -135.0, "A": -90.0, "W+A": -45.0}
SECTOR_RAD = math.radians(45.0)


def is_lateral(combo: str) -> bool:
    """La combinacion usa A o D (su desvio depende de `side`)."""
    return "A" in combo or "D" in combo


def combo_for_screen_angle(angle: float) -> str:
    """Combinacion cuyo desvio respecto del frente esta mas cerca del angulo de pantalla `angle` (positivo = derecha)."""
    return COMBOS[round(_wrap(angle) / SECTOR_RAD) % len(COMBOS)]


def combo_toward_ground(feet: tuple[float, float], pt: tuple[float, float], shape: tuple[int, ...], cfg: dict) -> str:
    """Combinacion para ir de los pies a un punto de pantalla, midiendo la direccion sobre el plano del piso (con la
    perspectiva) y partiendola en sectores de 45 grados."""
    fx, fz = ground.to_ground(feet, shape, cfg)
    px, pz = ground.to_ground(pt, shape, cfg)
    return combo_for_screen_angle(math.atan2(px - fx, pz - fz))


def screen_angle(v: Vec) -> float:
    """Angulo (rad) de un vector de pantalla respecto del frente (arriba): positivo = a la derecha."""
    return math.atan2(v[0], -v[1])


class KeyboardNav:
    """Estado de la navegacion con teclado: cam_h, sentido de A/D, velocidad de giro, de caminata y sentido de Q/E."""

    def __init__(self, cfg: dict) -> None:
        n = cfg["navigation"]
        k = n["keyboard"]
        self.min_game = float(n["min_segment_game"])
        self.tolerance = math.radians(float(n["heading_tolerance_deg"]))
        self.align = math.radians(float(k["align_deg"]))
        self.max_turn = math.radians(float(k["max_turn_deg"]))
        self.rate0 = math.radians(float(k["turn_deg_per_s"]))
        self.speed0 = float(k["walk_speed"])
        self.rate, self.speed = self.rate0, self.speed0
        self.view_right_key: str = k["view_right_key"]
        self.heading: float | None = None      # cam_h: hacia donde camina W en el juego (None = no se conoce)
        self.measured = False                  # True si salio de un tramo medido (False = estimado con los giros)
        self.side = 1                          # +1: D camina a la derecha de W (en angulo del juego); -1: al reves
        self.side_checked = False              # una muestra lateral ya confirmo (o corrigio) `side`
        self.pending: tuple[float | None, float, float] | None = None   # (cam_h previo medido, giro ordenado, segundos)
        self._acc = (0.0, 0.0, 0.0, "W")       # desplazamiento, segundos y combinacion acumulados que todavia no alcanzan
        self._rejects = 0                      # muestras seguidas rechazadas por estar lejos de un cam_h medido
        self._side_votes = 0                   # muestras laterales seguidas que indican `side` al reves
        self._turn_votes = 0                   # giros seguidos medidos en sentido contrario al ordenado

    @property
    def state(self) -> str:
        """'medido' | 'estimado' | 'desconocido'."""
        return "desconocido" if self.heading is None else ("medido" if self.measured else "estimado")

    def offset(self, combo: str) -> float:
        """Desvio (rad) de la combinacion respecto de donde camina W, con el `side` vigente."""
        base = math.radians(COMBO_BASE_DEG[combo])
        return _wrap(base * self.side) if is_lateral(combo) else base

    def plan_walk(self, bearing: float) -> tuple[str, float] | None:
        """(combinacion, error previsto en rad) para ir hacia el rumbo `bearing` (juego); None si cam_h se desconoce."""
        if self.heading is None:
            return None
        best = min(COMBOS, key=lambda c: abs(_wrap(self.heading + self.offset(c) - bearing)))
        return best, _wrap(self.heading + self.offset(best) - bearing)

    def needs_probe(self, combo: str) -> bool:
        """Conviene un tramo de prueba con W antes de `combo`: cam_h desconocido, o estimado y la combinacion es
        lateral mientras A/D no se hayan verificado (un cam_h estimado y `side` al reves se contaminarian)."""
        return self.heading is None or (not self.measured and is_lateral(combo) and not self.side_checked)

    # --- giro de la vista ---
    def _sign(self, direction: str) -> int:
        """+1 si la tecla `direction` ('left' | 'right', como en `GameInput.rotate_camera`) gira la vista a la derecha."""
        right_dir = "right" if self.view_right_key == "camera_right" else "left"
        return 1 if direction == right_dir else -1

    def plan_turn(self, delta: float) -> tuple[str, float]:
        """(tecla 'left'|'right', segundos) para girar la vista `delta` rad (positivo = derecha), recortado a `max_turn`."""
        d = max(-self.max_turn, min(self.max_turn, delta))
        right_dir = "right" if self.view_right_key == "camera_right" else "left"
        left_dir = "left" if right_dir == "right" else "right"
        return (right_dir if d >= 0 else left_dir), abs(d) / self.rate

    def on_turned(self, direction: str, seconds: float) -> None:
        """Se giro la vista con `direction` durante `seconds`: cam_h pasa a estimado y se guarda el giro para verificarlo
        con el proximo tramo medido."""
        if seconds <= 0:
            return
        ordered = self._sign(direction) * seconds * self.rate
        if self.pending is None:
            self.pending = (self.heading if self.measured else None, ordered, seconds)
        else:
            prev, o, s = self.pending
            self.pending = (prev, o + ordered, s + seconds)
        self._acc = (0.0, 0.0, 0.0, "W")
        if self.heading is not None:
            self.heading = _wrap(self.heading + ordered)
            self.measured = False
            if abs(self.pending[1]) > self.max_turn + SWEEP_MARGIN:   # barrido largo: el estimado ya no vale, se vuelve a medir
                self.invalidate()

    def invalidate(self) -> None:
        """Olvida cam_h (y el giro pendiente): el proximo tramo de prueba lo mide."""
        self.heading, self.measured, self.pending = None, False, None
        self._acc, self._rejects = (0.0, 0.0, 0.0, "W"), 0

    def flip_keys(self) -> None:
        """Q/E giran al reves de lo supuesto."""
        self.view_right_key = OTHER_KEY[self.view_right_key]
        self._turn_votes = 0
        self.invalidate()

    # --- tramos de caminata ---
    def _ema(self, old: float, new: float, ref: float) -> float:
        v = (1 - EMA) * old + EMA * new
        return max(RATE_FACTORS[0] * ref, min(RATE_FACTORS[1] * ref, v))

    def learn_rate(self, turned: float, seconds: float) -> None:
        """Un giro de `turned` rad (valor absoluto) con `seconds` de tecla: afina la velocidad de giro."""
        if seconds > 0:
            self.rate = self._ema(self.rate, turned / seconds, self.rate0)

    def _sample_side(self, ang: float, combo: str, sample: float) -> bool:
        """Con cam_h medido y una combinacion lateral: ve si la muestra encaja mejor con `side` al reves. True si ya van
        `VOTES` muestras seguidas que lo indican (en ese caso corrige `side` y reemplaza cam_h)."""
        flipped = _wrap(ang - _wrap(math.radians(COMBO_BASE_DEG[combo]) * -self.side))
        e_now, e_flip = abs(_wrap(sample - self.heading)), abs(_wrap(flipped - self.heading))
        if e_now > REJECT and e_flip <= FLIP_FIT:
            self._side_votes += 1
            if self._side_votes >= VOTES:
                self.side, self._side_votes, self.side_checked = -self.side, 0, True
                logger.warning("A/D caminan al reves de lo supuesto, corrijo")
                self.heading, self.measured, self._rejects = flipped, True, 0
                return True
        elif e_now <= e_flip:
            self._side_votes, self.side_checked = 0, True
        return False

    def _update_heading(self, sample: float) -> str:
        """Incorpora una muestra de cam_h: reemplaza si no era medido; si lo era, promedia (rechazando las que estan a mas
        de `REJECT`; dos rechazos seguidos reemplazan). Devuelve 'medido' | 'externo' (rechazada o reemplazo por rechazos)."""
        if self.heading is None or not self.measured:
            self.heading, self.measured, self._rejects = sample, True, 0
            return "medido"
        dev = _wrap(sample - self.heading)
        if abs(dev) > REJECT:
            self._rejects += 1
            if self._rejects < 2:
                logger.warning("Muestra de camara a %.0f grados de lo medido: la descarto", math.degrees(dev))
                return "externo"
            logger.warning("Camara girada por fuera: cam_h %.0f -> %.0f grados", math.degrees(self.heading),
                           math.degrees(sample))
            self.heading, self._rejects = sample, 0
            return "externo"
        self.heading, self._rejects = _wrap(self.heading + EMA * dev), 0
        return "medido"

    def _learn_turn(self, sample: float) -> str:
        """Compara cam_h medido antes del giro con la muestra de ahora: afina la velocidad de giro o, con `VOTES` giros
        seguidos al reves, invierte Q/E. Devuelve 'medido' | 'aprendido' | 'dudoso' | 'invertido'."""
        prev, ordered, secs = self.pending
        self.pending = None
        if prev is None or not MIN_ORDERED <= abs(ordered) <= MAX_LEARN_TURN:
            return "medido"
        real = _wrap(sample - prev)
        if abs(real) < MIN_REAL:
            return "medido"
        if real * ordered < 0:
            self._turn_votes += 1
            if self._turn_votes < VOTES:
                return "dudoso"
            logger.warning("Q/E giran al reves de lo supuesto, corrijo (ordene %.0f grados, giro %.0f)",
                           math.degrees(ordered), math.degrees(real))
            self.view_right_key, self._turn_votes = OTHER_KEY[self.view_right_key], 0
            return "invertido"
        self._turn_votes = 0
        if abs(ordered) >= MIN_RATE_TURN and secs > 0:
            self.learn_rate(abs(real), secs)
            return "aprendido"
        return "medido"

    def observe(self, delta: Vec, seconds: float, min_dist: float | None = None, combo: str = "W") -> str:
        """Incorpora un tramo con `combo` (desplazamiento en el juego, segundos sostenida). Devuelve 'corto' (todavia no
        alcanza para medir: se acumula), 'medido', 'invertido' (A/D o Q/E al reves), 'aprendido' (verifico un giro),
        'dudoso' (un giro medido al reves, falta confirmar) o 'externo' (la muestra no encaja con cam_h). `min_dist`:
        desplazamiento minimo para medir (por defecto `navigation.min_segment_game`)."""
        ax, ay, asec, acombo = self._acc
        if acombo != combo:
            ax, ay, asec = 0.0, 0.0, 0.0
        ax, ay, asec = ax + delta[0], ay + delta[1], asec + max(0.0, seconds)
        dist = math.hypot(ax, ay)
        if dist < (self.min_game if min_dist is None else min_dist):
            self._acc = (ax, ay, asec, combo)
            return "corto"
        self._acc = (0.0, 0.0, 0.0, "W")
        reliable = dist >= self.min_game   # con menos (cerca del borde) el redondeo de las coordenadas vuelve ruidoso el rumbo
        if asec > 0 and reliable:
            self.speed = self._ema(self.speed, dist / asec, self.speed0)
        ang = math.atan2(ay, ax)
        sample = _wrap(ang - self.offset(combo))
        if is_lateral(combo) and self.heading is not None and self.measured and self._sample_side(ang, combo, sample):
            self.pending = None
            return "invertido"
        out = "medido"
        if self.pending is not None and reliable:
            out = self._learn_turn(sample)
        res = self._update_heading(sample)
        return out if res == "medido" else res

    def describe(self, full: bool = False, bearing: float | None = None) -> str:
        """Texto para el log y el debug: cam_h (con su estado), rumbo buscado, sentido de A/D y velocidad; con `full`,
        tambien Q/E."""
        h = "camara ?" if self.heading is None else f"camara {math.degrees(self.heading) % 360:.0f} ({self.state})"
        if bearing is not None:
            h += f" | hacia {math.degrees(bearing) % 360:.0f}"
        out = f"{h} | A/D {'normal' if self.side > 0 else 'al reves'} | vel {self.speed:.1f} u/s"
        if full:
            out += f" | vista a la derecha = {self.view_right_key} | giro {math.degrees(self.rate):.0f} grados/s"
        return out
