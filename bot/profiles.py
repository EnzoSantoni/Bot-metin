"""Perfiles de configuracion (servidor, personaje, zona) y armado del `cfg`.

El `cfg` se arma asi: DEFAULTS <- perfil de servidor <- perfil de personaje <- perfil de zona <- config.json
(config.json gana). Los perfiles son JSON puro en `profiles/<tipo>/<nombre>.json` y solo guardan lo que difiere de DEFAULTS
mas su seccion propia. Cada tipo de perfil solo acepta las claves de su categoria (`RULES`).
"""
from __future__ import annotations

import copy
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bot.fileutil import atomic_write_text
from bot.config import (DEFAULTS, PROJECT_ROOT, ConfigError, _merge, read_raw_config, resolve_path, save_config,
                        validate_config)

logger = logging.getLogger(__name__)

KINDS = ("servidor", "personaje", "zona")
KIND_DIRS = {"servidor": "servidores", "personaje": "personajes", "zona": "zonas"}
DEFAULT_SELECTION = {"servidor": "Guabina", "personaje": "Farfarfa", "zona": "Ciudad 1"}
# Nombres viejos de perfiles de zona -> nombre actual (config.json, linea de comandos y ajustes de la interfaz los siguen aceptando)
ZONE_ALIASES = {"Guabina - Oscuridad y Negro": "Ciudad 1"}
_alias_logged: set[str] = set()
PROFILES_DIRNAME = "profiles"
MAP_SUFFIX = ".mapa.json"   # mapa de metin de la zona: vive junto al perfil pero no es un perfil

# Lista blanca: ruta de claves -> tipo de perfil al que pertenece. Gana el prefijo mas largo; lo que no esta aca es
# configuracion general (va en config.json).
RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("window_title",), "servidor"),
    (("expected_client_size",), "servidor"),
    (("controls",), "servidor"),
    (("vision",), "servidor"),
    (("server",), "servidor"),
    (("vision", "items", "owner_template"), "personaje"),
    (("character",), "personaje"),
    (("revive",), "personaje"),
    (("pickup", "dismount"), "personaje"),
    (("coords_filter", "max_speed"), "personaje"),
    (("camera_pitch", "from_limit_px"), "personaje"),   # cuantos px bajar desde el tope de la camara (el angulo del jugador)
    (("navigation", "keyboard", "view_right_key"), "servidor"),   # sentidos de Q/E y A/D: son del juego
    (("navigation", "keyboard", "ad_side"), "servidor"),
    (("farm_zone",), "zona"),
    (("target_metin_names",), "zona"),
    (("zone",), "zona"),
    (("spawns",), "zona"),
)

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WIN_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


@dataclass
class Override:
    """Valores de una seccion definidos en el perfil elegido y tambien en config.json (config.json gana)."""
    section: str
    kind: str
    profile: str
    paths: list[str]
    same: bool

    def message(self) -> str:
        msg = f"config.json pisa {self.section} del perfil de {self.kind} '{self.profile}'"
        if self.same:
            msg += " (con el mismo valor: podes borrarlo de config.json o migrar con 'calibrate.py perfiles migrar')"
        return msg

    def as_dict(self) -> dict:
        return {"section": self.section, "kind": self.kind, "profile": self.profile, "paths": list(self.paths),
                "same": self.same, "message": self.message()}


def profiles_root(root: str | Path | None = None) -> Path:
    """Carpeta de perfiles (por defecto `profiles/` en la raiz del proyecto)."""
    return resolve_path(root) if root is not None else PROJECT_ROOT / PROFILES_DIRNAME


def rule_for(path: tuple[str, ...]) -> tuple[tuple[str, ...], str] | None:
    """(prefijo de la regla, tipo) de una ruta de claves, o None si es configuracion general."""
    best: tuple[tuple[str, ...], str] | None = None
    for prefix, kind in RULES:
        if path[:len(prefix)] == prefix and (best is None or len(prefix) > len(best[0])):
            best = (prefix, kind)
    return best


def kind_of(path: tuple[str, ...]) -> str | None:
    """Tipo de perfil al que pertenece la ruta (None = general)."""
    r = rule_for(path)
    return r[1] if r else None


def leaf_paths(d: Any, prefix: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
    """Hojas (ruta, valor) de un dict anidado; un dict vacio cuenta como hoja."""
    if isinstance(d, dict) and d:
        out: list[tuple[tuple[str, ...], Any]] = []
        for k, v in d.items():
            out.extend(leaf_paths(v, (*prefix, k)))
        return out
    return [(prefix, d)] if prefix else []


def has_path(d: dict, path: tuple[str, ...]) -> bool:
    """True si `d` define esa ruta (o algo por debajo de ella, o un valor no-dict en un tramo anterior)."""
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict):
            return True
        if k not in cur:
            return False
        cur = cur[k]
    return True


def set_path(d: dict, path: tuple[str, ...], value: Any) -> None:
    for k in path[:-1]:
        if not isinstance(d.get(k), dict):
            d[k] = {}
        d = d[k]
    d[path[-1]] = copy.deepcopy(value)


def remove_path(d: dict, path: tuple[str, ...]) -> None:
    """Borra la hoja y los dicts padres que queden vacios."""
    if len(path) > 1 and isinstance(d.get(path[0]), dict):
        remove_path(d[path[0]], path[1:])
        if not d[path[0]]:
            del d[path[0]]
    elif len(path) == 1:
        d.pop(path[0], None)


# ----------------------------------------------------------------------------- archivos de perfil

def _check_kind(kind: str) -> None:
    if kind not in KINDS:
        raise ConfigError(f"tipo de perfil invalido {kind!r} (usa {', '.join(KINDS)})")


def check_name(name: str) -> str:
    """Nombre de perfil valido como nombre de archivo en Windows (espacios y guiones permitidos)."""
    if not isinstance(name, str) or not name.strip():
        raise ConfigError("el nombre del perfil no puede estar vacio")
    if name != name.strip() or name.endswith("."):
        raise ConfigError(f"nombre de perfil invalido {name!r}: no puede empezar/terminar con espacios ni terminar en '.'")
    if ".." in name or _INVALID_CHARS.search(name):
        raise ConfigError(f"nombre de perfil invalido {name!r}: no uses / \\ .. ni los caracteres < > : \" | ? *")
    if name.split(".")[0].lower() in _WIN_RESERVED:
        raise ConfigError(f"nombre de perfil invalido {name!r}: es un nombre reservado de Windows")
    return name


def profile_path(kind: str, name: str, root: str | Path | None = None) -> Path:
    """Ruta del archivo de un perfil (no exige que exista)."""
    _check_kind(kind)
    return profiles_root(root) / KIND_DIRS[kind] / f"{check_name(name)}.json"


def list_profiles(kind: str, root: str | Path | None = None) -> list[str]:
    """Nombres de los perfiles disponibles de ese tipo, ordenados."""
    _check_kind(kind)
    d = profiles_root(root) / KIND_DIRS[kind]
    return sorted(p.stem for p in d.glob("*.json") if not p.name.endswith(MAP_SUFFIX)) if d.is_dir() else []


def validate_profile_data(kind: str, data: Any, source: str = "el perfil") -> None:
    """Cada clave tiene que ser de la categoria del perfil; si no, ConfigError que dice a donde va."""
    _check_kind(kind)
    if not isinstance(data, dict):
        raise ConfigError(f"{source}: la raiz debe ser un objeto JSON")
    for path, _ in leaf_paths(data):
        rule = rule_for(path)
        if rule is None:
            raise ConfigError(f"{source}: la clave '{path[0]}' es configuracion general: no va en un perfil, va en config.json")
        if rule[1] != kind:
            raise ConfigError(f"{source}: la clave '{'.'.join(rule[0])}' no va en un perfil de {kind}; va en el de {rule[1]}")


def load_profile(kind: str, name: str, root: str | Path | None = None) -> dict:
    """Lee y valida un perfil. ConfigError (con la lista de los disponibles) si no existe."""
    p = profile_path(kind, name, root)
    if not p.is_file():
        avail = list_profiles(kind, root)
        raise ConfigError(f"No existe el perfil de {kind} {name!r} ({p}). Disponibles: "
                          + (", ".join(repr(a) for a in avail) if avail else "ninguno"))
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"{p} no es JSON valido (linea {e.lineno}, columna {e.colno}): {e.msg}") from e
    validate_profile_data(kind, data, f"perfil de {kind} {name!r}")
    return data


def save_profile(kind: str, name: str, data: dict, root: str | Path | None = None) -> Path:
    """Valida las categorias, copia el anterior a .bak y guarda JSON indentado UTF-8."""
    p = profile_path(kind, name, root)
    validate_profile_data(kind, data, f"perfil de {kind} {name!r}")
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        shutil.copy2(p, p.with_name(p.name + ".bak"))
    atomic_write_text(p, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return p


# ----------------------------------------------------------------------------- seleccion y armado

def canonical_zone(name: str) -> str:
    """Nombre actual del perfil de zona: si `name` es uno viejo (`ZONE_ALIASES`) devuelve el nuevo y avisa una sola vez."""
    new = ZONE_ALIASES.get(name)
    if new is None:
        return name
    if name not in _alias_logged:
        _alias_logged.add(name)
        logger.info("El perfil de zona '%s' ahora se llama '%s'", name, new)
    return new


def legacy_zone_names(zona: str) -> list[str]:
    """Nombres viejos con los que se conocio el perfil de zona `zona` (para migrar su mapa de metin)."""
    return [old for old, new in ZONE_ALIASES.items() if new == zona]


def read_selection(raw: dict) -> dict[str, str]:
    """Seleccion guardada en el bloque `profiles` de config.json (solo lo que esta; el resto se completa despues)."""
    block = raw.get("profiles")
    if block is None:
        return {}
    if not isinstance(block, dict):
        raise ConfigError("profiles debe ser un objeto con servidor, personaje y zona")
    out: dict[str, str] = {}
    for k, v in block.items():
        if k not in KINDS:
            raise ConfigError(f"profiles.{k}: clave desconocida (usa {', '.join(KINDS)})")
        if not isinstance(v, str) or not v.strip():
            raise ConfigError(f"profiles.{k} debe ser el nombre del perfil (texto no vacio)")
        out[k] = canonical_zone(v) if k == "zona" else v
    return out


def resolve_selection(raw: dict, servidor: str | None = None, personaje: str | None = None,
                      zona: str | None = None) -> dict[str, str]:
    """Argumentos > bloque `profiles` de config.json > perfiles por defecto."""
    saved, args = read_selection(raw), {"servidor": servidor, "personaje": personaje,
                                        "zona": canonical_zone(zona) if zona else zona}
    return {k: args[k] or saved.get(k) or DEFAULT_SELECTION[k] for k in KINDS}


def merge_layers(layers: list[dict]) -> dict:
    """Merge profundo de las capas, en orden (la ultima gana). Empieza desde DEFAULTS."""
    out = copy.deepcopy(DEFAULTS)
    for layer in layers:
        out = _merge(out, layer)
    return out


def raw_without_profiles(raw: dict) -> dict:
    return {k: v for k, v in raw.items() if k not in ("profiles", "_profiles")}


def find_overrides(raw: dict, datas: dict[str, dict], names: dict[str, str]) -> list[Override]:
    """Valores de config.json definidos tambien en el perfil elegido de su categoria, agrupados por seccion."""
    groups: dict[tuple[str, str], Override] = {}
    prof_leaves = {k: dict(leaf_paths(d)) for k, d in datas.items()}
    for path, value in leaf_paths(raw_without_profiles(raw)):
        rule = rule_for(path)
        if rule is None or path not in prof_leaves.get(rule[1], {}):
            continue
        kind = rule[1]
        section = ".".join(path[:2] if rule[0] == ("vision",) and len(path) > 1 else rule[0])
        o = groups.setdefault((kind, section), Override(section, kind, names[kind], [], True))
        o.paths.append(".".join(path))
        o.same = o.same and prof_leaves[kind][path] == value
    return list(groups.values())


def _legacy(raw: dict) -> dict:
    cfg = _merge(DEFAULTS, raw_without_profiles(raw))
    validate_config(cfg)
    cfg["_profiles"] = {**{k: None for k in KINDS}, "overrides": []}
    return cfg


def spawns_file_for(zona: str) -> str:
    """Archivo del mapa de metin de una zona: `profiles/zonas/<zona>.mapa.json` (relativo al proyecto, no se versiona)."""
    return f"{PROFILES_DIRNAME}/{KIND_DIRS['zona']}/{zona}{MAP_SUFFIX}"


def build_config(config_path: str | Path = "config.json", servidor: str | None = None, personaje: str | None = None,
                 zona: str | None = None, *, profiles_root_dir: str | Path | None = None, warn: bool = True) -> dict:
    """Arma el cfg: DEFAULTS <- servidor <- personaje <- zona <- config.json. Agrega `cfg["_profiles"]`.

    Los argumentos pisan la seleccion de config.json. Sin carpeta profiles/ (instalacion vieja) y sin argumentos
    funciona como antes: DEFAULTS + config.json. Con warn=True loguea un WARNING por cada seccion pisada.
    """
    p = resolve_path(config_path)
    if not p.exists():
        logger.warning("No existe %s: se crea con los valores por defecto", p)
        cfg = copy.deepcopy(DEFAULTS)
        save_config(cfg, p)
        return cfg
    raw = read_raw_config(p)
    root = profiles_root(profiles_root_dir)
    names = resolve_selection(raw, servidor, personaje, zona)
    if not root.is_dir():
        if servidor or personaje or zona:
            raise ConfigError(f"No existe la carpeta de perfiles {root}: no se puede elegir un perfil")
        return _legacy(raw)
    datas = {k: load_profile(k, names[k], profiles_root_dir) for k in KINDS}
    cfg = merge_layers([datas[k] for k in KINDS] + [raw_without_profiles(raw)])
    if not has_path(datas["zona"], ("spawns", "file")) and not has_path(raw_without_profiles(raw), ("spawns", "file")):
        cfg["spawns"]["file"] = spawns_file_for(names["zona"])   # mapa de metin propio de la zona elegida
    if not has_path(datas["zona"], ("spawns", "map_name")) and not has_path(raw_without_profiles(raw), ("spawns", "map_name")):
        cfg["spawns"]["map_name"] = names["zona"]   # el mapa se llama como la zona
    validate_config(cfg)
    overrides = find_overrides(raw, datas, names)
    if warn:
        for o in overrides:
            logger.warning(o.message())
    cfg["_profiles"] = {**names, "overrides": [o.as_dict() for o in overrides]}
    return cfg


# ----------------------------------------------------------------------------- migracion

@dataclass
class MigrationPlan:
    """Que valores pasan de config.json a que perfil."""
    selection: dict[str, str]
    moves: list[tuple[str, tuple[str, ...], Any]] = field(default_factory=list)   # (tipo, ruta, valor)
    new_raw: dict = field(default_factory=dict)
    profile_data: dict[str, dict] = field(default_factory=dict)                   # solo los tipos tocados


def plan_migration(raw: dict, selection: dict[str, str], root: str | Path | None = None) -> MigrationPlan:
    """Mueve cada hoja de config.json que tiene categoria al perfil activo de esa categoria; agrega el bloque profiles."""
    plan = MigrationPlan(selection=dict(selection))
    plan.new_raw = copy.deepcopy(raw_without_profiles(raw))
    datas = {k: load_profile(k, selection[k], root) for k in KINDS}
    for path, value in leaf_paths(raw_without_profiles(raw)):
        kind = kind_of(path)
        if kind is None:
            continue
        plan.moves.append((kind, path, copy.deepcopy(value)))
        set_path(datas[kind], path, value)
        remove_path(plan.new_raw, path)
    plan.profile_data = {k: datas[k] for k in KINDS if any(m[0] == k for m in plan.moves)}
    plan.new_raw["profiles"] = dict(selection)
    return plan


def apply_migration(plan: MigrationPlan, config_path: str | Path, root: str | Path | None = None) -> list[Path]:
    """Guarda los perfiles tocados y el config.json nuevo; hace .bak de todo lo que ya existia."""
    written = [save_profile(k, plan.selection[k], d, root) for k, d in plan.profile_data.items()]
    cp = resolve_path(config_path)
    if cp.exists():
        shutil.copy2(cp, cp.with_name(cp.name + ".bak"))
    save_config(plan.new_raw, cp)
    return [*written, cp]
