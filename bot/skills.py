"""Habilidades con temporizador: que toca lanzar y cuando. No manda input: lo hace `Bot._cast_skill`."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class Skill:
    """Una habilidad de `character.skills` (ya validada por `bot.config`)."""
    name: str
    key: str
    duration_s: float
    requires_dismount: bool
    animation_s: float
    enabled: bool = True

    @classmethod
    def from_config(cls, d: dict[str, Any]) -> "Skill":
        return cls(str(d["name"]), str(d["key"]).strip().lower(), float(d["duration_s"]), bool(d["requires_dismount"]),
                   float(d["animation_s"]), bool(d.get("enabled", True)))


class SkillScheduler:
    """Lleva la cuenta de cuando vence cada habilidad activa. Sin `start`, ninguna vence."""

    def __init__(self, skills: Iterable[Skill | dict], clock: Callable[[], float], recast_margin_s: float = 0.0) -> None:
        parsed = [s if isinstance(s, Skill) else Skill.from_config(s) for s in skills]
        self.skills = [s for s in parsed if s.enabled]
        self._clock = clock
        self._margin = float(recast_margin_s)
        self._next: dict[int, float] = {}    # indice en `skills` -> instante en que vence

    def __bool__(self) -> bool:
        return bool(self.skills)

    def start(self, t: float) -> None:
        """Al iniciar la sesion todas las activas quedan vencidas en `t`."""
        self._next = {i: t for i in range(len(self.skills))}

    def due(self, t: float) -> list[Skill]:
        """Habilidades vencidas en `t`, la mas atrasada primero (empate: orden de la lista)."""
        idx = sorted((i for i, at in self._next.items() if at <= t), key=lambda i: (self._next[i], i))
        return [self.skills[i] for i in idx]

    def mark_cast(self, skill: Skill, t: float) -> None:
        """Lanzada en `t`: la proxima vence en `duration_s` menos el margen."""
        i = next(i for i, s in enumerate(self.skills) if s is skill)
        self._next[i] = t + max(0.0, skill.duration_s - self._margin)

    def due_in(self, skill: Skill, t: float) -> float:
        """Segundos hasta que vence `skill` (0 si ya vencio o no se inicio)."""
        i = next(i for i, s in enumerate(self.skills) if s is skill)
        return max(0.0, self._next.get(i, t) - t)

    def next_due_in(self, t: float) -> float | None:
        """Segundos hasta el proximo vencimiento (0 si ya hay una vencida; None sin habilidades activas)."""
        if not self._next:
            return None
        return max(0.0, min(self._next.values()) - t)

    def snapshot(self) -> list[dict[str, Any]]:
        """Para la interfaz: nombre, tecla y segundos que faltan de cada habilidad activa."""
        t = self._clock()
        return [{"name": s.name, "key": s.key, "remaining_s": round(max(0.0, self._next.get(i, t) - t), 1)}
                for i, s in enumerate(self.skills)]
