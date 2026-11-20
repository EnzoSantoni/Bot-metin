"""Que claves de config.json pisan a los perfiles (sin tkinter): es lo mismo que `calibrate.py perfiles migrar` movería.
Sirve para el aviso amarillo de la ventana (general y por pestana) y para explicar la migracion."""
from __future__ import annotations

from pathlib import Path

from bot.config import ConfigError, read_raw_config, resolve_path
from bot.profiles import kind_of, leaf_paths, raw_without_profiles, rule_for

KIND_LABELS_ES = {"servidor": "servidor", "personaje": "personaje", "zona": "zona"}
_TWO_LEVELS = {"zone", "character", "revive", "vision", "controls", "server", "pickup", "coords_filter"}

TAB_LABELS = {"metin": "Metin", "zona": "Zona", "bichos": "Modo bichos", "personaje": "Personaje",
              "habilidades": "Habilidades"}   # pestanas que muestran su propia franja de aviso

Keys = list[tuple[tuple[str, ...], str]]   # (ruta de la clave, tipo de perfil dueno)


def display_key(path: tuple[str, ...]) -> str:
    """Nombre corto de la clave para mostrar: 'farm_zone', 'zone.farm_mode', 'character.skills'..."""
    rule = rule_for(path)
    prefix = rule[0] if rule else path[:1]
    if len(prefix) == 1 and prefix[0] in _TWO_LEVELS:
        return ".".join(path[:2])
    return ".".join(prefix)


def tab_of(path: tuple[str, ...]) -> str | None:
    """Pestana de la interfaz que edita esa clave (None = ninguna: p. ej. teclas del servidor)."""
    kind = kind_of(path)
    if kind == "zona":
        if path[0] == "target_metin_names" or path[:2] == ("zone", "metin_priority"):
            return "metin"
        if path[:2] in (("zone", "farm_mode"), ("zone", "bichos")):
            return "bichos"
        return "zona"
    if kind == "personaje":
        return "habilidades" if path[:2] == ("character", "skills") else "personaje"
    return None


def keys_in_config(raw: dict) -> Keys:
    """(ruta, tipo) de cada valor de config.json que tiene un perfil como dueno (en el orden del archivo)."""
    out: Keys = []
    for path, _ in leaf_paths(raw_without_profiles(raw)):
        kind = kind_of(path)
        if kind is not None:
            out.append((path, kind))
    return out


def read_config_keys(config_path: str | Path) -> Keys:
    """Como `keys_in_config` leyendo el archivo; vacio si no existe o no se puede leer."""
    p = resolve_path(config_path)
    if not p.is_file():
        return []
    try:
        return keys_in_config(read_raw_config(p))
    except (ConfigError, OSError):
        return []


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def all_names(keys: Keys) -> list[str]:
    return _unique([display_key(p) for p, _ in keys])


def names_by_tab(keys: Keys) -> dict[str, list[str]]:
    """Claves pisadas agrupadas por la pestana que las edita."""
    out: dict[str, list[str]] = {}
    for p, _ in keys:
        t = tab_of(p)
        if t:
            out.setdefault(t, []).append(display_key(p))
    return {t: _unique(v) for t, v in out.items()}


def banner_text(keys: Keys) -> str:
    """Texto de la franja de arriba de la ventana ('' si config.json no pisa nada)."""
    if not keys:
        return ""
    return ("Atención: config.json define valores que también van en los perfiles y tiene prioridad sobre ellos. "
            "Lo que edites en Metin, Zona, Modo bichos, Personaje o Habilidades se guarda en el perfil, pero el bot sigue "
            "usando config.json. Claves de config.json que pisan: " + ", ".join(all_names(keys)) + ".")


def tab_text(names: list[str]) -> str:
    """Texto de la franja de una pestana ('' si ahi no se pisa nada)."""
    if not names:
        return ""
    return ("config.json pisa lo que se edita en esta pestaña (" + ", ".join(names) + "): el bot usa config.json, "
            "no el perfil, hasta que migres.")


def migration_explanation(keys: Keys, selection: dict[str, str]) -> str:
    """Texto de la confirmacion previa a migrar (que se mueve, a donde y que respaldos se guardan)."""
    moves = []
    for kind in ("servidor", "personaje", "zona"):
        names = _unique([display_key(p) for p, k in keys if k == kind])
        if names:
            moves.append(f"- {', '.join(names)}  →  perfil de {KIND_LABELS_ES[kind]} '{selection.get(kind, '?')}'")
    return ("Se van a mover estos valores de config.json a los perfiles elegidos:\n\n" + "\n".join(moves) +
            "\n\nQué pasa:\n"
            "- config.json conserva solo la configuración general y recuerda qué perfiles usás.\n"
            "- Antes de escribir se guardan copias: config.json.bak y un .bak de cada perfil que cambia.\n"
            "- Si la migración cambiara la configuración con la que corre el bot, no se hace nada.\n"
            "- A continuación se muestra el plan y se vuelve a preguntar antes de guardar.\n\n¿Seguir?")
