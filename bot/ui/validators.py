"""Validadores puros (sin tkinter) para los campos de la interfaz. Cada uno recibe el texto y devuelve None si es valido o
un mensaje corto en espanol con el motivo."""
from __future__ import annotations

import math

from bot.config import DEFAULTS, ConfigError, _check_game_key


def _to_float(text: str) -> float | None:
    try:
        v = float(str(text).strip().replace(",", "."))
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def non_empty(text: str) -> str | None:
    """Texto no vacio."""
    return None if str(text).strip() else "No puede estar vacío."


def positive_number(text: str) -> str | None:
    """Numero mayor que 0 (acepta coma decimal)."""
    v = _to_float(text)
    if v is None:
        return "Tiene que ser un número."
    return None if v > 0 else "Tiene que ser mayor que 0."


def non_negative_number(text: str) -> str | None:
    """Numero mayor o igual que 0 (acepta coma decimal)."""
    v = _to_float(text)
    if v is None:
        return "Tiene que ser un número."
    return None if v >= 0 else "No puede ser negativo."


def non_negative_int(text: str) -> str | None:
    """Entero mayor o igual que 0."""
    try:
        v = int(str(text).strip())
    except ValueError:
        return "Tiene que ser un número entero."
    return None if v >= 0 else "No puede ser negativo."


def optional_hhmm(text: str) -> str | None:
    """Hora HH:MM (24 h) o vacío ("no parar a una hora")."""
    from bot.schedule import parse_hhmm
    if not str(text).strip():
        return None
    try:
        parse_hhmm(text)
    except ValueError:
        return "Usá el formato HH:MM (ej. 23:30)."
    return None


def game_key(text: str) -> str | None:
    """Tecla del juego valida (ej. f4, 4, ctrl+g) que no sea la de pausa ni la de panico del bot."""
    text = str(text).strip()
    if not text:
        return "Falta la tecla."
    reserved = {DEFAULTS["safety"]["panic_key"].strip().lower(), DEFAULTS["safety"]["pause_key"].strip().lower()}
    try:
        _check_game_key(text, "tecla", reserved)
    except ConfigError as e:
        if "panico o de pausa" in str(e):
            return "Choca con F11 o F12 (pausa y pánico del bot)."
        return f"La tecla {text!r} no existe."
    return None


def positive_int(text: str) -> str | None:
    """Entero mayor o igual que 1."""
    try:
        v = int(str(text).strip())
    except ValueError:
        return "Tiene que ser un número entero."
    return None if v >= 1 else "Tiene que ser 1 o más."


def optional_game_key(text: str) -> str | None:
    """Como `game_key`, pero vacío vale (significa "no usar")."""
    return None if not str(text).strip() else game_key(text)


def optional_file(text: str) -> str | None:
    """Ruta de un archivo que existe, o vacío ("no usar"). Las rutas relativas se toman desde la carpeta del proyecto."""
    from bot.config import resolve_path
    text = str(text).strip()
    if not text:
        return None
    return None if resolve_path(text).is_file() else "No existe ese archivo."
