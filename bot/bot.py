"""Maquina de estados del bot: BUSCAR -> ATACAR -> RECOGER -> EXPLORAR, con ACERCARSE (piedras lejanas sin etiqueta),
VOLVER, CENTRO, DESTRABAR (episodios de traba), CAPTCHA y MUERTO.

Cada ciclo (`Bot.step`): control -> ensure_ready -> captura -> percepcion -> tracker -> chequeos globales
(muerte, captcha) -> handler del estado (UNA accion acotada) -> ritmo minimo. Todo el input va por `GameInput`.
El bot NO resuelve el captcha: suelta todo, hace sonar la alarma y espera a que el panel desaparezca.
La vision trabaja en coordenadas de cliente; las coordenadas del juego son las del minimapa.
"""
from __future__ import annotations

import logging
import math
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from bot.capture import WindowMinimizedError, WindowNotFoundError
from bot.control import BotControl, PanicStop
from bot.input.game_input import GameInput
from bot.debug_draw import DebugRecorder
from bot.navigation import CameraModel, Navigator, _wrap
from bot.perception import Perception, perceive
from bot.priority import order_candidates
from bot.skills import Skill, SkillScheduler
from bot.tracking import CoordTracker, StuckEpisode
from bot.vision.captcha import find_captcha
from bot.vision.coords import read_coords
from bot.vision.far_metin import FarMetin
from bot.vision.metin import MetinDetection
from bot.zone import FarmZone, ZoneStatus

logger = logging.getLogger(__name__)

Xy = tuple[float, float]
Transition = tuple["State", str] | None   # None = seguir en el mismo estado


class State(Enum):
    BUSCAR = "BUSCAR"
    ATACAR = "ATACAR"
    RECOGER = "RECOGER"
    EXPLORAR = "EXPLORAR"
    ACERCARSE = "ACERCARSE"
    VOLVER = "VOLVER"
    CENTRO = "CENTRO"
    DESTRABAR = "DESTRABAR"
    CAPTCHA = "CAPTCHA"
    MUERTO = "MUERTO"
    FIN = "FIN"


# Partes de la percepcion que necesita cada estado (ventanas, mascara, captcha y muerte se calculan siempre).
PARTS = {
    State.BUSCAR: {"metins", "coords", "far", "monsters"}, State.ATACAR: {"panel", "coords", "metins", "monsters"},
    State.RECOGER: {"items", "coords"}, State.EXPLORAR: {"metins", "coords", "far", "monsters"},
    State.ACERCARSE: {"metins", "coords", "far", "monsters"},
    State.VOLVER: {"coords", "metins", "monsters"}, State.CENTRO: {"coords", "metins", "monsters"},
    State.DESTRABAR: {"coords", "metins", "far", "monsters"}, State.CAPTCHA: {"coords"},
    State.MUERTO: set(), State.FIN: set(),
}
WRONG_PANEL_KINDS = ("other", "other_metin")
BRAKE_DIRS = 4      # direcciones (cada 360/BRAKE_DIRS grados) que prueba el clic de freno
SCREEN_DIRS = {"up": (0.0, -1.0), "down": (0.0, 1.0)}   # direccion de pantalla de W y de S
HOLD_EPS = 1e-9
END_NO_VOLVER = "no puede volver a la zona"   # motivo de fin con alarma (VOLVER sin lograrlo)
END_NO_CENTRO = "no puede llegar al centro"   # motivo de fin con alarma (CENTRO sin lograrlo)
WALKING_STATES = (State.EXPLORAR, State.VOLVER, State.CENTRO, State.ACERCARSE)   # DESTRABAR vuelve a ellos


class _AvoidMask:
    """Mascara que ademas rechaza los puntos cercanos a metin que no son objetivo (duck typing de `ScreenMask`)."""

    def __init__(self, base: Any, points: list[tuple[int, int]], radius: float) -> None:
        self.base, self.points, self.radius = base, points, radius

    def allows(self, x: int, y: int) -> bool:
        return self.base.allows(x, y) and all(math.hypot(x - px, y - py) > self.radius for px, py in self.points)


@dataclass
class BotStats:
    """Resumen de una corrida."""
    metins: int = 0
    items: int = 0               # estimado: objetos validos que dejaron de verse mientras se pulsaba Z
    captchas: int = 0
    unstucks: int = 0            # intentos de destrabe
    episodes: int = 0            # episodios de traba abiertos
    episodes_resolved: int = 0   # cerrados con progreso real
    centros: int = 0             # veces que se fue al CENTRO
    approaches: int = 0          # acercamientos a piedras lejanas
    skills_cast: int = 0         # habilidades lanzadas
    elapsed_s: float = 0.0
    time_by_state: dict[str, float] = field(default_factory=dict)
    end_reason: str = ""

    def summary(self) -> str:
        per = ", ".join(f"{k} {v:.0f}s" for k, v in self.time_by_state.items() if v >= 0.5) or "-"
        skills = f" | habilidades lanzadas: {self.skills_cast}" if self.skills_cast > 0 else ""
        return (f"Metin rotos: {self.metins} | objetos recogidos (estimado): {self.items} | captchas: {self.captchas} | "
                f"destrabes: {self.unstucks} ({self.episodes} episodios, {self.episodes_resolved} resueltos, "
                f"{self.centros} al centro) | acercamientos: {self.approaches}{skills} | tiempo: {self.elapsed_s / 60:.1f} min ({per}) | "
                f"motivo de fin: {self.end_reason or '-'}")


@dataclass
class _Cand:
    det: MetinDetection
    gp: Xy | None      # posicion estimada en el juego (None sin camara lista)


@dataclass
class _Attack:
    start_t: float
    click: tuple[int, int]
    gp: Xy | None
    last_click_t: float
    progress_t: float                  # ultimo clic o ultimo cambio de posicion
    last_pos: tuple[int, int] | None
    confirmed: bool = False
    min_hp: float | None = None
    last_drop_t: float = 0.0
    reclicks: int = 0
    missing: int = 0                   # ciclos seguidos sin panel (ya confirmado)
    zero: int = 0                      # ciclos seguidos con vida en 0
    trail: list = field(default_factory=list)   # (t, posicion) de la ventana `attack.blocked_s`
    pending: tuple[str, bool] | None = None     # reclic para el proximo ciclo: (motivo, cuenta como reclic)
    start_pos: tuple[int, int] | None = None    # posicion del jugador al empezar el ataque


@dataclass
class _Walk:
    vec: Xy                            # vector de pantalla del clic (desde el personaje)
    before: tuple[int, int]
    start_t: float
    last_pos: tuple[int, int]
    last_move_t: float
    exp: Xy | None = None              # desplazamiento esperado en el juego (None en sondeos: el giro no es confiable)
    best_sd: float = 0.0               # mejor (mayor) distancia firmada a la zona vista en el tramo
    bad: int = 0                       # ciclos seguidos con el rumbo observado fuera de tolerancia
    probe: bool = False                # sondeo corto (camara sin calibrar): se deja terminar para poder medirlo


@dataclass
class _Pickup:
    start_t: float
    presses: int = 0
    since_click: int = 0
    empty: int = 0
    last_n: int | None = None
    last_action_t: float = -math.inf


@dataclass
class _Explore:
    dir: str
    phase: str = "sweep"
    steps: int = 0
    target: Xy | None = None
    walk_start: float = 0.0


@dataclass
class _Spot:
    """Posicion recordada (descartada, ignorada o con aproximaciones). `unc` > 0: se guardo sin camara con la posicion
    del JUGADOR (el metin esta a lo sumo a `unc` unidades de ella): aplica cerca de ese lugar (ver `Bot._covers`)."""
    xy: Xy
    expiry: float
    unc: float = 0.0
    vec: Xy | None = None   # vector de pantalla (desde el jugador) del candidato: permite ubicarlo cuando la camara se calibre
    rot: int = 0            # giros de camara en ese momento (el vector solo sirve con el mismo marco)
    n: int = 0              # aproximaciones (solo en `Bot._tries`)


@dataclass
class _Approach:
    """Caminata hacia una piedra lejana (sin etiqueta)."""
    start_t: float
    gp: Xy | None                      # posicion estimada en el juego (None sin camara lista)
    last_screen: tuple[int, int]       # ultima posicion de pantalla vista de la piedra
    lost: int = 0                      # ciclos seguidos sin verla
    spot: _Spot | None = None          # cuenta de aproximaciones a esta posicion


@dataclass
class _FarCand:
    far: FarMetin
    gp: Xy | None


@dataclass
class _Unstuck:
    """Un intento de destrabe dentro de un episodio: start -> (look ->) leg | settle -> vuelve a `back`."""
    back: State
    why: str = ""
    phase: str = "start"
    strategy: str = ""
    steps: int = 0
    cam_dir: str = "left"
    acted_t: float | None = None
    tried: Any = None                  # `TriedDir` de este intento (se completa con el angulo de juego al terminar)


class Bot:
    """Una instancia = una corrida. `run()` hasta FIN, o `step()` ciclo a ciclo (tests y simulador)."""

    def __init__(self, cfg: dict, window: Any, game_input: GameInput, control: BotControl, alarm: Any, *,
                 perceive_fn: Callable[..., Perception] = perceive, clock: Callable[[], float] = time.monotonic,
                 sleep_fn: Callable[[float], None] | None = None, rng: random.Random | None = None,
                 captcha_fn: Callable[[Any, dict], Any] = lambda img, cfg: find_captcha(img, cfg),
                 coords_fn: Callable[[Any, dict], Any] = read_coords) -> None:
        self.cfg, self.window, self.gi, self.control, self.alarm = cfg, window, game_input, control, alarm
        self._perceive, self.clock = perceive_fn, clock
        self._captcha_fn, self._coords_fn = captcha_fn, coords_fn   # lecturas rapidas entre tramos de una tecla sostenida
        self._sleep = sleep_fn or control.sleep
        self.rng = rng or random.Random()
        self.zone = FarmZone.from_config(cfg)
        self.tracker = CoordTracker(cfg)
        self.camera = CameraModel(cfg)
        self.navigator = Navigator(cfg, self.zone, self.camera, self.rng)
        self.loop, self.atk, self.pk = cfg["loop"], cfg["attack"], cfg["pickup"]
        self.ex, self.cw, self.stuck, self.nav = cfg["explore"], cfg["captcha_wait"], cfg["stuck"], cfg["navigation"]
        self.vlv, self.apr, self.ctr = cfg["volver"], cfg["approach"], cfg["center"]
        self._stuck_segments = max(1, math.ceil(self.stuck["window_s"] / self.nav["settle_s"]))
        self.state = State.BUSCAR
        self.stats = BotStats()
        self.debug = DebugRecorder(cfg)
        self.last_perception: Perception | None = None
        self.discarded: list[_Spot] = []   # metin descartados
        self.ignored: list[_Spot] = []     # piedras lejanas que resultaron de otro tipo, se perdieron o no se pudieron alcanzar
        self._tries: list[_Spot] = []      # aproximaciones a piedras lejanas que todavia no llegaron a atacar
        self.pending_return = False
        self._cycle = 0
        self._t_start = self._last_t = self.clock()
        self._img: Any = None
        self._atk: _Attack | None = None
        self._apr: _Approach | None = None
        self._walk: _Walk | None = None
        self._pk: _Pickup | None = None
        self._ex: _Explore | None = None
        self._us: _Unstuck | None = None
        self._ep: StuckEpisode | None = None      # episodio de traba vigente (persiste entre estados)
        self._ep_n = 0
        self._ep_starts: list[float] = []
        self._mem: tuple[float, list, str | None] = (-math.inf, [], None)   # (t, direcciones, estrategia) del ultimo intento
        self._legs: list[float] = []              # rumbos (juego) de los ultimos tramos de exploracion
        self._visited: list[Xy] = []              # puntos de partida y llegada de los ultimos tramos
        self._no_target_t = self.clock()          # ultima vez que hubo un objetivo (o se estuvo atacando)
        self._diag_seen: dict[tuple, float] = {}   # motivo de "no elegido" -> ultima vez que se vio
        self._planned: tuple[int, int] | None = None   # ultimo clic planeado (solo para el debug)
        self._dest: Xy | None = None
        self._vl_start = 0.0               # inicio del tramo actual de VOLVER/CENTRO (tope por tramo)
        self._vl_since: float | None = None   # entrada al VOLVER/CENTRO vigente (None fuera de ellos)
        self._vl_acc = 0.0                 # tiempo en VOLVER/CENTRO de tramos anteriores de la misma vuelta
        self._vl_mark: float | None = None    # medida de progreso al empezar el tramo
        self._wrong: list[list] = []       # [posicion, veces] de selecciones equivocadas por metin
        self._zero_streak = 0
        self._last_click_vec: Xy | None = None
        self._clear = 0
        self._dismounted = False   # False = montado (o sin caballo); lo comparten recoger y las habilidades
        self.skill_cfg = cfg["skill_timing"]
        self.skills = SkillScheduler(cfg["character"]["skills"], self.clock, float(self.skill_cfg["recast_margin_s"]))
        self._skills_started = False
        self._new_sweep()

    # ------------------------------------------------------------------ ciclo
    @property
    def finished(self) -> bool:
        return self.state is State.FIN

    def run(self, max_minutes: float | None = None) -> BotStats:
        """Corre hasta FIN. PanicStop se propaga. Sin tope de tiempo mientras espera el captcha."""
        self._t_start = self._last_t = self.clock()
        self._start_skills(self._t_start)
        limit = None if max_minutes is None else float(max_minutes) * 60
        try:
            while self.state is not State.FIN:
                if limit is not None and self.state is not State.CAPTCHA and self.clock() - self._t_start >= limit:
                    self._remount_quiet()
                    self._finish("tiempo")
                    break
                self.step()
        except WindowNotFoundError as e:
            logger.error("El juego se cerro o no se encuentra la ventana: %s", e)
            self._finish("juego cerrado")
        except PanicStop:
            self.stats.end_reason = "panico"
            raise
        except Exception:
            self._remount_quiet()
            raise
        finally:
            self._account(self.clock())
        return self.stats

    def step(self) -> None:
        """Un ciclo completo."""
        self.control.check()
        t0 = self.clock()
        self._account(t0)
        cycle, self._cycle = self._cycle, self._cycle + 1
        p = self._observe(cycle)
        if p is not None:
            t = self.clock()
            self.tracker.update(p.coords, t)
            self.last_perception = p
            self._decide(p, t)
        wait = self.cw["poll_s"] if self.state is State.CAPTCHA else self.loop["cycle_ms"] / 1000
        rest = wait - (self.clock() - t0)
        if rest > 0 and self.state is not State.FIN:
            self._sleep(rest)

    def _observe(self, cycle: int) -> Perception | None:
        """Captura y percibe. En CAPTCHA no se roba el foco: solo se lee si el juego ya esta al frente."""
        full = cycle % int(self.loop["captcha_full_search_every"]) == 0
        if self.state is State.CAPTCHA:
            full = True   # la busqueda completa incluye la zona fija
            try:
                if not self.window.is_foreground():
                    return None
                img = self.window.capture()
            except WindowMinimizedError:
                return None
        else:
            if not self.window.ensure_ready(should_stop=lambda: self.control.is_stopped):
                self.control.check()
                return None
            try:
                img = self.window.capture()
            except WindowMinimizedError:
                logger.debug("La ventana se minimizo antes de capturar; se reintenta en el proximo ciclo")
                return None
        self._img = img
        return self._perceive(img, self.cfg, full_captcha=full, parts=PARTS[self.state])

    def _decide(self, p: Perception, t: float) -> None:
        if p.death is not None and self.state not in (State.MUERTO, State.FIN):
            self._enter(State.MUERTO, "pantalla de muerte")
            self._finish("muerte")
            return
        if p.captcha is not None and self.state is not State.CAPTCHA:
            self._enter(State.CAPTCHA, "panel Bot Verify detectado")
            return
        if self._vl_acc and self.state not in (State.VOLVER, State.CENTRO, State.DESTRABAR):
            pos = self.tracker.position
            if pos is not None and self._inside_with_margin(pos):
                self._reset_return()   # volvio a quedar adentro: la proxima vuelta empieza de cero
        if self.state is not State.DESTRABAR:
            self._update_episode(t)
        if self.state in (State.ATACAR, State.RECOGER, State.ACERCARSE):
            self._no_target_t = t      # atacando o yendo a una piedra: hay objetivo
        if self._maybe_cast_skill(t):
            return
        before = self.state
        out = getattr(self, "_do_" + self.state.name.lower())(p, t)
        if out is not None and self.state is before:   # si el handler ya cambio de estado (captcha en una tecla) no se pisa
            self._enter(*out)

    def _account(self, t: float) -> None:
        d = self.stats.time_by_state
        d[self.state.name] = d.get(self.state.name, 0.0) + max(0.0, t - self._last_t)
        self._last_t = t
        self.stats.elapsed_s = t - self._t_start

    def _finish(self, reason: str) -> None:
        self.stats.end_reason = reason
        self._enter(State.FIN, reason)

    # ------------------------------------------------------------------ transiciones
    def _enter(self, new: State, reason: str) -> None:
        old = self.state
        if new is old:
            return
        logger.info("%s -> %s: %s", old.name, new.name, reason)
        self.state = new
        if old in (State.VOLVER, State.CENTRO) and self._vl_since is not None:
            self._vl_acc += self.clock() - self._vl_since
            self._vl_since = None
        if old is State.RECOGER and new not in (State.CAPTCHA, State.MUERTO, State.FIN):
            self._remount()   # con captcha o muerte no se manda nada: se vuelve a montar despues
        self._walk = None
        if new not in (State.ACERCARSE, State.DESTRABAR):
            self._apr = None
        if new is State.BUSCAR:
            self._atk = None
        elif new is State.RECOGER:
            self._close_episode("metin roto")
            self._start_pickup()
        elif new is State.EXPLORAR:
            self._new_sweep()
        elif new is State.ACERCARSE:
            if old is not State.DESTRABAR:   # volver del destrabe no es un acercamiento nuevo
                self.stats.approaches += 1
        elif new in (State.VOLVER, State.CENTRO):
            if new is State.CENTRO and old is not State.DESTRABAR:
                self._reset_return()   # presupuesto de tiempo nuevo para el centro
                self.stats.centros += 1
                logger.info("Voy al centro (%s)", reason)
            self._vl_start = self._vl_since = self.clock()   # el tiempo total de la vuelta acumula entre reentradas
            self._vl_mark = self._progress_metric(new)
        elif new is State.DESTRABAR:
            self._start_unstuck(old, reason)
        elif new is State.CAPTCHA:
            self._atk = None
            self.gi.release_all()
            self.alarm.start("captcha")
            self.stats.captchas += 1
            self._clear = 0
        elif new is State.MUERTO:
            self._atk = None
            self.gi.release_all()
            self.alarm.start("muerte")
        self._dbg("estado", f"{old.name} -> {new.name}: {reason}")

    # ------------------------------------------------------------------ debug
    def _dbg(self, kind: str, reason: str) -> None:
        """Imagen anotada del ciclo actual (si `debug.enabled`). Nunca rompe el loop."""
        if not self.debug.enabled or self.last_perception is None:
            return
        try:
            pos = self.tracker.position
            zone = "zona=?"
            if pos is not None:
                zone = f"zona={self.zone.status(pos).name} d={self.zone.signed_distance(pos):+.1f}"
            cam = self.camera
            lo, hi = cam.scale_range
            camline = (f"camara phi={math.degrees(cam.phi):.0f} escala={lo:.2f}-{hi:.2f} lista={cam.ready} "
                       f"giros={cam.rotations}")
            dest = None if self._dest is None else (round(self._dest[0]), round(self._dest[1]))
            p = self.last_perception
            lines = [f"[{kind}] pos={pos} {zone}", camline, f"destino={dest} clic={self._planned}",
                     f"etiquetas={len(p.metins)} lejanas={len(p.far_metins)} monstruos={len(p.monsters)} "
                     f"(cerca {p.monsters_near})"]
            self.debug.record(self._img, p, self.state.name, reason, lines, self._planned,
                              f"{kind} pos={pos} {zone} | {camline} | destino={dest} clic={self._planned}", kind=kind)
        except Exception:  # noqa: BLE001
            logger.exception("Debug: fallo al armar la imagen")

    # ------------------------------------------------------------------ zona y candidatos
    def _zone_status(self) -> ZoneStatus | None:
        pos = self.tracker.position
        return None if pos is None else self.zone.status(pos)

    def _inside_with_margin(self, pos: Xy) -> bool:
        return self.zone.signed_distance(pos) >= float(self.nav["explore_margin"])

    def _game_pos_of(self, click: tuple[int, int], p: Perception) -> Xy | None:
        """Posicion estimada en el juego de un punto de pantalla (None si la camara no esta lista)."""
        pos = self.tracker.position
        if pos is None or not self.camera.ready:
            return None
        v = self.camera.screen_to_game((click[0] - p.player_xy[0], click[1] - p.player_xy[1]))
        return pos[0] + v[0], pos[1] + v[1]

    def _walk_mask(self, p: Perception) -> Any:
        """Mascara para clics de caminata: evita clickear (y atacar) metin que no son objetivo o estan descartados."""
        r = float(self.nav["avoid_metin_px"])
        t = self.clock()
        pts = [d.click_point for d in p.metins
               if not d.is_target or self._is_discarded(self._game_pos_of(d.click_point, p), t, d.click_point, p.player_xy)]
        return _AvoidMask(p.mask, pts, r) if pts and r > 0 else p.mask

    # ------------------------------------------------------------------ posiciones recordadas (descartes, ignoradas)
    def _spot_radius(self, gp: Xy, base: float) -> float:
        pos = self.tracker.position
        # La posicion de un metin sale de la camara y el error crece con la distancia (al guardarla y ahora)
        return base + float(self.atk["discard_error_frac"]) * (math.dist(gp, pos) if pos else 0.0)

    def _discard_radius(self, gp: Xy) -> float:
        return self._spot_radius(gp, float(self.atk["discard_radius"]))

    def _new_spot(self, gp: Xy | None, screen: tuple[int, int] | None, t: float, minutes: float,
                  at: Xy | None = None, locate: bool = True) -> _Spot | None:
        """Posicion a recordar: la estimada en el juego o, sin camara, la del JUGADOR (`at` o ahora) con la incertidumbre
        `approach.blind_player_radius`. `locate`: `screen` se vio desde `at` (o ahora) y la camara, ya calibrada y sin
        giros, puede ubicarla despues."""
        expiry = t + minutes * 60
        if gp is not None:
            return _Spot(gp, expiry)
        pos = at if at is not None else self.tracker.position
        if pos is None:
            return None
        pxy = self.last_perception.player_xy if self.last_perception is not None else None
        vec = None if screen is None or pxy is None or not locate else (float(screen[0] - pxy[0]), float(screen[1] - pxy[1]))
        return _Spot(pos, expiry, float(self.apr["blind_player_radius"]), vec, self.camera.rotations)

    def _upgrade_spot(self, s: _Spot) -> None:
        """Una posicion guardada sin camara se ubica en el juego apenas la camara este lista (mismo marco de giro)."""
        if s.unc > 0 and s.vec is not None and self.camera.ready and s.rot == self.camera.rotations:
            v = self.camera.screen_to_game(s.vec)
            s.xy, s.unc, s.vec = (s.xy[0] + v[0], s.xy[1] + v[1]), 0.0, None

    def _covers(self, s: _Spot, gp: Xy | None, screen: tuple[int, int] | None, player_xy: tuple[int, int] | None,
                base: float) -> bool:
        """La posicion recordada `s` cubre a un candidato en `gp` (None sin camara) visto en `screen`.
        - Con `gp`: por distancia (el error de la camara crece con la distancia), mas la incertidumbre de `s`.
        - Sin camara: el candidato esta a lo sumo a `escala maxima x px en pantalla` unidades del jugador y `s` a lo sumo
          a `unc` de su centro: cubre si esas dos regiones se tocan, es decir si el jugador esta cerca de `s` y el
          candidato cerca de el en pantalla (uno lejano en pantalla solo se toca con una `s` cercana)."""
        pos = self.tracker.position
        if gp is not None:
            return math.dist(gp, s.xy) <= s.unc + self._spot_radius(gp, base)
        if pos is None or screen is None or player_xy is None:
            return False
        return math.dist(pos, s.xy) <= self.camera.scale_range[1] * math.dist(screen, player_xy) + s.unc + base

    def _expiry(self, spots: list[_Spot], gp: Xy | None, t: float, screen: tuple[int, int] | None,
                player_xy: tuple[int, int] | None, base: float) -> float | None:
        spots[:] = [s for s in spots if s.expiry > t]
        for s in spots:
            self._upgrade_spot(s)
        return max((s.expiry for s in spots if self._covers(s, gp, screen, player_xy, base)), default=None)

    def _is_discarded(self, gp: Xy | None, t: float, screen: tuple[int, int] | None = None,
                      player_xy: tuple[int, int] | None = None) -> bool:
        return self._discard_expiry(gp, t, screen, player_xy) is not None

    def _discard_expiry(self, gp: Xy | None, t: float, screen: tuple[int, int] | None = None,
                        player_xy: tuple[int, int] | None = None) -> float | None:
        """Vencimiento del descarte que cubre al candidato (None si no esta descartado)."""
        return self._expiry(self.discarded, gp, t, screen, player_xy, float(self.atk["discard_radius"]))

    def _is_ignored(self, gp: Xy | None, t: float, screen: tuple[int, int] | None = None,
                    player_xy: tuple[int, int] | None = None) -> bool:
        """Piedra lejana en una posicion que ya resulto ser de otro tipo (o que se perdio / no se pudo alcanzar)."""
        return self._expiry(self.ignored, gp, t, screen, player_xy, float(self.apr["ignore_radius"])) is not None

    def _ignore_position(self, gp: Xy | None, t: float, why: str, screen: tuple[int, int] | None = None) -> None:
        s = self._new_spot(gp, screen, t, float(self.apr["ignore_minutes"]))
        if s is not None:
            self.ignored.append(s)
        logger.info("Posicion ignorada por %.0f min (%s): %s", self.apr["ignore_minutes"], why,
                    "sin camara, desde la posicion del jugador" if gp is None else f"est. ({gp[0]:.0f}, {gp[1]:.0f})")
        self._dbg("ignorada", why)

    def _register_approach(self, c: _FarCand, p: Perception, t: float) -> _Spot | None:
        """Cuenta una aproximacion a la posicion de `c`. Con mas de `approach.max_tries_per_spot` sin llegar a atacar la
        posicion se ignora y devuelve None."""
        base = float(self.apr["ignore_radius"])
        self._tries[:] = [s for s in self._tries if s.expiry > t]
        s = next((s for s in self._tries if self._covers(s, c.gp, c.far.center, p.player_xy, base)), None)
        if s is None:
            s = self._new_spot(c.gp, c.far.center, t, float(self.apr["ignore_minutes"]))
            if s is None:
                return _Spot((0.0, 0.0), t)   # sin coordenadas: no se puede contar
            self._tries.append(s)
        s.n += 1
        if s.n > int(self.apr["max_tries_per_spot"]):
            self._tries.remove(s)
            self._ignore_position(c.gp, t, f"{s.n - 1} aproximaciones sin llegar a atacar", c.far.center)
            return None
        return s

    def _clear_tries(self, a: _Approach) -> None:
        """Llego a atacar: las aproximaciones a esa posicion dejan de contar."""
        if a.spot is not None and a.spot in self._tries:
            self._tries.remove(a.spot)

    def _until(self, expiry: float | None, t: float) -> str:
        """Hora de pared (hh:mm) en que vence un descarte."""
        if expiry is None:
            return "?"
        return time.strftime("%H:%M", time.localtime(time.time() + (expiry - t)))

    def _zone_reason(self, gp: Xy | None, st: ZoneStatus | None, what: str, screen: tuple[int, int] | None = None,
                     player_xy: tuple[int, int] | None = None) -> str | None:
        """Motivo por el que algo en `gp` queda fuera de la zona (None si esta aceptado). Sin camara (`gp` None) solo
        vale si el jugador esta adentro con margen y, con `screen`, el candidato esta cerca de el en pantalla."""
        if gp is not None:
            if self.zone.accepts(gp):
                return None
            return f"{what} fuera de zona (est. ({gp[0]:.0f}, {gp[1]:.0f}), d={self.zone.signed_distance(gp):.0f})"
        if st is not ZoneStatus.INSIDE:
            return f"{what}: sin camara y jugador fuera"
        pos = self.tracker.position
        if pos is None or not self._inside_with_margin(pos):
            return f"{what}: sin camara y jugador cerca del borde"
        if screen is not None and player_xy is not None and math.dist(screen, player_xy) > float(self.apr["blind_screen_px"]):
            return f"{what}: sin camara y lejos en pantalla ({math.dist(screen, player_xy):.0f} px)"
        return None

    def _label_view(self, p: Perception, t: float, check_discards: bool = True
                    ) -> tuple[list[_Cand], list[tuple[tuple, str]]]:
        """(candidatos, motivos de los no elegidos): metin con etiqueta objetivo, permitidos, no descartados, en la zona."""
        st = self._zone_status()
        out: list[_Cand] = []
        why: list[tuple[tuple, str]] = []
        for d in p.metins:
            if not d.is_target:
                why.append((("tipo", d.name), f"tipo ignorado ({d.name})"))
                continue
            if not d.allowed:
                why.append((("ui", d.name), "clic no permitido (interfaz)"))
                continue
            gp = self._game_pos_of(d.click_point, p)
            if check_discards and (exp := self._discard_expiry(gp, t, d.click_point, p.player_xy)) is not None:
                why.append((("descartado", d.name), f"descartado hasta {self._until(exp, t)}"))
                continue
            zr = self._zone_reason(gp, st, d.name, d.click_point, p.player_xy)
            if zr is not None:
                why.append((("zona", d.name), zr))
                continue
            out.append(_Cand(d, gp))
        prio = self.cfg["zone"]["metin_priority"]
        if prio and len(out) > 1:
            near = sorted(out, key=lambda c: math.dist(c.det.click_point, p.player_xy))
            out = order_candidates(out, p.player_xy, prio, lambda c: c.det.name, lambda c: c.det.click_point)
            if out[0] is not near[0] and out[0].det.name != near[0].det.name:
                self._log_priority(out[0].det.name, near[0].det.name)
        else:
            out.sort(key=lambda c: math.dist(c.det.click_point, p.player_xy))
        return out, why

    def _log_priority(self, chosen: str | None, nearer: str | None) -> None:
        """Una vez por decision: la prioridad cambio la eleccion respecto del mas cercano."""
        key = ("prioridad", chosen, nearer)
        t = self.clock()
        mem = float(self.cfg["debug"]["reason_memory_s"])
        if key not in self._diag_seen or t - self._diag_seen[key] > mem:
            logger.info("Elegido %s por prioridad (mas cerca habia %s)", chosen, nearer)
        self._diag_seen[key] = t

    def _candidates(self, p: Perception, t: float, check_discards: bool = True) -> list[_Cand]:
        """Metin objetivo permitidos, no descartados y dentro de la zona; el mas cercano primero."""
        return self._label_view(p, t, check_discards)[0]

    def _monsters_around(self, f: FarMetin, p: Perception) -> int:
        """Etiquetas de monstruos a menos de `approach.far_monsters_px` de la piedra."""
        r = float(self.apr["far_monsters_px"])
        return sum(1 for m in p.monsters if math.dist(m.center, f.center) <= r)

    def _far_view(self, p: Perception, t: float) -> tuple[list[_FarCand], list[tuple[tuple, str]]]:
        """(piedras lejanas elegibles, motivos de las no elegidas); la mas cercana primero."""
        st = self._zone_status()
        out: list[_FarCand] = []
        why: list[tuple[tuple, str]] = []
        for f in p.far_metins:
            if not f.allowed:
                why.append((("ui", "far"), "piedra lejana: clic no permitido (interfaz)"))
                continue
            if not f.smoke and self._monsters_around(f, p) >= int(self.apr["far_monsters_n"]):
                why.append((("monstruos", "far"), "piedra lejana sin humo rodeada de monstruos (falso positivo)"))
                continue
            gp = self._game_pos_of(f.center, p)
            if self._is_ignored(gp, t, f.center, p.player_xy):
                why.append((("ignorada", "far"), "piedra lejana: posicion ignorada"))
                continue
            if self._is_discarded(gp, t, f.center, p.player_xy):
                why.append((("descartada", "far"), "piedra lejana: posicion de un metin descartado"))
                continue
            zr = self._zone_reason(gp, st, "piedra lejana")
            if zr is not None:
                why.append((("zona", "far"), zr))
                continue
            out.append(_FarCand(f, gp))
        out.sort(key=lambda c: math.dist(c.far.center, p.player_xy))
        return out, why

    def _log_reasons(self, reasons: list[tuple[tuple, str]]) -> None:
        """Loguea por que no se eligio cada metin o piedra detectados. Un motivo cuenta como nuevo si no se vio en los
        ultimos `debug.reason_memory_s` (un parpadeo de la deteccion no lo repite): sin spam de log ni de imagenes."""
        t, mem = self.clock(), float(self.cfg["debug"]["reason_memory_s"])
        new = [msg for k, msg in reasons if k not in self._diag_seen or t - self._diag_seen[k] > mem]
        for k, _ in reasons:
            self._diag_seen[k] = t
        for msg in new:
            logger.info("No elegido: %s", msg)
        if new:
            self._dbg("descarte", new[0])

    def _add_discard(self, a: _Attack, t: float) -> None:
        """Descarta la posicion del metin del ataque. Con la estimada en el juego, esa. Sin camara: si ya estaba
        confirmado (atacandolo) el personaje esta junto a el y su posicion de ahora ES la del metin (error ~ alcance); si
        no, la del jugador al empezar (desde donde se vio, se ubica sola cuando la camara se calibre) y la de ahora."""
        minutes = float(self.atk["discard_minutes"])
        pos = self.tracker.position
        if a.gp is None and a.confirmed and pos is not None:
            spots = [_Spot(pos, t + minutes * 60)]
        else:
            spots = [self._new_spot(a.gp, a.click, t, minutes, at=a.start_pos)]
            if a.gp is None:
                spots.append(self._new_spot(None, None, t, minutes, locate=False))
        self.discarded.extend(s for s in spots if s is not None)

    def _discard(self, a: _Attack, reason: str, p: Perception, t: float) -> Transition:
        self._add_discard(a, t)
        self.gi.press("deselect")
        self._atk = None
        self._dbg("descarte", f"metin descartado ({reason})")
        return State.BUSCAR, f"metin descartado ({reason})"

    def _wrong_entry(self, a: _Attack) -> tuple[Xy | None, list | None]:
        """(clave, entrada) de las selecciones equivocadas del metin del ataque (misma identidad que el descarte)."""
        key = a.gp if a.gp is not None else self.tracker.position
        if key is None:
            return None, None
        r = self._discard_radius(key)
        return key, next((e for e in self._wrong if math.dist(e[0], key) <= r), None)

    def _count_wrong(self, a: _Attack) -> int:
        """Suma una seleccion equivocada de este metin y devuelve el total (se reinicia al descartarlo)."""
        key, e = self._wrong_entry(a)
        if key is None:
            return 1
        if e is None:
            e = [key, 0]
            self._wrong.append(e)
        e[1] += 1
        n = e[1]
        if n >= int(self.atk["max_wrong_selections"]):
            self._wrong.remove(e)
        return n

    def _forget_wrong(self, a: _Attack) -> None:
        _, e = self._wrong_entry(a)
        if e is not None:
            self._wrong.remove(e)

    # ------------------------------------------------------------------ BUSCAR
    def _pick_goal(self, p: Perception, t: float, attack_why: str) -> Transition:
        """Metin con etiqueta -> ATACAR; si no, piedra lejana permitida -> ACERCARSE; si no, None. Loguea los descartes."""
        cands, why = self._label_view(p, t)
        if self._try_attack(cands, p, t):
            self._no_target_t = t
            self._log_reasons(why)
            return State.ATACAR, attack_why
        far, why_far = self._far_view(p, t)
        self._log_reasons(why + why_far)
        for c in far:
            spot = self._register_approach(c, p, t)
            if spot is None:   # demasiadas aproximaciones a esa posicion: ya quedo ignorada
                continue
            self._no_target_t = t
            self._apr = _Approach(t, c.gp, c.far.center, spot=spot)
            est = "" if c.gp is None else f", est. ({c.gp[0]:.0f}, {c.gp[1]:.0f})"
            return State.ACERCARSE, f"piedra lejana en {c.far.center}{est}"
        return None

    def _do_buscar(self, p: Perception, t: float) -> Transition:
        if self._zone_status() is ZoneStatus.OUTSIDE:
            return State.VOLVER, "fuera de la zona"
        if self.pending_return:
            pos = self.tracker.position
            if pos is not None and self._inside_with_margin(pos):
                self.pending_return = False
            else:
                return State.VOLVER, "vuelta pendiente"
        out = self._pick_goal(p, t, "metin elegido")
        if out is not None:
            return out
        return State.EXPLORAR, "no hay metin visible"

    def _try_attack(self, cands: list[_Cand], p: Perception, t: float) -> bool:
        """Clic en el primer candidato cuyo clic se pueda mandar."""
        for c in cands:
            if self.gi.click_at(*c.det.click_point, p.mask):
                self.tracker.mark_moving(t)
                self._planned = c.det.click_point
                self._last_click_vec = (c.det.click_point[0] - p.player_xy[0], c.det.click_point[1] - p.player_xy[1])
                pos = self.tracker.position
                self._atk = _Attack(t, c.det.click_point, c.gp, t, t, pos, last_drop_t=t,
                                    trail=[] if pos is None else [(t, pos)], start_pos=pos)
                return True
        return False

    # ------------------------------------------------------------------ ACERCARSE
    def _near_label(self, p: Perception, a: _Approach) -> MetinDetection | None:
        """Etiqueta de metin (de cualquier tipo) que quedo donde estaba la piedra."""
        lim = float(self.apr["label_match_px"])
        near = [d for d in p.metins if math.dist(d.click_point, a.last_screen) <= lim]
        return min(near, key=lambda d: math.dist(d.click_point, a.last_screen)) if near else None

    def _do_acercarse(self, p: Perception, t: float) -> Transition:
        a = self._apr
        if a is None:
            return State.BUSCAR, "sin acercamiento en curso"
        if self._zone_status() is ZoneStatus.OUTSIDE:
            return State.VOLVER, "fuera de la zona acercandose a la piedra"
        if self._try_attack(self._candidates(p, t), p, t):
            self._clear_tries(a)
            return State.ATACAR, "aparecio la etiqueta de un metin objetivo"
        d = self._near_label(p, a)
        if d is not None:
            gp = self._game_pos_of(d.click_point, p) or a.gp
            if (gp is None and d.is_target and d.allowed
                    and not self._is_discarded(None, t, d.click_point, p.player_xy)):
                # sin camara el metin todavia queda lejos en pantalla para atacarlo: se sigue caminando hasta que se acerque
                a.last_screen, a.lost = d.click_point, 0
            else:   # la etiqueta de esta piedra no es elegible: tipo ignorado, descartada, fuera de zona...
                self._ignore_position(gp, t, f"la piedra es de otro tipo ({d.name})" if not d.is_target else
                                      f"la piedra no es elegible ({d.name})", d.click_point)
                return State.BUSCAR, f"piedra de otro tipo ({d.name}): posicion ignorada"
        else:
            lim = float(self.apr["match_px"])
            near = [f for f in p.far_metins if math.dist(f.center, a.last_screen) <= lim]
            if near:
                f = min(near, key=lambda f: math.dist(f.center, a.last_screen))
                a.last_screen, a.lost = f.center, 0
                a.gp = self._game_pos_of(f.center, p) or a.gp
            else:
                a.lost += 1
                if a.lost >= int(self.apr["lost_cycles"]):
                    self._ignore_position(a.gp, t, "se perdio la piedra de vista", a.last_screen)
                    return State.BUSCAR, "se perdio la piedra de vista"
        if a.gp is not None and not self.zone.accepts(a.gp):   # la camara se calibro en el camino: la piedra queda afuera
            self._ignore_position(a.gp, t, "piedra lejana fuera de zona", a.last_screen)
            logger.info("Piedra lejana fuera de zona (est. (%.0f, %.0f), distancia %.0f): se abandona", a.gp[0], a.gp[1],
                        self.zone.signed_distance(a.gp))
            return State.BUSCAR, "piedra lejana fuera de la zona"
        if t - a.start_t > float(self.apr["max_seconds"]):
            self._ignore_position(a.gp, t, "tiempo maximo acercandose", a.last_screen)
            return State.BUSCAR, "tiempo maximo acercandose a la piedra"
        vec = (a.last_screen[0] - p.player_xy[0], a.last_screen[1] - p.player_xy[1])
        res = self._walk_toward(p, t, a.gp or (0.0, 0.0), screen_vec=vec)
        if res == "stuck":
            return self._stuck_transition(State.ACERCARSE, t, "trabado acercandose a la piedra", goal=a.gp)
        if res in ("blocked", "nocoords"):
            self._ignore_position(a.gp, t, "sin punto de clic valido hacia la piedra", a.last_screen)
            return State.BUSCAR, "no se puede caminar hacia la piedra"
        return None

    # ------------------------------------------------------------------ ATACAR
    def _do_atacar(self, p: Perception, t: float) -> Transition:
        a = self._atk
        if a is None:
            return State.BUSCAR, "sin ataque en curso"
        pos = self.tracker.position
        if pos != a.last_pos:
            a.progress_t, a.last_pos = t, pos
        if pos is not None:
            win = float(self.atk["blocked_s"])
            a.trail = [e for e in a.trail if t - e[0] <= win] + [(t, pos)]
        if a.pending is not None:   # ESPACIO ya hecho en el ciclo anterior: ahora el reclic, con una captura fresca
            why, count = a.pending
            a.pending = None
            return self._reclick(a, why, p, t, count)
        panel = p.panel
        kind = panel.kind if panel is not None and panel.present else None
        if kind in WRONG_PANEL_KINDS:
            what = panel.name or kind
            if self._count_wrong(a) >= int(self.atk["max_wrong_selections"]):
                return self._discard(a, f"seleccion equivocada repetida ({what})", p, t)
            if kind == "other" and p.monsters_near >= int(self.atk["wrong_monsters_min"]):
                logger.info("Seleccion equivocada con %d monstruos cerca: ESPACIO, Escape y reclic en el metin",
                            p.monsters_near)
                self._dbg("equivocada", "monstruo seleccionado: ESPACIO + Escape")
                if not self._hold_space():   # captcha: ya esta en CAPTCHA, sin mas teclas
                    return None
                self.tracker.forget_reference()
                self.gi.press("deselect")
                a.pending = ("monstruo seleccionado", False)
                return None
            self.gi.press("deselect")
            self._atk = None
            return State.BUSCAR, f"se selecciono otra cosa ({what})"
        need = int(self.atk["broken_confirm_cycles"])
        broken = False
        if kind == "target_metin":
            a.missing = 0
            if not a.confirmed:
                a.confirmed, a.last_drop_t = True, t
                self._forget_wrong(a)
                self._close_episode("metin confirmado")
                logger.info("Metin confirmado en el panel (%s)", panel.name)
            hp = panel.hp_ratio
            if hp is not None:
                if a.min_hp is None or hp < a.min_hp:
                    a.min_hp, a.last_drop_t = hp, t
            a.zero = a.zero + 1 if hp is not None and hp <= 0 else 0
            broken = a.zero >= need
        elif a.confirmed:
            a.missing += 1
            broken = a.missing >= need
        if broken:
            self.stats.metins += 1
            self._atk = None
            if self._zone_status() is ZoneStatus.TOLERANCE:
                self.pending_return = True
            return State.RECOGER, "metin roto"
        if self._zone_status() is ZoneStatus.OUTSIDE:
            self.gi.press("deselect")
            self._atk = None
            if a.gp is None or not self.zone.accepts(a.gp):   # su posicion queda afuera (o no se sabe): no volver a caer
                self._add_discard(a, t)
                logger.info("Metin descartado: salio de la zona atacandolo (posicion %s)",
                            "desconocida, sin camara" if a.gp is None else f"est. ({a.gp[0]:.0f}, {a.gp[1]:.0f})")
                self._dbg("descarte", "salio de la zona atacando: metin descartado")
            return State.VOLVER, "salio de la zona atacando"
        if t - a.start_t > float(self.atk["max_seconds"]):
            return self._discard(a, "tiempo maximo de ataque", p, t)
        if a.confirmed:
            if kind == "target_metin":
                stalled = t - a.last_drop_t
                if stalled >= float(self.atk["blocked_s"]):
                    blocked = self._blocked_why(a, t)
                    if blocked is not None:
                        return self._unblock(a, blocked, stalled, p)
                if stalled >= float(self.atk["hp_stall_s"]):
                    return self._reclick(a, "la vida no baja", p, t)
        elif t - max(a.last_click_t, a.progress_t) >= float(self.atk["confirm_timeout_s"]):
            if a.reclicks >= 1 and self.tracker.is_stuck(t):   # ya se reintento una vez y no avanza
                self._atk = None
                return self._stuck_transition(State.ATACAR, t, "trabado yendo al metin", goal=a.gp)
            return self._reclick(a, "sin confirmar", p, t)
        return None

    def _blocked_why(self, a: _Attack, t: float) -> str | None:
        """'quieto' o 'girando en circulo' si en la ventana de `attack.blocked_s` el personaje no se esta moviendo
        de verdad (recorrido total chico, o grande frente al desplazamiento neto). None si se mueve bien."""
        win = float(self.atk["blocked_s"])
        if len(a.trail) < 2 or a.trail[0][0] > t - float(self.atk["blocked_window_frac"]) * win:
            return None
        pts = [pt for _, pt in a.trail]
        path = sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
        net = math.dist(pts[0], pts[-1])
        if path < float(self.stuck["min_move"]):
            return "quieto"
        if path >= float(self.atk["circle_min_path"]) and path > float(self.atk["circle_ratio"]) * max(net, 1.0):
            return "girando en circulo"
        return None

    def _unblock(self, a: _Attack, why: str, stalled: float, p: Perception) -> Transition:
        """Regla B: confirmado, la vida no baja y no avanza -> ESPACIO (empuja a los bichos) y reclic en el metin."""
        logger.info("Bloqueado atacando (%s, vida sin bajar %.0f s, monstruos cerca: %d): ESPACIO y reclic",
                    why, stalled, p.monsters_near)
        self._dbg("bloqueado", f"{why}: ESPACIO + reclic")
        a.trail = []
        if not self._hold_space():
            return None
        self.tracker.forget_reference()
        a.last_click_t = a.last_drop_t = self.clock()
        a.pending = (f"bloqueado ({why})", True)
        return None

    def _reclick(self, a: _Attack, why: str, p: Perception, t: float, count: bool = True) -> Transition:
        if count:
            a.reclicks += 1
        if a.reclicks > int(self.atk["max_reclicks"]):
            return self._discard(a, f"{why}, demasiados reclics", p, t)
        cands = self._candidates(p, t, check_discards=False)   # el metin en curso ya es el elegido
        if cands:
            point = cands[0].det.click_point
        elif a.confirmed:
            point = a.click
        else:
            self._atk = None
            return State.BUSCAR, "se perdio el metin de vista"
        logger.info("Reclic en %s (%s, intento %d)", point, why, a.reclicks)
        self.gi.click_at(*point, p.mask)   # sin mark_moving: el reclic no reinicia la ventana de trabado
        self._planned = point
        a.last_click_t = a.last_drop_t = self.clock()
        return None

    # ------------------------------------------------------------------ habilidades
    def _start_skills(self, t: float) -> None:
        """Al iniciar la sesion todas las habilidades activas quedan vencidas (una sola vez)."""
        if not self._skills_started:
            self._skills_started = True
            self.skills.start(t)

    def _skill_safe(self, t: float) -> bool:
        """Momento seguro: BUSCAR; EXPLORAR entre pasos; ATACAR con el metin confirmado y `attack_settle_s` despues del
        ultimo clic. Nunca en los demas estados ni a mitad de una caminata."""
        if self.state is State.BUSCAR:
            return True
        if self.state is State.EXPLORAR:
            return self._walk is None
        a = self._atk
        return (self.state is State.ATACAR and a is not None and a.confirmed and a.pending is None
                and t - a.last_click_t >= float(self.skill_cfg["attack_settle_s"]))

    def _maybe_cast_skill(self, t: float) -> bool:
        """Lanza la habilidad mas atrasada si hay una vencida y es un momento seguro. True = el ciclo termina aca."""
        if not self.skills:
            return False
        self._start_skills(t)
        due = self.skills.due(t)
        if not due or not self._skill_safe(t):
            return False
        self._cast_skill(due[0])
        return True

    def _skill_wait(self, seconds: float) -> bool:
        """Espera en tramos de `skill_timing.chunk_s` con chequeo de captcha. False si un captcha la corto (ya en CAPTCHA)."""
        def step(s: float) -> bool:
            self._sleep(s)
            return True
        return self._hold_chunked(step, seconds, float(self.skill_cfg["chunk_s"])) != "captcha"

    def _cast_skill(self, skill: Skill) -> None:
        """Suelta todo, se baja si hace falta, aprieta la tecla, espera la animacion y se vuelve a subir (solo si se bajo
        por esta habilidad). Un captcha la aborta: la habilidad queda vencida y `_do_captcha` sube al salir."""
        wait = float(self.skill_cfg["mount_wait_s"])
        self.gi.release_all()
        down_here = False
        if skill.requires_dismount and not self._dismounted:
            if not self.gi.press("mount"):
                return   # el juego no esta listo: se reintenta
            self._dismounted = down_here = True
            if not self._skill_wait(wait):
                return
        pressed = self.gi.press_key(skill.key, float(self.skill_cfg["press_hold_s"]))
        if pressed and not self._skill_wait(skill.animation_s):
            return
        if pressed:
            self.skills.mark_cast(skill, self.clock())
        if down_here:
            if self.gi.press("mount"):
                self._dismounted = False
                self._skill_wait(wait)   # un captcha aca ya no aborta nada: la habilidad salio
            else:
                logger.warning("No se pudo volver a montar tras la habilidad; se reintenta en la proxima salida")
        if not pressed:
            logger.warning("Habilidad %s: el juego no estaba listo, no se lanzo", skill.name)
            return
        self.stats.skills_cast += 1
        a = self._atk
        if self.state is State.ATACAR and a is not None:   # sigue atacando: reclic con una captura fresca
            a.last_click_t = a.last_drop_t = a.progress_t = self.clock()
            a.pending = ("habilidad lanzada", False)
        self.tracker.forget_reference()
        logger.info("Habilidad %s (%s) lanzada; proxima en %.0f s", skill.name, skill.key,
                    self.skills.due_in(skill, self.clock()))
        self._dbg("habilidad", f"{skill.name} ({skill.key})")

    # ------------------------------------------------------------------ RECOGER
    def _start_pickup(self) -> None:
        if self.pk["dismount"] and not self._dismounted and self.gi.press("mount"):
            self._dismounted = True
            self._sleep(float(self.pk["mount_wait_s"]))
        self._pk = _Pickup(self.clock())   # el tiempo maximo cuenta despues de bajarse

    def _remount(self) -> None:
        """Se sube de nuevo si se bajo para recoger (siempre emparejado)."""
        if not self._dismounted:
            return
        if self.gi.press("mount"):
            self._dismounted = False
            self._sleep(float(self.pk["mount_wait_s"]))
        else:
            logger.warning("No se pudo volver a montar; se reintenta en la proxima salida")

    def _remount_quiet(self) -> None:
        try:
            self._remount()
        except Exception:  # noqa: BLE001
            logger.exception("Fallo al volver a montar")

    def _do_recoger(self, p: Perception, t: float) -> Transition:
        k = self._pk
        if k is None:
            return State.BUSCAR, "sin recogida en curso"
        valid = [i for i in p.items if i.kind == "item" and not i.reserved_other]
        n = len(valid)
        if k.last_n is not None and n < k.last_n:
            self.stats.items += k.last_n - n
        k.last_n = n
        k.empty = 0 if n else k.empty + 1
        back = State.VOLVER if self.pending_return else State.BUSCAR
        if k.empty >= int(self.pk["empty_confirm_cycles"]):
            return back, "no quedan objetos"
        if k.presses >= int(self.pk["max_presses"]):
            return back, "tope de pulsaciones de Z"
        if t - k.start_t >= float(self.pk["max_seconds"]):
            return back, "tiempo maximo recogiendo"
        if n and t - k.last_action_t >= float(self.pk["press_interval_ms"]) / 1000:
            k.last_action_t = t
            c = p.item_choice
            if (k.since_click >= int(self.pk["presses_before_click"]) and c is not None and c.kind == "item"
                    and not c.reserved_other and c.allowed):
                self.gi.click_at(*c.click_point, p.mask)
                k.since_click = 0
            else:
                self.gi.press("pickup")
                k.presses += 1
                k.since_click += 1
        return None

    # ------------------------------------------------------------------ EXPLORAR
    def _new_sweep(self) -> None:
        self._ex = _Explore(dir=self.rng.choice(("left", "right")))

    def _new_leg(self, x: _Explore, t: float) -> None:
        """Elige el proximo tramo recto: destino a `explore.border_margin` del borde y rumbo distinto de los recientes."""
        pos = self.tracker.position
        if pos is None:
            x.target = self.navigator.exploration_target()
        else:
            recent = self._legs[-int(self.ex["recent_legs"]):] if int(self.ex["recent_legs"]) > 0 else []
            x.target, heading = self.navigator.leg_target(pos, recent, math.radians(float(self.ex["min_leg_sep_deg"])),
                                                          float(self.ex["leg_units"]), self._visited + [pos])
            self._legs.append(heading)
            self._visited = (self._visited + [pos, x.target])[-int(self.ex["visited_keep"]):]
            self._legs = self._legs[-max(1, int(self.ex["recent_legs"])):]
        x.phase, x.walk_start = "walk", t
        self._dest = x.target
        logger.info("Tramo de exploracion hacia (%.0f, %.0f)", *x.target)

    def _do_explorar(self, p: Perception, t: float) -> Transition:
        x = self._ex
        out = self._pick_goal(p, t, "metin a la vista")
        if out is not None:
            return out
        st = self._zone_status()
        if st is not None and st is not ZoneStatus.INSIDE:
            # La franja de tolerancia es para terminar un metin, no para explorar: un sondeo ciego alla puede salir.
            return State.VOLVER, "explorando fuera del poligono"
        idle = t - self._no_target_t
        if idle >= float(self.ex["max_without_metin_s"]):
            return State.CENTRO, f"sin ningun objetivo en {idle:.0f} s"
        if self.debug.explore_due(t):
            self._dbg("explorar", f"fase {x.phase}")
        if x.phase == "sweep":
            if x.steps < int(self.ex["camera_steps"]):
                self._rotate_camera(x.dir, float(self.ex["camera_step_s"]))
                self.camera.on_camera_rotated()
                x.steps += 1
            else:
                self._new_leg(x, t)
            return None
        res = self._walk_toward(p, t, x.target)
        if res == "arrived":
            self._close_episode("llego al destino")
        if res == "stuck":
            return self._stuck_transition(State.EXPLORAR, t, "trabado explorando")
        timeout = t - x.walk_start >= float(self.ex["leg_s"])
        if res in ("arrived", "blocked", "nocoords") or timeout:
            logger.info("Fin del tramo de exploracion (%s)", "tiempo" if timeout and res == "moving" else res)
            if self._walk is not None:   # parar antes del barrido
                self._brake(p)
                self._walk = None
            self._new_sweep()
        return None

    # ------------------------------------------------------------------ VOLVER y CENTRO
    def _vl_total(self, t: float) -> float:
        """Segundos en VOLVER/CENTRO de la vuelta en curso (acumula entre reentradas)."""
        return self._vl_acc + (t - self._vl_since if self._vl_since is not None else 0.0)

    def _reset_return(self) -> None:
        self._vl_acc, self._vl_since = 0.0, None

    def _do_volver(self, p: Perception, t: float) -> Transition:
        return self._go_back(p, t, State.VOLVER)

    def _do_centro(self, p: Perception, t: float) -> Transition:
        return self._go_back(p, t, State.CENTRO)

    def _go_back(self, p: Perception, t: float, kind: State) -> Transition:
        """VOLVER: hacia el centro hasta quedar adentro con margen. CENTRO: hasta `center.arrive_dist` del centro."""
        pos = self.tracker.position
        if pos is None:
            self.pending_return = False
            return State.BUSCAR, "sin coordenadas, no se puede volver"
        target = self.navigator.return_target(pos)
        self._dest = target
        if kind is State.VOLVER:
            arrived = self._inside_with_margin(pos) or self.navigator.arrived(pos, target)
        else:
            arrived = math.dist(pos, target) <= float(self.ctr["arrive_dist"])
        if arrived:
            return self._arrive_back(kind, t)
        if self._vl_total(t) >= float(self.vlv["max_seconds"]):
            reason = "no puede volver a la zona" if kind is State.VOLVER else "no puede llegar al centro"
            logger.error("%s: %.0f s volviendo sin lograrlo", reason, self._vl_total(t))
            self.gi.release_all()
            self.alarm.start(reason)
            # run_bot.ALARM_END_REASONS deja la alarma sonando con los dos motivos
            self._finish(reason)
            return None
        if t - self._vl_start >= float(self.ex["walk_timeout_s"]):
            m = self._progress_metric(kind)
            improved = m is not None and self._vl_mark is not None and m - self._vl_mark >= float(self.stuck["resolve_dist"])
            self._vl_start, self._vl_mark = t, m
            if not improved:
                return self._stuck_transition(kind, t, "sin progreso (tiempo agotado volviendo)")
        res = self._walk_toward(p, t, target)
        if res == "arrived":
            return self._arrive_back(kind, t)
        if res in ("stuck", "blocked"):
            return self._stuck_transition(kind, t, "trabado volviendo" if res == "stuck" else "sin punto de clic valido")
        return None

    def _arrive_back(self, kind: State, t: float) -> Transition:
        self.pending_return = False
        self._reset_return()
        self._close_episode("llego al destino")
        self._no_target_t = t   # el reloj de "sin objetivo" arranca de cero desde que se llega
        if kind is State.VOLVER:
            return State.BUSCAR, "de vuelta en la zona"
        return State.EXPLORAR, "en el centro: barrido y exploracion"

    # ------------------------------------------------------------------ caminata
    def _walk_toward(self, p: Perception, t: float, target: Xy, screen_vec: Xy | None = None) -> str:
        """Un ciclo de caminata: 'moving' | 'done' (tramo terminado) | 'cut' (tramo cortado: rumbo o zona) | 'arrived' |
        'stuck' | 'blocked' | 'nocoords'. Con `screen_vec` el clic va en esa direccion de PANTALLA (ACERCARSE)."""
        pos = self.tracker.position
        if pos is None:
            return "nocoords"
        w = self._walk
        if w is not None:
            if pos != w.last_pos:
                w.last_pos, w.last_move_t = pos, t
            why = self._walk_problem(w, pos)
            if why is not None:
                self._cut_walk(p, why, w)
                return "cut"
            if t - w.last_move_t >= float(self.nav["settle_s"]) or t - w.start_t >= float(self.nav["segment_timeout_s"]):
                return self._end_walk(t, pos)
            return "moving"
        if screen_vec is None and self.navigator.arrived(pos, target):
            return "arrived"
        mask = self._walk_mask(p)
        if screen_vec is not None:
            plan = self.navigator.plan_screen_click(screen_vec, pos, p.player_xy, mask)
        else:
            plan = self.navigator.plan_click(pos, target, p.player_xy, mask)
        if plan is None or not self.gi.click_at(plan.x, plan.y, mask):
            return "blocked"
        cam = self.camera
        lo, hi = cam.scale_range
        logger.info("Tramo: clic %s | camara phi=%.0f grados, escala %.2f-%.2f, lista=%s, giros=%d", (plan.x, plan.y),
                    math.degrees(cam.phi), lo, hi, cam.ready, cam.rotations)
        self._planned = (plan.x, plan.y)
        if self._zero_streak == 0:
            self.tracker.mark_moving(t)
        vec = (plan.x - p.player_xy[0], plan.y - p.player_xy[1])
        self._last_click_vec = vec
        exp = None if plan.probe else plan.expected_delta
        self._walk = _Walk(vec, pos, t, pos, t, exp, self.zone.signed_distance(pos), probe=plan.probe)
        return "moving"

    def _walk_problem(self, w: _Walk, pos: tuple[int, int]) -> str | None:
        """Durante un tramo (tambien un sondeo): 'zona' si el personaje esta mas alla del limite FIJO de seguridad
        (`guard_limit`) y la distancia firmada no mejora (si el tramo ya arranco mas alla, solo se dejan pasar los que
        la mejoran); 'rumbo' si el rumbo observado se aparta del esperado en 2 ciclos seguidos (camara girada por
        fuera). None si todo bien."""
        sd = self.zone.signed_distance(pos)
        worse = sd < w.best_sd
        w.best_sd = max(w.best_sd, sd)
        if worse and sd < self.navigator.guard_limit():
            return "zona"
        if w.exp is None:   # sondeo: el giro no es confiable, no hay rumbo esperado
            return None
        obs = (pos[0] - w.before[0], pos[1] - w.before[1])
        if math.hypot(*obs) < float(self.nav["min_segment_game"]):
            return None
        err = abs(_wrap(math.atan2(obs[1], obs[0]) - math.atan2(w.exp[1], w.exp[0])))
        w.bad = w.bad + 1 if err > math.radians(float(self.nav["heading_tolerance_deg"])) else 0
        return "rumbo" if w.bad >= 2 else None

    def _brake(self, p: Perception) -> None:
        """Frena con un clic pegado al personaje (el primer punto permitido de `BRAKE_DIRS` direcciones)."""
        r = float(self.nav["brake_px"])
        mask = self._walk_mask(p)
        for k in range(BRAKE_DIRS):
            x = round(p.player_xy[0] + r * math.cos(k * 2 * math.pi / BRAKE_DIRS))
            y = round(p.player_xy[1] + r * math.sin(k * 2 * math.pi / BRAKE_DIRS))
            if mask.allows(x, y) and self.gi.click_at(x, y, mask):
                break

    def _cut_walk(self, p: Perception, why: str, w: _Walk) -> None:
        """Corta el tramo y frena. Por 'rumbo' invalida el giro y, si lo recorrido alcanza para medir el rumbo, lo
        re-estima en el acto. Por 'zona' la camara no giro: el desplazamiento parcial igual alimenta a la camara
        (re-estima el giro si ya conoce A0; si no, cuenta como un tramo para aprender A0)."""
        logger.warning("Tramo cortado (%s): %s", why, "rumbo distinto del esperado, camara girada por fuera?"
                       if why == "rumbo" else "cerca del limite de la zona")
        self._walk = None
        self._zero_streak = 0
        self._brake(p)
        pos = self.tracker.position
        delta = None if pos is None else (pos[0] - w.before[0], pos[1] - w.before[1])
        if why == "rumbo":
            self.camera.on_camera_rotated()
        if delta is None:
            return
        if self.camera.has_a0:
            self.camera.reestimate_phi(w.vec, delta)
        elif why != "rumbo":
            self.camera.update(w.vec, delta)

    def _end_walk(self, t: float, pos: tuple[int, int]) -> str:
        w, self._walk = self._walk, None
        delta = (pos[0] - w.before[0], pos[1] - w.before[1])
        self.navigator.record_segment(w.vec, delta)
        moved = math.hypot(*delta) >= float(self.stuck["min_move"])
        self._zero_streak = 0 if moved else self._zero_streak + 1
        if self._zero_streak >= self._stuck_segments or self.tracker.is_stuck(t):
            return "stuck"
        return "done"

    # ------------------------------------------------------------------ episodios de traba
    def _progress_metric(self, state: State, pos: tuple[int, int] | None = None) -> float | None:
        """Medida de cercania al objetivo (mayor = mejor): VOLVER, distancia firmada a la zona; CENTRO, menos la
        distancia al centro. None si no aplica."""
        pos = self.tracker.position if pos is None else pos
        if pos is None:
            return None
        if state is State.VOLVER:
            return self.zone.signed_distance(pos)
        if state is State.CENTRO:
            return -math.dist(pos, self.navigator.return_target(pos))
        return None

    def _episode_progress(self, ep: StuckEpisode, pos: tuple[int, int]) -> bool:
        """Progreso real: alejarse >= `stuck.resolve_dist` del origen y, ademas, no haber empeorado la distancia
        firmada a la zona (en VOLVER / CENTRO tiene que mejorar la medida de cercania) ni la distancia al objetivo (en
        ATACAR / ACERCARSE). Empeorar la distancia a la zona solo cuenta si todavia no se esta bien adentro."""
        if math.dist(pos, ep.origin) < float(self.stuck["resolve_dist"]):
            return False
        tol = float(self.stuck["progress_tol"])
        if ep.state in (State.VOLVER.name, State.CENTRO.name):
            m = self._progress_metric(State[ep.state], pos)
            if m is not None and ep.metric0 is not None and m < ep.metric0 + tol:
                return False
        elif ep.origin_sd is not None:
            sd = self.zone.signed_distance(pos)
            if sd < ep.origin_sd - tol and sd < float(self.nav["explore_margin"]):
                return False
        if ep.goal is not None and ep.goal_dist0 is not None and math.dist(pos, ep.goal) > ep.goal_dist0 + tol:
            return False
        return True

    def _close_episode(self, reason: str) -> None:
        ep = self._ep
        if ep is None:
            return
        self._ep = None
        self.stats.episodes_resolved += 1
        logger.info("Episodio de traba %d cerrado (%s; %d intentos: %s)", ep.n, reason, ep.attempt, ", ".join(ep.strategies) or "-")

    def _update_episode(self, t: float) -> None:
        """Cierra el episodio con progreso real (`_episode_progress`) o si quedo vencido."""
        ep, pos = self._ep, self.tracker.position
        if ep is None:
            return
        if pos is not None and self._episode_progress(ep, pos):
            self._close_episode(f"se alejo {math.dist(pos, ep.origin):.0f} u del punto de traba, sin empeorar")
        elif t - ep.last_t > float(self.stuck["episode_ttl_s"]):
            self._close_episode("sin intentos nuevos")

    def _stuck_transition(self, old: State, t: float, why: str, goal: Xy | None = None) -> Transition:
        """Se detecto una traba en `old`: abre (o continua) el episodio y va a DESTRABAR, o al CENTRO si el episodio
        agoto los intentos o hubo demasiadas trabas seguidas (regla D)."""
        ep, pos = self._ep, self.tracker.position
        mx = int(self.stuck["max_attempts"])
        if ep is not None and t - ep.last_t > float(self.stuck["episode_ttl_s"]):
            self._close_episode("sin intentos nuevos")
            ep = None
        if ep is None:
            self._ep_n += 1
            self.stats.episodes += 1
            mem_t, mem_dirs, mem_strategy = self._mem
            recent = t - mem_t <= float(self.stuck["repeat_window_s"])   # traba seguida: no repetir lo recien probado
            ep = self._ep = StuckEpisode(
                self._ep_n, pos if pos is not None else (0, 0), t, t, last_strategy=mem_strategy if recent else None,
                dirs=list(mem_dirs) if recent else [], state=old.name,
                origin_sd=None if pos is None else self.zone.signed_distance(pos), metric0=self._progress_metric(old),
                goal=goal, goal_dist0=None if goal is None or pos is None else math.dist(pos, goal))
            win = float(self.stuck["repeat_window_s"])
            self._ep_starts = [s for s in self._ep_starts if t - s <= win] + [t]
            if old is not State.CENTRO and len(self._ep_starts) >= int(self.stuck["repeat_max"]):
                n, self._ep_starts = len(self._ep_starts), []
                return State.CENTRO, f"{n} trabas en {win:.0f} s (episodio {ep.n})"
        elif ep.attempt >= mx:
            ep.attempt = 0   # presupuesto nuevo (se conservan las estrategias y direcciones probadas)
            if old is not State.CENTRO:
                return State.CENTRO, f"episodio de traba {ep.n}: {mx} intentos sin salir"
        return State.DESTRABAR, why

    def _start_unstuck(self, old: State, why: str) -> None:
        back = old if old in WALKING_STATES else State.BUSCAR
        self._us = _Unstuck(back=back, why=why)
        self._zero_streak = 0
        self.stats.unstucks += 1
        if self._ep is None:   # solo en tests que entran a DESTRABAR a mano
            self._ep_n += 1
            self.stats.episodes += 1
            pos = self.tracker.position
            self._ep = StuckEpisode(self._ep_n, pos if pos is not None else (0, 0), self.clock(), self.clock(),
                                    state=old.name, origin_sd=None if pos is None else self.zone.signed_distance(pos))

    def _do_destrabar(self, p: Perception, t: float) -> Transition:
        u, ep = self._us, self._ep
        if u is None or ep is None:
            return State.BUSCAR, "sin episodio de traba"
        if u.phase == "start":
            return self._unstuck_start(p, t, u, ep)
        if u.phase == "look":
            return self._unstuck_look(p, t, u, ep)
        if u.phase == "leg":
            pos = self.tracker.position
            if self._walk_toward(p, t, pos if pos is not None else (0.0, 0.0)) == "moving":
                return None
            return self._finish_unstuck(u, ep)
        if u.acted_t is not None and self.clock() - u.acted_t < float(self.nav["settle_s"]):
            return None
        return self._finish_unstuck(u, ep)

    def _unstuck_start(self, p: Perception, t: float, u: _Unstuck, ep: StuckEpisode) -> Transition:
        mon = p.monsters_near
        strat = ep.next_strategy(mon >= int(self.stuck["monsters_min"]), int(self.stuck["space_fail_max"]))
        ep.start_attempt(strat, t)
        self._mem = (t, self._mem_dirs(ep), strat)   # el episodio siguiente (si es seguido) hereda esto
        u.strategy = strat
        logger.info("Destrabe: episodio %d, intento %d/%d, estrategia %s (%s; monstruos cerca: %d)", ep.n, ep.attempt,
                    self.stuck["max_attempts"], strat, u.why, mon)
        self._dbg("destrabe", f"{strat} intento {ep.attempt}/{self.stuck['max_attempts']}")
        if strat == "ESPACIO":   # regla A: montado, el ataque empuja a los bichos; despues avanzar (W vigilado)
            if self._hold_space():
                res = self._move_guarded("up", float(self.stuck["forward_s"]))
                if res == "guard":
                    logger.info("ESPACIO: W omitido o cortado, el personaje esta cerca del limite de la zona")
            self.tracker.forget_reference()
            self.tracker.mark_moving(self.clock())
            u.phase, u.acted_t = "settle", self.clock()
        elif strat == "ATRAS":
            res = self._move_guarded("down", float(self.stuck["back_key_s"]))
            self.tracker.forget_reference()
            if res == "captcha":
                return None
            base = math.atan2(self._last_click_vec[1], self._last_click_vec[0]) if self._last_click_vec else 0.0
            side = max((base + math.pi / 2, base - math.pi / 2),
                       key=lambda a: (self._dir_separation(ep, a), self.rng.random()))
            if self._dir_separation(ep, side) < math.radians(float(self.stuck["min_dir_sep_deg"])):
                side = self._pick_direction(ep)   # los costados ya se probaron: otra direccion cualquiera
            u.phase = "leg" if self._unstuck_click(p, t, ep, side) else "settle"
            u.acted_t = self.clock()
        else:
            u.phase, u.cam_dir = "look", self.rng.choice(("left", "right"))
        return None

    def _unstuck_look(self, p: Perception, t: float, u: _Unstuck, ep: StuckEpisode) -> Transition:
        """MIRAR (regla C): gira la camara a pasos grandes mirando etiquetas y piedras; si no aparece nada, camina un
        tramo en una direccion distinta de las ya probadas."""
        out = self._pick_goal(p, t, "metin a la vista tras girar la camara")
        if out is not None:
            return out
        if u.steps < int(self.stuck["look_steps"]):
            self._rotate_camera(u.cam_dir, float(self.stuck["look_step_s"]))
            self.camera.on_camera_rotated()
            u.steps += 1
            return None
        ang = self._pick_direction(ep)
        logger.info("MIRAR: nada a la vista, camino hacia %.0f grados de pantalla (marco de camara %d)",
                    math.degrees(ang) % 360, self.camera.rotations)
        u.phase = "leg" if self._unstuck_click(p, t, ep, ang) else "settle"
        u.acted_t = self.clock()
        return None

    def _finish_unstuck(self, u: _Unstuck, ep: StuckEpisode) -> Transition:
        td = u.tried
        if td is not None and td.game is None and td.frame == self.camera.rotations:
            td.game = self._game_angle(td.screen)   # el tramo recien caminado calibro la camara: ahora se sabe a donde fue
        pos = self.tracker.position
        d = math.dist(pos, ep.origin) if pos is not None else 0.0
        if pos is not None and self._episode_progress(ep, pos):
            self._close_episode(f"se alejo {d:.0f} u del punto de traba, sin empeorar")
            return u.back, f"destrabado (a {d:.0f} u del origen)"
        return u.back, f"fin del intento {ep.attempt} de destrabe (a {d:.0f} u del origen, el episodio sigue)"

    def _mem_dirs(self, ep: StuckEpisode) -> list:
        """Ultimas `stuck.memory_dirs` direcciones probadas (las hereda el episodio siguiente si es seguido)."""
        return ep.dirs[max(0, len(ep.dirs) - int(self.stuck["memory_dirs"])):]

    def _game_angle(self, screen_ang: float) -> float | None:
        """Angulo en el juego de una direccion de pantalla (None si la camara no esta lista)."""
        if not self.camera.ready:
            return None
        g = self.camera.screen_to_game((math.cos(screen_ang), math.sin(screen_ang)))
        return math.atan2(g[1], g[0])

    def _dir_separation(self, ep: StuckEpisode, ang: float) -> float:
        return ep.separation(self.camera.rotations, ang, self._game_angle(ang))

    def _pick_direction(self, ep: StuckEpisode) -> float:
        """Angulo de pantalla para caminar: separado >= `stuck.min_dir_sep_deg` de lo ya probado en el episodio y,
        si la camara esta lista, apuntando hacia el interior del poligono."""
        step = math.radians(float(self.stuck["dir_step_deg"]))
        n = max(3, round(2 * math.pi / step))
        base = self.rng.uniform(0, step)
        min_sep = math.radians(float(self.stuck["min_dir_sep_deg"]))
        pos = self.tracker.position
        ready = self.camera.ready and pos is not None
        cur = self.zone.signed_distance(pos) if pos is not None else 0.0
        need = max(min(self.navigator.border_margin, cur), 0.0)
        leg = float(self.ex["leg_units"])
        best, best_key = base, None
        for k in range(n):
            a = base + k * step
            game = self._game_angle(a)
            depth = 0.0
            if ready and game is not None:
                depth = self.zone.signed_distance((pos[0] + leg * math.cos(game), pos[1] + leg * math.sin(game)))
            sep = ep.separation(self.camera.rotations, a, game)
            key = ((depth >= need) if ready else True, sep >= min_sep, depth if ready else 0.0, sep, self.rng.random())
            if best_key is None or key > best_key:
                best, best_key = a, key
        return best

    def _unstuck_click(self, p: Perception, t: float, ep: StuckEpisode, ang: float) -> bool:
        """Clic en la direccion `ang` (o la mas cercana permitida); anota la direccion en el episodio y arranca un
        tramo de sondeo (para que la camara aprenda de lo caminado). False si no hay punto permitido."""
        vec = self._click_at_angle(p, t, ang)
        if vec is None:
            return False
        a = math.atan2(vec[1], vec[0])
        if self._us is not None:
            self._us.tried = ep.add_dir(self.camera.rotations, a, self._game_angle(a))
        else:
            ep.add_dir(self.camera.rotations, a, self._game_angle(a))
        self._mem = (t, self._mem_dirs(ep), ep.last_strategy)
        pos = self.tracker.position
        if pos is not None:
            self._walk = _Walk(vec, pos, t, pos, t, None, self.zone.signed_distance(pos), probe=True)
        return True

    def _click_at_angle(self, p: Perception, t: float, ang: float) -> Xy | None:
        """Clic a `unstuck_radius_px` del personaje en la direccion `ang`; prueba angulos vecinos si no esta permitido.
        Devuelve el vector de pantalla clickeado (None si no hubo ninguno)."""
        r = float(self.stuck["unstuck_radius_px"])
        if self.tracker.position is not None:
            r = self.navigator.safe_length(self.tracker.position, r)   # cerca del borde, un destrabe mas corto
        mask = self._walk_mask(p)
        step = math.radians(float(self.nav["angle_step_deg"]))
        top = math.radians(float(self.nav["max_angle_deg"]))
        offs, k = [0.0], 1
        while k * step <= top + 1e-9:
            offs += [k * step, -k * step]
            k += 1
        for off in offs:
            vx, vy = r * math.cos(ang + off), r * math.sin(ang + off)
            x, y = round(p.player_xy[0] + vx), round(p.player_xy[1] + vy)
            if not mask.allows(x, y):
                continue
            if self.gi.click_at(x, y, mask):
                self.tracker.mark_moving(t)
                self._last_click_vec = (vx, vy)
                self._planned = (x, y)
                return (float(x - p.player_xy[0]), float(y - p.player_xy[1]))
            return None
        logger.warning("Destrabe: no hay un punto de clic permitido")
        return None

    # ------------------------------------------------------------------ teclas sostenidas
    def _peek(self) -> Any:
        """Captura rapida entre tramos de una tecla sostenida (None si la ventana esta minimizada)."""
        try:
            return self.window.capture()
        except WindowMinimizedError:
            return None

    def _hold_chunked(self, hold: Callable[[float], bool], seconds: float, chunk: float | None = None,
                      guard: Callable[[Any], bool] | None = None) -> str:
        """Sostiene una tecla `seconds` en tramos de a lo sumo `loop.hold_chunk_s` (o `chunk`). Despues de cada tramo
        busca el captcha en la zona fija (captura + `find_captcha`, ~3 ms): si aparece, suelta todo y pasa a CAPTCHA
        enseguida. `guard(imagen)` (opcional) decide si seguir. Devuelve 'ok' | 'captcha' | 'guard' | 'blocked'."""
        step_max = float(self.loop["hold_chunk_s"]) if chunk is None else chunk
        left = float(seconds)
        while True:
            step = max(0.0, min(left, step_max))
            if not hold(step):
                return "blocked"
            left -= step
            img = self._peek()
            if img is not None and self._captcha_fn(img, self.cfg) is not None:
                logger.warning("Captcha detectado con una tecla sostenida: se suelta todo")
                self.gi.release_all()
                self._enter(State.CAPTCHA, "panel Bot Verify detectado durante una accion")
                return "captcha"
            if left <= HOLD_EPS:
                return "ok"
            if guard is not None and (img is None or not guard(img)):
                return "guard"

    def _hold_space(self) -> bool:
        """ESPACIO `stuck.space_s` segundos en tramos. False si un captcha lo interrumpio (ya esta en CAPTCHA)."""
        return self._hold_chunked(lambda s: self.gi.hold("attack_forward", s), float(self.stuck["space_s"])) != "captcha"

    def _rotate_camera(self, direction: str, seconds: float) -> bool:
        """Gira la camara en tramos. False si un captcha lo interrumpio."""
        return self._hold_chunked(lambda s: self.gi.rotate_camera(direction, s), seconds) != "captcha"

    def _peek_pos(self) -> tuple[int, int] | None:
        """Coordenadas del juego ahora mismo (captura + lectura, sin pasar por el filtro), None si no se leen."""
        img = self._peek()
        c = None if img is None else self._coords_fn(img, self.cfg)
        return None if c is None else (c.x, c.y)

    def _heads_inward(self, direction: str, pos: tuple[int, int]) -> bool:
        """Con la camara lista: la tecla `direction` ('up' = W, 'down' = S) mejora la distancia firmada a la zona."""
        if not self.camera.ready:
            return False
        g = self.camera.screen_to_game(SCREEN_DIRS[direction])
        n = math.hypot(*g)
        if n < HOLD_EPS:
            return False
        ahead = (pos[0] + g[0] / n, pos[1] + g[1] / n)
        return self.zone.signed_distance(ahead) > self.zone.signed_distance(pos)

    def _move_guarded(self, direction: str, seconds: float) -> str:
        """W ('up') o S ('down') en tramos de `stuck.forward_chunk_s` mirando las coordenadas entre tramos: no arranca
        sin lectura ni en la franja de tolerancia (salvo que la camara diga que va hacia adentro) y se corta si la
        distancia firmada empeora estando afuera. 'ok' | 'guard' | 'captcha' | 'blocked'."""
        pos = self._peek_pos()
        if pos is None:
            return "guard"
        prev = {"sd": self.zone.signed_distance(pos)}
        if prev["sd"] < 0 and not self._heads_inward(direction, pos):
            return "guard"

        def guard(img: Any) -> bool:
            c = self._coords_fn(img, self.cfg)
            if c is None:
                return False
            sd, before = self.zone.signed_distance((c.x, c.y)), prev["sd"]
            prev["sd"] = sd
            return sd >= 0 or sd > before

        return self._hold_chunked(lambda s: self.gi.move_key(direction, s), seconds,
                                  float(self.stuck["forward_chunk_s"]), guard)

    # ------------------------------------------------------------------ CAPTCHA
    def _do_captcha(self, p: Perception, t: float) -> Transition:
        """Espera pasiva: nada de input mientras el panel este. Lo resuelve una persona."""
        self._clear = self._clear + 1 if p.captcha is None else 0
        if self._clear >= int(self.cw["clear_confirm"]):
            self.alarm.stop()
            self._remount()
            return State.BUSCAR, "el panel desaparecio"
        return None

    # MUERTO y FIN no tienen handler: `_decide` termina la corrida apenas se detecta la muerte.
    def _do_muerto(self, p: Perception, t: float) -> Transition:
        return State.FIN, "muerte"

    def _do_fin(self, p: Perception, t: float) -> Transition:
        return None
