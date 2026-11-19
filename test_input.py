"""Prueba de input en el juego: manda pasos anunciados para que verifiques que llegan.

Uso: .venv/Scripts/python.exe test_input.py [--solo teclas|mouse|camara] [--simular]
Con --simular usa un backend falso (imprime cada evento) y no necesita el juego abierto.
F12 o el mouse en la esquina superior izquierda cortan; F11 pausa.
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

from bot.config import load_config
from bot.control import BotControl, PanicStop
from bot.input.fake import FakeBackend, FakeWindow
from bot.input.game_input import GameInput, InputBlockedError
from bot.vision.metin import player_point
from bot.vision.screen_mask import build_screen_mask, find_ui_windows

logger = logging.getLogger(__name__)

# Tiempos de la prueba (s); los tests los reducen.
CAMERA_HOLD_S = 0.8
MOUNT_WAIT_S = 2.0
WALK_HOLD_S = 0.4
STEP_WAIT_S = 1.5
ZOOM_CLICKS = 3
# Desplazamientos (px de cliente) respecto del personaje donde se prueba el clic en el piso.
CLICK_OFFSETS = [(0, 140), (140, 0), (-140, 0), (0, -140), (110, 110), (-110, 110), (110, -110), (-110, -110)]

GROUPS = {"camara": [1, 2], "teclas": [3, 4, 5, 7], "mouse": [6]}


class _PrintingBackend(FakeBackend):
    """FakeBackend que imprime cada evento (para --simular)."""

    def _log(self) -> None:
        print("   [simulado]", *self.events[-1])

    def _move_to(self, x: int, y: int) -> None:
        super()._move_to(x, y)
        self._log()

    def _mouse_down(self, button: str) -> None:
        super()._mouse_down(button)
        self._log()

    def _mouse_up(self, button: str) -> None:
        super()._mouse_up(button)
        self._log()

    def _key_down(self, key: str) -> None:
        super()._key_down(key)
        self._log()

    def _key_up(self, key: str) -> None:
        super()._key_up(key)
        self._log()

    def _scroll(self, clicks: int) -> None:
        super()._scroll(clicks)
        self._log()


class _NoKeyboard:
    """Reemplazo de `keyboard` sin hotkeys reales."""

    def add_hotkey(self, key: str, cb: Any) -> object:
        return object()

    def remove_hotkey(self, handle: object) -> None:
        pass


def _announce(n: int, title: str, watch: str) -> None:
    print(f"\n[Paso {n}] {title}\n   Mira: {watch}")


def _step_camera(gi: GameInput) -> None:
    _announce(1, "Camara: Q 0.8 s y E 0.8 s", "la camara gira hacia un lado y vuelve")
    gi.rotate_camera("left", CAMERA_HOLD_S)
    gi.rotate_camera("right", CAMERA_HOLD_S)


def _capture_with_mask(gi: GameInput) -> tuple[Any, Any] | None:
    """Deja el juego listo, captura y arma la mascara (None si no se pudo)."""
    gi.control.check()
    if not gi.window.ensure_ready(should_stop=lambda: gi.control.is_stopped):
        print("   No se pudo poner el juego al frente: se saltea.")
        return None
    img = gi.window.capture()
    return img, build_screen_mask(img.shape, gi.cfg, extra=find_ui_windows(img, gi.cfg))


def _step_zoom(gi: GameInput) -> None:
    _announce(2, "Zoom: rueda -3 y +3", "la camara se aleja y se acerca")
    shot = _capture_with_mask(gi)
    if shot is None:
        return
    gi.zoom(-ZOOM_CLICKS, shot[1])
    gi.control.sleep(STEP_WAIT_S)
    shot = _capture_with_mask(gi)
    if shot is not None:
        gi.zoom(ZOOM_CLICKS, shot[1])


def _step_pickup(gi: GameInput) -> None:
    _announce(3, "Z (recoger)", "el personaje intenta recoger (si no hay objetos no pasa nada visible)")
    gi.press("pickup")


def _step_mount(gi: GameInput) -> None:
    _announce(4, "Ctrl+G dos veces (bajarse, 2 s, subirse)", "el personaje se baja y despues se sube de la montura")
    gi.press("mount")
    gi.control.sleep(MOUNT_WAIT_S)
    gi.press("mount")


def _step_walk(gi: GameInput) -> None:
    _announce(5, "W 0.4 s (caminar un poco)", "el personaje da unos pasos")
    gi.move_key("up", WALK_HOLD_S)


def _step_click(gi: GameInput) -> None:
    _announce(6, "Clic en el piso cerca del personaje", "el personaje camina hasta el punto clickeado")
    shot = _capture_with_mask(gi)
    if shot is None:
        return
    img, mask = shot
    px, py = player_point(img.shape, gi.cfg)
    for dx, dy in CLICK_OFFSETS:
        if mask.allows(px + dx, py + dy):
            print(f"   Clic en ({px + dx}, {py + dy}) de cliente (personaje en ({px}, {py})).")
            gi.click_at(px + dx, py + dy, mask)
            return
    print("   No hay un punto del piso permitido cerca del personaje: se saltea.")


def _step_deselect(gi: GameInput) -> None:
    _announce(7, "Escape (deseleccionar)", "si hay algo seleccionado, se deselecciona")
    gi.press("deselect")


STEPS = {1: _step_camera, 2: _step_zoom, 3: _step_pickup, 4: _step_mount,
         5: _step_walk, 6: _step_click, 7: _step_deselect}


def main(argv: list[str] | None = None, *, cfg: dict | None = None, control: BotControl | None = None,
         backend: Any = None, window: Any = None) -> int:
    """Devuelve el codigo de salida. Los argumentos con nombre permiten inyectar dependencias (tests)."""
    ap = argparse.ArgumentParser(description="Prueba de input en el juego (F12/esquina cortan, F11 pausa).")
    ap.add_argument("--solo", choices=sorted(GROUPS), default=None)
    ap.add_argument("--simular", action="store_true", help="backend falso, imprime los eventos")
    ap.add_argument("--config", default="config.json")
    args = ap.parse_args(argv)

    if cfg is None:
        cfg = load_config(args.config)
        from bot.log import setup_logging
        setup_logging(cfg)
    steps = GROUPS[args.solo] if args.solo else sorted(STEPS)

    if args.simular:
        window = window or FakeWindow()
        backend = backend or _PrintingBackend()
        control = control or BotControl(cfg, keyboard=_NoKeyboard(), cursor_pos=lambda: (10_000, 10_000))
    else:
        from bot.capture import GameWindow, WindowNotFoundError
        from bot.input.pydirect import PyDirectInputBackend
        try:
            window = GameWindow(cfg["window_title"], tuple(cfg["expected_client_size"]),
                                cfg["window"]["settle_ms"], cfg["window"]["unobstructed_poll_ms"])
            window.find()
        except WindowNotFoundError as e:
            print(f"No se encontro el juego: {e}")
            return 1
        control = BotControl(cfg)
        backend = PyDirectInputBackend(cfg)
    gi = GameInput(window, backend, control, cfg)
    try:
        if not args.simular:
            gi.startup_check()
    except InputBlockedError as e:
        print(e)
        return 1

    control.add_stop_callback(backend.release_all)
    control.start()
    try:
        print("SIMULACION: no se manda nada, solo se imprimen los eventos." if args.simular
              else "Esta prueba va a mandar teclas y clics REALES al juego.")
        print(f"Pasos: {steps}. F12 o el mouse en la esquina superior izquierda cortan; F11 pausa.")
        print("Deja el juego a la vista y no toques el mouse ni el teclado durante la prueba.")
        control.countdown(on_tick=lambda n: print(f"Arrancando en {n}..."))
        for n in steps:
            STEPS[n](gi)
            control.sleep(STEP_WAIT_S)
        print("\nListo. Si cada paso hizo lo anunciado, el input funciona.")
        return 0
    except PanicStop:
        print(f"Cortado por panico ({cfg['safety']['panic_key'].upper()} o esquina)")
        return 0
    except Exception as e:  # noqa: BLE001
        from bot.capture import WindowNotFoundError
        if isinstance(e, WindowNotFoundError):
            print(f"El juego se cerro o no se encuentra la ventana: {e}")
            return 1
        raise
    finally:
        control.mark_finished()
        backend.release_all()
        control.close()


if __name__ == "__main__":
    sys.exit(main())
