"""Simulador de juego para los tests de navegacion: clickear mueve al personaje segun una transformacion oculta."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Circle:
    cx: float
    cy: float
    r: float

    def contains(self, x: float, y: float) -> bool:
        return math.hypot(x - self.cx, y - self.cy) <= self.r


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    def contains(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1


@dataclass(frozen=True)
class Segment:
    """Pared: segmento (x0, y0)-(x1, y1) con `half` unidades de semiancho."""
    x0: float
    y0: float
    x1: float
    y1: float
    half: float = 1.5

    def contains(self, x: float, y: float) -> bool:
        dx, dy = self.x1 - self.x0, self.y1 - self.y0
        n2 = dx * dx + dy * dy
        u = 0.0 if n2 == 0 else max(0.0, min(1.0, ((x - self.x0) * dx + (y - self.y0) * dy) / n2))
        return math.hypot(x - (self.x0 + u * dx), y - (self.y0 + u * dy)) <= self.half


def edge_cliffs(polygon, offset: float = 6.0, half: float = 1.5, skip=()) -> list[Segment]:
    """Riscos a lo largo del borde de un poligono, `offset` unidades hacia afuera (`skip` = indices de aristas sin risco)."""
    pts = [tuple(map(float, p)) for p in polygon]
    area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]))
    sign = 1.0 if area > 0 else -1.0
    out = []
    for i, ((x0, y0), (x1, y1)) in enumerate(zip(pts, pts[1:] + pts[:1])):
        if i in skip:
            continue
        n = math.hypot(x1 - x0, y1 - y0)
        nx, ny = sign * (y1 - y0) / n, -sign * (x1 - x0) / n   # normal hacia afuera
        out.append(Segment(x0 + nx * offset, y0 + ny * offset, x1 + nx * offset, y1 + ny * offset, half))
    return out


def make_a0(scale: float = 0.2, tilt: float = 0.7, flip_x: bool = False, flip_y: bool = False) -> np.ndarray:
    """A0 de una camara inclinada: el eje vertical de pantalla se estira por 1/sin(tilt)."""
    return scale * np.array([[-1.0 if flip_x else 1.0, 0.0], [0.0, (-1.0 if flip_y else 1.0) / math.sin(tilt)]])


class SimGame:
    """game = R(phi) . A0 . screen. El personaje camina a `speed` u/s hasta el clic o hasta chocar."""

    def __init__(self, A0, phi: float, start: tuple[float, float], speed: float = 10.0, obstacles=(), zone=None,
                 rng=None, rotate_rate: float = math.radians(60)) -> None:
        self.A0 = np.asarray(A0, dtype=float)
        self.phi = float(phi)
        self.pos = np.array(start, dtype=float)
        self.speed = speed
        self.obstacles = list(obstacles)
        self.zone = zone
        self.rng = rng
        self.rotate_rate = rotate_rate
        self.dest: np.ndarray | None = None
        self.blocked = False

    def _m(self) -> np.ndarray:
        c, s = math.cos(self.phi), math.sin(self.phi)
        return np.array([[c, -s], [s, c]]) @ self.A0

    def hits(self, x: float, y: float) -> bool:
        return any(o.contains(x, y) for o in self.obstacles)

    def click(self, screen_vec) -> None:
        """Clic a `screen_vec` px del personaje: fija el destino en el juego."""
        self.dest = self.pos + self._m() @ np.asarray(screen_vec, dtype=float)
        self.blocked = False

    def advance(self, dt: float) -> None:
        """Camina hasta dt segundos; se queda quieto al llegar o al chocar con un obstaculo."""
        if self.dest is None:
            return
        left = self.speed * dt
        while left > 1e-9 and self.dest is not None:
            vec = self.dest - self.pos
            d = float(np.hypot(*vec))
            if d < 1e-9:
                self.dest = None
                break
            step = min(left, d, 0.25)
            nxt = self.pos + vec / d * step
            if self.hits(*nxt):
                self.dest, self.blocked = None, True
                break
            self.pos, left = nxt, left - step
            if step >= d - 1e-9:
                self.dest = None

    def settle(self, max_s: float = 120.0, dt: float = 0.5) -> float:
        """Avanza hasta que el personaje se detiene. Devuelve los segundos usados."""
        t = 0.0
        while self.dest is not None and t < max_s:
            self.advance(dt)
            t += dt
        return t

    def rotate(self, seconds: float, direction: int = 1) -> None:
        """Gira la camara (direction +1 / -1) a velocidad fija."""
        self.phi += direction * self.rotate_rate * seconds

    def coords(self) -> tuple[int, int]:
        """Posicion entera, como la muestra el juego."""
        return int(round(self.pos[0])), int(round(self.pos[1]))

    def screen_offset_of(self, game_pt) -> tuple[float, float]:
        """Vector de pantalla (desde el personaje) donde se ve un punto del juego (para ubicar metin)."""
        v = np.linalg.solve(self._m(), np.asarray(game_pt, dtype=float) - self.pos)
        return float(v[0]), float(v[1])


# ---------------------------------------------------------------------------------------------------------
# Mundo completo para las pruebas de punta a punta (metin, objetos, eventos programados)
# ---------------------------------------------------------------------------------------------------------
@dataclass
class SimMetin:
    pos: tuple[float, float]
    name: str = "metin_de_oscuridad"      # slug de la plantilla
    hp: float = 1.0                       # fraccion de vida
    max_hp: int = 20000
    dps: float = 0.04                     # fraccion de vida por segundo mientras se lo ataca
    invulnerable: bool = False            # la vida no baja nunca
    alive: bool = True


@dataclass
class SimItem:
    pos: tuple[float, float]
    kind: str = "item"                    # "item" | "yang"
    reserved_other: bool = False


@dataclass
class SimMonster:
    pos: tuple[float, float]


DEFAULT_DROPS = (("item", False), ("item", False), ("yang", False), ("item", True))
SUBSTEP_S = 0.1
ITEM_HALF_W, ITEM_HALF_H = 40, 8   # mitad de la etiqueta de un objeto (px)


class SimWorld(SimGame):
    """SimGame + metin que se rompen, objetos, seleccion/panel, captcha, muerte y eventos programados.

    El reloj `t` avanza solo con `advance(dt)` (lo llama el SimControl). Mientras hay captcha o muerte el juego
    queda congelado (no hay combate ni movimiento). El personaje ataca un metin si lo clickeo, lo persigue y
    esta a <= `reach` unidades de el.
    """

    def __init__(self, A0, phi: float, start, *, rng, speed: float = 10.0, obstacles=(), reach: float = 3.0,
                 pickup_radius: float = 8.0, drop_radius: float = 4.0, drop_spec=DEFAULT_DROPS,
                 label_dist: float = 60.0, click_tol_px: float = 45.0, rotate_rate: float = math.radians(60),
                 far_dist: float = 150.0) -> None:
        super().__init__(A0, phi, start, speed=speed, obstacles=obstacles, rng=rng, rotate_rate=rotate_rate)
        self.t = 0.0
        self.far_dist = far_dist               # las piedras se ven (sin etiqueta) hasta esta distancia
        # Monstruos que rodean al personaje: con >= 2 pegados no puede avanzar ni pegarle al metin hasta que mantiene
        # ESPACIO `space_needed_s`: los empuja `push_dist` unidades y abre un hueco de `gap_s` segundos.
        self.surround = 0
        self.waves_left = 0
        self.free_until = 0.0
        self.ring_r, self.push_dist, self.space_needed_s, self.gap_s = 2.5, 30.0, 1.0, 8.0
        self.space_held, self.space_t, self.space_pushes = False, 0.0, 0
        self.space_up_t, self.space_gap_tol = -math.inf, 0.3   # el bot parte ESPACIO en tramos: una pausa corta no lo corta
        self.pushed: list[tuple[float, float]] = []
        self.reach, self.pickup_radius, self.drop_radius = reach, pickup_radius, drop_radius
        self.drop_spec, self.label_dist, self.click_tol_px = tuple(drop_spec), label_dist, click_tol_px
        self.metins: list[SimMetin] = []
        self.items: list[SimItem] = []
        self.picked: list[SimItem] = []
        self.bad_item_clicks: list[tuple[float, SimItem]] = []   # clics sobre yang / reservado
        self.broken: list[tuple[float, SimMetin]] = []
        self.selected: tuple[str, object] | None = None      # ("metin", SimMetin) | ("monster", None)
        self.chase: SimMetin | SimItem | None = None
        self.wrong_select = 0                  # proximos clics sobre un metin que seleccionan un monstruo
        self.clicks_on_metin: list[tuple[float, SimMetin]] = []
        self.selected_log: list[tuple[float, str, str]] = []   # (t, tipo, nombre) de cada seleccion
        self.mounted = False
        self.skill_log: list[tuple[float, str, bool, bool]] = []   # (t, tecla, estaba montado, tuvo efecto)
        self.held_move: np.ndarray | None = None   # direccion del juego (unitaria) mientras se mantiene W/A/S/D
        self.rot_dir = 0
        self.captcha_until = 0.0
        self.dead = False
        self.z_presses = 0
        self.events: list[tuple[float, object]] = []
        self.on_step: list[object] = []

    # --- construccion ---
    def add_metin(self, pos, **kw) -> SimMetin:
        m = SimMetin(tuple(pos), **kw)
        self.metins.append(m)
        return m

    def schedule(self, t: float, fn) -> None:
        self.events.append((t, fn))
        self.events.sort(key=lambda e: e[0])

    def schedule_captcha(self, t: float, duration: float) -> None:
        self.schedule(t, lambda: setattr(self, "captcha_until", self.t + duration))

    def schedule_death(self, t: float) -> None:
        self.schedule(t, lambda: setattr(self, "dead", True))

    def schedule_push(self, t: float, dx: float, dy: float) -> None:
        def push():
            self.pos = self.pos + np.array([dx, dy], dtype=float)
            self.dest, self.chase, self.held_move = None, None, None
        self.schedule(t, push)

    def surround_now(self, n: int = 3, waves: int = 1) -> None:
        """`n` monstruos rodean al personaje; reaparecen `waves` veces (cada ESPACIO exitoso consume una)."""
        self.surround, self.waves_left, self.free_until = n, waves, 0.0

    def schedule_surround(self, t: float, n: int = 3, waves: int = 1) -> None:
        self.schedule(t, lambda: self.surround_now(n, waves))

    @property
    def surrounded(self) -> bool:
        """Rodeado: con >= 2 monstruos pegados no se puede avanzar ni atacar."""
        return self.surround >= 2 and self.t >= self.free_until

    def monster_positions(self) -> list[tuple[float, float]]:
        """Posicion (juego) de los monstruos: en anillo alrededor del personaje, o donde quedaron tras el empujon."""
        if self.surround <= 0:
            return []
        if self.t >= self.free_until:
            return [(self.pos[0] + self.ring_r * math.cos(2 * math.pi * k / self.surround),
                     self.pos[1] + self.ring_r * math.sin(2 * math.pi * k / self.surround))
                    for k in range(self.surround)]
        return list(self.pushed)

    def _push_monsters(self) -> None:
        self.space_pushes += 1
        self.pushed = [(self.pos[0] + self.push_dist * math.cos(a), self.pos[1] + self.push_dist * math.sin(a))
                       for a in (2 * math.pi * k / self.surround + 0.5 for k in range(self.surround))]
        self.free_until = self.t + self.gap_s
        self.waves_left -= 1
        if self.waves_left <= 0:
            self.surround = 0
            self.pushed = []

    # --- estado ---
    @property
    def frozen(self) -> bool:
        return self.dead or self.t < self.captcha_until

    @property
    def captcha_active(self) -> bool:
        return self.t < self.captcha_until

    def visible_offset(self, pt, max_dist: float | None = None) -> tuple[float, float] | None:
        """Vector de pantalla de un punto del juego si esta a distancia de etiqueta (None si no)."""
        if math.dist(pt, self.pos) > (self.label_dist if max_dist is None else max_dist):
            return None
        return self.screen_offset_of(pt)

    # --- acciones del jugador ---
    def click_screen(self, vec) -> str:
        """Clic a `vec` px del personaje. Devuelve 'metin' | 'item' | 'ground' | 'ignored'."""
        if self.frozen:
            return "ignored"
        vec = np.asarray(vec, dtype=float)
        best = None
        for m in self.metins:
            if m.alive and (off := self.visible_offset(m.pos)) is not None:
                d = math.hypot(off[0] - vec[0], off[1] - vec[1])
                if d <= self.click_tol_px and (best is None or d < best[0]):
                    best = (d, m)
        if best is not None:
            self._click_metin(best[1])
            return "metin"
        near = []
        for it in self.items:
            if (off := self.visible_offset(it.pos)) is not None:
                d = math.hypot(off[0] - vec[0], off[1] - vec[1])
                if abs(off[0] - vec[0]) <= ITEM_HALF_W and abs(off[1] - vec[1]) <= ITEM_HALF_H:   # sobre su etiqueta
                    near.append((d, it))
        if near:
            it = min(near, key=lambda e: e[0])[1]
            if it.kind == "item" and not it.reserved_other:
                self.chase = it
                return "item"
            self.bad_item_clicks.append((self.t, it))   # clic sobre yang o reservado: se anota y cuenta como piso
        self.chase = None
        self.click(vec)
        return "ground"

    def _click_metin(self, m: SimMetin) -> None:
        self.clicks_on_metin.append((self.t, m))
        if self.surrounded:   # con los bichos encima, el clic sobre el metin selecciona un monstruo
            self.selected, self.chase, self.dest = ("monster", None), None, None
            self.selected_log.append((self.t, "monster", "monstruo"))
            return
        if self.wrong_select > 0:
            self.wrong_select -= 1
            self.selected, self.chase, self.dest = ("monster", None), None, None
            self.selected_log.append((self.t, "monster", "monstruo"))
            return
        self.selected, self.chase = ("metin", m), m
        self.selected_log.append((self.t, "metin", m.name))

    def press_pickup(self) -> None:
        self.z_presses += 1
        if self.frozen:
            return
        near = [i for i in self.items if i.kind == "item" and not i.reserved_other
                and math.dist(i.pos, self.pos) <= self.pickup_radius]
        if near:
            it = min(near, key=lambda i: math.dist(i.pos, self.pos))
            self.items.remove(it)
            self.picked.append(it)

    def deselect(self) -> None:
        if not self.frozen:
            self.selected, self.chase = None, None

    def toggle_mount(self) -> None:
        if not self.frozen:
            self.mounted = not self.mounted

    def cast_skill(self, key: str, requires_dismount: bool) -> None:
        """Tecla de habilidad. Una que requiere bajarse no tiene efecto estando montado (detecta errores de secuencia)."""
        effective = not self.frozen and not (requires_dismount and self.mounted)
        self.skill_log.append((self.t, key, self.mounted, effective))

    def key_move(self, direction: str | None) -> None:
        """Empieza (o termina con None) a caminar con W/A/S/D: 'up' = hacia donde apunta la camara (arriba en pantalla)."""
        if direction is None:
            self.held_move = None
            return
        sv = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}[direction]
        g = self._m() @ np.asarray(sv, dtype=float)
        n = float(np.hypot(*g))
        self.held_move = g / n if n > 1e-9 else None
        self.dest, self.chase = None, None

    # --- tiempo ---
    def advance(self, dt: float) -> None:
        left = float(dt)
        while left > 1e-12:
            h = min(SUBSTEP_S, left)
            left -= h
            self.t += h
            while self.events and self.events[0][0] <= self.t:
                self.events.pop(0)[1]()
            if not self.frozen:
                self._physics(h)
            for fn in list(self.on_step):
                fn(self)

    def _physics(self, h: float) -> None:
        if self.rot_dir:
            self.rotate(h, self.rot_dir)
        if self.space_held:
            self.space_t += h
            if self.surrounded and self.space_t >= self.space_needed_s:
                self._push_monsters()
        if self.surrounded:
            return
        if self.held_move is not None:
            nxt = self.pos + self.held_move * self.speed * h
            if not self.hits(*nxt):
                self.pos = nxt
            return
        ch = self.chase
        if ch is not None:
            d = math.dist(ch.pos, self.pos)
            if d <= self.reach:
                self.dest = None
                if isinstance(ch, SimItem):
                    if ch in self.items:
                        self.items.remove(ch)
                        self.picked.append(ch)
                    self.chase = None
                else:
                    self._hit(ch, h)
                return
            self.dest = np.array(ch.pos, dtype=float)
            self.blocked = False
        super().advance(h)
        if ch is not None and self.chase is ch:
            self.dest = None   # se recalcula en cada subpaso

    def _hit(self, m: SimMetin, h: float) -> None:
        if not m.alive or m.invulnerable:
            return
        m.hp = max(0.0, m.hp - m.dps * h)
        if m.hp <= 0:
            m.alive = False
            self.broken.append((self.t, m))
            self.selected, self.chase = None, None
            for kind, other in self.drop_spec:
                a = self.rng.uniform(0, 2 * math.pi)
                r = self.rng.uniform(0.5, self.drop_radius)
                self.items.append(SimItem((m.pos[0] + r * math.cos(a), m.pos[1] + r * math.sin(a)), kind, other))
