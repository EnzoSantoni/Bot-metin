"""Geometria pura (sin win32): rectangulos y conversion cliente <-> pantalla."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.right and self.y <= y < self.bottom

    def intersect(self, other: "Rect") -> "Rect | None":
        x0, y0 = max(self.x, other.x), max(self.y, other.y)
        x1, y1 = min(self.right, other.right), min(self.bottom, other.bottom)
        if x1 <= x0 or y1 <= y0:
            return None
        return Rect(x0, y0, x1 - x0, y1 - y0)

    def clip(self, w: int, h: int) -> "Rect | None":
        """Recorta a una imagen de w x h."""
        return self.intersect(Rect(0, 0, w, h))

    def crop(self, img: np.ndarray) -> np.ndarray:
        """Vista de img[y:y+h, x:x+w] tras recortar al tamano de la imagen."""
        r = self.clip(img.shape[1], img.shape[0])
        if r is None:
            return img[0:0, 0:0]
        return img[r.y:r.bottom, r.x:r.right]


def client_to_screen(x: int, y: int, client_rect_on_screen: Rect, strict: bool = True) -> tuple[int, int]:
    """Cliente -> pantalla. Con strict, fuera del area de cliente lanza ValueError."""
    if strict and not (0 <= x < client_rect_on_screen.w and 0 <= y < client_rect_on_screen.h):
        raise ValueError(
            f"({x}, {y}) cae fuera del area de cliente "
            f"({client_rect_on_screen.w}x{client_rect_on_screen.h})")
    return x + client_rect_on_screen.x, y + client_rect_on_screen.y


def screen_to_client(x: int, y: int, client_rect_on_screen: Rect) -> tuple[int, int]:
    """Pantalla -> cliente (no valida; puede dar valores fuera del area)."""
    return x - client_rect_on_screen.x, y - client_rect_on_screen.y
