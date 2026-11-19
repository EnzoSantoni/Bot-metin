"""Interfaz de los backends de input. Todo en coordenadas de PANTALLA."""
from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

_sleep = time.sleep   # alias del modulo (los tests lo parchean)

DEFAULT_HOLD_S = 0.05   # tiempo que se mantiene una tecla/boton si no se indica otro

# Nombres de tecla validos (mapeo de pydirectinput + alias que normaliza pydirect.py).
# Lista estatica para validar la config sin importar pydirectinput.
VALID_KEYS = frozenset(
    list("abcdefghijklmnopqrstuvwxyz0123456789") + [
        "'", ",", "-", ".", "/", ";", "=", "[", "\\", "]", "`",
        "add", "alt", "altleft", "altright", "apps", "backspace", "capslock", "ctrl", "ctrlleft",
        "ctrlright", "decimal", "del", "delete", "divide", "down", "end", "enter", "esc", "escape",
        "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
        "home", "insert", "left", "multiply", "numlock", "pagedown", "pageup", "pause",
        "printscreen", "prntscrn", "prtsc", "prtscr", "return", "right", "scrolllock",
        "shift", "shiftleft", "shiftright", "space", "subtract", "tab", "up",
        "win", "winleft", "winright",
        # alias de pydirect.KEY_ALIASES
        "control", "ins", "pgup", "pgdn", "spacebar",
    ])


def parse_keys(spec: str) -> list[str]:
    """"ctrl+g" -> ["ctrl", "g"]. Minusculas, sin espacios; lanza ValueError si queda vacio."""
    keys = [k.strip().lower() for k in spec.split("+")]
    if not spec.strip() or any(not k for k in keys):
        raise ValueError(f"combinacion de teclas invalida: {spec!r}")
    return keys


class InputBackend(ABC):
    """Mouse y teclado. Lleva la cuenta de lo apretado para que `release_all()` lo suelte siempre.

    Las subclases implementan solo las primitivas `_move_to`, `_mouse_down`, `_mouse_up`,
    `_key_down`, `_key_up` y `_scroll`.
    """

    def __init__(self) -> None:
        self._keys_down: list[str] = []
        self._buttons_down: list[str] = []
        # Protege las listas Y las primitivas de apretar/soltar: anotar + mandar es atomico, asi
        # release_all (desde el hilo del panico) no puede intercalarse entre las dos cosas y dejar
        # una tecla apretada en Windows sin anotar. Las primitivas (SendInput) son instantaneas.
        # RLock: key_up/mouse_up se llaman tambien desde release_all con el lock tomado.
        self._lock = threading.RLock()

    # --- primitivas del backend ---
    @abstractmethod
    def _move_to(self, x: int, y: int) -> None: ...

    @abstractmethod
    def _mouse_down(self, button: str) -> None: ...

    @abstractmethod
    def _mouse_up(self, button: str) -> None: ...

    @abstractmethod
    def _key_down(self, key: str) -> None: ...

    @abstractmethod
    def _key_up(self, key: str) -> None: ...

    @abstractmethod
    def _scroll(self, clicks: int) -> None: ...

    # --- API publica ---
    def move_to(self, x: int, y: int) -> None:
        self._move_to(int(x), int(y))

    def mouse_down(self, button: str = "left") -> None:
        with self._lock:
            if button not in self._buttons_down:
                self._buttons_down.append(button)   # se anota antes: si falla a medias, release_all lo suelta
            self._mouse_down(button)

    def mouse_up(self, button: str = "left") -> None:
        with self._lock:
            try:
                self._mouse_up(button)
            finally:
                self._forget(self._buttons_down, button)

    def key_down(self, key: str) -> None:
        with self._lock:
            if key not in self._keys_down:
                self._keys_down.append(key)
            self._key_down(key)

    def key_up(self, key: str) -> None:
        with self._lock:
            try:
                self._key_up(key)
            finally:
                self._forget(self._keys_down, key)

    def _forget(self, items: list[str], item: str) -> None:
        with self._lock:
            if item in items:
                items.remove(item)

    def click(self, x: int, y: int, button: str = "left", hold_s: float = DEFAULT_HOLD_S) -> None:
        self.move_to(x, y)
        try:
            self.mouse_down(button)
            _sleep(hold_s)
        finally:
            self.mouse_up(button)

    def press(self, key: str, hold_s: float = DEFAULT_HOLD_S) -> None:
        try:
            self.key_down(key)
            _sleep(hold_s)
        finally:
            self.key_up(key)

    def hotkey(self, keys: list[str], hold_s: float = DEFAULT_HOLD_S) -> None:
        """Aprieta en orden, mantiene `hold_s` y suelta en orden inverso."""
        pressed: list[str] = []
        try:
            for k in keys:
                pressed.append(k)
                self.key_down(k)
            _sleep(hold_s)
        finally:
            for k in reversed(pressed):
                try:
                    self.key_up(k)
                except Exception:  # noqa: BLE001
                    logger.exception("No se pudo soltar %r", k)

    def scroll(self, clicks: int) -> None:
        self._scroll(int(clicks))

    def release_all(self) -> None:
        """Suelta todas las teclas y botones apretados. Nunca lanza: loguea y sigue."""
        with self._lock:   # todo bajo el lock: nadie puede apretar algo nuevo a mitad de camino
            keys, buttons = list(self._keys_down), list(self._buttons_down)
            for k in reversed(keys):
                try:
                    self.key_up(k)
                except Exception:  # noqa: BLE001
                    logger.exception("release_all: no se pudo soltar la tecla %r", k)
                    self._forget(self._keys_down, k)
            for b in reversed(buttons):
                try:
                    self.mouse_up(b)
                except Exception:  # noqa: BLE001
                    logger.exception("release_all: no se pudo soltar el boton %r", b)
                    self._forget(self._buttons_down, b)

    @property
    def pressed(self) -> tuple[list[str], list[str]]:
        """(teclas, botones) apretados ahora."""
        with self._lock:
            return list(self._keys_down), list(self._buttons_down)
