"""Modo teclado con los valores por defecto (solo W + giro de la vista con Q/E, cono del minimapa): camino hacia un punto con una
pared/acantilado en el medio, rumbo desconocido (no se camina a ciegas contra la pared) y marco cono/coordenadas."""
import logging
import math

import pytest

import bot.input.base as base
from bot.bot import State
from sim_bot_env import SimEnv, make_cfg
from sim_game import Box

START = (470.0, 150.0)
TARGET = (620.0, 150.0)
SPEED = 25.0


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


def kenv(seed=1, heading=True, phi=math.pi / 2, start=START, **kw):
    cfg = make_cfg(navigation={"mode": "teclado"})
    cfg["navigation"]["keyboard"]["view_right_key"] = "camera_left"   # en el simulador camera_left sube el rumbo
    return SimEnv(seed, cfg=cfg, phi=phi, start=start, speed=SPEED, heading=heading, **kw)


def walk_to(env, target, max_s=150.0):
    env.bot.navigator.return_target = lambda pos: target
    env.bot._enter(State.CENTRO, "test")
    return env.run_until(lambda: env.bot.state is not State.CENTRO, max_s=max_s)


def test_por_defecto_camina_solo_con_w_y_gira_la_vista(caplog):
    caplog.set_level(logging.INFO)
    env = kenv()
    assert not env.bot.keynav.lateral
    assert walk_to(env, TARGET) == "cond"
    assert math.dist(tuple(env.world.pos), TARGET) <= float(env.cfg["center"]["arrive_dist"]) + 4
    pressed = {e[1] for e in env.backend.events if e[0] == "key_down"} if any(e[0] == "key_down" for e in env.backend.events) else set()
    assert not pressed & {"a", "d", "s"}


@pytest.mark.parametrize("phi", [0.3, 2.0, 4.0])
def test_pared_en_el_medio_la_rodea_girando_y_llega(caplog, phi):
    caplog.set_level(logging.INFO)
    env = kenv(phi=phi, obstacles=[Box(530, 138, 545, 162)])
    assert walk_to(env, TARGET, max_s=200) == "cond"
    assert math.dist(tuple(env.world.pos), TARGET) <= float(env.cfg["center"]["arrive_dist"]) + 4
    msgs = [r.getMessage() for r in caplog.records]
    assert any("no avanza" in m and "giro la vista" in m for m in msgs)     # el desvio gira la vista (no un paso al costado)
    assert not any("paso al costado" in m for m in msgs)


def test_rumbo_desconocido_contra_una_pared_no_empuja_siempre_igual(caplog):
    """Sin cono y contra una pared grande: tras `probe_blind` pruebas sin medir gira a ciegas y prueba otra direccion."""
    caplog.set_level(logging.INFO)
    env = kenv(heading=False, phi=math.pi / 2, start=(484.0, 150.0), obstacles=[Box(485, 60, 500, 240)])
    walk_to(env, TARGET, max_s=60)
    msgs = [r.getMessage() for r in caplog.records]
    assert any("giro la vista" in m and "a ciegas" in m for m in msgs)
    # no se quedo apretando W en el mismo lugar todo el tiempo
    assert env.world.t > 0 and sum("Tramo: W 0.40 s | camara ?" in m for m in msgs) < 12


def test_desfase_cono_se_afina_con_tramos_de_w_y_queda_acotado():
    from bot.bot import _KeyLeg
    env = kenv()
    bot = env.bot
    top = math.radians(env.cfg["vision"]["minimap"]["offset_max_deg"])
    cone = math.radians(100)
    ema = env.cfg["vision"]["minimap"]["offset_ema"]
    bot._learn_offset(_KeyLeg("W", (500, 150), 1.0, 0.0, cone), (20 * math.cos(cone + math.radians(15)), 20 * math.sin(cone + math.radians(15))))
    assert abs(bot._hoff - ema * math.radians(15)) < 1e-9          # W camina 15 grados a la derecha del cono: un paso de EMA
    before = bot._hoff
    bot._learn_offset(_KeyLeg("W", (500, 150), 1.0, 0.0, cone), (-20.0, 0.0))     # a mas de 40 grados: empujon, se ignora
    bot._learn_offset(_KeyLeg("D", (500, 150), 1.0, 0.0, cone), (20.0, 0.0))      # combinacion lateral: no sirve
    bot._learn_offset(_KeyLeg("W", (500, 150), 1.0, 0.0, None), (20.0, 0.0))      # sin cono
    assert bot._hoff == before
    for _ in range(80):                                   # un sesgo grande queda acotado a offset_max_deg
        bot._learn_offset(_KeyLeg("W", (500, 150), 1.0, 0.0, cone), (20 * math.cos(cone + math.radians(39)), 20 * math.sin(cone + math.radians(39))))
    assert bot._hoff <= top + 1e-9


def test_config_view_right_key_y_ad_side_fijos_desde_el_arranque():
    cfg = make_cfg(navigation={"mode": "teclado"})
    cfg["navigation"]["keyboard"].update(view_right_key="camera_left", ad_side="reverso")
    env = SimEnv(1, cfg=cfg)
    kn = env.bot.keynav
    assert kn.locked_q and kn.locked_ad and kn.view_right_key == "camera_left" and kn.side == -1
