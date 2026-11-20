"""Modo bichos: decision pura (bot/bichos.py), validacion de config, resumen, historial y @@STATUS."""
import copy
from datetime import datetime

import pytest

import bot.history as hist
from bot.bichos import FIGHT, LURE, PICKUP, WAIT, WALK, BichosCfg, BichosView, decide
from bot.bot import BotStats
from bot.config import DEFAULTS, ConfigError, validate_config
from bot.ui import controller as c

CFG = BichosCfg.from_config(DEFAULTS)


def view(**kw):
    base = dict(monsters=0, items=0, lure_ready=False, calm_s=0.0, idle_s=0.0, since_pickup_s=0.0, pickup_blocked=False)
    base.update(kw)
    return BichosView(**base)


def test_defaults():
    assert CFG.lure_key == "f4" and CFG.lure_every_s == 60 and CFG.min_monsters == 1 and CFG.space_s == 2.0
    assert (CFG.clear_s, CFG.pickup_every_s, CFG.pickup_max_monsters, CFG.idle_s) == (3.0, 60.0, 1, 20.0)


@pytest.mark.parametrize("kw,expected", [
    (dict(), WAIT),
    (dict(lure_ready=True), LURE),
    (dict(lure_ready=True, monsters=5, items=3, calm_s=99, since_pickup_s=99), LURE),   # el senuelo manda
    (dict(monsters=1), FIGHT),
    (dict(monsters=4, items=2), FIGHT),                                              # peleando no se recoge (antes del periodico)
    (dict(items=2, calm_s=2.9), WAIT),                                               # todavia no pasaron clear_s
    (dict(items=2, calm_s=3.0), PICKUP),
    (dict(items=0, calm_s=30), WAIT),                                                # sin objetos no hay que recoger
    (dict(items=2, calm_s=30, pickup_blocked=True), WAIT),
    (dict(monsters=1, items=1, since_pickup_s=60), PICKUP),                          # periodico con 1 monstruo
    (dict(monsters=2, items=1, since_pickup_s=60), FIGHT),                           # demasiados monstruos para el periodico
    (dict(monsters=1, items=1, since_pickup_s=59), FIGHT),
    (dict(monsters=1, items=0, since_pickup_s=600), FIGHT),
    (dict(monsters=1, items=1, since_pickup_s=600, pickup_blocked=True), FIGHT),
    (dict(idle_s=20), WALK),
    (dict(idle_s=19.9), WAIT),
    (dict(idle_s=50, items=1, calm_s=50), PICKUP),                                   # recoger antes que caminar
])
def test_decide(kw, expected):
    assert decide(CFG, view(**kw)) == expected


def test_min_monsters_y_pickup_max_se_respetan():
    c2 = BichosCfg(**{**CFG.__dict__, "min_monsters": 3, "pickup_max_monsters": 0})
    assert decide(c2, view(monsters=2)) == WAIT and decide(c2, view(monsters=3)) == FIGHT
    assert decide(c2, view(monsters=1, items=1, since_pickup_s=99)) == WAIT   # 1 > pickup_max_monsters
    assert decide(c2, view(monsters=0, items=1, since_pickup_s=99)) == PICKUP


# ---------------------------------------------------------------- config
def with_bichos(**kw):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["zone"]["bichos"].update(kw)
    return cfg


def test_config_valida():
    validate_config(with_bichos(min_monsters=2, space_s=1.5, pickup_max_monsters=0))


@pytest.mark.parametrize("kw", [dict(lure_every_s=0), dict(lure_every_s=-1), dict(space_s=0), dict(clear_s=-2),
                                dict(pickup_every_s=0), dict(idle_s=0), dict(idle_s="x"), dict(min_monsters=0),
                                dict(min_monsters=1.5), dict(pickup_max_monsters=-1), dict(pickup_max_monsters=True),
                                dict(lure_key="f12"), dict(lure_key="f11"), dict(lure_key="zz")])
def test_config_invalida(kw):
    with pytest.raises(ConfigError):
        validate_config(with_bichos(**kw))


def test_perfil_viejo_sin_claves_nuevas_toma_defaults(tmp_path):
    from bot.config import _merge
    old = {"zone": {"farm_mode": "bichos", "bichos": {"lure_key": "f5", "lure_every_s": 30}}}
    cfg = _merge(copy.deepcopy(DEFAULTS), old)
    validate_config(cfg)
    assert BichosCfg.from_config(cfg).idle_s == 20 and BichosCfg.from_config(cfg).lure_key == "f5"


# ---------------------------------------------------------------- estadisticas
def test_resumen_solo_muestra_bichos_en_ese_modo():
    s = BotStats(lures=5, fight_s=120.0)
    assert "senuelos" not in s.summary()
    s.farm_mode = "bichos"
    assert "senuelos: 5 | peleando: 2.0 min" in s.summary()


def test_historial_suma_modo_y_senuelos():
    t0 = datetime(2026, 11, 20, 14, 0, 0)
    s = BotStats(lures=7, farm_mode="bichos")
    r = hist.build_record(s, t0, t0, {}, "tiempo")
    assert r["farm_mode"] == "bichos" and r["lures"] == 7
    r = hist.build_record(BotStats(), t0, t0, {}, "tiempo")
    assert r["farm_mode"] == "metin" and r["lures"] == 0


def test_status_de_la_interfaz_trae_modo_y_senuelos():
    st = c.status_from_dict({"farm_mode": "bichos", "lures": "4", "fight_s": 12.5})
    assert (st.farm_mode, st.lures, st.fight_s) == ("bichos", 4, 12.5)
    st = c.status_from_dict({})
    assert (st.farm_mode, st.lures, st.fight_s) == ("metin", 0, 0.0)


def test_run_bot_status_incluye_farm_mode_y_lures():
    import run_bot

    class FakeBot:
        stats = BotStats(lures=3, fight_s=9.0, farm_mode="bichos")
        state = "BICHOS"
        skills = None

    proto = run_bot.UiBridge.__new__(run_bot.UiBridge)
    proto.bot, proto.cfg, proto.phase, proto.countdown = FakeBot(), {}, "running", 0
    proto.control = type("C", (), {"is_paused": False})()
    s = proto.status()
    assert s["farm_mode"] == "bichos" and s["lures"] == 3 and s["fight_s"] == 9.0
