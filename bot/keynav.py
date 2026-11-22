"""Navegacion SOLO con teclado (logica pura, sin input): hacia donde camina W (`cam_h`), combinaciones de teclas de
movimiento, velocidades y giro de la vista con Q/E.

W/A/S/D caminan relativo a la camara. `cam_h` (en `heading`) es el angulo, en coordenadas del juego (atan2(dy, dx)), hacia
donde camina W; sale del desplazamiento medido en un tramo menos el desvio de la combinacion usada. El juego no esta
espejado respecto de la pantalla (x a la derecha, y hacia abajo en ambos), asi que un aumento del angulo en el juego
equivale a girar hacia la derecha en pantalla: D = +90, A = -90 (`side` = +1). Si A/D resultan al reves (`side` = -1) se
se determina UNA vez. Q/E ya no se usan para caminar: solo para mirar; despues de un giro `cam_h` queda "estimado" (o se
re-lee del cono del minimapa con `sync_cone`). El angulo de pantalla de un vector v respecto del frente es
`screen_angle(v) = atan2(vx, -vy)`.

SENTIDOS FIJOS: el sentido de Q/E (`view_right_key`) y el de A/D (`side`) se determinan una sola vez, con
`navigation.keyboard.senses_votes` muestras coherentes, y despues quedan fijos: evidencia contraria = WARNING y se descarta
la muestra, nunca se invierte (`locked_q` / `locked_ad`). Se fijan de entrada con `view_right_key` / `ad_side` != "auto" en
la config o con `load_senses` (archivo de estado de la corrida anterior); `senses()` devuelve lo fijado para guardarlo.

A/D EN ESTE JUEGO: en las capturas reales (debug/run_20261121_205635) A/D GIRAN la vista (el cono sube ~36 grados por
segundo de tecla) en vez de desplazar el personaje de costado; por eso `navigation.keyboard.lateral` es false por defecto:
el bot camina solo con W y alinea la vista con Q/E (`plan_walk` devuelve siempre W).
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path

from bot import ground
from bot.fileutil import atomic_write_text
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
REJECT_VOTES = 3                    # muestras seguidas, coherentes entre si y lejos de cam_h, para declarar "camara girada por fuera"
REJECT_AGREE = math.radians(40.0)   # dos muestras rechazadas son "coherentes entre si" si difieren menos que esto
CONTRARY_MIN = math.radians(25.0)   # un giro medido al reves solo vota si se movio al menos esto (menos es ruido)
CONTRARY_FIT = math.radians(70.0)   # ...y si encaja con el giro ordenado al reves (a menos de esto)
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
        self.view_right_key: str = "camera_right" if k["view_right_key"] == "auto" else k["view_right_key"]
        self.locked_q = k["view_right_key"] != "auto"   # sentido de Q/E ya fijo (config o archivo de estado)
        self.lateral = bool(k["lateral"])               # caminar tambien con A/D (si no, solo W y la vista se alinea con Q/E)
        self.votes = int(k["senses_votes"])             # muestras coherentes para fijar un sentido
        self.heading: float | None = None      # cam_h: hacia donde camina W en el juego (None = no se conoce)
        self.measured = False                  # True si salio de un tramo medido (False = estimado con los giros)
        self.side = -1 if k["ad_side"] == "reverso" else 1   # +1: D camina a la derecha de W (en angulo del juego); -1: al reves
        self.locked_ad = k["ad_side"] != "auto"              # sentido de A/D ya fijo
        self.side_checked = self.locked_ad     # una muestra lateral ya confirmo (o determino) `side`
        self.senses_dirty = False              # se fijo un sentido nuevo y falta guardarlo (`senses()`)
        self.pending: tuple[float | None, float, float] | None = None   # (cam_h previo medido, giro ordenado, segundos)
        self._acc = (0.0, 0.0, 0.0, "W")       # desplazamiento, segundos y combinacion acumulados que todavia no alcanzan
        self._side_votes = 0                   # muestras laterales seguidas que indican `side` al reves
        self._side_ok = 0                      # muestras laterales coherentes con `side` (suman hasta fijarlo)
        self._turn_votes = 0                   # giros seguidos medidos en sentido contrario al ordenado
        self._turn_ok = 0                      # giros coherentes con el sentido supuesto de Q/E (suman hasta fijarlo)
        self._reject_buf: list[float] = []     # muestras seguidas descartadas (para decidir si la camara giro por fuera)
        self._warned: set[str] = set()         # avisos de evidencia contraria ya dados (uno por clase)

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
        best = min(COMBOS if self.lateral else ("W",), key=lambda c: abs(_wrap(self.heading + self.offset(c) - bearing)))
        return best, _wrap(self.heading + self.offset(best) - bearing)

    def needs_probe(self, combo: str) -> bool:
        """Conviene un tramo de prueba con W antes de `combo`: cam_h desconocido, o estimado (un giro sin cono que lo confirme)
        y la combinacion es lateral mientras A/D no se hayan verificado, o se camina solo con W (el giro estimado no es de fiar)."""
        if self.heading is None:
            return True
        if self.measured:
            return False
        return (is_lateral(combo) and not self.side_checked) or not self.lateral   # solo W: tras un giro se vuelve a medir

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
        self._acc, self._reject_buf = (0.0, 0.0, 0.0, "W"), []

    # --- sentidos fijos (Q/E y A/D) ---
    def senses(self) -> dict[str, str]:
        """Sentidos ya fijados (para guardarlos entre corridas): 'view_right_key' y/o 'ad_side' ('normal' | 'reverso')."""
        out: dict[str, str] = {}
        if self.locked_q:
            out["view_right_key"] = self.view_right_key
        if self.locked_ad:
            out["ad_side"] = "normal" if self.side > 0 else "reverso"
        return out

    def load_senses(self, saved: dict) -> None:
        """Aplica sentidos guardados de otra corrida. Lo que la config ya fijo (`locked_*`) manda sobre el archivo."""
        key, ad = saved.get("view_right_key"), saved.get("ad_side")
        if not self.locked_q and key in OTHER_KEY:
            self.view_right_key, self.locked_q = key, True
        if not self.locked_ad and ad in ("normal", "reverso"):
            self.side, self.locked_ad, self.side_checked = (1 if ad == "normal" else -1), True, True

    def _contrary(self, kind: str, msg: str) -> None:
        """Evidencia contraria a un sentido ya fijado: WARNING (una vez por clase) y la muestra se descarta."""
        if kind not in self._warned:
            self._warned.add(kind)
            logger.warning("%s (el sentido esta fijado: no lo invierto, descarto la muestra)", msg)

    # --- tramos de caminata ---
    def _ema(self, old: float, new: float, ref: float) -> float:
        v = (1 - EMA) * old + EMA * new
        return max(RATE_FACTORS[0] * ref, min(RATE_FACTORS[1] * ref, v))

    def learn_rate(self, turned: float, seconds: float) -> None:
        """Un giro de `turned` rad (valor absoluto) con `seconds` de tecla: afina la velocidad de giro."""
        if seconds > 0:
            self.rate = self._ema(self.rate, turned / seconds, self.rate0)

    def _fix_ad(self, side: int) -> None:
        self.side, self.locked_ad, self.side_checked, self.senses_dirty = side, True, True, True
        self._side_votes = self._side_ok = 0
        logger.info("Sentido de A/D determinado: %s (queda fijo)", "normal" if side > 0 else "al reves")

    def _sample_side(self, ang: float, combo: str, sample: float) -> bool:
        """Con cam_h medido y una combinacion lateral: ve si la muestra encaja mejor con `side` al reves. Sin fijar, `votes`
        muestras seguidas al reves lo invierten UNA vez (y lo fijan; True, reemplaza cam_h) y `votes` coherentes lo fijan tal
        como esta. Ya fijo: la evidencia contraria solo avisa (False)."""
        flipped = _wrap(ang - _wrap(math.radians(COMBO_BASE_DEG[combo]) * -self.side))
        e_now, e_flip = abs(_wrap(sample - self.heading)), abs(_wrap(flipped - self.heading))
        if e_now > REJECT and e_flip <= FLIP_FIT:
            if self.locked_ad:
                self._contrary("ad", "A/D: una muestra lateral encaja con el sentido contrario")
                return False
            self._side_votes += 1
            if self._side_votes >= self.votes:
                self._fix_ad(-self.side)
                self.heading, self.measured, self._reject_buf = flipped, True, []
                return True
        elif e_now <= e_flip:
            self._side_votes, self.side_checked = 0, True
            if not self.locked_ad:
                self._side_ok += 1
                if self._side_ok >= self.votes:
                    self._fix_ad(self.side)
        return False

    def _update_heading(self, sample: float) -> str:
        """Incorpora una muestra de cam_h: reemplaza si no era medido; si lo era, promedia. Una muestra a mas de `REJECT` se
        descarta; "camara girada por fuera" solo se declara con `REJECT_VOTES` descartadas seguidas y coherentes entre si
        (evidencia fuerte). Devuelve 'medido' | 'externo' (descartada, o reemplazo por rechazos)."""
        if self.heading is None or not self.measured:
            self.heading, self.measured, self._reject_buf = sample, True, []
            return "medido"
        dev = _wrap(sample - self.heading)
        if abs(dev) > REJECT:
            buf = self._reject_buf
            if buf and abs(_wrap(sample - buf[-1])) > REJECT_AGREE:
                buf.clear()   # no coinciden entre si: ruido, no un giro por fuera
            buf.append(sample)
            if len(buf) < REJECT_VOTES:
                logger.info("Muestra de camara a %.0f grados de lo medido: la descarto", math.degrees(dev))
                return "externo"
            new = math.atan2(sum(math.sin(a) for a in buf), sum(math.cos(a) for a in buf))
            logger.warning("Camara girada por fuera: cam_h %.0f -> %.0f grados (%d muestras coherentes)",
                           math.degrees(self.heading), math.degrees(new), len(buf))
            self.heading, self._reject_buf = new, []
            return "externo"
        self.heading, self._reject_buf = _wrap(self.heading + EMA * dev), []
        return "medido"

    def sync_cone(self, h: float) -> str:
        """El cono del minimapa leyo que la camara mira a `h` (rad, marco del juego): reemplaza cam_h (queda medido) y, si habia
        un giro pendiente, lo verifica con esta lectura (velocidad de giro y sentido de Q/E). Devuelve el resultado de
        `_learn_turn` ('medido' si no habia giro)."""
        out = "medido"
        if self.pending is not None:
            out = self._learn_turn(h)
        self.heading, self.measured, self._reject_buf = _wrap(h), True, []
        return out

    def _fix_q(self, key: str) -> None:
        self.view_right_key, self.locked_q, self.senses_dirty = key, True, True
        self._turn_votes = self._turn_ok = 0
        logger.info("Sentido de Q/E determinado: la vista gira a la derecha con %s (queda fijo)", key)

    def _learn_turn(self, sample: float) -> str:
        """Compara cam_h medido antes del giro con la muestra de ahora: afina la velocidad de giro y, sin fijar el sentido de
        Q/E, lo determina (`votes` giros coherentes lo fijan; `votes` seguidos al reves lo invierten UNA vez y lo fijan).
        Ya fijo, un giro al reves solo avisa. Devuelve 'medido' | 'aprendido' | 'dudoso' | 'invertido'."""
        prev, ordered, secs = self.pending
        self.pending = None
        if prev is None or not MIN_ORDERED <= abs(ordered) <= MAX_LEARN_TURN:
            return "medido"
        real = _wrap(sample - prev)
        if abs(real) < MIN_REAL:
            return "medido"
        if real * ordered < 0:
            if abs(real) < CONTRARY_MIN or abs(_wrap(real + ordered)) > CONTRARY_FIT:
                return "medido"   # al reves pero poco claro (ruido o giro ambiguo): no vota
            if self.locked_q:
                self._contrary("q", f"Q/E: un giro salio al reves (pedi {math.degrees(ordered):.0f} grados, giro "
                                    f"{math.degrees(real):.0f})")
                return "dudoso"
            self._turn_votes += 1
            if self._turn_votes < self.votes:
                return "dudoso"
            logger.warning("Q/E giran al reves de lo supuesto (pedi %.0f grados, giro %.0f)", math.degrees(ordered),
                           math.degrees(real))
            self._fix_q(OTHER_KEY[self.view_right_key])
            return "invertido"
        self._turn_votes = 0
        if not self.locked_q:
            self._turn_ok += 1
            if self._turn_ok >= self.votes:
                self._fix_q(self.view_right_key)
        if abs(ordered) >= MIN_RATE_TURN and secs > 0:
            self.learn_rate(abs(real), secs)
            return "aprendido"
        return "medido"

    def observe(self, delta: Vec, seconds: float, min_dist: float | None = None, combo: str = "W",
                update_heading: bool = True) -> str:
        """Incorpora un tramo con `combo` (desplazamiento en el juego, segundos sostenida). Devuelve 'corto' (todavia no
        alcanza para medir: se acumula), 'medido', 'invertido' (A/D o Q/E al reves), 'aprendido' (verifico un giro),
        'dudoso' (un giro medido al reves, falta confirmar) o 'externo' (la muestra no encaja con cam_h). `min_dist`:
        desplazamiento minimo para medir (por defecto `navigation.min_segment_game`). Con `update_heading` False cam_h no se toca."""
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
        if not update_heading:   # cam_h viene de otra fuente (el cono): el tramo solo aporta la velocidad y el sentido de A/D
            return "medido"
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
            out += (f" | vista a la derecha = {self.view_right_key} ({'fijo' if self.locked_q else 'sin fijar'}) | "
                    f"A/D {'fijo' if self.locked_ad else 'sin fijar'} | giro {math.degrees(self.rate):.0f} grados/s")
        return out


class SensesStore:
    """Archivo de estado con los sentidos de Q/E y A/D ya determinados (`logs/sentidos.json`), para reusarlos en la proxima
    corrida. `path=None`: no lee ni escribe nada (tests)."""

    def __init__(self, path: Path | None) -> None:
        self.path = path

    def load(self) -> dict:
        """Lo guardado ({} si no hay archivo o es ilegible)."""
        if self.path is None or not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning("Sentidos: no se pudo leer %s (%s): se determinan de nuevo", self.path, e)
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, senses: dict) -> None:
        """Guarda `senses` (solo si hay algo fijado)."""
        if self.path is None or not senses:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self.path, json.dumps(senses, indent=2))
        except OSError as e:
            logger.warning("Sentidos: no se pudo guardar %s: %s", self.path, e)
