"""Esperas cortas y PRECISAS para los toques de tecla de la camara (T): la espera normal (`BotControl.sleep`, que usa
`Condition.wait`) en Windows salta de a ~15 ms, y `GameInput.hold` la parte en tramos de 50 ms: un toque de 120 ms podia
durar 120-165 ms y el angulo final del reinicio no se repetia. Aca se mide con `time.perf_counter`, se duerme hasta
`SPIN_S` antes del final (con el temporizador del sistema a 1 ms) y el resto es espera activa.

`_clock` / `_sleep` son alias del modulo (los tests los parchean)."""
from __future__ import annotations

import contextlib
import logging
import sys
import time
from typing import Iterator

logger = logging.getLogger(__name__)

_clock = time.perf_counter
_sleep = time.sleep
SPIN_S = 0.002   # los ultimos 2 ms se esperan activamente (sin dormir): el sleep se puede pasar ~1 ms


@contextlib.contextmanager
def fine_timer() -> Iterator[None]:
    """En Windows sube la resolucion del temporizador del sistema a 1 ms mientras dura (timeBeginPeriod/timeEndPeriod)."""
    winmm = None
    if sys.platform == "win32":
        try:
            import ctypes
            winmm = ctypes.WinDLL("winmm")
            winmm.timeBeginPeriod(1)
        except Exception:  # noqa: BLE001
            logger.debug("No se pudo subir la resolucion del temporizador (timeBeginPeriod)")
            winmm = None
    try:
        yield
    finally:
        if winmm is not None:
            try:
                winmm.timeEndPeriod(1)
            except Exception:  # noqa: BLE001
                pass


def wait_until(deadline: float) -> None:
    """Espera hasta que `_clock()` llegue a `deadline` (nunca vuelve antes): duerme hasta SPIN_S antes y despues gira."""
    while True:
        left = deadline - _clock()
        if left <= 0:
            return
        if left > SPIN_S:
            _sleep(left - SPIN_S)
