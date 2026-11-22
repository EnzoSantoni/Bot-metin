"""Caminata por coordenadas SIN girar la camara (modo teclado): 8 direcciones con W/A/S/D, cam_h aprendido midiendo,
A/D al reves corregido solo, giros de camara ruidosos (Q/E solo para mirar) y volver al poligono guiandose solo por
coordenadas. Todo contra `SimWorld` (input falso)."""
import logging
import math

import pytest

import bot.input.base as base
from bot.bot import State
from sim_bot_env import SimEnv
from sim_game import Box
from test_keyboard_sim import kcfg, keys_down, tol, x_at_sd

SPEED = 25.0      # u/s del personaje simulado (parecido al real, ~27)
START = (640.0, 155.0)
ARRIVE_EXTRA = 4.0


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


def cam_h(env) -> float:
    """Hacia donde camina W en el juego (verdad del simulador)."""
    g = env.world._m() @ [0.0, -1.0]
    return math.atan2(g[1], g[0])


def walk_to(env, target, max_s=120.0):
    """Manda al bot (estado CENTRO con destino `target`) y corre hasta que llega."""
    env.bot.navigator.return_target = lambda pos: target
    env.bot._enter(State.CENTRO, "test")
    why = env.run_until(lambda: env.bot.state is not State.CENTRO, max_s=max_s)
    return why


def clicks_left(env):
    return sum(1 for e in env.backend.events if e == ("mouse_down", "left"))


@pytest.mark.parametrize("sector", range(8))
@pytest.mark.parametrize("phi", [0.4, 2.5, -1.9])
def test_llega_a_un_punto_en_cada_uno_de_los_8_sectores(sector, phi):
    env = SimEnv(sector + 1, cfg=kcfg(), phi=phi, start=START, speed=SPEED)
    ang = math.radians(45 * sector) + 0.3
    target = (START[0] + 25 * math.cos(ang), START[1] + 25 * math.sin(ang))
    assert env.zone.signed_distance(target) > 10
    dist = env.track_zone()
    assert walk_to(env, target) == "cond"
    assert math.dist(tuple(env.world.pos), target) <= float(env.cfg["center"]["arrive_dist"]) + ARRIVE_EXTRA
    k = keys_down(env)
    assert k.get("q", 0) == 0 and k.get("e", 0) == 0          # nunca gira la camara para caminar
    assert clicks_left(env) == 0 and max(dist) <= tol(env)
    assert env.bot.keynav.state == "medido" and env.bot.keynav.side == 1


def test_usa_combinaciones_diagonales_cuando_conviene(caplog):
    caplog.set_level(logging.INFO)
    env = SimEnv(3, cfg=kcfg(), phi=0.4, start=START, speed=SPEED)
    h = cam_h(env)
    walk_to(env, (START[0] + 30 * math.cos(h + math.radians(135)), START[1] + 30 * math.sin(h + math.radians(135))))
    legs = [r.message for r in caplog.records if r.message.startswith("Tramo: ")]
    assert any(m.startswith("Tramo: S+D") for m in legs), legs
    assert all(("camara" in m and "A/D normal" in m and "u/s" in m) for m in legs)
    assert any("hacia" in m and "(medido)" in m for m in legs)
    k = keys_down(env)
    assert k.get("s", 0) > 0 and k.get("d", 0) > 0


@pytest.mark.parametrize("phi,rel", [(0.4, 90), (2.5, 45), (-1.9, -90), (1.0, 135)])
def test_con_a_d_al_reves_lo_corrige_en_pocos_tramos(caplog, phi, rel):
    caplog.set_level(logging.INFO)
    env = SimEnv(5, cfg=kcfg(), phi=phi, start=START, speed=SPEED, mirror_ad=True)
    h = cam_h(env)
    # el destino queda hacia un lado de W: con A/D espejadas el bot lo va a buscar del lado equivocado hasta darse cuenta
    ang = h + math.radians(rel)
    target = (START[0] + 25 * math.cos(ang), START[1] + 25 * math.sin(ang))
    dist = env.track_zone()
    assert walk_to(env, target) == "cond"
    msgs = [r.message for r in caplog.records]
    flip = next(i for i, m in enumerate(msgs) if "Sentido de A/D determinado: al reves" in m)
    assert sum(1 for m in msgs[:flip] if m.startswith("Tramo: ")) <= 5     # 1 de prueba + 3 muestras al reves
    assert env.bot.keynav.side == -1
    assert math.dist(tuple(env.world.pos), target) <= float(env.cfg["center"]["arrive_dist"]) + ARRIVE_EXTRA
    assert max(dist) <= tol(env) and clicks_left(env) == 0


def test_a_d_normales_nunca_dicen_que_estan_al_reves(caplog):
    caplog.set_level(logging.WARNING)
    for seed, phi in ((1, 0.2), (2, 2.2), (3, -2.7)):
        env = SimEnv(seed, cfg=kcfg(), phi=phi, start=START, speed=SPEED, coords_noise=0.02)
        walk_to(env, (START[0] + 25, START[1] - 25))
        env.bot._enter(State.EXPLORAR, "fin")
    assert "A/D determinado: al reves" not in caplog.text


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_giros_de_camara_ruidosos_igual_llega_y_no_abandona_la_zona(seed):
    env = SimEnv(seed, cfg=kcfg(), start=(470.0, 170.0), speed=SPEED, turn_noise=(0.3, 1.5))
    dist = env.track_zone()
    visited = []
    env.world.on_step.append(lambda w: visited.append(tuple(w.pos)))
    env.run_until(max_s=240)
    assert max(dist) <= tol(env), max(dist)
    assert math.dist(min(visited), max(visited)) > 40                  # recorrio la zona (no se quedo en el lugar)
    assert env.bot.keynav.view_right_key in ("camera_left", "camera_right")
    # centro con la camara girada de forma ruidosa
    env2 = SimEnv(seed, cfg=kcfg(), start=(470.0, 170.0), speed=SPEED, turn_noise=(0.3, 1.5))
    target = env2.bot.navigator.return_target((470, 170))
    for deg in (60, -120, 100):                                     # mira alrededor (Q/E) antes de caminar
        env2.bot._rotate_camera("right" if deg > 0 else "left", abs(deg) / 60.0)
    assert walk_to(env2, target) == "cond"
    assert math.dist(tuple(env2.world.pos), target) <= float(env2.cfg["center"]["arrive_dist"]) + ARRIVE_EXTRA


def test_nunca_concluye_q_e_al_reves_con_una_sola_muestra():
    """Una muestra de giro contraria a lo ordenado (por ruido) no invierte Q/E: hacen falta `senses_votes` seguidas."""
    env = SimEnv(2, cfg=kcfg(), start=START, speed=SPEED, turn_noise=(0.3, 1.5))
    kn = env.bot.keynav
    flips = []
    orig = kn._fix_q
    kn._fix_q = lambda key: (flips.append(1), orig(key))[1]
    seen = []
    obs = kn.observe
    kn.observe = lambda *a, **k: (seen.append(obs(*a, **k)), seen[-1])[1]
    env.run_until(max_s=240)
    # cada "invertido" de Q/E vino precedido por un "dudoso" (dos muestras consistentes)
    for i, r in enumerate(seen):
        if r == "invertido" and kn.view_right_key != "camera_right":
            assert "dudoso" in seen[max(0, i - 3):i]


def test_arranca_afuera_en_una_bajada_sin_metin_y_vuelve_solo_por_coordenadas():
    """Mundo sin metin ni referencias, afuera del poligono y en una bajada que arrastra hacia afuera: vuelve a entrar
    usando solo las coordenadas (sin clics y sin girar la camara)."""
    env = SimEnv(7, cfg=kcfg(), start=START, speed=SPEED, phi=1.3)
    x = x_at_sd(env, -14)
    env.world.pos[:] = (x, 150.0)
    env.world.drifts = [(Box(x - 60, 90, x + 60, 215), (6.0, 0.0))]      # empuja hacia la derecha... (afuera o adentro)
    drift_out = env.zone.signed_distance((x + 5, 150.0)) < env.zone.signed_distance((x, 150.0))
    if not drift_out:
        env.world.drifts = [(Box(x - 60, 90, x + 60, 215), (-6.0, 0.0))]
    dist = env.track_zone()
    why = env.run_until(lambda: env.zone.signed_distance(tuple(env.world.pos)) >= 15 and env.bot.state is not State.VOLVER,
                        max_s=120)
    assert why == "cond", (why, env.transitions[-5:])
    assert any(t.new == "VOLVER" for t in env.transitions)
    assert clicks_left(env) == 0 and max(dist) <= 14 + 12            # no se aleja mas de lo que la bajada lo arrastra
    k = keys_down(env)
    assert k.get("q", 0) == 0 and k.get("e", 0) == 0


@pytest.mark.parametrize("sd", [-12, -45])
def test_vuelve_desde_afuera_aunque_la_imagen_no_muestre_nada(sd):
    env = SimEnv(9, cfg=kcfg(), start=START, speed=SPEED, phi=-0.8)
    env.world.pos[:] = (x_at_sd(env, sd), 150.0)
    seen = []
    orig = env.bot._perceive

    def spy(img, cfg, **kw):
        p = orig(img, cfg, **kw)
        seen.append((env.bot.state.name, kw.get("parts")))
        return p
    env.bot._perceive = spy
    dist = env.track_zone()
    why = env.run_until(lambda: env.zone.signed_distance(tuple(env.world.pos)) >= 15 and env.bot.state is not State.VOLVER,
                        max_s=150)
    assert why == "cond", (why, env.transitions[-5:])
    assert max(dist) <= max(-sd, 0) + 12
    for name, parts in seen:                       # VOLVER y CENTRO solo leen las coordenadas
        if name in ("VOLVER", "CENTRO"):
            assert set(parts) == {"coords", "monsters"}


def test_explorar_cerca_del_borde_con_el_tramo_alejandose_vuelve_al_centro(caplog):
    caplog.set_level(logging.INFO)
    env = SimEnv(4, cfg=kcfg(), start=START, speed=SPEED)
    env.bot._enter(State.EXPLORAR, "test")
    env.world.pos[:] = (x_at_sd(env, 2.0), 150.0)     # adentro, a 2 u del borde (< edge_return_u = 5)
    env.bot.tracker.forget_reference()
    ex = env.bot._ex
    ex.phase, ex.target, ex.walk_start = "walk", (x_at_sd(env, 2.0) + 40.0, 150.0), env.world.t   # el destino queda afuera
    env.run_until(lambda: env.bot.state is State.VOLVER, max_s=30)
    assert env.bot.state is State.VOLVER
    assert any("alejandose del poligono" in t.reason for t in env.transitions)


def test_barrido_vacio_cerca_del_borde_no_vuelve_al_centro_por_el_borde_va_al_punto_o_centro():
    """Una vuelta vacia cerca del borde ya no dispara VOLVER: sigue con el tramo (punto del mapa o centro)."""
    env = SimEnv(4, cfg=kcfg(), start=START, speed=SPEED, phi=0.7)
    env.world.pos[:] = (x_at_sd(env, 12.0), 150.0)     # adentro pero a menos de border_margin (25) del borde
    env.bot.tracker.forget_reference()
    env.run_until(lambda: env.bot._ex is not None and env.bot._ex.phase == "walk" and env.bot._ex.steps > 0, max_s=120)
    assert not any("barrido completo" in t.reason for t in env.transitions)
