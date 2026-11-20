"""Lista de candidatos (metin vistos) con su posicion estimada en el MAPA (coordenadas del juego), logica pura.

La posicion en pantalla deja de servir apenas gira la camara; en el mapa no. Con el vector de piso del candidato respecto
de los pies (`bot/ground.py`: X a la derecha, Z hacia adelante), el rumbo de la camara y una escala `k` (unidades del
juego por unidad de piso): pos = jugador + k * (Z * adelante + X * derecha). Los rumbos son angulos del juego (atan2(dy, dx),
y hacia abajo): "derecha" = rumbo + 90 grados.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from bot import ground

Xy = tuple[float, float]


def floor_vec(player_xy: tuple[int, int], pt: Xy, shape: tuple[int, ...], cfg: dict) -> Xy:
    """Vector de piso (X derecha, Z adelante) de un punto de pantalla respecto de los pies del personaje."""
    fx, fz = ground.to_ground(ground.feet_of(player_xy, cfg), shape, cfg)
    px, pz = ground.to_ground(pt, shape, cfg)
    return px - fx, pz - fz


def world_pos(player: Xy, heading: float, vec: Xy, k: float) -> Xy:
    """Posicion en el mapa de algo a `vec` (X, Z) de piso del jugador con la camara mirando al rumbo `heading` (rad)."""
    fwd = (math.cos(heading), math.sin(heading))
    right = (-fwd[1], fwd[0])
    return (player[0] + k * (vec[1] * fwd[0] + vec[0] * right[0]), player[1] + k * (vec[1] * fwd[1] + vec[0] * right[1]))


class ScaleEstimator:
    """Escala `k` (unidades del juego por unidad de piso): valor inicial de la config, afinado con EMA cuando un clic lleva al
    personaje hasta un metin (distancia recorrida + alcance de ataque = k * distancia de piso del clic)."""

    def __init__(self, k0: float, ema: float, bounds: tuple[float, float]) -> None:
        self.k0, self.k, self.ema, self.bounds = k0, k0, ema, bounds
        self.samples = 0

    def learn(self, walked_u: float, floor_d: float, reach_u: float) -> float | None:
        """Una muestra: devuelve el `k` de esa muestra (o None si no sirve) y actualiza la EMA."""
        if floor_d <= 1e-6 or walked_u <= 0:
            return None
        sample = (walked_u + reach_u) / floor_d
        lo, hi = self.bounds[0] * self.k0, self.bounds[1] * self.k0
        if not lo <= sample <= hi:
            return None
        self.k = (1 - self.ema) * self.k + self.ema * sample
        self.samples += 1
        return sample


@dataclass
class Candidate:
    """Un metin visto: `label` = con etiqueta de tipo objetivo (si no, piedra lejana sin confirmar)."""
    xy: Xy
    kind: str
    label: bool
    t: float
    n: int = 1


class CandidateMap:
    """Candidatos con posicion en el mapa: fusiona los que caen a <= `merge_u`, vence a los `keep_s` y elige el mejor."""

    def __init__(self, keep_s: float, merge_u: float, tie_u: float, stone_ratio: float) -> None:
        self.keep_s, self.merge_u, self.tie_u, self.stone_ratio = keep_s, merge_u, tie_u, stone_ratio
        self.items: list[Candidate] = []

    def __len__(self) -> int:
        return len(self.items)

    def add(self, xy: Xy, kind: str, label: bool, t: float) -> Candidate:
        """Agrega o fusiona (promedio ponderado de la posicion; una etiqueta mejora a una piedra y se queda con el tipo)."""
        near = [c for c in self.items if math.dist(c.xy, xy) <= self.merge_u]
        if not near:
            c = Candidate(xy, kind, label, t)
            self.items.append(c)
            return c
        c = min(near, key=lambda c: math.dist(c.xy, xy))
        w = c.n
        c.xy = ((c.xy[0] * w + xy[0]) / (w + 1), (c.xy[1] * w + xy[1]) / (w + 1))
        c.n += 1
        c.t = t
        if label and not c.label:
            c.label, c.kind = True, kind
        return c

    def prune(self, t: float) -> int:
        """Quita los vencidos (vistos hace mas de `keep_s`). Devuelve cuantos."""
        n = len(self.items)
        self.items = [c for c in self.items if t - c.t < self.keep_s]
        return n - len(self.items)

    def remove(self, c: Candidate) -> None:
        if c in self.items:
            self.items.remove(c)

    def remove_near(self, xy: Xy, radius: float) -> int:
        """Quita los candidatos a <= `radius` de `xy` (p. ej. alrededor de un metin roto)."""
        n = len(self.items)
        self.items = [c for c in self.items if math.dist(c.xy, xy) > radius]
        return n - len(self.items)

    def clear(self) -> None:
        self.items = []

    def best(self, player: Xy, center: Xy, t: float, accepts: Callable[[Xy], bool] = lambda xy: True) -> Candidate | None:
        """El mejor: solo en la zona (`accepts`) y vigentes; las etiquetas antes que las piedras (una piedra solo gana si esta
        a menos de `stone_ratio` x la distancia de la etiqueta mas cercana); el mas cercano al jugador y, entre los que
        empatan (a <= `tie_u` del mas cercano), el mas cerca del centro de la zona."""
        ok = [c for c in self.items if t - c.t < self.keep_s and accepts(c.xy)]
        if not ok:
            return None
        labels = [c for c in ok if c.label]
        stones = [c for c in ok if not c.label]
        pool = labels or stones
        if labels and stones:
            d_l = min(math.dist(c.xy, player) for c in labels)
            d_s = min(math.dist(c.xy, player) for c in stones)
            if d_s < self.stone_ratio * d_l:
                pool = stones
        d0 = min(math.dist(c.xy, player) for c in pool)
        tied = [c for c in pool if math.dist(c.xy, player) <= d0 + self.tie_u]
        return min(tied, key=lambda c: (math.dist(c.xy, center), math.dist(c.xy, player)))
