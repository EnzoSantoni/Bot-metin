"""Logica pura (sin tkinter) del modo de farmeo de un perfil de zona: Metin / Bichos, tecla del senuelo y cada cuantos segundos."""
from __future__ import annotations

import copy
import logging
from pathlib import Path

from bot.config import DEFAULTS, ConfigError, _validate_profile_sections
from bot.profiles import load_profile, save_profile
from bot.ui.skills_editor import parse_seconds

logger = logging.getLogger(__name__)

MODES: tuple[tuple[str, str], ...] = (("metin", "Metin"), ("bichos", "Bichos"))   # (valor del perfil, texto de la interfaz)
MODE_LABELS = dict(MODES)
_BICHOS_DEFAULTS = DEFAULTS["zone"]["bichos"]


def validate_bichos(mode: str, key: str, seconds_text: str) -> tuple[dict | None, list[str]]:
    """Valida lo que se escribio en la interfaz con las mismas reglas que bot/config.py. Devuelve ({farm_mode, lure_key,
    lure_every_s}, []) o (None, errores)."""
    errors: list[str] = []
    if mode not in MODE_LABELS:
        errors.append("El modo de farmeo tiene que ser Metin o Bichos.")
    key = str(key).strip()
    seconds = 0.0
    try:
        seconds = parse_seconds(seconds_text, "Los segundos del señuelo")
        if seconds <= 0:
            errors.append("Los segundos del señuelo tienen que ser mayores que 0.")
    except ValueError as e:
        errors.append(str(e))
    if not key:
        errors.append("Falta la tecla del señuelo.")
    elif not errors:
        cfg = copy.deepcopy(DEFAULTS)
        cfg["zone"]["bichos"].update(lure_key=key, lure_every_s=seconds)
        try:
            _validate_profile_sections(cfg)
        except ConfigError as e:
            msg = str(e).replace("zone.bichos.lure_key", "La tecla del señuelo")
            if msg.endswith("usa la tecla de panico o de pausa"):
                msg = msg.replace("usa la tecla de panico o de pausa", "choca con F11 o F12 (pausa y pánico del bot)")
            errors.append(msg)
    if errors:
        return None, errors
    return {"farm_mode": mode, "lure_key": key, "lure_every_s": seconds}, []


def seconds_text(value: float) -> str:
    return f"{value:g}"


class FarmModeEditor:
    """Modo de farmeo, tecla y periodo del señuelo del perfil de zona elegido, con indicador de cambios sin guardar y
    bloqueo durante la corrida. `lure_key` y `seconds` son los textos de los campos (se validan al guardar)."""

    def __init__(self, profiles_root: str | Path | None = None) -> None:
        self.profiles_root = profiles_root
        self.zona = ""
        self.mode = "metin"
        self.lure_key = str(_BICHOS_DEFAULTS["lure_key"])
        self.seconds = seconds_text(_BICHOS_DEFAULTS["lure_every_s"])
        self._saved = self._current()
        self.locked = False

    def _current(self) -> tuple[str, str, str]:
        return (self.mode, self.lure_key.strip(), self.seconds.strip())

    @property
    def dirty(self) -> bool:
        return self._current() != self._saved

    def load(self, zona: str) -> None:
        """Carga el modo y el señuelo del perfil de zona (los valores por defecto si no se puede leer)."""
        self.zona = zona
        try:
            data = load_profile("zona", zona, self.profiles_root) if zona else {}
        except ConfigError as e:
            logger.warning("No se pudo cargar el modo de farmeo: %s", e)
            data = {}
        zn = data.get("zone") or {}
        bi = zn.get("bichos") or {}
        self.mode = zn.get("farm_mode") if zn.get("farm_mode") in MODE_LABELS else "metin"
        self.lure_key = str(bi.get("lure_key", _BICHOS_DEFAULTS["lure_key"]))
        every = bi.get("lure_every_s", _BICHOS_DEFAULTS["lure_every_s"])
        self.seconds = seconds_text(float(every)) if isinstance(every, (int, float)) and not isinstance(every, bool) \
            else seconds_text(_BICHOS_DEFAULTS["lure_every_s"])
        self._saved = self._current()

    def errors(self) -> list[str]:
        return validate_bichos(self.mode, self.lure_key, self.seconds)[1]

    def set_values(self, mode: str, lure_key: str, seconds: str) -> None:
        if self.locked:
            raise RuntimeError("el modo de farmeo esta bloqueado mientras el bot corre")
        self.mode, self.lure_key, self.seconds = mode, lure_key, seconds

    def save(self) -> Path:
        """Guarda en el perfil de zona conservando el resto de sus claves (con .bak). ValueError si algo es invalido."""
        if not self.zona:
            raise ValueError("No hay un perfil de zona elegido.")
        values, errs = validate_bichos(self.mode, self.lure_key, self.seconds)
        if values is None:
            raise ValueError(" ".join(errs))
        data = load_profile("zona", self.zona, self.profiles_root)
        zn = data.setdefault("zone", {})
        zn["farm_mode"] = values["farm_mode"]
        zn.setdefault("bichos", {}).update(lure_key=values["lure_key"], lure_every_s=values["lure_every_s"])
        path = save_profile("zona", self.zona, data, self.profiles_root)
        self.lure_key, self.seconds = values["lure_key"], seconds_text(values["lure_every_s"])
        self._saved = self._current()
        return path
