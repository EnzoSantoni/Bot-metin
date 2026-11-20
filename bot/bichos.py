"""Modo bichos ("F4 cada X segundos + ESPACIO + recoger"): configuracion y decision pura de que hacer en cada ciclo.

Sin capturas ni input: `decide` recibe una foto del momento (`BichosView`) y devuelve la accion. Quien ejecuta es
`Bot._do_bichos`.
"""
from __future__ import annotations

from dataclasses import dataclass

LURE, PICKUP, FIGHT, WALK, WAIT = "lure", "pickup", "fight", "walk", "wait"


@dataclass(frozen=True)
class BichosCfg:
    """`zone.bichos` del perfil de zona."""
    lure_key: str
    lure_every_s: float
    min_monsters: int
    space_s: float
    clear_s: float
    pickup_every_s: float
    pickup_max_monsters: int
    idle_s: float

    @classmethod
    def from_config(cls, cfg: dict) -> "BichosCfg":
        b = cfg["zone"]["bichos"]
        return cls(str(b["lure_key"]), float(b["lure_every_s"]), int(b["min_monsters"]), float(b["space_s"]),
                   float(b["clear_s"]), float(b["pickup_every_s"]), int(b["pickup_max_monsters"]), float(b["idle_s"]))


@dataclass(frozen=True)
class BichosView:
    """Lo que se sabe en este ciclo (tiempos en segundos)."""
    monsters: int            # monstruos cerca (`Perception.monsters_near`)
    items: int               # objetos validos a la vista
    lure_ready: bool         # toca usar el senuelo
    calm_s: float            # hace cuanto no hay monstruos cerca (0 si hay)
    idle_s: float            # hace cuanto no pasa nada util (sin monstruos, sin caminar): para caminar a otro lugar
    since_pickup_s: float    # desde la ultima recogida
    pickup_blocked: bool     # la ultima recogida no junto nada: no insistir todavia


def decide(c: BichosCfg, v: BichosView) -> str:
    """LURE (senuelo vencido) > PICKUP periodico (con pocos monstruos) > FIGHT (monstruos cerca) > PICKUP (zona limpia
    `clear_s`) > WALK (`idle_s` sin monstruos) > WAIT."""
    can_pick = v.items > 0 and not v.pickup_blocked
    if v.lure_ready:
        return LURE
    if can_pick and v.since_pickup_s >= c.pickup_every_s and v.monsters <= c.pickup_max_monsters:
        return PICKUP
    if v.monsters >= c.min_monsters:
        return FIGHT
    if can_pick and v.calm_s >= c.clear_s:
        return PICKUP
    if v.idle_s >= c.idle_s:
        return WALK
    return WAIT
