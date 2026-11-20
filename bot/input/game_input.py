"""Acciones del juego: unico lugar que manda input al juego, siempre en coordenadas de CLIENTE.

Antes de cada accion: `control.check()` (panico/pausa) y `window.ensure_ready()`. Si algo falla
(PanicStop u otra excepcion) se sueltan todas las teclas y botones y se relanza.
"""
from __future__ import annotations

import logging
import math
import random
from contextlib import contextmanager
from typing import Any, Iterator

from bot.capture import elevation_mismatch
from bot.control import BotControl, PanicStop
from bot.input.base import InputBackend, parse_keys
from bot.vision.metin import player_point
from bot.vision.screen_mask import ScreenMask

logger = logging.getLogger(__name__)

JITTER_TRIES = 10   # intentos de variacion aleatoria antes de usar el punto original
HOLD_STEP_S = 0.05   # `hold` se parte en tramos de este tamano para detectar pausa/foco perdido
CAMERA_ACTIONS = {"left": "camera_left", "right": "camera_right"}
MOVE_ACTIONS = {"up": "move_up", "down": "move_down", "left": "move_left", "right": "move_right"}


def _noop() -> None:
    pass


class InputBlockedError(Exception):
    """Windows bloquearia el input del bot (juego elevado y Python no)."""


class GameInput:
    """Clics, teclas, camara y zoom hacia el juego, con seguridad en cada paso."""

    def __init__(self, window: Any, backend: InputBackend, control: BotControl, cfg: dict,
                 rng: random.Random | None = None) -> None:
        self.window = window
        self.backend = backend
        self.control = control
        self.cfg = cfg
        self.rng = rng or random.Random()
        self._in = cfg["input"]
        self._controls = cfg["controls"]
        control.add_pause_callback(backend.release_all, _noop)   # en pausa no queda nada apretado

    # --- helpers ---
    def startup_check(self) -> None:
        """Llamar una vez al arrancar: error claro si el juego es admin y Python no."""
        if elevation_mismatch(self.window.hwnd):
            raise InputBlockedError(
                "El juego corre como administrador y Python no: Windows bloquea los clics y teclas "
                "del bot. Abrí la terminal como administrador.")

    def release_all(self) -> None:
        """Suelta todas las teclas y botones apretados (delega al backend; nunca lanza)."""
        self.backend.release_all()

    def _rand_s(self, key: str) -> float:
        lo, hi = self._in[key]
        return self.rng.uniform(lo, hi) / 1000

    def _delay(self) -> None:
        self.control.sleep(self._rand_s("delay_ms"))

    @contextmanager
    def _action(self) -> Iterator[bool]:
        """Chequeos previos; ante cualquier excepcion suelta todo y relanza. Entrega True si se puede.

        `ensure_ready` se llama SIN timeout a proposito: si el juego no esta al frente se espera a
        que Enzo lo ponga (la espera se corta con F12 o la esquina del mouse).
        """
        try:
            ready = self._wait_ready()
            if not ready:
                logger.warning("El juego no esta listo (foco/tapado): no se manda la accion")
            self.control.check()   # un panico durante la espera no deja salir ninguna tecla
            yield ready
        except BaseException:
            self.backend.release_all()
            raise

    def _wait_ready(self) -> bool:
        """check() + ensure_ready() + check(). Lanza PanicStop si se paro."""
        self.control.check()
        ready = bool(self.window.ensure_ready(should_stop=lambda: self.control.is_stopped))
        if not ready and self.control.is_stopped:
            raise PanicStop()
        self.control.check()
        return ready

    def _window_unchanged(self) -> bool:
        """False (con WARNING) si no hubo captura o la ventana se movio/cambio de tamano desde la ultima."""
        last = getattr(self.window, "last_capture_rect", None)
        if last is None or last != self.window.client_rect_on_screen():
            logger.warning("No se manda el input: la ventana se movió o cambió de tamaño desde la captura")
            return False
        return True

    def _key_spec(self, action: str) -> list[str]:
        if action not in self._controls:
            raise KeyError(f"accion desconocida: {action!r} (no esta en controls)")
        return parse_keys(self._controls[action])

    def _jitter(self, x: int, y: int, mask: ScreenMask) -> tuple[int, int]:
        r = float(self._in["click_jitter_px"])
        if r <= 0:
            return x, y
        for _ in range(JITTER_TRIES):
            dx, dy = self.rng.uniform(-r, r), self.rng.uniform(-r, r)
            if math.hypot(dx, dy) > r:
                continue
            nx, ny = x + round(dx), y + round(dy)
            if mask.allows(nx, ny):
                return nx, ny
        return x, y

    # --- acciones ---
    def click_at(self, x: int, y: int, mask: ScreenMask | None, button: str = "left") -> bool:
        """Clic en (x, y) de cliente. `mask` (de la captura actual) es obligatorio."""
        if mask is None:
            raise ValueError("click_at requiere la mascara de la captura actual")
        with self._action() as ready:
            if not ready or not self._window_unchanged():
                return False
            if not mask.allows(x, y):
                logger.warning("Clic rechazado en (%d, %d): zona no permitida", x, y)
                return False
            x, y = self._jitter(x, y, mask)
            try:
                sx, sy = self.window.client_to_screen(x, y, strict=True)
            except ValueError:
                logger.warning("Clic rechazado en (%d, %d): fuera del cliente", x, y)
                return False
            self.control.check()
            self.backend.move_to(sx, sy)
            self.control.sleep(self._rand_s("move_settle_ms"))
            self.control.check()
            self.backend.click(sx, sy, button, hold_s=self._rand_s("key_hold_ms"))
            self._delay()
            return True

    def hover_at(self, x: int, y: int, mask: ScreenMask | None) -> bool:
        """Solo mueve el mouse a (x, y) de cliente (sin clic), con el mismo chequeo de mascara y jitter que `click_at`.
        Sirve para que el juego muestre la etiqueta de lo que hay debajo."""
        if mask is None:
            raise ValueError("hover_at requiere la mascara de la captura actual")
        with self._action() as ready:
            if not ready or not self._window_unchanged():
                return False
            if not mask.allows(x, y):
                logger.warning("Hover rechazado en (%d, %d): zona no permitida", x, y)
                return False
            x, y = self._jitter(x, y, mask)
            try:
                sx, sy = self.window.client_to_screen(x, y, strict=True)
            except ValueError:
                logger.warning("Hover rechazado en (%d, %d): fuera del cliente", x, y)
                return False
            self.control.check()
            self.backend.move_to(sx, sy)
            self.control.sleep(self._rand_s("move_settle_ms"))
            return True

    def press(self, action: str, hold_s: float | None = None) -> bool:
        """Aprieta la tecla (o combinacion) de `controls[action]`."""
        keys = self._key_spec(action)
        with self._action() as ready:
            if not ready:
                return False
            hold = self._rand_s("key_hold_ms") if hold_s is None else hold_s
            if len(keys) == 1:
                self.backend.press(keys[0], hold)
            else:
                self.backend.hotkey(keys, hold)
            self._delay()
            return True

    def hold(self, action: str, seconds: float) -> bool:
        """Mantiene la tecla `seconds`; siempre la suelta."""
        keys = self._key_spec(action)
        if len(keys) != 1:
            raise ValueError(f"hold solo admite una tecla, {action!r} es una combinacion")
        return self._hold_down(keys, seconds)

    def hold_keys(self, actions: list[str], seconds: float) -> bool:
        """Mantiene a la vez 1 o 2 teclas de movimiento (acciones de `controls`, p. ej. ["move_up", "move_right"]) durante
        `seconds`; siempre las suelta todas."""
        if not 1 <= len(actions) <= 2 or len(set(actions)) != len(actions):
            raise ValueError(f"hold_keys admite 1 o 2 acciones distintas: {actions!r}")
        keys: list[str] = []
        for a in actions:
            spec = self._key_spec(a)
            if len(spec) != 1:
                raise ValueError(f"hold_keys solo admite teclas simples, {a!r} es una combinacion")
            keys.append(spec[0])
        return self._hold_down(keys, seconds)

    def _hold_down(self, keys: list[str], seconds: float) -> bool:
        """Nucleo de `hold` / `hold_keys`: key_down de todas, espera en pasos de HOLD_STEP_S (pausa/foco) y suelta todas."""
        with self._action() as ready:
            if not ready:
                return False
            remaining = float(seconds)
            while True:   # al menos una pulsacion, aunque `seconds` sea 0
                try:
                    for key in keys:
                        self.backend.key_down(key)
                    while True:
                        step = max(0.0, min(HOLD_STEP_S, remaining))
                        self.control.sleep(step)
                        remaining -= step
                        if remaining <= 0:
                            break
                        if any(key not in self.backend.pressed[0] for key in keys):
                            logger.debug("hold: una tecla se solto (pausa); sigo al reanudar")
                            break
                        if not self.window.is_foreground():
                            logger.warning("hold: el juego perdio el foco; suelto %s y espero", keys)
                            break
                finally:
                    for key in reversed(keys):
                        try:
                            self.backend.key_up(key)
                        except Exception:  # noqa: BLE001
                            logger.exception("No se pudo soltar %r", key)
                if remaining <= 0:
                    break
                if not self._wait_ready():
                    return False
            self._delay()
            return True

    def rotate_camera(self, direction: str, seconds: float) -> bool:
        """Gira la camara con Q/E (segun `controls`)."""
        if direction not in CAMERA_ACTIONS:
            raise ValueError(f"direccion de camara invalida: {direction!r}")
        return self.hold(CAMERA_ACTIONS[direction], seconds)

    def move_key(self, direction: str, seconds: float) -> bool:
        """Camina con W/A/S/D (segun `controls`)."""
        if direction not in MOVE_ACTIONS:
            raise ValueError(f"direccion de movimiento invalida: {direction!r}")
        return self.hold(MOVE_ACTIONS[direction], seconds)

    def zoom(self, clicks: int, mask: ScreenMask | None) -> bool:
        """Rueda del mouse sobre el personaje (sin clic). `mask` es obligatorio: el punto tiene que estar permitido."""
        if mask is None:
            raise ValueError("zoom requiere la mascara de la captura actual")
        with self._action() as ready:
            if not ready or not self._window_unchanged():
                return False
            rect = self.window.client_rect_on_screen()
            px, py = player_point((rect.h, rect.w), self.cfg)
            if not mask.allows(px, py):
                logger.warning("Zoom omitido: el punto (%d, %d) no esta permitido", px, py)
                return False
            try:
                sx, sy = self.window.client_to_screen(px, py, strict=True)
            except ValueError:
                logger.warning("Zoom omitido: (%d, %d) fuera del cliente", px, py)
                return False
            self.control.check()
            self.backend.move_to(sx, sy)
            self.control.sleep(self._rand_s("move_settle_ms"))
            self.control.check()
            self.backend.scroll(int(clicks))
            self._delay()
            return True
