"""Logica pura de la interfaz (sin tkinter): linea de comando, protocolo con run_bot.py --ui, estado de los botones,
ajustes guardados y manejo del subproceso. Testeable sin pantalla."""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from bot.config import PROJECT_ROOT, ConfigError
from bot.fileutil import atomic_write_text
from bot.profiles import KINDS, build_config, list_profiles
from bot.schedule import parse_hhmm

logger = logging.getLogger(__name__)

STATUS_TAG = "@@STATUS "
END_TAG = "@@END "
ASK_TAG = "@@ASK "                    # pregunta de calibrate.py --ui (se responde por stdin)
CMD_PAUSE = "pause"
CMD_STOP = "stop"
READER_JOIN_S = 1.0                   # espera maxima al hilo lector tras terminar el subproceso
STOP_GRACE_S = 5.0                    # tras `stop`, cuanto se espera antes de terminar el proceso a la fuerza
SETTINGS_FILE = PROJECT_ROOT / "ui_settings.json"
RUN_BOT = PROJECT_ROOT / "run_bot.py"
_LOG_RE = re.compile(r"\[(DEBUG|INFO|WARNING|ERROR|CRITICAL)\]")
_GEOMETRY_RE = re.compile(r"\d+x\d+[+-]-?\d+[+-]-?\d+")


# ----------------------------------------------------------------------------- opciones

@dataclass
class UiOptions:
    """Opciones elegidas en la ventana (se guardan en ui_settings.json)."""
    servidor: str = ""
    personaje: str = ""
    zona: str = ""
    max_minutes: float = 0.0          # 0 = sin limite
    debug: bool = False
    observar: bool = False
    always_on_top: bool = False
    geometry: str = ""                # "ANCHOxALTO+X+Y" de tkinter
    stop_at: str = ""                 # "HH:MM" local; "" = no parar a una hora
    stop_after_metins: int = 0        # 0 = sin tope de metin


def validate_options(opts: UiOptions) -> list[str]:
    """Lista de errores (vacia si esta todo bien)."""
    errors = []
    mm = opts.max_minutes
    if not isinstance(mm, (int, float)) or isinstance(mm, bool) or mm != mm or mm < 0 or mm == float("inf"):
        errors.append("El tiempo máximo tiene que ser 0 (sin límite) o mayor que 0.")
    if opts.stop_at.strip():
        try:
            parse_hhmm(opts.stop_at)
        except ValueError:
            errors.append("La hora de parada tiene que tener el formato HH:MM (ej. 23:30) o estar vacía.")
    n = opts.stop_after_metins
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        errors.append("Parar tras N metin tiene que ser un entero 0 (no parar) o mayor.")
    for k in KINDS:
        if not getattr(opts, k):
            errors.append(f"Falta elegir el perfil de {k}.")
    return errors


def parse_stop_metins(text: str) -> int:
    """Texto de 'Parar tras N metin' -> entero >= 0 (vacio = 0). ValueError si no es valido."""
    t = str(text).strip()
    if not t:
        return 0
    try:
        n = int(t)
    except ValueError as e:
        raise ValueError("Parar tras N metin tiene que ser un número entero (0 = no parar).") from e
    if n < 0:
        raise ValueError("Parar tras N metin no puede ser negativo (0 = no parar).")
    return n


def normalize_hhmm(text: str) -> str:
    """'7:05' -> '07:05'; vacio -> ''. ValueError si el formato es invalido."""
    if not str(text).strip():
        return ""
    h, m = parse_hhmm(text)
    return f"{h:02d}:{m:02d}"


def schedule_text(opts: UiOptions, metins: int) -> str:
    """Texto de las estadisticas en vivo: 'Para a las HH:MM' y/o 'Faltan N metin' ('' si no hay parada programada)."""
    parts = []
    if opts.stop_at.strip():
        parts.append(f"Para a las {opts.stop_at.strip()}")
    if opts.stop_after_metins > 0:
        parts.append(f"Faltan {max(0, opts.stop_after_metins - metins)} metin")
    return " | ".join(parts)


def parse_minutes(text: str) -> float:
    """Texto del campo de minutos -> float >= 0. ValueError si no es un numero valido."""
    try:
        v = float(str(text).strip().replace(",", "."))
    except ValueError as e:
        raise ValueError("El tiempo máximo tiene que ser un número (0 = sin límite).") from e
    if v != v or v < 0 or v == float("inf"):
        raise ValueError("El tiempo máximo tiene que ser 0 (sin límite) o mayor que 0.")
    return v


def gui_python(executable: str | None = None) -> str:
    """Interprete para el subproceso: si la GUI corre con pythonw.exe (sin stdout), usa el python.exe vecino."""
    exe = executable or sys.executable
    p = Path(exe)
    if p.name.lower() == "pythonw.exe" and p.with_name("python.exe").exists():
        return str(p.with_name("python.exe"))
    return exe


def build_command(opts: UiOptions, *, python: str | None = None, script: str | Path = RUN_BOT,
                  config: str | None = None) -> list[str]:
    """Linea de comando de `run_bot.py --ui` segun las opciones (debug se manda siempre, explicito)."""
    cmd = [python or gui_python(), "-u", str(script), "--ui"]
    if config:
        cmd += ["--config", config]
    for k in KINDS:
        v = getattr(opts, k)
        if v:
            cmd += [f"--{k}", v]
    if opts.max_minutes and opts.max_minutes > 0:
        cmd += ["--max-minutos", f"{opts.max_minutes:g}"]
    if opts.stop_at.strip():
        cmd += ["--parar-a", opts.stop_at.strip()]
    if opts.stop_after_metins and opts.stop_after_metins > 0:
        cmd += ["--parar-tras-metin", str(int(opts.stop_after_metins))]
    cmd.append("--debug" if opts.debug else "--sin-debug")
    if opts.observar:
        cmd.append("--observar")
    return cmd


# ----------------------------------------------------------------------------- protocolo

@dataclass
class Status:
    """Estado en vivo informado por el bot."""
    state: str = ""
    phase: str = "running"            # countdown | running | ended
    countdown: int = 0
    metins: int = 0
    items: int = 0
    captchas: int = 0
    unstucks: int = 0
    elapsed_s: float = 0.0
    paused: bool = False
    auto_paused: bool = False         # la pausa la puso el bot por input del usuario
    profiles: dict = field(default_factory=dict)
    skills_cast: int = 0
    farm_mode: str = "metin"          # "metin" | "bichos"
    lures: int = 0                    # senuelos usados (modo bichos)
    deaths: int = 0                   # muertes de la corrida
    revives: int = 0                  # revives logrados
    surrounded: int = 0               # ESPACIO por estar rodeado sin avanzar
    fight_s: float = 0.0              # segundos peleando (modo bichos)
    coords_total: int = 0             # lecturas de coordenadas intentadas
    coords_failed: int = 0            # lecturas sin resultado
    skills: list = field(default_factory=list)   # [{name, key, remaining_s}]
    items_by_type: list = field(default_factory=list)    # [{id, name, count}]
    metins_by_type: dict = field(default_factory=dict)   # {slug: cantidad}
    pos: tuple[float, float] | None = None   # posicion del personaje en el juego (None = sin dato)
    zone_status: str = ""             # "inside" | "tolerance" | "outside" | "" (sin dato)


@dataclass
class ParsedLine:
    kind: str                         # status | end | log
    text: str = ""
    level: str = "INFO"
    status: Status | None = None
    end: dict | None = None


def _as_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _as_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def parse_skills_status(raw: Any) -> list[dict]:
    """`skills` de @@STATUS -> [{name, key, remaining_s}] (ignora entradas mal formadas)."""
    out = []
    for e in raw if isinstance(raw, list) else []:
        if isinstance(e, dict):
            out.append({"name": str(e.get("name", "")), "key": str(e.get("key", "")),
                        "remaining_s": _as_float(e.get("remaining_s"))})
    return out


def parse_items_status(raw: Any) -> list[dict]:
    """`items_by_type` de @@STATUS -> [{id, name, count}] (ignora entradas mal formadas o con cantidad <= 0)."""
    out = []
    for e in raw if isinstance(raw, list) else []:
        if isinstance(e, dict) and _as_int(e.get("count")) > 0:
            out.append({"id": str(e.get("id", "")), "name": str(e.get("name") or e.get("id") or ""), "count": _as_int(e.get("count"))})
    return out


def parse_metins_status(raw: Any) -> dict[str, int]:
    """`metins_by_type` de @@STATUS -> {slug: cantidad} (ignora lo mal formado)."""
    return {str(k): _as_int(v) for k, v in (raw.items() if isinstance(raw, dict) else []) if _as_int(v) > 0}


ZONE_STATUSES = ("inside", "tolerance", "outside")


def parse_pos_status(raw: Any) -> tuple[float, float] | None:
    """`pos` de @@STATUS -> (x, y) o None si no son dos numeros finitos."""
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or abs(v) == float("inf") for v in raw):
        return None
    return float(raw[0]), float(raw[1])


def parse_zone_status(raw: Any) -> str:
    """`zone_status` de @@STATUS -> uno de ZONE_STATUSES o '' si viene otra cosa."""
    return raw if isinstance(raw, str) and raw in ZONE_STATUSES else ""


def status_from_dict(d: dict) -> Status:
    prof = d.get("profiles")
    return Status(state=str(d.get("state", "")), phase=str(d.get("phase", "running")), countdown=_as_int(d.get("countdown")),
                  metins=_as_int(d.get("metins")), items=_as_int(d.get("items")), captchas=_as_int(d.get("captchas")),
                  unstucks=_as_int(d.get("unstucks")), elapsed_s=_as_float(d.get("elapsed_s")),
                  paused=bool(d.get("paused", False)), auto_paused=bool(d.get("auto_paused", False)), profiles=prof if isinstance(prof, dict) else {},
                  skills_cast=_as_int(d.get("skills_cast")),
                  farm_mode=str(d.get("farm_mode") or "metin"), lures=_as_int(d.get("lures")), deaths=_as_int(d.get("deaths")),
                  revives=_as_int(d.get("revives")), surrounded=_as_int(d.get("surrounded")), coords_total=_as_int(d.get("coords_total")),
                  coords_failed=_as_int(d.get("coords_failed")),
                  fight_s=_as_float(d.get("fight_s")), skills=parse_skills_status(d.get("skills")),
                  items_by_type=parse_items_status(d.get("items_by_type")),
                  metins_by_type=parse_metins_status(d.get("metins_by_type")),
                  pos=parse_pos_status(d.get("pos")), zone_status=parse_zone_status(d.get("zone_status")))


def log_level(text: str) -> str:
    """Nivel de una linea de log (formato bot.log) o INFO si no lo trae. Los errores de arranque cuentan como ERROR."""
    m = _LOG_RE.search(text)
    if m:
        return m.group(1)
    if text.startswith(("Traceback", "  File ", "Falta una plantilla", "Config invalida", "No se encontro")):
        return "ERROR"
    return "INFO"


def parse_line(line: str) -> ParsedLine:
    """Clasifica una linea del subproceso. Nunca lanza: una linea @@ mal formada se trata como log."""
    text = line.rstrip("\r\n")
    for tag, kind in ((STATUS_TAG, "status"), (END_TAG, "end")):
        if text.startswith(tag):
            try:
                d = json.loads(text[len(tag):])
                if not isinstance(d, dict):
                    raise ValueError("no es un objeto")
            except ValueError:
                return ParsedLine("log", text, "WARNING")
            return ParsedLine("status", text, status=status_from_dict(d)) if kind == "status" \
                else ParsedLine("end", text, end=d)
    return ParsedLine("log", text, log_level(text))


def tab_title(title: str, dirty: bool) -> str:
    """Titulo de una pestana con '*' si tiene cambios sin guardar."""
    return f"{title} *" if dirty else title


def paused_text(paused: bool, auto: bool) -> str:
    """Cartel de pausa de Control ('' si no esta pausado)."""
    if not paused:
        return ""
    return "PAUSADO (automático)" if auto else "PAUSADO"


def format_elapsed(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


# ----------------------------------------------------------------------------- botones

STOPPED, RUNNING, STOPPING = "detenido", "corriendo", "terminando"


@dataclass(frozen=True)
class Buttons:
    start: bool
    pause: bool
    stop: bool
    pause_label: str
    options: bool                     # se pueden tocar perfiles y opciones


def button_state(phase: str, paused: bool = False) -> Buttons:
    """Que botones estan habilitados segun el estado del proceso."""
    if phase == RUNNING:
        return Buttons(False, True, True, "Reanudar" if paused else "Pausar", False)
    if phase == STOPPING:
        return Buttons(False, False, False, "Pausar", False)
    return Buttons(True, False, False, "Pausar", True)


# ----------------------------------------------------------------------------- ajustes guardados

def available_profiles(root: str | Path | None = None) -> dict[str, list[str]]:
    return {k: list_profiles(k, root) for k in KINDS}


def load_settings(path: str | Path = SETTINGS_FILE, profiles: dict[str, list[str]] | None = None) -> UiOptions:
    """Lee ui_settings.json. Archivo roto/ausente -> valores por defecto; perfil inexistente -> el primero disponible."""
    data: dict = {}
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if isinstance(raw, dict):
            data = raw
    except (OSError, ValueError):
        pass
    opts = UiOptions()
    for k in KINDS:
        v = data.get(k)
        setattr(opts, k, v if isinstance(v, str) else "")
    mm = data.get("max_minutes")
    if isinstance(mm, (int, float)) and not isinstance(mm, bool) and 0 <= mm < float("inf"):
        opts.max_minutes = float(mm)
    if isinstance(data.get("stop_at"), str):
        try:
            opts.stop_at = normalize_hhmm(data["stop_at"])
        except ValueError:
            pass
    sm = data.get("stop_after_metins")
    if isinstance(sm, int) and not isinstance(sm, bool) and sm >= 0:
        opts.stop_after_metins = sm
    for k in ("debug", "observar", "always_on_top"):
        if isinstance(data.get(k), bool):
            setattr(opts, k, data[k])
    if isinstance(data.get("geometry"), str) and _GEOMETRY_RE.fullmatch(data["geometry"]):
        opts.geometry = data["geometry"]
    if profiles is not None:
        for k in KINDS:
            avail = profiles.get(k) or []
            if getattr(opts, k) not in avail:
                setattr(opts, k, avail[0] if avail else "")
    return opts


def save_settings(opts: UiOptions, path: str | Path = SETTINGS_FILE) -> bool:
    """Guarda las opciones. False (y log) si no se pudo escribir; nunca lanza."""
    try:
        atomic_write_text(path, json.dumps(asdict(opts), indent=2, ensure_ascii=False) + "\n")
        return True
    except OSError:
        logger.exception("No se pudo guardar %s", path)
        return False


def override_warnings(opts: UiOptions, config_path: str | Path = "config.json",
                      profiles_root_dir: str | Path | None = None) -> list[str]:
    """Avisos de 'config.json pisa X del perfil Y' para la seleccion actual (o el error de config, si lo hay)."""
    try:
        cfg = build_config(config_path, opts.servidor or None, opts.personaje or None, opts.zona or None,
                           profiles_root_dir=profiles_root_dir, warn=False)
    except ConfigError as e:
        return [f"Config inválida: {e}"]
    except Exception as e:  # noqa: BLE001 - la ventana no debe romperse por esto
        return [f"No se pudo leer la config: {e}"]
    return [o["message"] for o in (cfg.get("_profiles") or {}).get("overrides", [])]


def data_paths(config_path: str | Path = "config.json", profiles_root_dir: str | Path | None = None
               ) -> tuple[Path, Path, float]:
    """(carpeta del catalogo de objetos, archivo del historial, `items_catalog.match`) de la config. Si la config no
    se puede leer, los de DEFAULTS."""
    from bot.config import DEFAULTS, resolve_path
    try:
        cfg = build_config(config_path, profiles_root_dir=profiles_root_dir, warn=False)
    except Exception:  # noqa: BLE001 - la ventana no debe romperse por esto
        cfg = DEFAULTS
    return (resolve_path(cfg["items_catalog"]["dir"]), resolve_path(cfg["history"]["file"]),
            float(cfg["items_catalog"]["match"]))


def labels_dir(config_path: str | Path = "config.json", profiles_root_dir: str | Path | None = None) -> Path:
    """Carpeta de plantillas de etiqueta de metin de la config (la de DEFAULTS si la config no se puede leer)."""
    from bot.config import DEFAULTS, resolve_path
    try:
        cfg = build_config(config_path, profiles_root_dir=profiles_root_dir, warn=False)
    except Exception:  # noqa: BLE001 - la ventana no debe romperse por esto
        cfg = DEFAULTS
    return resolve_path(cfg["vision"]["metin"]["label_templates_dir"])


# ----------------------------------------------------------------------------- subproceso

class BotProcess:
    """Subproceso del bot: un hilo lee stdout y deja las lineas en una cola; `send` escribe comandos por stdin."""

    def __init__(self) -> None:
        self.lines: "queue.Queue[str | None]" = queue.Queue()   # None = el proceso cerro stdout (fin)
        self.proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def returncode(self) -> int | None:
        return None if self.proc is None else self.proc.poll()

    def start(self, cmd: list[str], cwd: str | Path = PROJECT_ROOT) -> None:
        if self.running:
            raise RuntimeError("el bot ya esta corriendo")
        self.lines = queue.Queue()
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(cmd, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                     bufsize=1, env=env, creationflags=flags)
        self._reader = threading.Thread(target=self._read, args=(self.proc, self.lines), name="bot-reader", daemon=True)
        self._reader.start()

    @staticmethod
    def _read(proc: subprocess.Popen, q: "queue.Queue[str | None]") -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                q.put(line)
        except (OSError, ValueError):
            pass
        finally:
            q.put(None)

    def send(self, command: str) -> bool:
        """Manda un comando (`pause` / `stop`). False si el proceso ya no lo recibe."""
        if not self.running or self.proc is None or self.proc.stdin is None:
            return False
        try:
            self.proc.stdin.write(command + "\n")
            self.proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def stop(self, grace_s: float = STOP_GRACE_S) -> bool:
        """`stop` y espera; si no termina en `grace_s`, lo mata. True si termino solo."""
        if self.proc is None:
            return True
        self.send(CMD_STOP)
        try:
            self.proc.wait(timeout=grace_s)
            self._close_pipes()
            return True
        except subprocess.TimeoutExpired:
            self.kill()
            return False

    def kill(self) -> None:
        """Mata el subproceso (si sigue vivo), espera al hilo lector con tope y cierra las tuberias."""
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                logger.warning("El subproceso no termino tras kill()")
        self._close_pipes()

    def _close_pipes(self) -> None:
        """Con el proceso muerto el lector llega a EOF: se lo espera un momento y se cierran stdin/stdout."""
        if self.proc is None or self.proc.poll() is None:
            return
        r = self._reader
        if r is not None and r.is_alive() and r is not threading.current_thread():
            r.join(timeout=READER_JOIN_S)
        for pipe in (self.proc.stdin, self.proc.stdout):
            try:
                if pipe is not None and (pipe is self.proc.stdin or not (r is not None and r.is_alive())):
                    pipe.close()
            except (OSError, ValueError):
                pass

    def drain(self, limit: int = 500) -> list[str | None]:
        """Lineas pendientes (sin bloquear). None marca el fin del flujo."""
        out: list[str | None] = []
        while len(out) < limit:
            try:
                out.append(self.lines.get_nowait())
            except queue.Empty:
                break
        return out

    def wait_closed(self, timeout: float = 5.0) -> None:
        """Espera a que el proceso termine (tests y cierre)."""
        if self.proc is not None:
            t0 = time.monotonic()
            while self.proc.poll() is None and time.monotonic() - t0 < timeout:
                time.sleep(0.02)
