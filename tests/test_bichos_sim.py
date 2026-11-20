"""Modo bichos (`zone.farm_mode = "bichos"`) de punta a punta: el Bot completo contra `SimWorld` con el senuelo
(`SimWorld.lure`: aparecen bichos que mueren con ESPACIO y dejan un objeto). Input falso, nada real."""
import pytest

import bot.input.base as base
from sim_bot_env import SimEnv, make_cfg

LURE_EVERY = 40.0
MARGIN = 4.0   # margen de redondeo/latencia sobre outside_tolerance


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


def bcfg(mode="teclado", **bi):
    cfg = make_cfg(navigation={"mode": mode})
    cfg["zone"]["farm_mode"] = "bichos"
    cfg["zone"]["bichos"].update(lure_every_s=LURE_EVERY, **bi)
    return cfg


def benv(seed=1, cfg=None, lure_count=3, **kw):
    env = SimEnv(seed, cfg=cfg or bcfg(), **kw)
    env.world.lure_count = lure_count
    return env


def tol(env):
    return env.zone.outside_tolerance + MARGIN


def left_clicks(env):
    return [e for e in env.backend.events if e[0] == "mouse_down" and e[1] == "left"]


def test_arranca_en_bichos_y_no_busca_metins():
    env = benv()
    assert env.bot.state.name == "BICHOS" and env.initial_state == "BICHOS"
    env.world.add_metin((520, 160), name="metin_de_oscuridad")   # hay un metin a la vista: se ignora
    env.run_until(lambda: env.world.t >= 60, max_s=70)
    names = {tr.new for tr in env.transitions}
    assert not names & {"BUSCAR", "ATACAR", "ACERCARSE", "EXPLORAR"}
    assert env.world.clicks_on_metin == [] and left_clicks(env) == []


@pytest.mark.parametrize("mode", ["teclado", "clic"])
def test_corrida_de_bichos(mode):
    env = benv(cfg=bcfg(mode))
    w = env.world
    dist = env.track_zone()
    why = env.run_until(lambda: w.t >= 200, max_s=260)
    assert why == "cond"
    # senuelo al empezar y cada lure_every_s
    assert len(w.lure_log) >= 4 and w.lure_log[0] < 5
    gaps = [b - a for a, b in zip(w.lure_log, w.lure_log[1:])]
    assert all(LURE_EVERY <= g <= LURE_EVERY + 15 for g in gaps), gaps
    assert env.bot.stats.lures == len(w.lure_log) and env.bot.stats.fight_s > 0
    # peleo con ESPACIO y recogio lo que dejaron
    assert w.bugs_killed >= 9 and len(w.picked) >= 6
    assert "RECOGER" in {tr.new for tr in env.transitions}
    assert all(tr.reason.startswith("bichos") for tr in env.transitions if tr.new == "RECOGER")
    assert 3 <= env.bot.stats.items <= len(w.picked)   # el conteo (estimado) sigue un objeto fijo; el simulador junta el mas cercano
    # nunca salio de la zona; clic izquierdo solo si el modo de caminata es por clics
    assert max(dist) <= tol(env)
    if mode == "teclado":
        assert left_clicks(env) == []
    assert w.clicks_on_metin == []
    # Ctrl+G siempre en pares: al final no quedo a pie salvo recogiendo
    if env.bot.state.name != "RECOGER":
        assert not env.bot._dismounted


def test_recoge_cuando_se_limpian():
    env = benv(cfg=bcfg(clear_s=3.0))
    w = env.world
    env.run_until(lambda: w.bugs_killed >= 3 and not w.bugs, max_s=60)
    t_clear = w.t
    env.run_until(lambda: w.mounted or env.bot.state.name == "RECOGER", max_s=30)
    first = next(tr for tr in env.transitions if tr.new == "RECOGER")
    assert first.t - t_clear <= 12.0   # clear_s cuenta desde la ultima vez que los vio (antes de que murieran)
    env.run_until(lambda: len(w.picked) >= 3 and env.bot.state.name != "RECOGER", max_s=90)
    assert len(w.picked) == 3 and env.bot.state.name == "BICHOS"


def test_vuelve_a_la_zona_si_arranca_afuera():
    env = benv(start=(550.0, 290.0))
    assert env.zone.signed_distance((550.0, 290.0)) < -env.zone.outside_tolerance
    w = env.world
    env.run_until(lambda: bool(w.lure_log), max_s=120)
    assert w.lure_log and env.zone_distance() == 0.0
    trs = [(tr.new, tr.reason) for tr in env.transitions]
    assert trs[0] == ("VOLVER", "bichos: fuera de la zona")
    assert ("BICHOS", "de vuelta en la zona") in trs and "BUSCAR" not in {n for n, _ in trs}
    assert env.world.lure_log[0] > env.transitions[0].t


def test_captcha_en_medio_de_la_pelea():
    env = benv()
    w = env.world
    w.schedule_captcha(2.0, 15.0)
    dist = env.track_zone()
    env.run_until(lambda: w.t >= 90, max_s=120)
    assert ("start", "captcha") in [(a, r) for _, a, r in env.alarm.log]
    assert any(a == "stop" for _, a, _r in env.alarm.log)
    assert any(tr.new == "CAPTCHA" for tr in env.transitions)
    quiet = [t for t, e in zip(env.backend.event_t, env.backend.events) if e[0] in ("key_down", "mouse_down")
             and 2.0 + 1.0 < t < 2.0 + 15.0]
    assert quiet == []   # nada de input mientras el panel esta
    cap_end = next(tr.t for tr in env.transitions if tr.old == "CAPTCHA")
    assert env.transitions[[tr.old for tr in env.transitions].index("CAPTCHA")].new == "BICHOS"
    assert w.bugs_killed >= 3 and max(dist) <= tol(env) and cap_end >= 17.0


def test_sin_monstruos_camina_adentro_de_la_zona():
    env = benv(cfg=bcfg(idle_s=20.0), lure_count=0)
    w = env.world
    dist = env.track_zone()
    start = tuple(w.pos)
    env.run_until(lambda: w.t >= 150, max_s=200)
    assert w.lure_log and w.bugs_killed == 0
    assert max(abs(w.pos[0] - start[0]), abs(w.pos[1] - start[1])) > 10 or len(env.backend.events) > 40
    assert max(dist) <= tol(env) and left_clicks(env) == []
    assert not {tr.new for tr in env.transitions} & {"BUSCAR", "EXPLORAR", "ATACAR"}
    # nunca giro la camara (Q/E)
    assert not any(e[0] == "key_down" and e[1] in ("q", "e") for e in env.backend.events)


def test_habilidades_en_bichos_no_en_recoger():
    cfg = bcfg()
    cfg["character"]["skills"] = [{"name": "Aura", "key": "f1", "duration_s": 30, "requires_dismount": False,
                                   "animation_s": 0.5, "enabled": True}]
    env = benv(cfg=cfg)
    w = env.world
    env.run_until(lambda: w.t >= 120, max_s=160)
    assert len(w.skill_log) >= 3 and all(eff for _, _, _, eff in w.skill_log)
    for t, *_ in w.skill_log:
        assert env.state_at(t) == "BICHOS"
