"""Backend falso: registra las llamadas en `events` sin tocar el sistema."""
from __future__ import annotations

from typing import Any, Callable

import numpy as np

from bot import geometry
from bot.geometry import Rect

from .base import InputBackend


class FakeBackend(InputBackend):
    """`events` = lista de tuplas ("move_to", x, y), ("key_down", "z"), etc."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[Any, ...]] = []

    def _move_to(self, x: int, y: int) -> None:
        self.events.append(("move_to", x, y))

    def _mouse_down(self, button: str) -> None:
        self.events.append(("mouse_down", button))

    def _mouse_up(self, button: str) -> None:
        self.events.append(("mouse_up", button))

    def _key_down(self, key: str) -> None:
        self.events.append(("key_down", key))

    def _key_up(self, key: str) -> None:
        self.events.append(("key_up", key))

    def _scroll(self, clicks: int) -> None:
        self.events.append(("scroll", clicks))


class FakeWindow:
    """Ventana simulada (misma interfaz que `GameWindow` para el input). No toca el sistema."""

    def __init__(self, client_rect: Rect = Rect(8, 31, 1858, 1057),
                 ready: bool | Callable[[], bool] = True, hwnd: int = 1) -> None:
        self._rect = client_rect
        self.ready = ready          # bool o funcion sin argumentos
        self.hwnd = hwnd
        self.ensure_ready_calls = 0
        self.foreground: bool | Callable[[], bool] = True   # bool o funcion: simula perdida de foco
        self.last_capture_rect: Rect | None = None

    def is_foreground(self) -> bool:
        return bool(self.foreground() if callable(self.foreground) else self.foreground)

    def move_window(self, client_rect: Rect) -> None:
        """Simula que el usuario movio/redimensiono la ventana."""
        self._rect = client_rect

    def client_rect_on_screen(self) -> Rect:
        return self._rect

    def ensure_ready(self, timeout: float | None = None,
                     should_stop: Callable[[], bool] | None = None) -> bool:
        self.ensure_ready_calls += 1
        return bool(self.ready() if callable(self.ready) else self.ready)

    def client_to_screen(self, x: int, y: int, strict: bool = True) -> tuple[int, int]:
        return geometry.client_to_screen(x, y, self._rect, strict)

    def screen_to_client(self, x: int, y: int) -> tuple[int, int]:
        return geometry.screen_to_client(x, y, self._rect)

    def capture(self, region: Rect | None = None) -> np.ndarray:
        """Imagen negra del tamano del cliente (solo para simulaciones)."""
        self.last_capture_rect = self._rect
        return np.zeros((self._rect.h, self._rect.w, 3), dtype=np.uint8)
