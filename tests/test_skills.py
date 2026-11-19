"""Fase 11: habilidades con temporizador (planificador, lanzamiento, momentos seguros, captcha, panico, baselines)."""
import json

import pytest

import baseline_scenarios as bs
import bot.input.base as base
from bot.bot import BotStats, State
from bot.config import ConfigError, validate_config
from bot.skills import Skill, SkillScheduler
from sim_bot_env import SimEnv, make_cfg

OSC = "metin_de_oscuridad"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


def sk(name="A", key="f1", dur=60.0, rd=False, anim=1.0, en=True) -> dict:
    return {"name": name, "key": key, "duration_s": dur, "requires_dismount": rd, "animation_s": anim, "enabled": en}


def skill_env(skills, seed=1, timing=None, metins=(), mounted=True, pickup=None, **kw) -> SimEnv:
    over = {"character": {"skills": skills}}
    if timing:
        over["skill_timing"] = timing
    if pickup:
        over["pickup"] = pickup
    env = SimEnv(seed, cfg=make_cfg(**over), **kw)
    env.world.mounted = mounted
    for pos in metins:
        env.world.add_metin(pos, name=OSC)
    return env


def key_downs(env) -> list[str]:
    return [e[1] for e in env.backend.events if e[0] == "key_down"]


def spy_casts(env) -> list[dict]:
    """Anota el contexto (estado, ataque, caminata) en el instante de cada lanzamiento."""
    bot, out = env.bot, []
    orig = bot._cast_skill

    def wrap(skill):
        a = bot._atk
        out.append({"t": env.world.t, "state": bot.state, "walk": bot._walk, "skill": skill,
                    "confirmed": a.confirmed if a else None, "since_click": env.world.t - a.last_click_t if a else None,
                    "pending": a.pending if a else None, "mounted": env.world.mounted})
        return orig(skill)
    bot._cast_skill = wrap
    return out


def all_effective(env) -> bool:
    return all(eff for _, _, _, eff in env.world.skill_log)


# ---------------------------------------------------------------- planificador
def test_scheduler_start_due_y_deshabilitadas():
    now = [0.0]
    s = SkillScheduler([sk("A"), sk("B", "f2", en=False), sk("C", "f3")], lambda: now[0])
    assert [x.name for x in s.skills] == ["A", "C"]
    assert s.due(100) == [] and s.next_due_in(0) is None    # sin start no vence nada
    s.start(10.0)
    assert [x.name for x in s.due(10.0)] == ["A", "C"]
    assert s.due(9.9) == [] and s.next_due_in(5.0) == 5.0 and s.next_due_in(11.0) == 0.0


def test_scheduler_mark_cast_orden_por_atraso_y_margen():
    s = SkillScheduler([sk("A", dur=10), sk("B", "f2", dur=10), sk("C", "f3", dur=10)], lambda: 0.0, recast_margin_s=2.0)
    s.start(0.0)
    a, b, c = s.skills
    s.mark_cast(c, 0.0)      # vence en 8
    s.mark_cast(b, 1.0)      # vence en 9
    s.mark_cast(a, 5.0)      # vence en 13
    assert s.due(7.9) == []
    assert s.due(8.0) == [c] and s.due(9.5) == [c, b]
    assert s.due(20.0) == [c, b, a]                  # la mas atrasada primero
    assert s.due_in(a, 5.0) == 8.0 and s.due_in(c, 9.0) == 0.0


def test_scheduler_margen_mayor_que_la_duracion_vence_enseguida_y_snapshot():
    now = [3.0]
    s = SkillScheduler([sk("A", dur=5)], lambda: now[0], recast_margin_s=9.0)
    s.start(0.0)
    s.mark_cast(s.skills[0], 3.0)
    assert [x.name for x in s.due(3.0)] == ["A"]
    s2 = SkillScheduler([sk("A", dur=30)], lambda: now[0])
    s2.start(0.0)
    s2.mark_cast(s2.skills[0], 3.0)
    assert s2.snapshot() == [{"name": "A", "key": "f1", "remaining_s": 30.0}]
    now[0] = 13.0
    assert s2.snapshot()[0]["remaining_s"] == 20.0


def test_skill_desde_config():
    assert Skill.from_config(sk("X", key=" F1 ")) == Skill("X", "f1", 60.0, False, 1.0, True)


# ---------------------------------------------------------------- config
def test_validacion_de_habilidades_y_tiempos():
    validate_config(make_cfg(character={"skills": [sk()]}))
    for bad in (sk(dur=0), sk(anim=-1), sk(key="f12"), sk(key="f11"), sk(key="nada"), sk(en="si"), {**sk(), "requires_dismount": 1}):
        with pytest.raises(ConfigError):
            validate_config(make_cfg(character={"skills": [bad]}))
    for k, v in (("chunk_s", 0.6), ("chunk_s", 0), ("press_hold_s", 0), ("mount_wait_s", -1), ("recast_margin_s", "x")):
        with pytest.raises(ConfigError):
            validate_config(make_cfg(skill_timing={k: v}))


def test_press_key_valida_la_tecla():
    env = skill_env([])
    assert env.gi.press_key("f1", 0.0) and ("key_down", "f1") in env.backend.events
    for bad in ("tecla_rara", "f12", "ctrl+f11", ""):
        with pytest.raises(ValueError):
            env.gi.press_key(bad)


# ---------------------------------------------------------------- baselines: con la lista vacia nada cambia
@pytest.mark.parametrize("name", bs.NAMES)
@pytest.mark.parametrize("skills", [[], [sk(en=False), sk("B", "f2", rd=True, en=False)]], ids=["vacia", "deshabilitadas"])
def test_baseline_identico_sin_habilidades_activas(name, skills):
    expected = json.loads((bs.DATA / f"baseline_events_{name}.json").read_text(encoding="utf-8"))
    got = json.loads(json.dumps(bs.run(name, skills)))
    assert got["events"] == expected["events"]
    assert got["transitions"] == expected["transitions"]
    assert got == expected


# ---------------------------------------------------------------- lanzamiento
def test_al_iniciar_se_lanzan_todas_en_orden_con_la_secuencia_exacta():
    env = skill_env([sk("A", "f1", rd=True, anim=1.0), sk("B", "4", rd=False, anim=0.5), sk("C", "f5", rd=True, anim=1.0)],
                    timing={"mount_wait_s": 1.5})
    assert env.run_until(lambda: env.bot.stats.skills_cast == 3, max_s=30) == "cond"
    assert key_downs(env) == ["ctrl", "g", "f1", "ctrl", "g", "4", "ctrl", "g", "f5", "ctrl", "g"]
    ts = [t for t, e in zip(env.backend.event_t, env.backend.events) if e[0] == "key_down"]
    assert ts[2] - ts[0] == pytest.approx(1.5, abs=0.05)        # Ctrl+G -> espera -> tecla
    assert ts[3] - ts[2] == pytest.approx(1.0, abs=0.05)        # tecla -> animacion -> Ctrl+G
    assert [(k, m) for _, k, m, _ in env.world.skill_log] == [("f1", False), ("4", True), ("f5", False)]
    assert all_effective(env) and env.world.mounted and not env.bot._dismounted
    assert env.backend.pressed == ([], [])


def test_sin_requires_dismount_solo_la_tecla():
    env = skill_env([sk("A", "f1", rd=False, anim=0.5)])
    assert env.run_until(lambda: env.bot.stats.skills_cast == 1, max_s=10) == "cond"
    assert key_downs(env) == ["f1"] and env.world.mounted


def test_ya_desmontado_no_se_baja_ni_se_sube():
    env = skill_env([sk("A", "f1", rd=True)], mounted=False)
    env.bot._dismounted = True
    assert env.run_until(lambda: env.bot.stats.skills_cast == 1, max_s=10) == "cond"
    assert key_downs(env) == ["f1"] and not env.world.mounted and env.bot._dismounted and all_effective(env)


def test_relanza_al_vencer():
    env = skill_env([sk("A", "f1", dur=20.0, rd=True, anim=1.0)])
    assert env.run_until(lambda: env.bot.stats.skills_cast >= 4, max_s=200) == "cond"
    ts = [t for t, *_ in env.world.skill_log]
    assert ts[0] < 2.0
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    assert all(21.0 <= g <= 45.0 for g in gaps), gaps       # duracion + animacion (+ esperar a un momento seguro)
    assert all_effective(env) and env.world.mounted


def test_estadisticas_y_estado_para_la_interfaz():
    import run_bot
    env = skill_env([sk("A", "f1", dur=40.0), sk("B", "f2", dur=90.0, en=False)])
    assert "habilidades" not in BotStats().summary()
    assert env.run_until(lambda: env.bot.stats.skills_cast == 1, max_s=10) == "cond"
    assert "habilidades lanzadas: 1" in env.bot.stats.summary()
    st = run_bot.UiBridge(env.bot, env.control, env.cfg, stdin=None).status()
    assert st["skills_cast"] == 1
    assert [(x["name"], x["key"]) for x in st["skills"]] == [("A", "f1")] and 30 < st["skills"][0]["remaining_s"] <= 40
    json.dumps(st)


# ---------------------------------------------------------------- momentos seguros
def test_nunca_en_recoger_ni_a_mitad_de_caminata_y_atacando_solo_confirmado_y_con_reposo():
    env = skill_env([sk("A", "f1", dur=8.0, rd=False, anim=0.5), sk("B", "f2", dur=11.0, rd=False, anim=0.5)],
                    metins=[(500, 160), (600, 150), (680, 165), (470, 180)])
    casts = spy_casts(env)
    settle = env.cfg["skill_timing"]["attack_settle_s"]
    targets = list(env.world.metins)
    assert env.run_until(lambda: all(not m.alive for m in targets) and not any(
        i.kind == "item" and not i.reserved_other for i in env.world.items), max_s=900) == "cond"
    assert len(env.world.broken) == 4 and len(casts) >= 4
    assert {c["state"] for c in casts} <= {State.BUSCAR, State.EXPLORAR, State.ATACAR}
    attacking = [c for c in casts if c["state"] is State.ATACAR]
    assert attacking, "ninguna habilidad se lanzo atacando: el escenario no prueba nada"
    for c in casts:
        if c["state"] is State.EXPLORAR:
            assert c["walk"] is None
        if c["state"] is State.ATACAR:
            assert c["confirmed"] and c["pending"] is None and c["since_click"] >= settle - 1e-6
        assert env.state_at(c["t"] + 1e-6) != "RECOGER"
    # despues del lanzamiento en ATACAR hay otro clic en el metin sin descartarlo
    for c in attacking:
        assert any(t > c["t"] + 0.4 for t, _ in env.world.clicks_on_metin if t < c["t"] + 3.0), c
    assert not any("descartado" in t.reason for t in env.transitions)
    # un clic (move_to -> mouse_up) nunca se parte con una tecla de habilidad
    in_click = False
    for e in env.backend.events:
        if e[0] == "move_to":
            in_click = True
        elif e[0] == "mouse_up":
            in_click = False
        elif e[0] == "key_down" and e[1] in ("f1", "f2"):
            assert not in_click
    assert all_effective(env)


# ---------------------------------------------------------------- captcha y panico
@pytest.mark.parametrize("t_captcha,pressed_before", [(0.8, 0), (2.5, 1)])
def test_captcha_a_mitad_del_lanzamiento_aborta_sube_al_salir_y_relanza(t_captcha, pressed_before):
    env = skill_env([sk("A", "f1", dur=60.0, rd=True, anim=3.0)], timing={"mount_wait_s": 1.5})
    env.world.schedule_captcha(t_captcha, 10.0)
    assert env.run_until(lambda: any(a == "stop" for _, a, _ in env.alarm.log), max_s=60) == "cond"
    t0, t1 = env.alarm.log[0][0], env.alarm.log[1][0]
    assert env.alarm.log[0][2] == "captcha" and t0 - t_captcha < 1.0
    assert len(env.world.skill_log) == pressed_before and env.bot.stats.skills_cast == 0
    assert not [t for t in env.backend.input_times() if t0 < t < t1]
    assert env.backend.pressed == ([], [])
    assert env.run_until(lambda: env.bot.stats.skills_cast == 1, max_s=60) == "cond"
    assert env.bot.stats.captchas == 1
    assert len(env.world.skill_log) == pressed_before + 1 and all_effective(env)   # quedo vencida: se relanzo
    assert env.world.mounted and not env.bot._dismounted                          # Ctrl+G emparejado
    assert key_downs(env).count("g") % 2 == 0


@pytest.mark.parametrize("t_panic", [0.8, 2.5, 4.2])
def test_panico_a_mitad_del_lanzamiento_no_manda_nada_mas(t_panic):
    env = skill_env([sk("A", "f1", dur=60.0, rd=True, anim=3.0)], timing={"mount_wait_s": 1.5})
    env.schedule_panic(t_panic)
    assert env.run_until(max_s=30) == "panico"
    idx = env.control.on_panic_index
    assert all(e[0] in ("key_up", "mouse_up") for e in env.backend.events[idx:])
    assert env.backend.pressed == ([], [])
    assert env.bot.stats.skills_cast == 0


def test_muerte_durante_el_lanzamiento_termina_la_corrida_sin_input_nuevo():
    env = skill_env([sk("A", "f1", dur=60.0, rd=True, anim=3.0)])
    env.world.schedule_death(2.5)
    assert env.run_until(max_s=30) == "fin"
    assert env.bot.stats.end_reason == "muerte" and env.alarm.log[0][2] == "muerte"
    n = len(env.backend.events)
    for _ in range(5):
        env.bot.step()
    assert len(env.backend.events) == n and env.backend.pressed == ([], [])


# ---------------------------------------------------------------- junto con pickup.dismount
@pytest.mark.parametrize("seed", [1, 2])
def test_con_pickup_dismount_ctrl_g_nunca_se_desparea(seed):
    env = skill_env([sk("A", "f1", dur=9.0, rd=True, anim=1.0), sk("B", "f2", dur=13.0, rd=False, anim=0.5)], seed=seed,
                    pickup={"dismount": True, "mount_wait_s": 1.0}, timing={"mount_wait_s": 1.0},
                    metins=[(500, 160), (600, 150), (680, 165), (470, 180)])
    targets = list(env.world.metins)
    assert env.run_until(lambda: all(not m.alive for m in targets) and not any(
        i.kind == "item" and not i.reserved_other for i in env.world.items), max_s=900) == "cond"
    assert env.run_until(lambda: env.bot.state in (State.BUSCAR, State.EXPLORAR) and not env.bot._dismounted
                         and env.world.t > 0, max_s=60) == "cond"
    assert env.bot.stats.skills_cast >= 3 and len(env.world.picked) == 8
    assert all_effective(env)
    assert env.world.mounted and not env.bot._dismounted
    assert key_downs(env).count("g") % 2 == 0
