"""Operaciones puras (sin tkinter) sobre perfiles para la pestana Personaje: validar nombres nuevos y duplicar perfiles
de personaje y de zona."""
from __future__ import annotations

import copy
import logging
from pathlib import Path

from bot.config import ConfigError
from bot.profiles import (KIND_DIRS, MAP_SUFFIX, check_name, list_profiles, load_profile, profile_path, profiles_root,
                          remove_path, save_profile)
from bot.vision.metin import slugify

OWNER_TEMPLATE_PATH = ("vision", "items", "owner_template")

logger = logging.getLogger(__name__)


def load_profile_or_empty(kind: str, name: str, root: str | Path | None, what: str) -> dict:
    """Datos del perfil, o {} si no hay nombre o no se puede leer (se loguea `what`). Lo usan todos los editores."""
    try:
        return load_profile(kind, name, root) if name else {}
    except ConfigError as e:
        logger.warning("%s: %s", what, e)
        return {}


def validate_new_profile_name(kind: str, name: str, existing: list[str] | None = None,
                              root: str | Path | None = None) -> str | None:
    """None si `name` sirve como nombre de un perfil nuevo; si no, el mensaje en espanol. `existing` = nombres que ya hay
    (por defecto los de la carpeta de perfiles). Windows no distingue mayusculas: 'farfarfa' choca con 'Farfarfa'."""
    if not name.strip():
        return "Escribí un nombre."
    if name.startswith("-"):
        return "El nombre no puede empezar con un guion."
    try:
        check_name(name)
    except ConfigError:
        return "Nombre no válido: sin espacios al borde, sin / \\ : * ? \" < > | ni nombres reservados de Windows."
    if name.lower().endswith(".mapa") or not slugify(name):
        return "Nombre no válido: elegí otro."
    have = list_profiles(kind, root) if existing is None else existing
    if name.lower() in {n.lower() for n in have}:
        return "Ya existe un perfil con ese nombre."
    if kind == "zona" and (profiles_root(root) / KIND_DIRS[kind] / f"{name}{MAP_SUFFIX}").exists():
        return "Ya hay un mapa de metin guardado con ese nombre: elegí otro."
    return None


def duplicate_character_profile(src: str, dst: str, same_character: bool, root: str | Path | None = None) -> list[str]:
    """Copia el perfil de personaje `src` como `dst` (no pisa uno existente). Devuelve avisos para mostrar.

    Con `same_character` queda igual (mismo nombre amarillo y misma plantilla). Si es otro personaje, `character.name`
    pasa a ser `dst` y se saca la plantilla del nombre amarillo (hay que capturarla)."""
    err = validate_new_profile_name("personaje", dst, root=root)
    if err:
        raise ConfigError(err)
    data = copy.deepcopy(load_profile("personaje", src, root))
    notes: list[str] = []
    if not same_character:
        data.setdefault("character", {})["name"] = dst
        remove_path(data, OWNER_TEMPLATE_PATH)
        notes.append(f"Falta capturar el nombre amarillo de '{dst}': elegilo en la lista de personajes y usá \"Capturar nombre "
                     "amarillo\" (hace falta tener el juego abierto con un objeto de ese personaje en el piso).")
    save_profile("personaje", dst, data, root)
    return notes


def duplicate_zone_profile(src: str, dst: str, root: str | Path | None = None) -> Path:
    """Copia el perfil de zona `src` como `dst`: poligono, tipos de metin, prioridad, modo y bichos. El mapa de metin NO
    se copia (queda vacio y se arma jugando)."""
    err = validate_new_profile_name("zona", dst, root=root)
    if err:
        raise ConfigError(err)
    data = copy.deepcopy(load_profile("zona", src, root))
    data.pop("spawns", None)   # un spawns.file explicito compartiria el mapa de la zona original
    zn = data.get("zone")
    if isinstance(zn, dict) and "name" in zn:
        zn["name"] = dst
    return save_profile("zona", dst, data, root)


def profile_exists(kind: str, name: str, root: str | Path | None = None) -> bool:
    try:
        return profile_path(kind, name, root).is_file()
    except ConfigError:
        return False
