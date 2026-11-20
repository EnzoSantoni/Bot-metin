"""Parada programada: parseo de HH:MM y calculo de la proxima ocurrencia (hora local). Sin dependencias del bot."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

_HHMM_RE = re.compile(r"(\d{1,2}):(\d{2})")


def parse_hhmm(text: str) -> tuple[int, int]:
    """'HH:MM' (24 h) -> (hora, minuto). ValueError con mensaje en espanol si el formato es invalido."""
    m = _HHMM_RE.fullmatch(str(text).strip())
    if m is None:
        raise ValueError(f"La hora tiene que tener el formato HH:MM (ej. 23:30), no {text!r}.")
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        raise ValueError(f"La hora {text!r} no existe (HH entre 00 y 23, MM entre 00 y 59).")
    return h, mi


def next_stop(text: str, now: datetime) -> tuple[datetime, bool]:
    """(momento de la parada, es_manana). Si HH:MM ya paso hoy (o es justo ahora), es manana."""
    h, mi = parse_hhmm(text)
    t = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    if t <= now:
        return t + timedelta(days=1), True
    return t, False
