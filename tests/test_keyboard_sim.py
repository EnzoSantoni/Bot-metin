"""Modo teclado (navigation.mode = "teclado"): el bot camina SOLO con W/A/S/D (8 direcciones, sin girar la camara) y mira con
Q/E; el clic izquierdo queda para atacar un metin (RECOGER camina hasta los objetos y aprieta Z, sin clics). Todo contra
`SimWorld` (input falso)."""
import math

import pytest

import bot.input.base as base
from bot.bot import State
from sim_bot_env import SimEnv, make_cfg
from sim_game import Box, make_a0

OSC, NEG = "metin_de_oscuridad", "metin_negro"
INSIDE_METINS = [((500, 160), OSC), ((600, 150), NEG), ((680, 165), OSC), ((470, 180), NEG)]
CLICK_TOL_PX = 30.0     # todo clic izquierdo cae a esta distancia de un metin (RECOGER ya no clickea objetos); el clic va al centro del cuerpo, ~24 px sobre la base
MARGIN = 4.0            # margen de redondeo/latencia sobre outside_tolerance


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


def kcfg(**kb):
    cfg = make_cfg(navigation={"mode": "teclado"})
    cfg["navigation"]["keyboard"] = dict(cfg["navigation"]["keyboard"], **{"lateral": True, **kb})   # modelo de 8 combinaciones
    return cfg


def kenv(seed=1, cfg=None, **kw):
    return SimEnv(seed, cfg=cfg or kcfg(), **kw)


def tol(env):
    return env.zone.outside_tolerance + MARGIN


def x_at_sd(env, target, y=150.0):
    lo, hi = 500.0, 900.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if env.zone.signed_distance((mid, y)) > target:
            lo = mid
        else:
            hi = mid
    return lo


def watch_clicks(env):
    """Registra, por cada clic izquierdo, la distancia en pantalla al metin mas cercano (None = ninguno)."""
    w, out = env.world, []
    orig = w.click_screen

    def spy(vec):
        # una piedra lejana (sin etiqueta, hasta far_dist) tambien es un metin: el clic izquierdo sobre ella esta permitido
        near = [math.dist(off, vec) for m in w.metins if m.alive
                and (off := w.visible_offset(m.pos, w.far_dist)) is not None]
        out.append(min(near) if near else None)
        return orig(vec)
    w.click_screen = spy
    return out


def keys_down(env):
    """Teclas apretadas (key_down) por tipo: w, a, s, d, q, e, space..."""
    out: dict[str, int] = {}
    for e in env.backend.events:
        if e[0] == "key_down":
            out[e[1]] = out.get(e[1], 0) + 1
    return out


def ground_clicks(clicks):
    return [d for d in clicks if d is None or d > CLICK_TOL_PX]


# ---------------------------------------------------------------- (a) farmeo completo
@pytest.mark.parametrize("seed", [1, 2])
def test_farmeo_completo_solo_con_teclado(seed):
    env = kenv(seed)
    for pos, name in INSIDE_METINS:
        env.world.add_metin(pos, name=name)
    clicks = watch_clicks(env)
    dist = env.track_zone()
    targets = list(env.world.metins)
    why = env.run_until(lambda: all(not m.alive for m in targets) and not any(
        i.kind == "item" and not i.reserved_other for i in env.world.items), max_s=1500)
    assert why == "cond", (why, env.transitions[-5:])
    assert clicks and not ground_clicks(clicks), ground_clicks(clicks)
    assert max(dist) <= tol(env)
    # ahora a los metin lejanos se va con clic sobre ellos (el personaje camina solo): W ya no es imprescindible
    assert len(env.world.clicks_on_metin) >= len(targets)
    assert not env.bot.camera.has_a0          # sin clics en el piso la camara no aprende nada


# ---------------------------------------------------------------- (b) VOLVER desde afuera
@pytest.mark.parametrize("seed", [1, 3])
def test_volver_desde_afuera_con_teclado(seed):
    env = kenv(seed, start=(550.0, 150.0))
    env.world.pos[:] = (x_at_sd(env, -12), 150.0)
    clicks = watch_clicks(env)
    dist = env.track_zone()
    why = env.run_until(lambda: env.zone.signed_distance(tuple(env.world.pos)) >= 15 and
                        env.bot.state is not State.VOLVER, max_s=200)
    assert why == "cond", (why, env.transitions[-5:])
    assert not clicks
    assert max(dist) <= tol(env)
    assert any(t.new == "VOLVER" for t in env.transitions)


# ---------------------------------------------------------------- (c) CENTRO
def test_centro_con_teclado():
    env = kenv(2, start=(480.0, 150.0))
    clicks = watch_clicks(env)
    dist = env.track_zone()
    env.bot._enter(State.CENTRO, "test")
    target = env.bot.navigator.return_target((480, 150))
    why = env.run_until(lambda: env.bot.state is not State.CENTRO, max_s=200)
    assert why == "cond"
    assert math.dist(tuple(env.world.pos), target) <= float(env.cfg["center"]["arrive_dist"]) + 3
    assert not clicks and max(dist) <= tol(env)


# ---------------------------------------------------------------- (d) ACERCARSE a una piedra lejana
# ---------------------------------------------------------------- (e) destrabe con teclas
def test_destrabe_mirar_y_atras_con_teclado_sin_clics():
    env = kenv(2, start=(550.0, 150.0))
    clicks = watch_clicks(env)
    bot = env.bot
    for strat in ("MIRAR", "ATRAS"):
        bot._enter(State.EXPLORAR, "test")
        bot._start_unstuck(State.EXPLORAR, "test")
        bot._us.back = State.EXPLORAR
        env.bot.state = State.DESTRABAR
        ep = bot._ep
        ep.attempt = 1
        # fuerza la estrategia probada
        orig = ep.next_strategy
        ep.next_strategy = lambda *a, s=strat, **k: s
        w0 = dict(keys_down(env))
        pos0 = tuple(env.world.pos)
        t0 = env.world.t
        for _ in range(60):
            if bot.state is not State.DESTRABAR:
                break
            bot.step()
        ep.next_strategy = orig
        k = keys_down(env)
        if strat == "MIRAR":
            walked = sum(k.get(x, 0) for x in "wasd") > sum(w0.get(x, 0) for x in "wasd")   # camina con alguna combinacion
            assert walked and (k.get("q", 0) + k.get("e", 0)) > (w0.get("q", 0) + w0.get("e", 0))   # y mira con Q/E
        else:
            assert k.get("s", 0) > w0.get("s", 0)
        assert env.world.t > t0 and math.dist(tuple(env.world.pos), pos0) > 1.0
        env.world.pos[:] = (550.0, 150.0)
        bot.tracker.forget_reference()
    assert not clicks


def test_traba_real_contra_un_muro_dispara_el_destrabe_con_teclas():
    env = kenv(2, start=(550.0, 150.0), obstacles=[Box(560, 100, 700, 210)])   # muro a la derecha: W choca
    clicks = watch_clicks(env)
    env.bot._enter(State.CENTRO, "test")
    env.world.pos[:] = (520.0, 150.0)
    env.bot.tracker.forget_reference()
    env.run_until(lambda: env.count_unstucks() >= 1, max_s=200)
    assert env.count_unstucks() >= 1
    assert not clicks


# ---------------------------------------------------------------- (f) Q/E ya no se usan para caminar
def test_caminar_a_un_punto_no_aprieta_q_e_ni_invierte_las_teclas():
    """Con la caminata por coordenadas la camara no gira: Q/E quedan solo para mirar; nada que aprender ni invertir."""
    env = kenv(4, start=(520.0, 150.0), phi=0.3)
    kn, bot = env.bot.keynav, env.bot
    bot._enter(State.CENTRO, "test")
    target = bot.navigator.return_target((520, 150))
    env.run_until(lambda: bot.state is not State.CENTRO, max_s=300)
    assert bot.state is not State.CENTRO
    k = keys_down(env)
    assert k.get("q", 0) == 0 and k.get("e", 0) == 0
    assert kn.view_right_key == "camera_right" and kn.side == 1
    assert math.dist(tuple(env.world.pos), target) <= float(env.cfg["center"]["arrive_dist"]) + 3


def test_view_right_key_correcta_no_se_invierte():
    env = SimEnv(4, cfg=kcfg(view_right_key="camera_left"), start=(520.0, 150.0), phi=0.3)
    env.bot._enter(State.CENTRO, "test")
    env.run_until(lambda: env.bot.state is not State.CENTRO, max_s=300)
    assert env.bot.keynav.view_right_key == "camera_left"


# ---------------------------------------------------------------- (g) nunca sale de la tolerancia
@pytest.mark.parametrize("seed", [1, 2, 3, 5])
@pytest.mark.parametrize("start", [(442.0, 170.0), (700.0, 110.0), (550.0, 150.0)])
def test_nunca_sale_de_la_tolerancia_explorando(seed, start):
    env = kenv(seed, start=start)
    dist = env.track_zone()
    env.run_until(max_s=240)
    assert max(dist) <= tol(env), (seed, start, max(dist))
    assert not any(e[0] == "mouse_down" for e in env.backend.events)
