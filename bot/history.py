"""Historial de sesiones: una linea JSON por corrida en `data/historial.jsonl` (config `history`).

Los objetos se guardan por id del catalogo (`bot.item_catalog`): si Enzo renombra un tipo, el historial pasado
muestra el nombre nuevo. Todo es puro salvo `append_session` y `load_history` (IO tolerante a errores).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from bot.item_catalog import ItemCatalog

logger = logging.getLogger(__name__)

# Motivo de fin (stats.end_reason / run_bot) -> texto para la interfaz.
REASON_LABELS = {"tiempo": "tope de tiempo", "panico": "pánico (F12 o esquina)", "interfaz": "detenido desde la interfaz",
                 "juego cerrado": "juego cerrado", "muerte": "muerte", "error": "error",
                 "hora programada": "hora programada", "metin programados": "metin programados",
                 "no puede volver a la zona": "no pudo volver a la zona", "no puede llegar al centro": "no pudo llegar al centro"}


def reason_label(reason: str) -> str:
    return REASON_LABELS.get(reason, reason or "-")


def metin_label(slug: str) -> str:
    """'metin_de_oscuridad' -> 'Metin de oscuridad'."""
    text = slug.replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else slug


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _counts(raw: Any) -> dict[str, int]:
    """{clave: cantidad} tolerante (ignora entradas mal formadas o <= 0)."""
    out: dict[str, int] = {}
    for k, v in (raw.items() if isinstance(raw, dict) else []):
        n = _int(v)
        if n > 0:
            out[str(k)] = n
    return out


def build_record(stats: Any, inicio: datetime, fin: datetime, perfiles: dict, motivo: str) -> dict:
    """Linea del historial para una corrida terminada (`stats` = BotStats)."""
    return {"inicio": inicio.isoformat(timespec="seconds"), "fin": fin.isoformat(timespec="seconds"),
            "duracion_s": round(max(0.0, (fin - inicio).total_seconds()), 1),
            "perfiles": {k: perfiles.get(k) for k in ("servidor", "personaje", "zona")}, "motivo": motivo,
            "metins": int(stats.metins), "metins_por_tipo": dict(stats.metins_by_type),
            "objetos": int(stats.items), "objetos_por_tipo": dict(stats.items_by_type),
            "captchas": int(stats.captchas), "destrabes": int(stats.unstucks), "acercamientos": int(stats.approaches),
            "rodeado": int(getattr(stats, "surrounded", 0)),
            "habilidades": int(stats.skills_cast), "farm_mode": str(getattr(stats, "farm_mode", "metin")),
            "lures": int(getattr(stats, "lures", 0)),
            "muertes": int(getattr(stats, "deaths", 0)), "revives": int(getattr(stats, "revives", 0))}


def append_session(path: Path | None, record: dict) -> bool:
    """Agrega la linea al archivo (crea la carpeta). False (y WARNING) si no se pudo; nunca lanza."""
    if path is None:
        return False
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError as e:
        logger.warning("Historial: no se pudo escribir %s: %s", path, e)
        return False


def load_history(path: Path | str | None) -> list[dict]:
    """Sesiones del archivo en orden de escritura. Saltea lineas rotas (WARNING); archivo ausente -> []."""
    if path is None or not Path(path).is_file():
        return []
    out: list[dict] = []
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        logger.warning("Historial: no se pudo leer %s: %s", path, e)
        return []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            if not isinstance(d, dict):
                raise ValueError("no es un objeto")
            datetime.fromisoformat(str(d["inicio"]))
        except (ValueError, KeyError, TypeError) as e:
            logger.warning("Historial: linea %d de %s ilegible (%s): la salteo", n, path, e)
            continue
        out.append(d)
    return out


def start_of(session: dict) -> datetime:
    return datetime.fromisoformat(str(session["inicio"]))


@dataclass
class Summary:
    """Totales de un conjunto de sesiones. Los objetos van por nombre actual del catalogo."""
    sessions: int = 0
    duration_s: float = 0.0
    metins: int = 0
    items: int = 0
    metins_by_type: dict[str, int] = field(default_factory=dict)    # nombre mostrable -> cantidad
    items_by_name: dict[str, int] = field(default_factory=dict)     # nombre actual -> cantidad


def named_items(session: dict, catalog: ItemCatalog) -> dict[str, int]:
    """Objetos de una sesion por nombre actual (tipos con el mismo nombre se suman)."""
    out: dict[str, int] = {}
    for i, n in _counts(session.get("objetos_por_tipo")).items():
        name = catalog.name_of(i)
        out[name] = out.get(name, 0) + n
    return out


def named_metins(session: dict) -> dict[str, int]:
    return {metin_label(k): n for k, n in _counts(session.get("metins_por_tipo")).items()}


def summarize(sessions: list[dict], catalog: ItemCatalog, desde: datetime | None = None) -> Summary:
    """Totales de las sesiones que empezaron en `desde` o despues (None = todas)."""
    s = Summary()
    for d in sessions:
        try:
            if desde is not None and start_of(d) < desde:
                continue
            start_of(d)   # sin fecha de inicio valida la sesion no cuenta
        except (ValueError, KeyError, TypeError):
            continue
        s.sessions += 1
        s.duration_s += float(d.get("duracion_s") or 0.0) if isinstance(d.get("duracion_s"), (int, float)) else 0.0
        s.metins += _int(d.get("metins"))
        s.items += _int(d.get("objetos"))
        for k, n in named_metins(d).items():
            s.metins_by_type[k] = s.metins_by_type.get(k, 0) + n
        for k, n in named_items(d, catalog).items():
            s.items_by_name[k] = s.items_by_name.get(k, 0) + n
    return s
