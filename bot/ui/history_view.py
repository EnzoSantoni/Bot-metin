"""Logica pura (sin tkinter) de las pestanas Historial y Objetos y de la tabla de objetos en vivo.

Recibe sesiones (`bot.history.load_history`) y el catalogo (`bot.item_catalog.ItemCatalog`) y devuelve filas y textos
listos para mostrar. Todo testeable sin pantalla.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bot.history import Summary, metin_label, named_items, named_metins, reason_label, start_of, summarize
from bot.item_catalog import ItemCatalog, validate_name


def format_duration(seconds: float) -> str:
    """'1 h 05 min', '12 min 03 s' o '45 s'."""
    s = max(0, int(seconds))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min {sec:02d} s"
    return f"{sec} s"


@dataclass(frozen=True)
class SessionRow:
    index: int          # posicion en la lista original de sesiones
    date: str           # "2026-11-20 14:03"
    duration: str
    metins: int
    items: int
    reason: str


def _num(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def session_rows(sessions: list[dict]) -> list[SessionRow]:
    """Filas de la tabla de sesiones, la mas nueva arriba (por fecha de inicio)."""
    rows = []
    for i, d in enumerate(sessions):
        try:
            start = start_of(d)
        except (ValueError, KeyError, TypeError):
            continue
        dur = d.get("duracion_s")
        rows.append((start, SessionRow(i, start.strftime("%Y-%m-%d %H:%M"),
                                       format_duration(dur if isinstance(dur, (int, float)) else 0),
                                       _num(d.get("metins")), _num(d.get("objetos")), reason_label(str(d.get("motivo", ""))))))
    rows.sort(key=lambda e: e[0], reverse=True)
    return [r for _, r in rows]


def _breakdown(title: str, counts: dict[str, int]) -> list[str]:
    return [f"{title}:"] + [f"  {name}: {n}" for name, n in sorted(counts.items(), key=lambda e: (-e[1], e[0]))]


def session_detail(session: dict, catalog: ItemCatalog) -> str:
    """Detalle de una sesion: perfiles, tiempos, metin y objetos por tipo, y contadores."""
    prof = session.get("perfiles") if isinstance(session.get("perfiles"), dict) else {}
    lines = [f"Perfiles: {prof.get('servidor') or '-'} / {prof.get('personaje') or '-'} / {prof.get('zona') or '-'}"]
    try:
        lines.append(f"Inicio: {start_of(session).strftime('%Y-%m-%d %H:%M:%S')}")
    except (ValueError, KeyError, TypeError):
        pass
    fin = session.get("fin")
    if isinstance(fin, str):
        try:
            lines.append(f"Fin: {datetime.fromisoformat(fin).strftime('%Y-%m-%d %H:%M:%S')} ({reason_label(str(session.get('motivo', '')))})")
        except ValueError:
            pass
    lines.append(f"Metin rotos: {_num(session.get('metins'))}")
    if named_metins(session):
        lines += _breakdown("Por tipo de metin", named_metins(session))
    lines.append(f"Objetos recogidos: {_num(session.get('objetos'))}")
    items = named_items(session, catalog)
    if items:
        lines += _breakdown("Por tipo de objeto", items)
    lines.append(f"Captchas: {_num(session.get('captchas'))} | destrabes: {_num(session.get('destrabes'))} | "
                 f"acercamientos: {_num(session.get('acercamientos'))} | habilidades: {_num(session.get('habilidades'))}")
    if session.get("farm_mode") == "bichos":
        lines.append(f"Modo de farmeo: Bichos | señuelos usados: {_num(session.get('lures'))}")
    return "\n".join(lines)


def summary_text(label: str, s: Summary) -> str:
    """Texto de los totales (Hoy / Total)."""
    head = f"{label}: {s.sessions} sesion{'es' if s.sessions != 1 else ''}, {format_duration(s.duration_s)}, " \
           f"{s.metins} metin, {s.items} objetos"
    parts = []
    if s.metins_by_type:
        parts.append("Metin: " + ", ".join(f"{k} {n}" for k, n in sorted(s.metins_by_type.items(), key=lambda e: (-e[1], e[0]))))
    if s.items_by_name:
        parts.append("Objetos: " + ", ".join(f"{k} {n}" for k, n in sorted(s.items_by_name.items(), key=lambda e: (-e[1], e[0]))))
    return "\n".join([head] + parts)


def totals_texts(sessions: list[dict], catalog: ItemCatalog, now: datetime | None = None) -> tuple[str, str]:
    """(texto de Hoy, texto de Total). Hoy = sesiones que empezaron desde las 00:00 locales."""
    now = now or datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (summary_text("Hoy", summarize(sessions, catalog, desde=midnight)),
            summary_text("Total", summarize(sessions, catalog)))


@dataclass(frozen=True)
class CatalogRow:
    id: str
    name: str
    count: int
    thumbnail: str | None   # ruta del PNG o None


def catalog_rows(catalog: ItemCatalog) -> list[CatalogRow]:
    """Filas de la pestana Objetos, de mayor a menor cantidad."""
    rows = []
    for e in catalog.entries:
        p = catalog.thumbnail_path(e.id)
        rows.append(CatalogRow(e.id, e.name, e.count, str(p) if p else None))
    return sorted(rows, key=lambda r: (-r.count, r.name))


def rename_item(catalog: ItemCatalog, item_id: str, name: str) -> str | None:
    """Renombra un tipo. None si salio bien; si no, el texto del error (nombre vacio, largo o id inexistente)."""
    try:
        validate_name(name)
        catalog.rename(item_id, name)
    except ValueError as e:
        return str(e)
    return None


def live_item_rows(items_by_type: list[dict]) -> list[tuple[str, int]]:
    """Tabla 'Objetos de esta sesion': (nombre, cantidad) de mayor a menor."""
    return sorted(((str(e.get("name", "")), int(e.get("count", 0))) for e in items_by_type),
                  key=lambda e: (-e[1], e[0]))


def live_metin_rows(metins_by_type: dict[str, int]) -> list[tuple[str, int]]:
    return sorted(((metin_label(k), n) for k, n in metins_by_type.items()), key=lambda e: (-e[1], e[0]))
