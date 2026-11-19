"""Escenarios del simulador con semilla fija para comparar la secuencia de input y de estados (Fase 11: con la lista
de habilidades vacia nada cambia). `python tests/baseline_scenarios.py` regraba `tests/data/baseline_events_*.json`."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parent.parent)]

import bot.input.base as base  # noqa: E402
from sim_bot_env import SimEnv, make_cfg  # noqa: E402
from sim_game import Box  # noqa: E402

OSC, NEG = "metin_de_oscuridad", "metin_negro"
INSIDE = [((500, 160), OSC), ((600, 150), NEG), ((680, 165), OSC), ((470, 180), NEG)]
DATA = Path(__file__).parent / "data"
NAMES = ("farmeo", "captcha", "trabas", "recoger_montado")


def _farm(seed: int, cfg: dict) -> SimEnv:
    env = SimEnv(seed, cfg=cfg)
    for pos, name in INSIDE:
        env.world.add_metin(pos, name=name)
    return env


def build(name: str, skills: list | None = None) -> tuple[SimEnv, int]:
    """(entorno, tiempo simulado maximo) del escenario. `skills`: lista de character.skills (vacia por defecto)."""
    over = {"character": {"skills": list(skills or [])}}
    cfg = make_cfg(**over)
    if name == "farmeo":
        return _farm(1, cfg), 200
    if name == "captcha":
        env = SimEnv(5, phi=1.0, cfg=cfg)
        env.world.add_metin((580, 150), name=OSC)
        env.world.schedule_captcha(12.0, 40.0)
        return env, 120
    if name == "trabas":
        env = SimEnv(4, phi=0.3, start=(490, 165), obstacles=[Box(528, 118, 538, 212)], label_dist=90, cfg=cfg)
        env.world.add_metin((580, 160), name=OSC)
        return env, 150
    if name == "recoger_montado":
        return _farm(2, make_cfg(pickup={"dismount": True, "mount_wait_s": 1.0}, **over)), 200
    raise KeyError(name)


def run(name: str, skills: list | None = None) -> dict:
    """Corre el escenario y devuelve eventos del backend (con su tiempo) y transiciones de estado."""
    env, max_s = build(name, skills)
    env.run_until(max_s=max_s)
    return {"events": [[round(t, 4), *e] for t, e in zip(env.backend.event_t, env.backend.events)],
            "transitions": [[round(t.t, 4), t.old, t.new, t.reason] for t in env.transitions],
            "alarm": [[round(t, 4), a, r] for t, a, r in env.alarm.log], "mounted": env.world.mounted}


if __name__ == "__main__":
    base._sleep = lambda s: None
    for n in NAMES:
        data = run(n)
        (DATA / f"baseline_events_{n}.json").write_text(json.dumps(data), encoding="utf-8")
        print(n, len(data["events"]), len(data["transitions"]))
