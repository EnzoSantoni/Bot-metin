"""Prioridad entre tipos de metin (puro): orden de los candidatos con etiqueta."""
from __future__ import annotations

import math
from typing import Callable, Sequence, TypeVar

from bot.vision.metin import slugify

T = TypeVar("T")


def priority_rank(name_slug: str | None, priority_list: Sequence[str]) -> int:
    """Posicion del tipo en la lista (comparando por slug); si no esta, despues de todos."""
    slug = slugify(name_slug or "")
    for i, n in enumerate(priority_list):
        if slugify(n) == slug:
            return i
    return len(priority_list)


def order_candidates(candidates: Sequence[T], player_xy: tuple[int, int], priority_list: Sequence[str],
                     key_name: Callable[[T], str | None], key_pos: Callable[[T], tuple[int, int]]) -> list[T]:
    """Mayor prioridad primero y, a igual prioridad, el mas cercano. Orden estable (como el sort por distancia de siempre);
    con la lista vacia es exactamente ese orden."""
    if not priority_list:
        return sorted(candidates, key=lambda c: math.dist(key_pos(c), player_xy))
    return sorted(candidates, key=lambda c: (priority_rank(key_name(c), priority_list), math.dist(key_pos(c), player_xy)))
