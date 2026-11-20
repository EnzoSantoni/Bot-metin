"""Distancias sobre el plano del piso a partir de puntos de pantalla (logica pura, sin captura ni input).

Camara de pinhole con horizonte en la fila `h` y focal `f`: un punto del piso visto en (u, v) esta a profundidad
Z = f / (v - h) y a lado X = (u - cx) / (v - h). Mas abajo en la pantalla = mas cerca; el mismo salto en pixeles vale
mas unidades de piso cerca del personaje que lejos. Solo sirve para COMPARAR cual de varios puntos esta mas cerca.
Config: `vision.ground` (horizon_frac, focal_frac, feet_dy_px, min_dv_px).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

Xy = tuple[float, float]


def _params(shape: tuple[int, ...], cfg: dict) -> tuple[float, float, float, float]:
    """(horizonte, focal, cx, min_dv) en pixeles para una imagen de `shape` = (alto, ancho, ...)."""
    g = cfg["vision"]["ground"]
    h, w = shape[0], shape[1]
    return float(g["horizon_frac"]) * h, float(g["focal_frac"]) * w, w / 2.0, float(g["min_dv_px"])


def to_ground(pt: Xy, shape: tuple[int, ...], cfg: dict) -> Xy:
    """Punto de pantalla -> (X, Z) en el piso. (v - h) se acota abajo por `min_dv_px` (por encima del horizonte = muy lejos)."""
    hor, f, cx, min_dv = _params(shape, cfg)
    dv = max(pt[1] - hor, min_dv)
    return (pt[0] - cx) / dv, f / dv


def depth_px(y: float, shape: tuple[int, ...], cfg: dict) -> float:
    """Distancia vertical en pixeles de la fila `y` al horizonte (acotada por `min_dv_px`): el tamano en pantalla de un objeto
    del piso es proporcional a ella (perspectiva)."""
    hor, _, _, min_dv = _params(shape, cfg)
    return max(y - hor, min_dv)


def feet_of(player_xy: tuple[int, int], cfg: dict) -> Xy:
    """Pies del personaje: el punto de pantalla del personaje (cabeza/torso) mas `feet_dy_px` hacia abajo."""
    return float(player_xy[0]), float(player_xy[1]) + float(cfg["vision"]["ground"]["feet_dy_px"])


def ground_dist(a: Xy, b: Xy, shape: tuple[int, ...], cfg: dict) -> float:
    """Distancia en el piso (unidades de camara, solo comparables entre si) entre dos puntos de pantalla."""
    return math.dist(to_ground(a, shape, cfg), to_ground(b, shape, cfg))


@dataclass(frozen=True)
class Goal:
    """Un metin a la vista: `kind` = 'etiqueta' | 'piedra'; `point` = base en pantalla (para medir); `ref` = lo que el
    llamador quiera recuperar (candidato de etiqueta o de piedra)."""
    kind: str
    name: str
    point: tuple[int, int]
    ref: Any = None


def rank_goals(goals: list[Goal], player_xy: tuple[int, int], shape: tuple[int, ...], cfg: dict
               ) -> list[tuple[Goal, float]]:
    """(meta, distancia de piso a los pies del personaje) en orden de eleccion. Las etiquetas objetivo van primero (la mas
    cercana antes); una piedra sin etiqueta solo le gana a la etiqueta mas cercana si su distancia es menor que
    `approach.unlabeled_closer_ratio` x la de ella. Las piedras sin etiqueta con la base por debajo de
    `approach.near_unlabeled_y_frac` del alto (cerca del personaje: de cerca los metin siempre muestran su nombre, casi seguro
    es un monstruo) quedan al final. A igual distancia queda primero la que vino primero."""
    feet = feet_of(player_xy, cfg)
    apr = cfg["approach"]
    ratio, y_lim = float(apr["unlabeled_closer_ratio"]), float(apr["near_unlabeled_y_frac"]) * shape[0]
    scored = sorted(((g, ground_dist(feet, g.point, shape, cfg)) for g in goals), key=lambda e: e[1])
    labels = [e for e in scored if e[0].kind == "etiqueta"]
    stones = [e for e in scored if e[0].kind != "etiqueta" and e[0].point[1] < y_lim]
    near = [e for e in scored if e[0].kind != "etiqueta" and e[0].point[1] >= y_lim]
    out: list[tuple[Goal, float]] = []
    while labels and stones:
        out.append(stones.pop(0) if stones[0][1] < ratio * labels[0][1] else labels.pop(0))
    return out + labels + stones + near
