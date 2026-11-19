"""Alarma sonora no bloqueante: un patron de beeps en un hilo daemon hasta stop() o max_seconds."""
from __future__ import annotations

import logging
import threading
import time

try:
    import winsound
except ImportError:  # no-Windows (solo para poder importar el modulo)
    winsound = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_monotonic = time.monotonic     # alias del modulo (los tests lo parchean sin tocar el reloj global)
_JOIN_TIMEOUT_S = 2.0           # espera maxima a que el hilo de la alarma termine en stop()


class Alarm:
    """`start(reason)` suena en segundo plano; `stop()` la corta. Idempotente."""

    def __init__(self, cfg: dict) -> None:
        a = cfg["alarm"]
        self._freq = int(a["freq"])
        self._beep_ms = int(a["beep_ms"])
        self._pause_ms = int(a["pause_ms"])
        self._max_seconds = float(a["max_seconds"])
        self._lock = threading.Lock()
        self._stop = threading.Event()   # evento de la corrida actual (uno nuevo por start)
        self._thread: threading.Thread | None = None

    @property
    def is_active(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def beep_once(self, stop: threading.Event | None = None) -> None:
        """Un beep (bloquea beep_ms). Si winsound.Beep falla, usa MessageBeep; nunca lanza."""
        try:
            winsound.Beep(self._freq, self._beep_ms)
        except Exception as e:  # noqa: BLE001
            logger.debug("winsound.Beep fallo (%s); uso MessageBeep", e)
            try:
                winsound.MessageBeep()
            except Exception:  # noqa: BLE001
                logger.debug("MessageBeep tambien fallo")
                (stop or self._stop).wait(self._beep_ms / 1000)

    def start(self, reason: str) -> None:
        """Empieza a sonar sin bloquear. No hace nada si ya esta sonando."""
        with self._lock:
            if self.is_active:
                return
            logger.warning("ALARMA: %s", reason)
            self._stop = threading.Event()      # nuevo por corrida: un hilo viejo nunca ve el de otro
            self._thread = threading.Thread(target=self._run, args=(self._stop,), name="alarm", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Corta la alarma y espera brevemente al hilo."""
        with self._lock:
            t, self._thread = self._thread, None
            was_active = t is not None and t.is_alive()
            self._stop.set()
        if t is not None and t is not threading.current_thread():
            t.join(timeout=_JOIN_TIMEOUT_S)
        if was_active:
            logger.info("Alarma detenida")

    def _run(self, stop: threading.Event) -> None:
        try:
            t0 = _monotonic()
            while not stop.is_set():
                if self._max_seconds > 0 and _monotonic() - t0 >= self._max_seconds:
                    logger.info("Alarma detenida (max_seconds=%s)", self._max_seconds)
                    break
                self.beep_once(stop)
                stop.wait(self._pause_ms / 1000)
        except Exception:  # noqa: BLE001
            logger.exception("Fallo el hilo de la alarma")
