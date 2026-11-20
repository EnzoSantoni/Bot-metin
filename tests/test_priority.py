"""Fase 12 (parte 1): prioridad entre tipos de metin (bot/priority.py, eleccion en BUSCAR, validacion de config)."""
import json
import logging
import math

import pytest

import baseline_scenarios as bs
from bot.config import ConfigError, validate_config
from bot.priority import order_by_priority, priority_rank
from sim_bot_env import SimEnv, make_cfg

OSC, NEG = "metin_de_oscuridad", "metin_negro"


# ---------------------------------------------------------------- funciones puras
def test_rank_por_slug_y_resto_al_final():
    lst = ["Metin Negro", "Metin de Oscuridad"]
    assert priority_rank("metin_negro", lst) == 0
    assert priority_rank("metin_de_oscuridad", lst) == 1
    assert priority_rank("otro", lst) == 2
    assert priority_rank(None, lst) == 2
    assert priority_rank("x", []) == 0


def test_rank_ignora_mayusculas_y_tildes():
    assert priority_rank("metin_de_oscuridad", ["METIN DE OSCURIDAD"]) == 0
    assert priority_rank("metin_de_oscuridad", ["Métin dé Oscuridad"]) == 0


def _order(cands, prio):
    """cands = (nombre, distancia de piso); se entregan ordenadas por distancia de piso (como `rank_goals`)."""
    return order_by_priority(sorted(cands, key=lambda c: c[1]), prio, lambda c: c[0])


def test_lista_vacia_es_el_orden_por_distancia_estable():
    # empates de distancia (a y b) conservan el orden de entrada, como el sort de siempre
    cands = [("a", 10.0), ("b", 10.0), ("c", 5.0), ("d", 20.0)]
    assert _order(cands, []) == [("c", 5.0), ("a", 10.0), ("b", 10.0), ("d", 20.0)]
    assert order_by_priority(cands, [], lambda c: c[0]) == cands   # sin lista no reordena nada


def test_prioridad_empate_por_cercania_y_no_listados_al_final():
    cands = [("metin_de_oscuridad", 5.0), ("metin_negro", 50.0), ("metin_negro", 20.0), ("raro", 1.0)]
    out = _order(cands, ["Metin Negro"])
    assert [c[1] for c in out] == [20.0, 50.0, 1.0, 5.0]   # no listados: todos iguales, por distancia de piso


def test_piedra_sin_tipo_va_como_no_listada():
    items = [(None, 1.0), ("metin_negro", 9.0), ("otro", 3.0)]   # None = piedra sin etiqueta
    assert [i[1] for i in _order(items, ["Metin Negro"])] == [9.0, 1.0, 3.0]


def test_orden_de_la_lista_manda():
    cands = [("a", 1.0), ("b", 2.0), ("c", 3.0)]
    assert [c[0] for c in _order(cands, ["c", "b"])] == ["c", "b", "a"]


# ---------------------------------------------------------------- bot en el simulador
def _env(prio):
    cfg = make_cfg(zone={"metin_priority": prio})
    env = SimEnv(3, phi=0.0, cfg=cfg)
    env.world.add_metin((552, 150), name=OSC)    # cerca
    env.world.add_metin((557, 150), name=NEG)    # lejos
    return env


def _attacked(env):
    """Nombre del metin al que apunta el primer clic de ataque."""
    assert env.run_until(lambda: env.bot._atk is not None, max_s=30) == "cond"
    cp = env.bot._atk.click

    def dist(m):
        off = env.world.visible_offset(m.pos)
        return 1e9 if off is None else math.dist(cp, (env.backend.player[0] + off[0], env.backend.player[1] + off[1]))
    return min(env.world.metins, key=dist).name


def test_con_prioridad_va_al_negro_y_lo_loguea(caplog):
    with caplog.at_level(logging.INFO):
        assert _attacked(_env(["Metin Negro"])) == NEG
    msgs = [r.getMessage() for r in caplog.records if "por prioridad" in r.getMessage()]
    assert msgs == [f"Elegido {NEG} por prioridad (mas cerca habia {OSC})"]


def test_sin_prioridad_va_al_mas_cercano(caplog):
    with caplog.at_level(logging.INFO):
        assert _attacked(_env([])) == OSC
    assert not [r for r in caplog.records if "por prioridad" in r.getMessage()]


def test_prioridad_a_favor_del_mas_cercano_no_loguea(caplog):
    with caplog.at_level(logging.INFO):
        assert _attacked(_env(["Metin de Oscuridad"])) == OSC
    assert not [r for r in caplog.records if "por prioridad" in r.getMessage()]


# ---------------------------------------------------------------- baselines identicos con metin_priority = []
@pytest.mark.parametrize("name", bs.NAMES)
def test_baseline_identico_con_prioridad_vacia(name):
    expected = json.loads((bs.DATA / f"baseline_events_{name}.json").read_text(encoding="utf-8"))
    env, _ = bs.build(name)
    assert env.cfg["zone"]["metin_priority"] == []
    assert json.loads(json.dumps(bs.run(name))) == expected


# ---------------------------------------------------------------- validacion
def _cfg(prio):
    return make_cfg(zone={"metin_priority": prio})


def test_valida_ok_y_vacia():
    validate_config(_cfg([]))
    validate_config(_cfg(["Metin Negro", "Metin de Oscuridad"]))


@pytest.mark.parametrize("prio", ["Metin Negro", [""], [1], ["  "], ["Metin Negro", "metin negro"],
                                  ["Métin Negro", "METIN NEGRO"]])
def test_valida_rechaza(prio):
    with pytest.raises(ConfigError):
        validate_config(_cfg(prio))


def test_nombre_que_no_es_objetivo_avisa(caplog):
    with caplog.at_level(logging.WARNING):
        validate_config(_cfg(["Metin Raro", "Metin Negro"]))
    assert any("Metin Raro" in r.getMessage() and "se ignora" in r.getMessage() for r in caplog.records)
    assert not any("Metin Negro" in r.getMessage() for r in caplog.records)
