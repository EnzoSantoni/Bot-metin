"""Control de seguridad: panico (F12 / mouse en la esquina), pausa (F11) y cuenta regresiva.

IMPORTANTE: si el juego corre como administrador y Python no, Windows puede no entregar F11/F12
al bot mientras el juego tiene el foco (ver `elevation_mismatch` en capture.py). Ejecuta el bot
como administrador. El panico por esquina del mouse no depende de eso.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

HARD_KILL_RELEASE_JOIN_S = 0.5   # en el corte forzado, cuanto se espera a los callbacks de parada


class PanicStop(Exception):
    """Se pidio parar (panico): el loop tiene que terminar."""


def _default_cursor_pos() -> tuple[int, int]:
    import win32api
    return win32api.GetCursorPos()


class BotControl:
    """Panico, pausa y esperas cancelables. Seguro entre hilos."""

    def __init__(self, cfg: dict, *, keyboard: Any = None,
                 cursor_pos: Callable[[], tuple[int, int]] | None = None,
                 monotonic: Callable[[], float] = time.monotonic,
                 exit_fn: Callable[[int], None] = os._exit) -> None:
        s = cfg["safety"]
        self._panic_key = str(s["panic_key"])
        self._pause_key = str(s["pause_key"])
        self._corner_px = float(s["corner_px"])
        self._poll_s = float(s["corner_poll_ms"]) / 1000
        self._countdown_s = float(s["countdown_s"])
        self._hard_kill_s = float(s["hard_kill_after_s"])
        if keyboard is None:
            import keyboard as _kb
            keyboard = _kb
        self._kb = keyboard
        self._cursor_pos = cursor_pos or _default_cursor_pos
        self._monotonic = monotonic
        self._exit_fn = exit_fn

        self._cond = threading.Condition()
        self._stopped = False
        self._paused = False
        self._finished = False
        self._callbacks: list[Callable[[], None]] = []
        self._pause_callbacks: list[tuple[Callable[[], None], Callable[[], None]]] = []
        self._hotkeys: list[Any] = []
        self._closed = threading.Event()      # para el vigia
        self._watcher: threading.Thread | None = None
        self._kill_timer: threading.Timer | None = None

    # --- estado ---
    @property
    def is_stopped(self) -> bool:
        return self._stopped

    @property
    def is_paused(self) -> bool:
        return self._paused

    def add_stop_callback(self, fn: Callable[[], None]) -> None:
        """`fn()` se llama al pararse (ej. `backend.release_all`)."""
        self._callbacks.append(fn)

    def add_pause_callback(self, on_pause: Callable[[], None], on_resume: Callable[[], None]) -> None:
        """`on_pause()` / `on_resume()` se llaman al entrar / salir de la pausa (hilo de la hotkey)."""
        self._pause_callbacks.append((on_pause, on_resume))

    # --- ciclo de vida ---
    def start(self) -> None:
        """Registra F12/F11 y lanza el vigia de la esquina."""
        if self._watcher is not None:
            return
        self._closed.clear()
        self._hotkeys.append(self._kb.add_hotkey(self._panic_key, lambda: self.panic(self._panic_key.upper())))
        self._hotkeys.append(self._kb.add_hotkey(self._pause_key, self.toggle_pause))
        self._watcher = threading.Thread(target=self._watch, name="panic-watch", daemon=True)
        self._watcher.start()

    def close(self) -> None:
        """Desregistra las hotkeys y para el vigia. Idempotente."""
        self._closed.set()
        with self._cond:
            t, self._kill_timer = self._kill_timer, None
        if t is not None:
            t.cancel()
        hotkeys, self._hotkeys = self._hotkeys, []
        for h in hotkeys:
            try:
                self._kb.remove_hotkey(h)
            except Exception:  # noqa: BLE001
                logger.exception("No se pudo desregistrar una hotkey")
        w, self._watcher = self._watcher, None
        if w is not None and w is not threading.current_thread():
            w.join(timeout=1.0)

    def _watch(self) -> None:
        while not self._closed.wait(self._poll_s):
            try:
                x, y = self._cursor_pos()
            except Exception:  # noqa: BLE001
                logger.exception("No se pudo leer el cursor")
                continue
            if x <= self._corner_px and y <= self._corner_px:
                self.panic("esquina")
                return

    # --- panico / pausa ---
    def panic(self, reason: str) -> None:
        """Para todo: evento de parada, callbacks y corte forzado si el loop no termina."""
        with self._cond:
            if self._stopped:
                return
            self._stopped = True
            self._cond.notify_all()
        logger.warning("PANICO (%s): deteniendo el bot", reason)
        with self._cond:   # el corte se arma ANTES de los callbacks: si uno se cuelga, igual ocurre
            if not self._finished and not self._closed.is_set():
                t = threading.Timer(self._hard_kill_s, self._hard_kill)
                t.daemon = True
                self._kill_timer = t
                t.start()
        self._run_stop_callbacks()

    def _run_stop_callbacks(self) -> None:
        for fn in list(self._callbacks):
            try:
                fn()
            except Exception:  # noqa: BLE001
                logger.exception("Fallo un callback de parada")

    def _hard_kill(self) -> None:
        logger.critical("El bot no termino %.1f s despues del panico: se corta el proceso", self._hard_kill_s)
        releaser = threading.Thread(target=self._run_stop_callbacks, name="hard-kill-release", daemon=True)
        releaser.start()
        releaser.join(HARD_KILL_RELEASE_JOIN_S)   # intenta soltar todo, pero sin colgarse
        self._exit_fn(1)

    def mark_finished(self) -> None:
        """El loop termino: cancela el corte forzado."""
        with self._cond:
            self._finished = True
            t, self._kill_timer = self._kill_timer, None
        if t is not None:
            t.cancel()

    def toggle_pause(self) -> None:
        with self._cond:
            self._paused = not self._paused
            paused = self._paused
            self._cond.notify_all()
        if paused:
            logger.info("Pausado (%s para seguir)", self._pause_key.upper())
        else:
            logger.info("Reanudado")
        for on_pause, on_resume in list(self._pause_callbacks):
            try:
                (on_pause if paused else on_resume)()
            except Exception:  # noqa: BLE001
                logger.exception("Fallo un callback de pausa")

    # --- puntos de control ---
    def check(self) -> None:
        """Lanza PanicStop si se paro; si esta en pausa, bloquea hasta reanudar."""
        with self._cond:
            while True:
                if self._stopped:
                    raise PanicStop()
                if not self._paused:
                    return
                self._cond.wait()

    def sleep(self, seconds: float) -> None:
        """Espera `seconds` (sin contar la pausa). Con panico lanza PanicStop al instante."""
        remaining = float(seconds)
        with self._cond:
            while True:
                if self._stopped:
                    raise PanicStop()
                if self._paused:
                    self._cond.wait()
                    continue
                if remaining <= 0:
                    return
                t0 = self._monotonic()
                self._cond.wait(remaining)
                remaining -= self._monotonic() - t0   # si llego una pausa, solo cuenta lo ya esperado

    def countdown(self, seconds: float | None = None, on_tick: Callable[[int], None] | None = None) -> None:
        """Cuenta regresiva de un tick por segundo; cancelable con el panico."""
        n = int(self._countdown_s if seconds is None else seconds)
        while n > 0:
            self.check()
            if on_tick is not None:
                on_tick(n)
            else:
                logger.info("Arrancando en %d...", n)
            self.sleep(1.0)
            n -= 1
        self.check()
