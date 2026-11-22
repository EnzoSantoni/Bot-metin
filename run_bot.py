"""Ejecutable del bot de farmeo.

Uso: .venv/Scripts/python.exe run_bot.py [--observar] [--max-minutos N] [--parar-a HH:MM] [--parar-tras-metin N]
  --observar: percibe y decide con el juego a la vista, pero NO manda teclas ni clics (solo loguea lo que haria).
F12 o el mouse en la esquina superior izquierda cortan; F11 pausa (tambien se pausa solo si tocas el mouse o el teclado:
safety.auto_pause_on_user_input; no con --observar). El bot no resuelve el captcha: suena la alarma.
La muerte y "no puede volver a la zona" terminan la corrida y dejan la alarma sonando hasta F12 (o alarm.max_seconds).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from bot.alarm import Alarm
from bot.bot import Bot
from bot.config import ConfigError, load_config, resolve_path
from bot.history import append_session, build_record
from bot.item_catalog import ItemCatalog
from bot.spawns import spawn_map_from_config
from bot.control import BotControl, PanicStop
from bot.input.fake import FakeBackend
from bot.input.game_input import GameInput, InputBlockedError
from bot.profiles import build_config
from bot.schedule import next_stop, parse_hhmm
from bot.user_input import UserInputDetector, WindowsInputHook, vk_codes
from bot.ui.controller import CMD_PAUSE, CMD_STOP, END_TAG, STATUS_TAG

logger = logging.getLogger("run_bot")

UI_MAX_CANDIDATES = 40   # tope de candidatos en cada @@STATUS
UI_STATUS_PERIOD_S = 0.5   # en --ui, cada cuanto se emite la linea @@STATUS

ALARM_POLL_S = 0.2   # tras la muerte, cada cuanto se mira si la alarma sigue o se pidio parar
OBSERVE_POLL_S = 0.25   # en --observar, cada cuanto se mira si el juego esta al frente
ALARM_END_REASONS = ("muerte", "no puede volver a la zona", "no puede llegar al centro")   # terminan con la alarma sonando


def _hhmm_arg(text: str) -> str:
    """Tipo de argparse para --parar-a: valida HH:MM y lo devuelve normalizado."""
    try:
        h, m = parse_hhmm(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from e
    return f"{h:02d}:{m:02d}"


def _nonneg_int_arg(text: str) -> int:
    """Tipo de argparse para --parar-tras-metin: entero >= 0."""
    try:
        n = int(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"tiene que ser un entero >= 0, no {text!r}") from e
    if n < 0:
        raise argparse.ArgumentTypeError("tiene que ser un entero >= 0 (0 = sin tope)")
    return n


def resolve_schedule(cfg: dict, args: Any, now: datetime) -> tuple[datetime | None, int]:
    """(momento de la parada por hora, tope de metin) con la linea de comandos pisando la config. Loguea cuando es manana."""
    sc = cfg["schedule"]
    text = args.parar_a if args.parar_a is not None else sc["stop_at"]
    n = args.parar_tras_metin if args.parar_tras_metin is not None else sc["stop_after_metins"]
    stop_at = None
    if str(text).strip():
        stop_at, tomorrow = next_stop(str(text), now)
        logger.info("Parada programada a las %s%s (%s)", stop_at.strftime("%H:%M"),
                    " de MANANA: esa hora ya paso hoy" if tomorrow else "", stop_at.strftime("%Y-%m-%d %H:%M"))
    if n > 0:
        logger.info("Parada programada tras romper %d metin", n)
    return stop_at, int(n)


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


def log_profiles(cfg: dict) -> None:
    """Loguea los perfiles usados y los avisos de config.json que pisa valores de un perfil."""
    prof = cfg.get("_profiles") or {}
    if not prof.get("servidor"):
        logger.info("Perfiles: sin carpeta profiles/ (DEFAULTS + config.json)")
        return
    logger.info("Perfiles: servidor=%s, personaje=%s, zona=%s", prof["servidor"], prof["personaje"], prof["zona"])
    for o in prof.get("overrides", []):
        logger.warning(o["message"])


def _wait_alarm(control: BotControl, alarm: Any, reason: str) -> None:
    """Tras la muerte o si no puede volver a la zona: la alarma sigue sonando hasta F12 (PanicStop) o hasta su max_seconds."""
    print(f"Alarma sonando ({reason}). F12 (o la esquina) la corta.")
    while alarm.is_active:
        control.sleep(ALARM_POLL_S)


class UiBridge:
    """Protocolo con la interfaz (--ui): emite @@STATUS cada ~0.5 s y @@END al terminar, y lee `pause` / `stop` por stdin.
    Solo lee `bot.state` / `bot.stats`. Si stdin se cierra (la interfaz murio), detiene el bot como `stop`."""

    def __init__(self, bot: Any, control: BotControl, cfg: dict, stdin: Any, out: Any = None,
                 period_s: float = UI_STATUS_PERIOD_S) -> None:
        self.bot, self.control, self.cfg, self._stdin = bot, control, cfg, stdin
        self._out = out if out is not None else sys.stdout
        self._period = period_s
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._end_sent = False
        self.phase = "countdown"
        self.countdown = 0
        self.stop_requested = False   # el usuario pidio `stop` desde la interfaz (para el motivo del historial)

    def emit(self, tag: str, payload: dict) -> None:
        line = tag + json.dumps(payload, ensure_ascii=False) + "\n"
        with self._lock:
            try:
                self._out.write(line)
                self._out.flush()
            except (OSError, ValueError):
                pass

    def _position(self) -> tuple[list[float] | None, str | None]:
        """([x, y], estado de la zona) del personaje, o (None, None) si no hay lectura. Nunca lanza."""
        try:
            pos = self.bot.tracker.position
            zs = self.bot.zone.status(pos) if pos is not None else None
            return (None if pos is None else [round(float(pos[0]), 1), round(float(pos[1]), 1)],
                    None if zs is None else zs.value)
        except Exception:  # noqa: BLE001 - un bot falso sin tracker no debe romper el estado
            return None, None

    def status(self) -> dict:
        st, state = self.bot.stats, self.bot.state
        pos, zone_status = self._position()
        prof = self.cfg.get("_profiles") or {}
        return {"state": getattr(state, "value", str(state)), "phase": self.phase, "countdown": self.countdown,
                "metins": st.metins, "items": st.items, "captchas": st.captchas, "unstucks": st.unstucks,
                "elapsed_s": round(st.elapsed_s, 1), "paused": self.control.is_paused,
                "auto_paused": bool(getattr(self.control, "auto_paused", False)),
                "skills_cast": getattr(st, "skills_cast", 0), "farm_mode": getattr(st, "farm_mode", "metin"),
                "surrounded": getattr(st, "surrounded", 0),
                "deaths": getattr(st, "deaths", 0), "revives": getattr(st, "revives", 0),
                "coords_total": getattr(getattr(st, "coords", None), "total", 0),
                "coords_failed": getattr(getattr(st, "coords", None), "failed", 0),
                "lures": getattr(st, "lures", 0), "fight_s": round(getattr(st, "fight_s", 0.0), 1),
                "pos": pos, "zone_status": zone_status, "candidates": self._candidates(), "items_by_type": self._items_by_type(), "metins_by_type": dict(getattr(st, "metins_by_type", {})),
                "skills": self.bot.skills.snapshot() if hasattr(getattr(self.bot, "skills", None), "snapshot") else [],
                "profiles": {k: prof.get(k) for k in ("servidor", "personaje", "zona")}}

    def _candidates(self) -> list[list]:
        """Lista de candidatos de la vuelta de busqueda: [[x, y, tipo], ...] (coords del juego). Nunca lanza."""
        try:
            return [[round(float(c.xy[0]), 1), round(float(c.xy[1]), 1), str(c.kind)]
                    for c in list(self.bot.cands.items)[:UI_MAX_CANDIDATES]]
        except Exception:  # noqa: BLE001 - un bot falso sin lista no debe romper el estado
            return []

    def _items_by_type(self) -> list[dict]:
        """Objetos recogidos en la sesion por tipo: [{id, name, count}] con el nombre actual del catalogo."""
        cat = getattr(self.bot, "catalog", None)
        by = dict(getattr(self.bot.stats, "items_by_type", {}))
        return [{"id": i, "name": cat.name_of(i) if cat is not None else i, "count": n} for i, n in by.items()]

    def start(self) -> None:
        threading.Thread(target=self._status_loop, name="ui-status", daemon=True).start()
        threading.Thread(target=self._stdin_loop, name="ui-stdin", daemon=True).start()

    def _status_loop(self) -> None:
        while not self._done.is_set():
            self.emit(STATUS_TAG, self.status())
            self._done.wait(self._period)

    def _stdin_loop(self) -> None:
        try:
            for line in self._stdin:
                cmd = line.strip().lower()
                if cmd == CMD_PAUSE:
                    self.control.toggle_pause()
                elif cmd == CMD_STOP:
                    self.stop_requested = True
                    self.control.panic("interfaz")
        except (OSError, ValueError):
            pass
        if not self._done.is_set():   # stdin cerrado sin que el bot haya terminado: la interfaz murio
            self.control.panic("interfaz cerrada")

    def set_countdown(self, n: int) -> None:
        self.phase, self.countdown = ("countdown", n) if n > 0 else ("running", 0)

    def end(self, reason: str, summary: str) -> None:
        """Emite @@END una sola vez y deja de emitir @@STATUS."""
        if self._end_sent:
            return
        self._end_sent = True
        self.phase = "ended"
        self._done.set()
        self.emit(END_TAG, {"reason": reason, "summary": summary, **{k: v for k, v in self.status().items()
                                                                       if k in ("metins", "items", "captchas", "unstucks", "elapsed_s")}})


def _record_history(cfg: dict, bot: Any, inicio: datetime, reason: str) -> None:
    """Agrega la corrida al historial (todas las salidas pasan por el finally de `main`). Nunca lanza."""
    h = cfg["history"]
    if not h["enabled"]:
        return
    try:
        prof = cfg.get("_profiles") or {}
        append_session(resolve_path(h["file"]), build_record(bot.stats, inicio, datetime.now(), prof, reason))
    except Exception:  # noqa: BLE001 - el historial nunca debe impedir el cierre limpio
        logger.exception("No se pudo registrar la sesion en el historial")


def main(argv: list[str] | None = None, *, cfg: dict | None = None, window: Any = None, backend: Any = None,
         control: BotControl | None = None, alarm: Any = None, bot_kwargs: dict | None = None,
         stdin: Any = None, input_hook: Callable[..., Any] | None = None) -> int:
    """Devuelve el codigo de salida. Los argumentos con nombre permiten inyectar dependencias (tests)."""
    ap = argparse.ArgumentParser(description="Bot de farmeo (F12/esquina cortan, F11 pausa).")
    ap.add_argument("--observar", action="store_true", help="percibe y decide sin mandar input (loguea lo que haria)")
    ap.add_argument("--max-minutos", type=float, default=None, help="termina solo a los N minutos (no corta mientras hay un captcha en pantalla: espera a que se resuelva)")
    ap.add_argument("--parar-a", type=_hhmm_arg, default=None, metavar="HH:MM",
                    help="termina solo a esa hora local (si ya paso hoy, es manana); pisa schedule.stop_at. No corta un captcha ni RECOGER")
    ap.add_argument("--parar-tras-metin", type=_nonneg_int_arg, default=None, metavar="N",
                    help="termina solo tras romper N metin (0 = sin tope); pisa schedule.stop_after_metins")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--servidor", default=None, help="perfil de servidor (default: el de config.json o Guabina)")
    ap.add_argument("--personaje", default=None, help="perfil de personaje (default: el de config.json o Farfarfa)")
    ap.add_argument("--zona", default=None, help="perfil de zona (default: el de config.json o Ciudad 1)")
    dbg = ap.add_mutually_exclusive_group()
    dbg.add_argument("--debug", dest="debug", action="store_true", default=None, help="activa debug.enabled solo en esta corrida")
    dbg.add_argument("--sin-debug", dest="debug", action="store_false", help="desactiva debug.enabled solo en esta corrida")
    ap.add_argument("--sin-cuenta-regresiva", action="store_true", help="omite la cuenta regresiva inicial (no recomendado)")
    ap.add_argument("--ui", action="store_true", help="modo interfaz: emite @@STATUS/@@END por stdout y lee pause/stop por stdin")
    args = ap.parse_args(argv)

    if cfg is None:
        try:
            cfg = build_config(args.config, args.servidor, args.personaje, args.zona, warn=False)
        except ConfigError as e:
            print(f"Config invalida: {e}")
            return 1
        from bot.log import setup_logging
        setup_logging(cfg)
        log_profiles(cfg)
    if args.debug is not None:
        cfg["debug"]["enabled"] = args.debug   # solo esta corrida: config.json no se toca
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
    real_input = backend is None and not args.observar   # solo con el backend real tiene sentido vigilar el input fisico
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
    if not args.observar:   # marca de "a pie": la proxima corrida se sube si esta se corto a pie (F12)
        kwargs.setdefault("mount_marker", resolve_path(cfg["log"]["dir"]) / "a_pie.flag")
    if not args.observar:   # sentidos de Q/E y A/D: se determinan una vez y se reusan en la proxima corrida
        from bot.keynav import SensesStore
        kwargs.setdefault("senses", SensesStore(resolve_path(cfg["navigation"]["keyboard"]["senses_file"])))
    if args.observar and cfg["spawns"]["enabled"] and "spawn_map" not in kwargs:   # observar lee el mapa pero NO lo escribe
        kwargs["spawn_map"] = spawn_map_from_config(cfg["spawns"], read_only=True)
    if args.observar and "catalog" not in kwargs:   # observar no cuenta objetos: catalogo desactivado
        kwargs["catalog"] = ItemCatalog(None)
    bot = Bot(cfg, window, gi, control, alarm, **kwargs)

    control.add_stop_callback(gi.release_all)
    control.add_stop_callback(alarm.stop)
    # Pausa automatica por input del usuario: con --observar no hay input del bot para distinguir, asi que se desactiva.
    s = cfg["safety"]
    detector = UserInputDetector(cfg, enabled=not args.observar,
                                 ignored_keys=vk_codes(str(s["pause_key"])) | vk_codes(str(s["panic_key"])))
    if hasattr(control, "attach_user_input"):
        control.attach_user_input(detector)
    hook: Any = None
    if detector.enabled and (input_hook is not None or real_input):
        if input_hook is not None:
            hook = input_hook(detector, control.auto_pause)
        else:
            hook = WindowsInputHook(detector, control.auto_pause, ignore_pids={os.getppid()} if args.ui else set())
    control.start()
    died = False
    ui = UiBridge(bot, control, cfg, stdin if stdin is not None else sys.stdin) if args.ui else None
    end_reason = hist_reason = "error"
    run_start: datetime | None = None
    if ui is not None:
        ui.start()
    try:
        stop_at, stop_after = resolve_schedule(cfg, args, datetime.now())
        print("MODO OBSERVAR: no se manda nada, solo se loguea lo que el bot haria." if args.observar
              else "El bot va a mandar teclas y clics REALES al juego.")
        if cfg["debug"]["enabled"]:
            print(f"MODO DEBUG: imagenes anotadas en {resolve_path(cfg['debug']['dir'])} (carpeta run_<fecha_hora>, "
                  f"hasta {cfg['debug']['max_images']} imagenes).")
        print(f"F12 o el mouse en la esquina superior izquierda cortan; F11 pausa/reanuda. "
              f"{'Tope: %g min. ' % args.max_minutos if args.max_minutos else ''}El bot no resuelve el captcha: suena la alarma.")
        if not args.sin_cuenta_regresiva:
            def _tick(n: int) -> None:
                print(f"Arrancando en {n}...")
                if ui is not None:
                    ui.set_countdown(n)
            control.countdown(on_tick=_tick)
        if ui is not None:
            ui.set_countdown(0)
        detector.arm()   # desde aca se vigila el input del usuario (no durante la cuenta regresiva)
        if hook is not None:
            hook.start()
        run_start = datetime.now()
        stats = bot.run(max_minutes=args.max_minutos, stop_at=stop_at, stop_after_metins=stop_after)
        print("\n" + stats.summary())
        end_reason = hist_reason = stats.end_reason or "fin"
        if ui is not None:
            ui.end(end_reason, stats.summary())
        died = stats.end_reason in ALARM_END_REASONS
        if died:
            _wait_alarm(control, alarm, stats.end_reason)
        return 1 if stats.end_reason == "juego cerrado" else 0
    except PanicStop:
        print(f"\nCortado por pánico ({cfg['safety']['panic_key'].upper()} o esquina)")
        print(bot.stats.summary())
        end_reason = "panico"
        hist_reason = "interfaz" if ui is not None and ui.stop_requested else "panico"
        return 0
    finally:
        if not args.observar and run_start is not None:   # observar no es farmeo real: no se registra
            _record_history(cfg, bot, run_start, hist_reason)
        if ui is not None:
            ui.end(end_reason, bot.stats.summary())
        if hook is not None:
            hook.close()
        control.mark_finished()
        gi.release_all()
        if not died:
            alarm.stop()
        control.close()


if __name__ == "__main__":
    sys.exit(main())
