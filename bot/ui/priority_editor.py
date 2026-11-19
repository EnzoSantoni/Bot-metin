"""Logica pura (sin tkinter) del editor de prioridad entre tipos de metin de un perfil de zona."""
from __future__ import annotations

import logging
from pathlib import Path

from bot.config import ConfigError
from bot.profiles import load_profile, save_profile
from bot.vision.metin import slugify

logger = logging.getLogger(__name__)


class PriorityEditor:
    """Orden de trabajo: primero los de la prioridad guardada, despues el resto de los objetivos de la zona."""

    def __init__(self, profiles_root: str | Path | None = None) -> None:
        self.profiles_root = profiles_root
        self.zona = ""
        self.names: list[str] = []
        self.enabled = False
        self._saved: tuple[list[str], bool] = ([], False)
        self.locked = False

    @property
    def dirty(self) -> bool:
        return (self.names, self.enabled) != self._saved

    def load(self, zona: str) -> list[str]:
        """Carga los objetivos y la prioridad del perfil de zona (vacio si no se puede leer)."""
        self.zona = zona
        try:
            data = load_profile("zona", zona, self.profiles_root) if zona else {}
        except ConfigError as e:
            logger.warning("No se pudo cargar la prioridad: %s", e)
            data = {}
        targets = [n for n in data.get("target_metin_names") or [] if isinstance(n, str)]
        prio = (data.get("zone") or {}).get("metin_priority") or []
        by_slug = {slugify(n): n for n in targets}
        first = [by_slug[s] for s in dict.fromkeys(slugify(n) for n in prio if isinstance(n, str)) if s in by_slug]
        self.names = first + [n for n in targets if n not in first]
        self.enabled = bool(first)
        self._saved = (list(self.names), self.enabled)
        return self.names

    def _check_unlocked(self) -> None:
        if self.locked:
            raise RuntimeError("la prioridad esta bloqueada mientras el bot corre")

    def move(self, index: int, delta: int) -> int:
        """Mueve una posicion (-1 arriba, +1 abajo). Devuelve el indice nuevo (el mismo si no se puede)."""
        self._check_unlocked()
        j = index + delta
        if not (0 <= index < len(self.names) and 0 <= j < len(self.names)):
            return index
        self.names[index], self.names[j] = self.names[j], self.names[index]
        return j

    def set_enabled(self, value: bool) -> None:
        self._check_unlocked()
        self.enabled = bool(value)

    def save(self) -> Path:
        """Guarda la lista ordenada (o vacia si esta apagado) conservando el resto del perfil, con .bak."""
        if not self.zona:
            raise ValueError("No hay un perfil de zona elegido.")
        data = load_profile("zona", self.zona, self.profiles_root)
        data.setdefault("zone", {})["metin_priority"] = list(self.names) if self.enabled else []
        path = save_profile("zona", self.zona, data, self.profiles_root)
        self._saved = (list(self.names), self.enabled)
        return path
