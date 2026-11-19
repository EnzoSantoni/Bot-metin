"""Logica pura de la lista editable de habilidades (sin tkinter): carga y guardado en el perfil de personaje,
validacion (reusa la de bot/config.py), alta/edicion/baja/orden y ejemplos."""
from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

from bot.config import DEFAULTS, ConfigError, _validate_profile_sections, parse_keys
from bot.profiles import load_profile, save_profile

logger = logging.getLogger(__name__)

DEFAULT_DURATION_S = 100.0
DEFAULT_ANIMATION_S = 2.0

# EJEMPLOS para cargar rapido. La duracion y la animacion (100 s / 2 s) son solo un punto de partida:
# hay que ajustarlas a la duracion real de cada habilidad en el servidor.
EXAMPLES: tuple[tuple[str, str], ...] = (
    ("Aura de Espada (guerrero)", "F1"),
    ("Espada Espejo (sura)", "F1"),
    ("Defensiva", "4"),
)


def new_skill(name: str = "", key: str = "") -> dict:
    """Habilidad con los valores por defecto (100 s, animacion 2 s, bajarse si, activa)."""
    return {"name": name, "key": key, "duration_s": DEFAULT_DURATION_S, "requires_dismount": True,
            "animation_s": DEFAULT_ANIMATION_S, "enabled": True}


def example_skills() -> list[dict]:
    """Los ejemplos como habilidades completas (nombre sin la aclaracion de clase entre parentesis)."""
    return [new_skill(label.split(" (")[0], key) for label, key in EXAMPLES]


def validate_skill(skill: dict) -> list[str]:
    """Errores de una habilidad, con las mismas reglas que bot/config.py (lista vacia = valida)."""
    cfg = copy.deepcopy(DEFAULTS)
    cfg["character"]["skills"] = [copy.deepcopy(skill)]
    try:
        _validate_profile_sections(cfg)
    except ConfigError as e:
        msg = str(e).replace("character.skills[0].", "")
        if msg.endswith("usa la tecla de panico o de pausa"):
            msg = msg.replace("usa la tecla de panico o de pausa", "choca con F11 o F12 (pausa y panico del bot)")
        return [msg]
    return []


def key_warnings(skill: dict, controls: dict[str, str] | None = None) -> list[str]:
    """Avisos (no bloquean) si la tecla coincide con una de `controls` (por ejemplo 'z' de recoger)."""
    controls = DEFAULTS["controls"] if controls is None else controls
    try:
        keys = set(parse_keys(skill.get("key")))
    except Exception:  # noqa: BLE001 - tecla invalida: ya la reporta validate_skill
        return []
    out = []
    for cname, spec in controls.items():
        try:
            same = bool(keys & set(parse_keys(spec)))
        except Exception:  # noqa: BLE001
            continue
        if same:
            out.append(f"La tecla {skill.get('key')!r} coincide con controls.{cname} ({spec!r}).")
    return out


def controls_for(servidor: str | None, root: str | Path | None = None) -> dict[str, str]:
    """`controls` efectivos: los de DEFAULTS pisados por el perfil de servidor (si se puede leer)."""
    controls = dict(DEFAULTS["controls"])
    if servidor:
        try:
            controls.update(load_profile("servidor", servidor, root).get("controls") or {})
        except ConfigError:
            pass
    return controls


def parse_seconds(text: str, what: str) -> float:
    """Texto de un campo numerico -> float. ValueError con mensaje claro si no es un numero."""
    try:
        v = float(str(text).strip().replace(",", "."))
    except ValueError as e:
        raise ValueError(f"{what} tiene que ser un numero.") from e
    if v != v or v in (float("inf"), float("-inf")):
        raise ValueError(f"{what} tiene que ser un numero finito.")
    return v


def remaining_text(remaining_s: float) -> str:
    """Tiempo restante para mostrar: 'ya' si vencio, si no mm:ss (redondeado hacia arriba)."""
    if remaining_s <= 0:
        return "ya"
    s = int(remaining_s) + (0 if remaining_s == int(remaining_s) else 1)
    return f"{s // 60:02d}:{s % 60:02d}"


class SkillsEditor:
    """Lista de habilidades del personaje elegido, con indicador de cambios sin guardar y bloqueo durante la corrida."""

    def __init__(self, profiles_root: str | Path | None = None) -> None:
        self.profiles_root = profiles_root
        self.personaje = ""
        self.skills: list[dict] = []
        self._saved: list[dict] = []
        self.locked = False

    @property
    def dirty(self) -> bool:
        return self.skills != self._saved

    def load(self, personaje: str) -> list[dict]:
        """Carga las habilidades del perfil (lista vacia si no hay perfil o no tiene la clave)."""
        self.personaje = personaje
        try:
            data = load_profile("personaje", personaje, self.profiles_root) if personaje else {}
        except ConfigError as e:
            logger.warning("No se pudieron cargar las habilidades: %s", e)
            data = {}
        sk = (data.get("character") or {}).get("skills")
        self.skills = [dict(s) for s in sk] if isinstance(sk, list) else []
        self._saved = copy.deepcopy(self.skills)
        return self.skills

    def _check_unlocked(self) -> None:
        if self.locked:
            raise RuntimeError("la lista de habilidades esta bloqueada mientras el bot corre")

    @staticmethod
    def _check_valid(skill: dict) -> None:
        errors = validate_skill(skill)
        if errors:
            raise ValueError("; ".join(errors))

    def add(self, skill: dict) -> None:
        self._check_unlocked()
        self._check_valid(skill)
        self.skills.append(dict(skill))

    def add_example(self, index: int) -> dict:
        """Agrega el ejemplo `index` de EXAMPLES y lo devuelve."""
        self._check_unlocked()
        sk = example_skills()[index]
        self.skills.append(sk)
        return sk

    def update(self, index: int, skill: dict) -> None:
        self._check_unlocked()
        self._check_valid(skill)
        self.skills[index] = dict(skill)

    def remove(self, index: int) -> None:
        self._check_unlocked()
        del self.skills[index]

    def move(self, index: int, delta: int) -> int:
        """Mueve una posicion (delta -1 arriba, +1 abajo). Devuelve el indice nuevo (el mismo si no se puede)."""
        self._check_unlocked()
        j = index + delta
        if not (0 <= index < len(self.skills) and 0 <= j < len(self.skills)):
            return index
        self.skills[index], self.skills[j] = self.skills[j], self.skills[index]
        return j

    def errors(self) -> list[str]:
        """Errores de toda la lista (con el numero de fila)."""
        return [f"Fila {i + 1}: {e}" for i, s in enumerate(self.skills) for e in validate_skill(s)]

    def save(self) -> Path:
        """Guarda en el perfil conservando el resto de sus claves (con .bak). ValueError si hay habilidades invalidas."""
        if not self.personaje:
            raise ValueError("No hay un perfil de personaje elegido.")
        errs = self.errors()
        if errs:
            raise ValueError("; ".join(errs))
        data: Any = load_profile("personaje", self.personaje, self.profiles_root)
        data.setdefault("character", {})["skills"] = copy.deepcopy(self.skills)
        path = save_profile("personaje", self.personaje, data, self.profiles_root)
        self._saved = copy.deepcopy(self.skills)
        return path
