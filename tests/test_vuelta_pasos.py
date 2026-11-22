"""Vuelta de busqueda en pasos chicos (con superposicion): se frena ante un metin con nombre cercano, se hace hover sobre las piedras."""
import dataclasses
import logging
import re

import pytest

import bot.input.base as base
from bot.bot import State, _Explore
from test_bot_states import Harness, metin, perc
from test_vuelta_unica import FACING_WEST, OSC, env_for, msgs


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


def test_los_pasos_de_la_vuelta_giran_cerca_de_sweep_step_deg(caplog):
    caplog.set_level(logging.DEBUG)
    env = env_for(FACING_WEST)
    step = float(env.cfg["explore"]["sweep_step_deg"])
    assert step <= 35.0   # >= 30% de superposicion con un campo de vision de ~70 grados
    env.run_until(lambda: any("Vuelta de busqueda:" in m and "grados en" in m for m in msgs(caplog)), max_s=200)
    turned = [float(m.split("giro ")[1].split(" grados")[0]) for m in msgs(caplog) if "grados medidos" in m]
    assert len(turned) >= 8
    assert all(abs(d - step) <= 0.35 * step for d in turned), turned


def test_metin_con_nombre_y_cerca_corta_la_vuelta_y_se_ataca(caplog):
    caplog.set_level(logging.INFO)
    env = env_for(FACING_WEST)
    near = env.world.add_metin((530.0, 150.0), name=OSC)   # 30 u: dentro de explore.sweep_attack_u
    assert env.run_until(lambda: env.world.clicks_on_metin, max_s=200) == "cond"
    assert env.world.clicks_on_metin[0][1] is near
    assert not any("grados en" in m for m in msgs(caplog))     # la vuelta no termino


def test_hover_de_la_vuelta_con_nombre_de_otro_tipo_saca_la_piedra_de_la_lista():
    h = Harness(lambda h, n: perc())
    h.bot.tracker.update(perc().coords, h.clock())
    w = (600.0, 150.0)
    h.bot.cands.add(w, "piedra", False, 0.0)
    x = _Explore(dir="left")
    x.hov = ((900, 300), w)
    otro = dataclasses.replace(metin((900, 310)), is_target=False, name="metin_negro")
    h.bot._resolve_hover(perc(metins=[otro]), 0.0, x)
    assert len(h.bot.cands) == 0 and x.hov is None


def test_hover_de_la_vuelta_con_nombre_objetivo_la_deja_en_la_lista():
    h = Harness(lambda h, n: perc())
    w = (600.0, 150.0)
    h.bot.cands.add(w, "piedra", False, 0.0)
    x = _Explore(dir="left")
    x.hov = ((900, 300), w)
    h.bot._resolve_hover(perc(metins=[metin((900, 310))]), 0.0, x)
    assert len(h.bot.cands) == 1


# ---------------------------------------------------------------- metin "cerca" (approach.near_floor) corta la vuelta
@pytest.mark.parametrize("near_floor, cut", [(4.0, True), (2.0, False)])
def test_metin_cerca_corta_la_vuelta_segun_near_floor(caplog, near_floor, cut):
    """Metin con nombre a ~40 u (piso ~3.1) detras de la camara y otro lejos: con near_floor 4 la vuelta se corta apenas lo
    ve y se ataca ese; con near_floor 2 la vuelta termina (y despues se elige el mas cercano)."""
    caplog.set_level(logging.INFO)
    env = env_for(FACING_WEST)
    env.cfg["approach"]["near_floor"] = near_floor
    near = env.world.add_metin((600.0, 150.0), name=OSC)
    env.world.add_metin((690.0, 160.0), name=OSC)
    assert env.run_until(lambda: env.world.clicks_on_metin, max_s=200) == "cond"
    assert env.world.clicks_on_metin[0][1] is near
    ms = msgs(caplog)
    assert any("corto la vuelta" in m for m in ms) is cut
    assert (not any("grados en" in m for m in ms)) is cut


def test_vuelta_hover_solo_en_piedras_cerca(monkeypatch):
    """Durante la vuelta solo se frena (hover) en una piedra sin confirmar a <= near_floor; las lejanas quedan en la lista."""
    from types import SimpleNamespace
    from bot.ground import Goal
    h = Harness(lambda h, n: perc())
    calls = []
    monkeypatch.setattr(h.bot.gi, "hover_at", lambda x, y, mask: calls.append((x, y)) or True)
    near_f = float(h.bot.cfg["approach"]["near_floor"])

    def stone(pt):
        return Goal("piedra", "piedra", pt, SimpleNamespace(far=SimpleNamespace(rect=SimpleNamespace(center=pt))))

    x = _Explore(dir="left")
    far = [(stone((300, 280)), near_f + 2.5)]
    assert not h.bot._sweep_hover(perc(), 0.0, x, far, [(700.0, 150.0)])
    assert calls == [] and x.hov is None
    both = far + [(stone((1200, 420)), near_f - 1.0)]
    assert h.bot._sweep_hover(perc(), 0.0, x, both, [(700.0, 150.0), (590.0, 160.0)])
    assert calls == [(1200, 420)] and x.hov == ((1200, 420), (590.0, 160.0))


def test_al_girar_hacia_un_candidato_un_metin_cerca_se_ataca_ya(caplog):
    """Con un candidato lejos en la lista (sin vuelta), al terminar de girar hacia el aparece un metin con nombre cerca: se
    ataca ese en vez de ir al candidato."""
    caplog.set_level(logging.INFO)
    env = env_for(FACING_WEST)
    near = env.world.add_metin((600.0, 150.0), name=OSC)        # ~40 u detras (piso ~3.1)
    env.world.add_metin((690.0, 152.0), name=OSC)               # el candidato, ~130 u en la misma direccion
    env.bot.cands.add((690.0, 152.0), "piedra", False, env.world.t)
    assert env.run_until(lambda: env.world.clicks_on_metin, max_s=200) == "cond"
    assert env.world.clicks_on_metin[0][1] is near
    ms = msgs(caplog)
    assert any("Giro hacia el candidato en (690, 152)" in m for m in ms)
    assert any("metin cerca al girar hacia un candidato" in m for m in ms)
