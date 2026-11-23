"""Ejecutable del bot de farmeo.

Uso: .venv/Scripts/python.exe run_bot.py [--observar] [--max-minutos N]
  --observar: percibe y decide con el juego a la vista, pero NO manda teclas ni clics (solo loguea lo que haria).
F12 o el mouse en la esquina superior izquierda cortan; F11 pausa. El bot no resuelve el captcha: suena la alarma.
La muerte y "no puede volver a la zona" terminan la corrida y dejan la alarma sonando hasta F12 (o alarm.max_seconds).
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Any, Callable

from bot.alarm import Alarm
from bot.bot import Bot
from bot.config import ConfigError, load_config, resolve_path
from bot.spawns import spawn_map_from_config
from bot.control import BotControl, PanicStop
from bot.input.fake import FakeBackend
from bot.input.game_input import GameInput, InputBlockedError

logger = logging.getLogger("run_bot")

ALARM_POLL_S = 0.2   # tras la muerte, cada cuanto se mira si la alarma sigue o se pidio parar
OBSERVE_POLL_S = 0.25   # en --observar, cada cuanto se mira si el juego esta al frente
ALARM_END_REASONS = ("muerte", "no puede volver a la zona", "no puede llegar al centro")   # terminan con la alarma sonando


class _LoggingBackend(FakeBackend):
    """Backend de --observar: no manda nada, loguea cada accion que el bot haria."""

    def _note(self) -> None:
        logger.info("[observar] haria: %s", " ".join(str(v) for v in self.events[-1]))

    def _move_to(self, x: int, y: int) -> None:
        super()._move_to(x, y)
        self._note()

    def _mouse_down(self, button: str) -> None:
        super()._mouse_down(button)
        self._note()

    def _mouse_up(self, button: str) -> None:
        super()._mouse_up(button)
        self._note()

    def _key_down(self, key: str) -> None:
        super()._key_down(key)
        self._note()

    def _key_up(self, key: str) -> None:
        super()._key_up(key)
        self._note()

    def _scroll(self, clicks: int) -> None:
        super()._scroll(clicks)
        self._note()


class PassiveWindow:
    """Ventana para --observar: delega todo en la real, pero `ensure_ready` NO activa nada (ni ALT ni foco): solo
    espera, cancelable, a que el juego ya este al frente y sin nada encima."""

    def __init__(self, window: Any, poll_s: float = OBSERVE_POLL_S) -> None:
        self._w, self._poll, self._warned = window, poll_s, False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._w, name)

    def _ready(self) -> bool:
        w = self._w
        minimized = getattr(w, "is_minimized", lambda: False)()
        unobstructed = getattr(w, "is_unobstructed", lambda: True)()
        return not minimized and bool(w.is_foreground()) and unobstructed

    def ensure_ready(self, timeout: float | None = None, should_stop: Callable[[], bool] | None = None) -> bool:
        start = time.monotonic()
        while not self._ready():
            if not self._warned:
                print("MODO OBSERVAR: no toco el juego. Ponelo al frente (sin nada encima) para que el bot lo vea.")
                self._warned = True
            if (should_stop is not None and should_stop()) or (timeout is not None and time.monotonic() - start >= timeout):
                return False
            time.sleep(self._poll)
        return True


def verify_templates(cfg: dict) -> None:
    """Falla al arrancar, con un mensaje claro, si falta alguna plantilla (no en medio del loop)."""
    from bot.vision.digits import get_glyphs
    from bot.vision.items import load_owner_template, load_yang_template
    from bot.vision.metin import load_label_templates, slugify
    from bot.vision.panel import load_name_templates

    load_label_templates(cfg)
    panel = cfg["vision"]["panel"]
    names = load_name_templates(panel)
    for n in cfg["target_metin_names"]:
        if slugify(n) not in names:
            raise FileNotFoundError(
                f"Falta la plantilla del panel para {n!r}: {resolve_path(panel['name_templates_dir']) / (slugify(n) + '.png')} "
                "(tools/make_panel_name_template.py). Sin ella el bot no puede confirmar que selecciono el metin.")
    load_yang_template(cfg)
    load_owner_template(cfg)
    for section in ("coords", "panel"):
        if not get_glyphs(cfg["vision"]["glyphs"][section]).chars:
            raise FileNotFoundError(f"No hay plantillas de caracteres en {cfg['vision']['glyphs'][section]['dir']} "
                                    "(tools/make_glyph_templates.py)")
    v = cfg["vision"]
    files = [w["template"] for w in v["ui_windows"]["windows"]]
    files += [w["anchor2"]["template"] for w in v["ui_windows"]["windows"] if "anchor2" in w]
    files += [a["template"] for w in v["ui_windows"]["windows"] for a in w.get("alt", [])]
    files += [v["captcha"]["footer"]["template"], v["captcha"]["keypad"]["template"],
              v["death"]["here_template"], v["death"]["city_template"]]
    missing = [f for f in files if not Path(resolve_path(f)).is_file()]
    if missing:
        raise FileNotFoundError("Faltan plantillas: " + ", ".join(str(resolve_path(f)) for f in missing))


def _wait_alarm(control: BotControl, alarm: Any, reason: str) -> None:
    """Tras la muerte o si no puede volver a la zona: la alarma sigue sonando hasta F12 (PanicStop) o hasta su max_seconds."""
    print(f"Alarma sonando ({reason}). F12 (o la esquina) la corta.")
    while alarm.is_active:
        control.sleep(ALARM_POLL_S)


def main(argv: list[str] | None = None, *, cfg: dict | None = None, window: Any = None, backend: Any = None,
         control: BotControl | None = None, alarm: Any = None, bot_kwargs: dict | None = None) -> int:
    """Devuelve el codigo de salida. Los argumentos con nombre permiten inyectar dependencias (tests)."""
    ap = argparse.ArgumentParser(description="Bot de farmeo (F12/esquina cortan, F11 pausa).")
    ap.add_argument("--observar", action="store_true", help="percibe y decide sin mandar input (loguea lo que haria)")
    ap.add_argument("--max-minutos", type=float, default=None, help="termina solo a los N minutos (no corta mientras hay un captcha en pantalla: espera a que se resuelva)")
    ap.add_argument("--config", default="config.json")
    args = ap.parse_args(argv)

    if cfg is None:
        try:
            cfg = load_config(args.config)
        except ConfigError as e:
            print(f"Config invalida: {e}")
            return 1
        from bot.log import setup_logging
        setup_logging(cfg)
    try:
        verify_templates(cfg)
    except FileNotFoundError as e:
        print(f"Falta una plantilla: {e}")
        return 1

    if window is None:
        from bot.capture import GameWindow, WindowNotFoundError
        try:
            window = GameWindow(cfg["window_title"], tuple(cfg["expected_client_size"]),
                                cfg["window"]["settle_ms"], cfg["window"]["unobstructed_poll_ms"])
            window.find()
        except WindowNotFoundError as e:
            print(f"No se encontro el juego: {e}")
            return 1
    control = control or BotControl(cfg)
    if backend is None:
        if args.observar:
            backend = _LoggingBackend()
        else:
            from bot.input.pydirect import PyDirectInputBackend
            backend = PyDirectInputBackend(cfg)
    alarm = alarm or Alarm(cfg)
    if args.observar:
        window = PassiveWindow(window)   # observar no manda input: tampoco activa la ventana (ALT / SetForegroundWindow)
    gi = GameInput(window, backend, control, cfg)
    if not args.observar:
        try:
            gi.startup_check()
        except InputBlockedError as e:
            print(e)
            return 1
    kwargs = dict(bot_kwargs or {})
    if not args.observar:   # sentidos de Q/E y A/D: se determinan una vez y se reusan en la proxima corrida
        from bot.keynav import SensesStore
        kwargs.setdefault("senses", SensesStore(resolve_path(cfg["navigation"]["keyboard"]["senses_file"])))
    if args.observar and cfg["spawns"]["enabled"] and "spawn_map" not in kwargs:   # observar lee el mapa pero NO lo escribe
        kwargs["spawn_map"] = spawn_map_from_config(cfg["spawns"], read_only=True)
    bot = Bot(cfg, window, gi, control, alarm, **kwargs)

    control.add_stop_callback(gi.release_all)
    control.add_stop_callback(alarm.stop)
    control.start()
    died = False
    try:
        print("MODO OBSERVAR: no se manda nada, solo se loguea lo que el bot haria." if args.observar
              else "El bot va a mandar teclas y clics REALES al juego.")
        if cfg["debug"]["enabled"]:
            print(f"MODO DEBUG: imagenes anotadas en {resolve_path(cfg['debug']['dir'])} (carpeta run_<fecha_hora>, "
                  f"hasta {cfg['debug']['max_images']} imagenes).")
        print(f"F12 o el mouse en la esquina superior izquierda cortan; F11 pausa/reanuda. "
              f"{'Tope: %g min. ' % args.max_minutos if args.max_minutos else ''}El bot no resuelve el captcha: suena la alarma.")
        control.countdown(on_tick=lambda n: print(f"Arrancando en {n}..."))
        stats = bot.run(max_minutes=args.max_minutos)
        print("\n" + stats.summary())
        died = stats.end_reason in ALARM_END_REASONS
        if died:
            _wait_alarm(control, alarm, stats.end_reason)
        return 1 if stats.end_reason == "juego cerrado" else 0
    except PanicStop:
        print(f"\nCortado por pánico ({cfg['safety']['panic_key'].upper()} o esquina)")
        print(bot.stats.summary())
        return 0
    finally:
        control.mark_finished()
        gi.release_all()
        if not died:
            alarm.stop()
        control.close()


if __name__ == "__main__":
    sys.exit(main())
