"""Navegacion por realimentacion: aprende como se traduce un clic en pantalla a un desplazamiento en el
juego y elige donde clickear para llegar a un punto en coordenadas del juego (logica pura, sin input).

Modelo de camara: game = R(phi) . A0 . screen, con A0 una matriz 2x2 (escala, inclinacion, posible reflexion)
que se aprende con tramos no colineales y se conserva, y phi el giro de la camara (Q/E) respecto de cuando se
aprendio A0, que se re-estima con un solo tramo. Se separan porque girar la camara es lo que cambia seguido
y no altera A0 (una rotacion conserva escala y reflexion): asi un giro cuesta 1 tramo, no 2.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

from bot.tracking import angle_sep
from bot.vision.screen_mask import ScreenMask
from bot.zone import _CENTROID_MIN_DEPTH, FarmZone

logger = logging.getLogger(__name__)

Vec = tuple[float, float]
_PROBE_TURN = math.pi / 2   # giro entre dos sondeos seguidos (tramos no colineales)
_LEG_HEADINGS = 12   # rumbos candidatos de un tramo de exploracion
_LEG_FACTORS = (1.0, 0.75, 0.5, 0.35)   # acortamientos del tramo si el largo entero no entra


def _rot(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s], [s, c]])


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


class CameraModel:
    """Estima la transformacion pantalla -> juego a partir de tramos caminados."""

    def __init__(self, cfg: dict) -> None:
        n = cfg["navigation"]
        self.min_px = float(n["min_segment_px"])
        self.min_game = float(n["min_segment_game"])
        self.min_sin = float(n["min_sin"])
        self.fit_n = int(n["fit_segments"])
        self.max_ratio_error = float(n["max_ratio_error"])
        self.relearn_after = int(n["relearn_after_rejects"])
        self.phi_smooth = math.radians(float(n["phi_smooth_deg"]))
        self.external_rotation = math.radians(float(n["external_rotation_deg"]))
        self._hint = (float(n["scale_hint"][0]), float(n["scale_hint"][1]))
        self.rotations = 0   # cuantas veces giro la camara (para saber si un tramo anterior sigue valiendo)
        self._a0: np.ndarray | None = None
        self._phi = 0.0
        self._phi_valid = False
        self._segs: list[tuple[np.ndarray, np.ndarray]] = []   # (pantalla, juego en el marco de referencia phi=0)
        self._rejects = 0
        self._scale: tuple[float, float] | None = None   # (menor, mayor) valor singular de la ultima A0 (sobrevive a reset)
        self._confirmed = False   # A0 ya fue corroborada por un tramo posterior (con 2 tramos el ajuste es exacto)

    @property
    def has_a0(self) -> bool:
        return self._a0 is not None

    @property
    def scale_range(self) -> tuple[float, float] | None:
        """(menor, mayor) escala pantalla->juego (unidades por pixel): la de la ultima A0 aprendida o la de la config."""
        return self._scale or self._hint

    @property
    def ready(self) -> bool:
        """A0 conocida y giro vigente: sirve para apuntar."""
        return self._a0 is not None and self._phi_valid

    @property
    def phi(self) -> float:
        return self._phi

    def on_camera_rotated(self) -> None:
        """La camara giro: phi deja de valer hasta el proximo tramo. Sin A0 aprendida, los tramos guardados
        estan en otro marco de giro y no se pueden mezclar con los nuevos: se descartan."""
        self._phi_valid = False
        self.rotations += 1
        if self._a0 is None:
            self._segs = []

    def reestimate_phi(self, screen_vec: Vec, game_delta: Vec) -> bool:
        """Re-estima solo el giro con un tramo parcial (solo cuenta el rumbo, no el largo). Necesita A0."""
        if self._a0 is None:
            return False
        s = np.asarray(screen_vec, dtype=float)
        g = np.asarray(game_delta, dtype=float)
        pred = self._a0 @ s
        if np.hypot(*g) < self.min_game or np.hypot(*pred) < 1e-9:
            return False
        self._phi, self._phi_valid = _wrap(math.atan2(g[1], g[0]) - math.atan2(pred[1], pred[0])), True
        return True

    def reset(self) -> None:
        self._a0, self._phi, self._phi_valid, self._segs, self._rejects = None, 0.0, False, [], 0
        self._confirmed = False

    def _matrix(self) -> np.ndarray:
        if self._a0 is None:
            raise RuntimeError("la camara todavia no aprendio A0")
        return _rot(self._phi) @ self._a0

    def screen_to_game(self, vec: Vec) -> Vec:
        g = self._matrix() @ np.asarray(vec, dtype=float)
        return float(g[0]), float(g[1])

    def game_to_screen(self, vec: Vec) -> Vec:
        s = np.linalg.solve(self._matrix(), np.asarray(vec, dtype=float))
        return float(s[0]), float(s[1])

    def update(self, screen_vec: Vec, game_delta: Vec) -> bool:
        """Incorpora un tramo caminado. Devuelve False si se ignora (muy corto, bloqueado o inconsistente)."""
        s = np.asarray(screen_vec, dtype=float)
        g = np.asarray(game_delta, dtype=float)
        if np.hypot(*s) < self.min_px or np.hypot(*g) < self.min_game:
            return False
        if self._a0 is None:
            self._segs.append((s, g))
            self._segs = self._segs[-self.fit_n:]
            self._fit()
            if self._a0 is not None:
                self._phi, self._phi_valid = 0.0, True
            return True
        pred = self._a0 @ s
        ratio = float(np.hypot(*g) / max(np.hypot(*pred), 1e-9))
        if abs(ratio - 1.0) > self.max_ratio_error:
            self._rejects += 1
            if not self._confirmed and ratio > 1.0:
                # A0 salio de solo 2 tramos y un tramo corto (p. ej. chocado contra una pared) pudo falsearla: el
                # tramo nuevo, mas largo de lo que predice, la contradice. Un tramo mas corto es solo un freno.
                logger.warning("Camara: el primer tramo posterior contradice la A0 recien aprendida, se reaprende")
                self.reset()
            elif self._rejects >= self.relearn_after:
                logger.warning("Camara: %d tramos inconsistentes seguidos, se reaprende", self._rejects)
                self.reset()
            return False
        self._rejects = 0
        self._confirmed = True
        new_phi = _wrap(math.atan2(g[1], g[0]) - math.atan2(pred[1], pred[0]))
        if self._phi_valid and abs(_wrap(new_phi - self._phi)) > self.external_rotation:
            logger.warning("Camara girada por fuera: giro %.0f -> %.0f grados, se re-estima con este tramo",
                           math.degrees(self._phi), math.degrees(new_phi))
            self.rotations += 1   # los tramos anteriores ya no valen (por ejemplo, para deshacer el ultimo)
            self._phi = new_phi
            return True
        if self._phi_valid and abs(_wrap(new_phi - self._phi)) < self.phi_smooth:
            refined = self._phi + 0.5 * _wrap(new_phi - self._phi)
            # tramo coherente con el giro vigente: sirve tambien para afinar A0
            self._segs.append((s, _rot(-self._phi) @ g))
            self._segs = self._segs[-self.fit_n:]
            self._fit()
            self._phi = refined
        else:
            self._phi = new_phi
        self._phi_valid = True
        return True

    def _fit(self) -> None:
        """Minimos cuadrados de A0 sobre los tramos guardados, si hay al menos dos no colineales."""
        if len(self._segs) < 2:
            return
        s = np.array([a for a, _ in self._segs])
        g = np.array([b for _, b in self._segs])
        u = s / np.linalg.norm(s, axis=1, keepdims=True)
        best = max(abs(u[i, 0] * u[j, 1] - u[i, 1] * u[j, 0])
                   for i in range(len(u)) for j in range(i + 1, len(u)))
        if best < self.min_sin:
            return
        sol, *_ = np.linalg.lstsq(s, g, rcond=None)   # s @ A0.T = g
        a0 = sol.T
        if abs(np.linalg.det(a0)) > 1e-9:
            self._a0 = a0
            sv = np.linalg.svd(a0, compute_uv=False)
            self._scale = (float(sv[-1]), float(sv[0]))


@dataclass(frozen=True)
class ClickPlan:
    x: int
    y: int
    probe: bool
    expected_delta: Vec | None   # desplazamiento esperado en el juego (None si la camara no esta calibrada)


class Navigator:
    """Elige donde clickear para ir a un punto del juego; aprende de cada tramo."""

    def __init__(self, cfg: dict, zone: FarmZone, camera: CameraModel, rng) -> None:
        n = cfg["navigation"]
        self.zone, self.camera, self.rng = zone, camera, rng
        self.probe_radius = float(n["probe_radius_px"])
        self.click_min, self.click_max = (float(v) for v in n["click_radius_px"])
        self.max_walk = float(n["max_walk_px"])
        self.click_max = min(self.click_max, self.max_walk)
        self.click_min = min(self.click_min, self.click_max)
        self.guard_margin = float(n["zone_guard_margin"])
        self.arrive_dist = float(n["arrive_dist"])
        self.explore_margin = float(n["explore_margin"])
        # los destinos de exploracion y de vuelta quedan al menos a `explore.border_margin` del borde
        self.border_margin = max(self.explore_margin, float(cfg.get("explore", {}).get("border_margin", 0.0)))
        self.angle_step = math.radians(float(n["angle_step_deg"]))
        self.max_angle = math.radians(float(n["max_angle_deg"]))
        self.factors = [float(f) for f in n["shorten_factors"]]
        self.min_px = float(n["min_segment_px"])
        self.min_game = float(n["min_segment_game"])
        self.measure_factor = float(n["probe_measure_factor"])
        self.turn_jitter = math.radians(float(n["probe_turn_jitter_deg"]))
        self.tiebreak = float(cfg["explore"]["leg_tiebreak_units"])
        self.tolerance = zone.outside_tolerance
        self._probe_angle: float | None = None
        self._last_seg: tuple[Vec, Vec, int] | None = None   # (pantalla, juego, giros de camara) del ultimo tramo

    def _offsets(self) -> list[float]:
        out = [0.0]
        k = 1
        while k * self.angle_step <= self.max_angle + 1e-9:
            out += [k * self.angle_step, -k * self.angle_step]
            k += 1
        return out

    def guard_limit(self) -> float:
        """Distancia firmada minima (negativa) a la que se deja llegar a un tramo de caminata."""
        return -(self.tolerance - self.guard_margin)

    def _stays_in_zone(self, origin: Vec, v: Vec) -> bool:
        """El destino previsto de un clic (con la camara calibrada) no se pasa de `guard_limit` (salvo que acerque)."""
        g = self.camera.screen_to_game(v)
        end = self.zone.signed_distance((origin[0] + g[0], origin[1] + g[1]))
        return end >= self.guard_limit() or end >= self.zone.signed_distance(origin)

    def _pick(self, base: Vec, length: float, player_screen: Vec, mask: ScreenMask,
              floor: float | None = None, origin: Vec | None = None) -> tuple[int, int, Vec] | None:
        """Primer punto permitido probando angulos cercanos y distancias mas cortas (no menos de `floor` px).
        Con `origin` (posicion en el juego) se rechazan los puntos cuyo destino previsto se pasa de la tolerancia."""
        floor = self.min_px if floor is None else max(floor, self.min_px)
        length = min(length, self.max_walk)
        a0 = math.atan2(base[1], base[0])
        for off in self._offsets():
            for f in self.factors:
                d = length * f
                if d < floor - 1e-9:
                    continue
                v = (d * math.cos(a0 + off), d * math.sin(a0 + off))
                x, y = round(player_screen[0] + v[0]), round(player_screen[1] + v[1])
                if not mask.allows(x, y):
                    continue
                vec = (x - player_screen[0], y - player_screen[1])
                if origin is None or self._stays_in_zone(origin, vec):
                    return x, y, vec
        return None

    def plan_click(self, player_game: Vec, target_game: Vec, player_screen: Vec, mask: ScreenMask) -> ClickPlan | None:
        """Punto de pantalla permitido donde clickear. None si no hay ninguno."""
        if not self.camera.ready:
            return self._plan_probe(player_game, target_game, player_screen, mask)
        delta = (target_game[0] - player_game[0], target_game[1] - player_game[1])
        want = self.camera.game_to_screen(delta)
        # Largo del clic: la distancia al destino, acotada. Si el destino esta muy cerca el clic se acorta hasta
        # el, pero nunca menos de min_segment_px (un tramo mas corto se ignora y no se puede medir).
        length = max(min(math.hypot(*want), self.click_max), self.min_px)
        found = self._pick(want, length, player_screen, mask, floor=min(self.click_min, length), origin=player_game)
        if found is None:   # ningun punto sirve sin salirse: largo de sondeo acotado, que no puede salir de la franja
            found = self._pick(want, self.safe_length(player_game), player_screen, mask)
        if found is None:
            return None
        x, y, v = found
        return ClickPlan(x, y, False, self.camera.screen_to_game(v))

    def plan_screen_click(self, vec: Vec, player_game: Vec, player_screen: Vec, mask: ScreenMask) -> ClickPlan | None:
        """Clic de caminata en la direccion de PANTALLA `vec` (por ejemplo, hacia una piedra que se ve). Con la camara
        lista rechaza los puntos que sacarian de la tolerancia; sin ella, solo vale la guardia de zona del tramo."""
        ready = self.camera.ready
        length = max(min(math.hypot(*vec), self.click_max), self.min_px)
        origin = player_game if ready else None
        found = self._pick(vec, length, player_screen, mask, floor=min(self.click_min, length), origin=origin)
        if found is None and ready:
            found = self._pick(vec, self.safe_length(player_game), player_screen, mask)
        if found is None:
            return None
        x, y, v = found
        return ClickPlan(x, y, False, self.camera.screen_to_game(v) if ready else None)

    def _plan_retrace(self, player_game: Vec, player_screen: Vec, mask: ScreenMask) -> ClickPlan | None:
        """Si el ultimo tramo (misma camara) nos alejo del poligono y ya estamos afuera, el clic opuesto vuelve
        exactamente por donde vino (la relacion es lineal), aunque no se conozca el giro."""
        if self._last_seg is None:
            return None
        s, g, rot = self._last_seg
        if rot != self.camera.rotations or self.zone.signed_distance(player_game) >= 0:
            return None
        before = (player_game[0] - g[0], player_game[1] - g[1])
        if self.zone.signed_distance(player_game) >= self.zone.signed_distance(before):
            return None
        found = self._pick((-s[0], -s[1]), math.hypot(*s), player_screen, mask)
        if found is None:
            return None
        x, y, v = found
        return ClickPlan(x, y, True, (-g[0], -g[1]))

    def _plan_probe(self, player_game: Vec, target_game: Vec, player_screen: Vec, mask: ScreenMask) -> ClickPlan | None:
        back = self._plan_retrace(player_game, player_screen, mask)
        if back is not None:
            return back
        if self.camera.has_a0:   # solo falta phi: sondear hacia el destino con el ultimo giro conocido
            want = self.camera.game_to_screen((target_game[0] - player_game[0], target_game[1] - player_game[1]))
            base = math.atan2(want[1], want[0])
        elif self._probe_angle is None:
            base = self.rng.uniform(0, 2 * math.pi)
        else:   # tramos no colineales: girar ~90 grados respecto del sondeo anterior
            base = self._probe_angle + _PROBE_TURN + self.rng.uniform(-self.turn_jitter, self.turn_jitter)
        self._probe_angle = base
        found = self._pick((math.cos(base), math.sin(base)), self.safe_length(player_game), player_screen, mask)
        if found is None:
            return None
        x, y, v = found
        exp = self.camera.screen_to_game(v) if self.camera.has_a0 else None
        return ClickPlan(x, y, True, exp)

    def safe_length(self, player_game: Vec, radius: float | None = None) -> float:
        """Largo (px) de un clic de direccion incierta (sondeo, destrabe). Sin giro conocido puede ir en cualquier
        direccion: cerca del borde se acorta (segun la escala de la ultima A0) para que no pueda salir de la franja
        de tolerancia. Mientras hay espacio no baja de lo que se puede medir; con poco o ningun espacio (`room` chico o
        negativo) se acorta hasta el minimo clickeable, sin piso de medicion: la guardia de zona del tramo hace el resto."""
        r = min(self.probe_radius if radius is None else float(radius), self.max_walk)
        lo, hi = self.camera.scale_range
        room = self.zone.signed_distance(player_game) + self.tolerance - self.explore_margin
        clickable = self.measure_factor * self.min_px
        measurable = max(self.measure_factor * self.min_game / lo, clickable)   # todavia tiene que poder medirse
        cap = room / hi
        return max(min(r, cap), measurable if cap >= measurable else clickable)

    def record_segment(self, screen_vec: Vec, game_delta: Vec) -> bool:
        """Registra un tramo caminado (vector de pantalla del clic, cambio de coordenadas del juego)."""
        self._last_seg = (tuple(screen_vec), tuple(game_delta), self.camera.rotations)
        return self.camera.update(screen_vec, game_delta)

    def arrived(self, player_game: Vec, target_game: Vec) -> bool:
        return math.dist(player_game, target_game) <= self.arrive_dist

    def exploration_target(self) -> Vec:
        """Punto aleatorio dentro del poligono, bien adentro (`border_margin` del borde)."""
        return self.zone.random_point_inside(self.rng, self.border_margin)

    def leg_target(self, pos: Vec, recent: list[float], min_sep: float, leg_units: float,
                   visited: list[Vec] = ()) -> tuple[Vec, float]:
        """Destino de un tramo recto de exploracion: (punto, rumbo en el juego). El destino queda a >= `border_margin`
        del borde y el rumbo se aparta >= `min_sep` (rad) de los `recent`; entre los validos gana el mas lejos de lo
        ya `visited` (no volver sobre lo explorado). Si no hay ninguno, se relaja la separacion y, al final, se va
        hacia el centro."""
        base = self.rng.uniform(0, 2 * math.pi / _LEG_HEADINGS)
        for f in _LEG_FACTORS:   # primero el largo entero, aun relajando la separacion; despues tramos mas cortos
            for sep in (min_sep, 0.0):
                valid = []
                for k in range(_LEG_HEADINGS):
                    ang = base + 2 * math.pi * k / _LEG_HEADINGS
                    pt = (pos[0] + leg_units * f * math.cos(ang), pos[1] + leg_units * f * math.sin(ang))
                    if (self.zone.signed_distance(pt) >= self.border_margin
                            and all(angle_sep(ang, r) >= sep for r in recent)):
                        valid.append((pt, ang))
                if valid:
                    return max(valid, key=lambda v: min((math.dist(v[0], q) for q in visited), default=0.0)
                               + self.rng.uniform(0.0, self.tiebreak))
        c = self.return_target(pos)
        ang = math.atan2(c[1] - pos[1], c[0] - pos[0])
        d = min(leg_units, math.dist(c, pos))
        return (pos[0] + d * math.cos(ang), pos[1] + d * math.sin(ang)), ang

    def return_target(self, player_game: Vec) -> Vec:
        """Centro del poligono (centroide, o `interior_center` si el centroide queda pegado al borde)."""
        z = self.zone
        c = z.centroid
        if z.signed_distance(c) < _CENTROID_MIN_DEPTH * z.signed_distance(z.interior_center):
            c = z.interior_center
        return c
