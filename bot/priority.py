"""Prioridad entre tipos de metin (puro): orden de los candidatos con etiqueta."""
from __future__ import annotations

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


def order_by_priority(items: Sequence[T], priority_list: Sequence[str], key_name: Callable[[T], str | None]) -> list[T]:
    """Ordena por rango de prioridad del tipo (`key_name` None = tipo desconocido = "no listado"). Estable: a igual rango
    queda el orden de entrada (el llamador la trae ordenada por distancia de piso). Con la lista vacia no cambia nada."""
    if not priority_list:
        return list(items)
    return sorted(items, key=lambda it: priority_rank(key_name(it), priority_list))
