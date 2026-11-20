"""Logica pura (sin tkinter) de las opciones del personaje elegido: recoger a pie, revivir al morir y teclas de pociones.

Se guardan en el perfil de personaje (con .bak) conservando el resto de sus claves: `pickup.dismount`, `revive.*` y
`character.potions` (esta ultima solo si el perfil ya la tiene)."""
from __future__ import annotations

import copy
from pathlib import Path

from bot.config import DEFAULTS, PROJECT_ROOT, ConfigError, _validate_profile_sections
from bot.profiles import load_profile, save_profile
from bot.ui.profile_ops import load_profile_or_empty
from bot.ui import validators as vd

# campo -> validador de texto (los booleanos no tienen)
TEXT_VALIDATORS = {
    "horse_key": vd.optional_game_key,
    "horse_item_template": vd.optional_file,
    "button_wait_s": vd.non_negative_number,
    "max_revives": vd.non_negative_int,
    "hp_key": vd.game_key,
    "mp_key": vd.game_key,
}
BOOL_FIELDS = ("dismount", "auto_revive", "recast_skills", "potions_auto")
FIELDS = ("dismount", "auto_revive", "horse_key", "horse_item_template", "button_wait_s", "max_revives", "recast_skills",
          "hp_key", "mp_key", "potions_auto")


def _num_text(v: float | int) -> str:
    return f"{v:g}"


def relative_template_path(path: str | Path, root: Path = PROJECT_ROOT) -> str:
    """Ruta para guardar en el perfil: relativa al proyecto (con /) si el archivo esta dentro, si no la absoluta."""
    p = Path(path)
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(p)


def defaults() -> dict:
    """Valores por defecto de los campos (los de DEFAULTS)."""
    rv, po = DEFAULTS["revive"], DEFAULTS["character"]["potions"]
    return {"dismount": bool(DEFAULTS["pickup"]["dismount"]), "auto_revive": bool(rv["auto_revive"]),
            "horse_key": str(rv["horse_key"]), "horse_item_template": str(rv["horse_item_template"]),
            "button_wait_s": _num_text(rv["button_wait_s"]), "max_revives": str(rv["max_revives"]),
            "recast_skills": bool(rv["recast_skills"]), "hp_key": str(po["hp_key"]), "mp_key": str(po["mp_key"]),
            "potions_auto": bool(po["auto"])}


def field_errors(values: dict, has_potions: bool = True) -> dict[str, str]:
    """Mensaje de error por campo de texto invalido (vacio = todo bien). Las pociones solo se validan si el perfil las tiene."""
    out = {}
    for k, fn in TEXT_VALIDATORS.items():
        if k in ("hp_key", "mp_key") and not has_potions:
            continue
        err = fn(str(values.get(k, "")))
        if err:
            out[k] = err
    return out


class CharacterEditor:
    """Opciones del perfil de personaje elegido, con indicador de cambios sin guardar y bloqueo durante la corrida.
    `values` tiene los textos/booleanos de los campos (se validan al guardar)."""

    def __init__(self, profiles_root: str | Path | None = None) -> None:
        self.profiles_root = profiles_root
        self.personaje = ""
        self.has_potions = False
        self.values: dict = defaults()
        self._saved: dict = dict(self.values)
        self.locked = False

    @property
    def dirty(self) -> bool:
        return self._norm(self.values) != self._norm(self._saved)

    @staticmethod
    def _norm(values: dict) -> dict:
        return {k: (v.strip() if isinstance(v, str) else v) for k, v in values.items()}

    def load(self, personaje: str) -> None:
        """Carga las opciones del perfil (los valores por defecto donde el perfil no define nada)."""
        self.personaje = personaje
        data = load_profile_or_empty("personaje", personaje, self.profiles_root, "No se pudieron cargar las opciones del personaje")
        v = defaults()
        pk = data.get("pickup") or {}
        if isinstance(pk.get("dismount"), bool):
            v["dismount"] = pk["dismount"]
        rv = data.get("revive") or {}
        for k, conv in (("auto_revive", bool), ("recast_skills", bool), ("horse_key", str), ("horse_item_template", str)):
            if k in rv and isinstance(rv[k], conv):
                v[k] = rv[k]
        if isinstance(rv.get("button_wait_s"), (int, float)) and not isinstance(rv["button_wait_s"], bool):
            v["button_wait_s"] = _num_text(rv["button_wait_s"])
        if isinstance(rv.get("max_revives"), int) and not isinstance(rv["max_revives"], bool):
            v["max_revives"] = str(rv["max_revives"])
        po = (data.get("character") or {}).get("potions")
        self.has_potions = isinstance(po, dict)
        if self.has_potions:
            for k, name in (("hp_key", "hp_key"), ("mp_key", "mp_key")):
                if isinstance(po.get(name), str):
                    v[k] = po[name]
            if isinstance(po.get("auto"), bool):
                v["potions_auto"] = po["auto"]
        self.values = v
        self._saved = dict(v)

    def set(self, field: str, value: object) -> None:
        if field not in FIELDS:
            raise KeyError(field)
        if self.locked:
            raise RuntimeError("las opciones del personaje estan bloqueadas mientras el bot corre")
        self.values[field] = value

    def errors(self) -> list[str]:
        return list(field_errors(self.values, self.has_potions).values())

    def save(self) -> Path:
        """Guarda en el perfil conservando el resto (con .bak). ValueError si algo es invalido."""
        if not self.personaje:
            raise ValueError("No hay un perfil de personaje elegido.")
        errs = self.errors()
        if errs:
            raise ValueError(" ".join(errs))
        v = self._norm(self.values)
        data = load_profile("personaje", self.personaje, self.profiles_root)
        data.setdefault("pickup", {})["dismount"] = bool(v["dismount"])
        rv = data.setdefault("revive", {})
        rv.update(auto_revive=bool(v["auto_revive"]), recast_skills=bool(v["recast_skills"]), horse_key=v["horse_key"],
                  horse_item_template=v["horse_item_template"], button_wait_s=float(str(v["button_wait_s"]).replace(",", ".")),
                  max_revives=int(v["max_revives"]))
        if self.has_potions:
            po = data.setdefault("character", {}).setdefault("potions", {})
            po.update(hp_key=v["hp_key"], mp_key=v["mp_key"], auto=bool(v["potions_auto"]))
        cfg = copy.deepcopy(DEFAULTS)   # red de seguridad: las mismas reglas de bot/config.py sobre lo que se va a guardar
        for sect in ("pickup", "revive"):
            cfg[sect].update(data[sect])
        if self.has_potions:
            cfg["character"]["potions"].update(data["character"]["potions"])
        try:
            _validate_profile_sections(cfg)
        except ConfigError as e:
            raise ValueError(str(e)) from e
        path = save_profile("personaje", self.personaje, data, self.profiles_root)
        self.values = dict(v)
        self._saved = dict(v)
        return path
