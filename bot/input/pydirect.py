"""Backend con pydirectinput (scan codes via SendInput). Rueda con SendInput propio (ctypes)."""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

try:
    import pydirectinput
except ImportError:  # no-Windows (solo para poder importar el modulo)
    pydirectinput = None  # type: ignore[assignment]

from .base import InputBackend, parse_keys

logger = logging.getLogger(__name__)

# Alias de nombres comunes -> nombre de pydirectinput (lado izquierdo para los modificadores).
KEY_ALIASES = {
    "ctrl": "ctrlleft", "control": "ctrlleft", "shift": "shiftleft", "alt": "altleft",
    "esc": "escape", "return": "enter", "del": "delete", "ins": "insert",
    "pgup": "pageup", "pgdn": "pagedown", "spacebar": "space",
}

MOUSEEVENTF_WHEEL = 0x0800
INPUT_MOUSE = 0
WHEEL_DELTA = 120   # un "paso" de la rueda


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("mi", _MOUSEINPUT)]


def _user32():
    """user32 (los tests lo reemplazan para no mandar nada)."""
    return ctypes.windll.user32


def send_wheel(clicks: int) -> None:
    """Un evento de rueda por paso (clicks > 0 = hacia adelante/arriba)."""
    sign = 1 if clicks > 0 else -1
    for _ in range(abs(clicks)):
        # mouseData es DWORD: un delta negativo se pasa en complemento a dos.
        inp = _INPUT(INPUT_MOUSE, _MOUSEINPUT(0, 0, (sign * WHEEL_DELTA) & 0xFFFFFFFF, MOUSEEVENTF_WHEEL, 0, 0))
        if _user32().SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT)) != 1:
            raise OSError("SendInput (rueda) fallo")


def normalize_key(key: str) -> str:
    """Nombre de tecla segun pydirectinput ("ctrl" -> "ctrlleft", "esc" -> "escape")."""
    k = key.strip().lower()
    return KEY_ALIASES.get(k, k)


class PyDirectInputBackend(InputBackend):
    """Input real. No usar en tests: usa `FakeBackend` o un pydirectinput simulado."""

    def __init__(self, cfg: dict) -> None:
        super().__init__()
        if pydirectinput is None:
            raise RuntimeError("pydirectinput no esta disponible")
        pydirectinput.PAUSE = 0          # sin pausa automatica: los tiempos los maneja el bot
        pydirectinput.FAILSAFE = False   # el panico es nuestro (F12 / esquina)
        self._validate_keys(cfg)

    @staticmethod
    def _validate_keys(cfg: dict) -> None:
        """Todas las teclas de controls y safety deben existir en el mapeo de pydirectinput."""
        specs = [(f"controls.{k}", v) for k, v in cfg["controls"].items()]
        specs += [(f"safety.{k}", cfg["safety"][k]) for k in ("panic_key", "pause_key")]
        for name, spec in specs:
            for k in parse_keys(spec):
                if normalize_key(k) not in pydirectinput.KEYBOARD_MAPPING:
                    raise ValueError(f"{name}: la tecla {k!r} (de {spec!r}) no existe en pydirectinput")

    def _check_key(self, key: str) -> str:
        k = normalize_key(key)
        if k not in pydirectinput.KEYBOARD_MAPPING:
            raise ValueError(f"tecla desconocida: {key!r}")
        return k

    def _move_to(self, x: int, y: int) -> None:
        pydirectinput.moveTo(x, y)

    def _mouse_down(self, button: str) -> None:
        pydirectinput.mouseDown(button=button)

    def _mouse_up(self, button: str) -> None:
        pydirectinput.mouseUp(button=button)

    def _key_down(self, key: str) -> None:
        pydirectinput.keyDown(self._check_key(key))

    def _key_up(self, key: str) -> None:
        pydirectinput.keyUp(self._check_key(key))

    def _scroll(self, clicks: int) -> None:
        if clicks:
            send_wheel(clicks)
