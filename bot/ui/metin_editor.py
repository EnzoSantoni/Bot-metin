"""Logica pura (sin tkinter) de la pestana Metin: tipos de metin de una zona, cuales se atacan y su prioridad.

Los tipos son los `target_metin_names` del perfil de zona mas los que tienen plantilla de etiqueta en la carpeta de
plantillas. Guardar escribe `target_metin_names` y `zone.metin_priority` en el perfil de zona (con .bak)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from bot.config import ConfigError
from bot.profiles import load_profile, save_profile
from bot.vision.metin import slugify

logger = logging.getLogger(__name__)

_SMALL_WORDS = {"de", "del", "la", "las", "los", "el", "y", "en"}
ERR_NONE_CHECKED = "Marcá al menos un tipo de metin para atacar."


def pretty_name(slug: str) -> str:
    """'metin_de_avaricia' -> 'Metin de Avaricia' (nombre para mostrar de un tipo que solo tiene plantilla)."""
    words = [w for w in slug.split("_") if w]
    return " ".join(w if (i and w in _SMALL_WORDS) else w.capitalize() for i, w in enumerate(words))


def template_slugs(labels_dir: str | Path | None) -> list[str]:
    """Slugs de las plantillas de etiqueta (*.png) de la carpeta, ordenados. Vacio si no existe."""
    if labels_dir is None:
        return []
    d = Path(labels_dir)
    return sorted(p.stem for p in d.glob("*.png")) if d.is_dir() else []


@dataclass
class MetinType:
    """Un tipo de metin de la lista: nombre, si se ataca y si tiene plantilla de etiqueta."""
    name: str
    attack: bool
    has_template: bool

    @property
    def slug(self) -> str:
        return slugify(self.name)


def metin_types(targets: list[str], priority: list[str], labels_dir: str | Path | None) -> list[MetinType]:
    """Lista ordenada de tipos: primero los de la prioridad guardada (que sean objetivo), luego el resto de los objetivos
    y al final los que solo tienen plantilla (sin marcar)."""
    slugs = set(template_slugs(labels_dir))
    by_slug = {slugify(n): n for n in targets}
    first = [by_slug[s] for s in dict.fromkeys(slugify(n) for n in priority if isinstance(n, str)) if s in by_slug]
    ordered = first + [n for n in targets if n not in first]
    out = [MetinType(n, True, slugify(n) in slugs) for n in ordered]
    known = {t.slug for t in out}
    out += [MetinType(pretty_name(s), False, True) for s in sorted(slugs) if s not in known]
    return out


class MetinEditor:
    """Tipos de metin de la zona elegida con casilla "Atacar" y orden de prioridad. Indicador de cambios sin guardar y
    bloqueo durante la corrida."""

    def __init__(self, profiles_root: str | Path | None = None) -> None:
        self.profiles_root = profiles_root
        self.zona = ""
        self.types: list[MetinType] = []
        self.enabled = False
        self.locked = False
        self._targets: list[str] = []
        self._saved: tuple = self._state()

    def _state(self) -> tuple:
        return ([(t.name, t.attack) for t in self.types], self.enabled)

    @property
    def dirty(self) -> bool:
        return self._state() != self._saved

    @property
    def names(self) -> list[str]:
        return [t.name for t in self.types]

    def load(self, zona: str, labels_dir: str | Path | None = None) -> None:
        """Carga los tipos de la zona (vacio si el perfil no se puede leer)."""
        self.zona = zona
        try:
            data = load_profile("zona", zona, self.profiles_root) if zona else {}
        except ConfigError as e:
            logger.warning("No se pudieron cargar los tipos de metin: %s", e)
            data = {}
        self._targets = [n for n in data.get("target_metin_names") or [] if isinstance(n, str)]
        prio = [n for n in (data.get("zone") or {}).get("metin_priority") or [] if isinstance(n, str)]
        self.types = metin_types(self._targets, prio, labels_dir)
        self.enabled = any(slugify(p) in {t.slug for t in self.types if t.attack} for p in prio)
        self._saved = self._state()

    def reload_types(self, labels_dir: str | Path | None) -> None:
        """Recarga desde disco (por ejemplo despues de agregar un tipo nuevo)."""
        self.load(self.zona, labels_dir)

    def _check_unlocked(self) -> None:
        if self.locked:
            raise RuntimeError("los tipos de metin estan bloqueados mientras el bot corre")

    def set_attack(self, index: int, value: bool) -> None:
        self._check_unlocked()
        if 0 <= index < len(self.types):
            self.types[index].attack = bool(value)

    def move(self, index: int, delta: int) -> int:
        """Mueve una posicion (-1 arriba, +1 abajo). Devuelve el indice nuevo (el mismo si no se puede)."""
        self._check_unlocked()
        j = index + delta
        if not (0 <= index < len(self.types) and 0 <= j < len(self.types)):
            return index
        self.types[index], self.types[j] = self.types[j], self.types[index]
        return j

    def set_enabled(self, value: bool) -> None:
        self._check_unlocked()
        self.enabled = bool(value)

    def errors(self) -> list[str]:
        """Errores que impiden guardar."""
        return [] if any(t.attack for t in self.types) else [ERR_NONE_CHECKED]

    def warnings(self) -> list[str]:
        """Avisos (no bloquean): tipos marcados sin plantilla de etiqueta."""
        return [f"Falta la plantilla de etiqueta de '{t.name}': el bot no va a arrancar hasta agregarla "
                f"(botón Agregar tipo nuevo)." for t in self.types if t.attack and not t.has_template]

    def priority_position(self, index: int) -> int | None:
        """Posicion (desde 1) entre los tipos marcados, o None si no se ataca o la prioridad esta apagada."""
        t = self.types[index]
        if not self.enabled or not t.attack:
            return None
        return 1 + sum(1 for u in self.types[:index] if u.attack)

    def rows(self) -> list[tuple[str, str, str, str]]:
        """Filas para la tabla: (prioridad, atacar, nombre, plantilla)."""
        out = []
        for i, t in enumerate(self.types):
            pos = self.priority_position(i)
            out.append((str(pos) if pos else "-", "☑" if t.attack else "☐", t.name, "Sí" if t.has_template else "No"))
        return out

    def save(self) -> Path:
        """Guarda `target_metin_names` y la prioridad conservando el resto del perfil, con .bak. ValueError si no es valido."""
        if not self.zona:
            raise ValueError("No hay un perfil de zona elegido.")
        errs = self.errors()
        if errs:
            raise ValueError(" ".join(errs))
        data = load_profile("zona", self.zona, self.profiles_root)
        checked = [t.name for t in self.types if t.attack]
        checked_slugs = {slugify(n) for n in checked}
        original = [n for n in data.get("target_metin_names") or [] if isinstance(n, str) and slugify(n) in checked_slugs]
        have = {slugify(n) for n in original}
        targets = original + [n for n in checked if slugify(n) not in have]
        data["target_metin_names"] = targets
        data.setdefault("zone", {})["metin_priority"] = checked if self.enabled else []
        path = save_profile("zona", self.zona, data, self.profiles_root)
        self._targets = targets
        self._saved = self._state()
        return path
