"""Pruebas de punta a punta: el Bot completo contra `SimWorld` (reloj simulado unico, input falso)."""
import math
import random

import pytest

import bot.input.base as base
from bot.bot import State
from bot.control import PanicStop
from bot.navigation import CameraModel
from sim_bot_env import SimEnv, make_cfg
from sim_game import Box, Circle, make_a0

MARGIN = 4.0                      # margen de redondeo/latencia sobre outside_tolerance
OSC, NEG, AVA = "metin_de_oscuridad", "metin_negro", "metin_de_avaricia"
INSIDE_METINS = [((500, 160), OSC), ((600, 150), NEG), ((680, 165), OSC), ((470, 180), NEG)]
LIMITS = {"ATACAR": 125, "RECOGER": 25, "VOLVER": 25, "CENTRO": 25, "ACERCARSE": 45, "DESTRABAR": 25}   # segundos seguidos maximos por estado
# En los mundos al azar la camara de modo clic puede seguir sin calibrar cuando el personaje sale a la tolerancia yendo a
# una piedra lejana (se va con clic sobre ella, sin la guardia de zona de la caminata): el VOLVER a ciegas tarda mas.
RANDOM_LIMITS = {**LIMITS, "VOLVER": 50}


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


def farm_env(seed=1, **kw):
    env = SimEnv(seed, **kw)
    for pos, name in INSIDE_METINS:
        env.world.add_metin(pos, name=name)
    return env


def tol(env):
    return env.zone.outside_tolerance + MARGIN


def no_input_after(env, idx):
    return all(e[0] in ("key_up", "mouse_up") for e in env.backend.events[idx:])


# ---------------------------------------------------------------- 1. farmeo normal
def test_farmeo_normal():
    env = farm_env(z_needed=3)   # la Z del servidor tiene retraso: cada objeto necesita 3 Z seguidas
    far = env.world.add_metin((330, 150), name=OSC)
    ava = env.world.add_metin((560, 175), name=AVA)
    dist = env.track_zone()
    targets = [m for m in env.world.metins if m.name in (OSC, NEG) and m is not far]
    why = env.run_until(lambda: all(not m.alive for m in targets) and not any(
        i.kind == "item" and not i.reserved_other for i in env.world.items), max_s=900)
    assert why == "cond"
    assert far.alive and ava.alive and ava.hp == 1.0 and far.hp == 1.0
    assert all(m.name != AVA and m is not far for _, m in env.world.clicks_on_metin)
    assert max(dist) <= tol(env)
    w = env.world
    assert len(w.picked) == 8 and all(i.kind == "item" and not i.reserved_other for i in w.picked)
    assert any(i.kind == "yang" for i in w.items) and any(i.reserved_other for i in w.items)
    assert all(env.state_at(t) != "RECOGER" for t, _ in w.bad_item_clicks)   # al recoger nunca clickea yang/reservados
    assert w.z_presses >= 3 * 8 and w.t < 400
    m = len(w.broken)
    print(f"METRICAS farmeo: {m} metin en {w.t:.0f}s simulados ({m / (w.t / 60):.2f}/min), "
          f"{len(w.clicks_on_metin) / m:.2f} clics/metin, {env.count_unstucks()} destrabes")


def test_regresion_camara_descarta_tramos_si_gira_sin_a0():
    """Bug: dos sondeos hechos con la camara en distinto giro se mezclaban y aprendian una A0 falsa."""
    cam = CameraModel(make_cfg())
    cam.update((150, 0), (30, 0))
    cam.on_camera_rotated()          # la camara giro antes de aprender A0: ese tramo ya no vale
    assert cam.update((0, 150), (0, -30)) and not cam.has_a0
    cam.update((150, 0), (-30, 0))
    assert cam.has_a0


# ---------------------------------------------------------------- 2. camara desconocida y giros (los del barrido)
@pytest.mark.parametrize("seed,phi,a0", [(1, 2.5, None), (2, -1.0, make_a0(0.15, 0.5, flip_x=True)),
                                         (3, 4.0, make_a0(0.25, 1.0, flip_y=True))])
def test_camara_desconocida_y_giros_del_barrido(seed, phi, a0):
    env = SimEnv(seed, phi=phi, a0=a0, start=(450, 170))
    m = env.world.add_metin((690, 170), name=OSC)
    dist = env.track_zone()
    assert env.run_until(lambda: not m.alive, max_s=600) == "cond"
    assert max(dist) <= tol(env)


# ---------------------------------------------------------------- 3. obstaculo
def test_obstaculo_entre_el_personaje_y_el_metin():
    env = SimEnv(4, phi=0.3, start=(490, 165), obstacles=[Box(528, 118, 538, 212)], label_dist=90)
    m = env.world.add_metin((580, 160), name=OSC)
    dist = env.track_zone()
    why = env.run_until(lambda: not m.alive, max_s=400)
    assert max(dist) <= tol(env)
    reasons = [t.reason for t in env.transitions]
    assert why == "cond" or any("descartado" in r for r in reasons), reasons
    assert env.count_unstucks() >= 1
    for st, secs in env.max_state_seconds().items():
        assert secs <= LIMITS.get(st, 1e9), (st, secs)


# ---------------------------------------------------------------- 4. captcha
def test_captcha_en_medio_de_un_ataque():
    env = SimEnv(5, phi=1.0)
    m = env.world.add_metin((580, 150), name=OSC)
    env.world.schedule_captcha(12.0, 40.0)
    assert env.run_until(lambda: not m.alive and env.world.t > 80, max_s=300) == "cond"
    log = env.alarm.log
    assert [a for _, a, _ in log] == ["start", "stop"] and log[0][2] == "captcha"
    t0, t1 = log[0][0], log[1][0]
    assert 12.0 <= t0 <= 13.5 and t1 >= 52.0
    assert not [t for t in env.backend.input_times() if t0 < t < t1]
    assert env.backend.pressed == ([], [])
    assert env.bot.stats.captchas == 1 and len(env.world.broken) == 1


# ---------------------------------------------------------------- 5. muerte
def test_muerte():
    env = SimEnv(6, phi=1.0)
    env.world.add_metin((580, 150), name=OSC)
    env.world.schedule_death(12.0)
    assert env.run_until(max_s=300) == "fin"
    assert env.bot.stats.end_reason == "muerte"
    assert [(a, r) for _, a, r in env.alarm.log] == [("start", "muerte")]
    n = len(env.backend.events)
    for _ in range(5):
        env.bot.step()
    assert len(env.backend.events) == n
    assert not [t for t in env.backend.input_times() if t > env.alarm.log[0][0]]
    assert env.backend.pressed == ([], [])


# ---------------------------------------------------------------- 6. panico
@pytest.mark.parametrize("seed", range(8))
def test_panico_en_momento_aleatorio(seed):
    env = farm_env(seed + 10)
    t_p = random.Random(seed).uniform(1.0, 120.0)
    env.schedule_panic(t_p)
    assert env.run_until(max_s=400) == "panico"
    c = env.control
    assert c.panic_t == pytest.approx(t_p, abs=0.15)
    assert no_input_after(env, c.on_panic_index)
    assert env.backend.pressed == ([], [])


def test_panico_en_run_propaga_y_anota_motivo():
    env = farm_env(11)
    env.schedule_panic(30.0)
    with pytest.raises(PanicStop):
        env.bot.run()
    assert env.bot.stats.end_reason == "panico"
    assert no_input_after(env, env.control.on_panic_index)


# ---------------------------------------------------------------- 7. empujon
def test_empujon_fuera_de_la_tolerancia_vuelve_enseguida():
    env = SimEnv(7, phi=0.5)
    m = env.world.add_metin((560, 150), name=OSC)
    env.world.schedule_push(15.0, -190.0, 0.0)
    pushed = {}
    env.world.schedule(15.0001, lambda: pushed.setdefault("d", env.zone_distance()))
    assert env.run_until(lambda: env.world.t > 15.5, max_s=100) == "cond"
    assert pushed["d"] > env.zone.outside_tolerance
    assert env.run_until(lambda: env.zone_distance() == 0 and env.world.t > 16, max_s=120) == "cond"
    volver = [t for t in env.transitions if t.new == "VOLVER" and t.t >= 15.0]
    assert volver and volver[0].t - 15.0 < 3.0
    assert env.run_until(lambda: not m.alive, max_s=300) == "cond"


# ---------------------------------------------------------------- 8. franja de tolerancia
def test_metin_en_la_franja_se_ataca_y_se_vuelve_adentro():
    env = SimEnv(8, phi=-0.7, start=(480, 170))
    m = env.world.add_metin((415, 170), name=OSC)
    for v in ((100, 0), (0, 100)):   # camara ya calibrada: sin ella un metin lejos en pantalla no se ataca
        env.bot.camera.update(v, tuple(env.world._m() @ v))
    assert env.bot.camera.ready
    dist = env.track_zone()
    assert env.run_until(lambda: not m.alive, max_s=300) == "cond"
    t_broke = env.world.t
    assert env.run_until(lambda: env.zone_distance() == 0 and env.bot.state is State.EXPLORAR, max_s=200) == "cond"
    assert 0 < max(dist) <= tol(env)
    assert any(t.new == "VOLVER" and t.t >= t_broke for t in env.transitions)   # volvio al terminar el metin
    assert not env.bot.pending_return


# ---------------------------------------------------------------- 9. seleccion equivocada
@pytest.mark.parametrize("n_wrong", [1, 2])
def test_seleccion_equivocada_escape_y_sigue(n_wrong):
    env = SimEnv(9, phi=2.0)
    m = env.world.add_metin((580, 150), name=OSC)
    env.world.wrong_select = n_wrong
    assert env.run_until(lambda: not m.alive, max_s=300) == "cond"
    esc = sum(1 for e in env.backend.events if e == ("key_down", "esc"))
    assert esc >= n_wrong
    assert [k for _, k, _ in env.world.selected_log][:n_wrong] == ["monster"] * n_wrong


# ---------------------------------------------------------------- 10. invulnerable
def test_metin_invulnerable_reclics_descarte_y_no_se_vuelve_a_elegir():
    env = SimEnv(12, phi=0.4)
    m = env.world.add_metin((580, 150), name=OSC, invulnerable=True)
    atk = env.cfg["attack"]
    assert env.run_until(lambda: any("descartado" in t.reason for t in env.transitions), max_s=300) == "cond"
    t_d = env.world.t
    n = len(env.world.clicks_on_metin)
    assert n == 1 + atk["max_reclicks"]
    assert env.run_until(max_s=atk["discard_minutes"] * 60 - 30) == "limite"
    assert len(env.world.clicks_on_metin) == n, "volvio a elegir el metin descartado"
    assert m.hp == 1.0 and env.world.t - t_d > 500


# ---------------------------------------------------------------- 11. max_minutes
def test_max_minutes_corta_con_motivo_tiempo():
    env = farm_env(13)
    stats = env.bot.run(max_minutes=1.0)
    assert stats.end_reason == "tiempo" and env.bot.state is State.FIN
    assert 60 <= env.world.t <= 75
    assert env.backend.pressed == ([], [])


# ---------------------------------------------------------------- 12. robustez
def random_world(seed, cfg=None):
    r = random.Random(seed)
    env = SimEnv(seed, cfg=cfg, phi=r.uniform(-math.pi, math.pi), start=(r.uniform(470, 700), r.uniform(130, 185)),
                 a0=make_a0(r.uniform(0.15, 0.25), r.uniform(0.5, 1.0), r.random() < 0.3, r.random() < 0.3),
                 coords_noise=0.02 if seed % 2 else 0.0)
    z = env.zone
    placed: list[tuple[float, float]] = []
    for _ in range(r.randint(1, 4)):
        for _try in range(100):
            pt = (r.uniform(440, 740), r.uniform(100, 200))
            if z.signed_distance(pt) >= 8 and all(math.dist(pt, q) > 25 for q in placed) \
                    and math.dist(pt, tuple(env.world.pos)) > 20:
                placed.append(pt)
                env.world.add_metin(pt, name=r.choice((OSC, NEG)))
                break
    if r.random() < 0.5:
        env.world.add_metin((r.uniform(450, 700), r.uniform(150, 190)), name=AVA)
    if r.random() < 0.5:
        env.world.add_metin((r.uniform(300, 380), r.uniform(120, 200)), name=OSC)   # afuera de la tolerancia
    for _ in range(r.randint(0, 2)):
        pt = (r.uniform(450, 720), r.uniform(110, 195))
        if all(math.dist(pt, q) > 14 for q in placed + [tuple(env.world.pos)]):
            env.world.obstacles.append(Circle(pt[0], pt[1], 5))
    return env


@pytest.mark.parametrize("seed", range(20))
def test_robustez_mundos_aleatorios(seed):
    env = random_world(seed)
    dist = env.track_zone()
    assert env.run_until(max_s=300) == "limite"
    assert max(dist) <= tol(env)
    assert len(env.world.broken) >= 1 or any(t.new == "EXPLORAR" for t in env.transitions)
    for st, secs in env.max_state_seconds(ignore=("CAPTCHA", "EXPLORAR")).items():
        assert secs <= RANDOM_LIMITS.get(st, 5), (st, secs)
    assert env.backend.pressed == ([], [])


@pytest.mark.parametrize("seed", range(20))
def test_robustez_mundos_aleatorios_con_teclado(seed):
    """Mismos mundos al azar (camaras giradas, espejadas, inclinadas) con la caminata por coordenadas: VOLVER vuelve al tope
    de 25 s (a ciegas por clic tardaba 40-46 s en 2 de 20 mundos)."""
    env = random_world(seed, make_cfg(navigation={"mode": "teclado"}))
    dist = env.track_zone()
    assert env.run_until(max_s=300) == "limite"
    assert max(dist) <= tol(env)
    assert len(env.world.broken) >= 1 or any(t.new == "EXPLORAR" for t in env.transitions)
    for st, secs in env.max_state_seconds(ignore=("CAPTCHA", "EXPLORAR")).items():
        assert secs <= LIMITS.get(st, 5), (st, secs)
    assert env.backend.pressed == ([], [])


# ---------------------------------------------------------------- regresiones de los bugs encontrados
@pytest.mark.parametrize("seed,start", [(21, (450, 170)), (22, (700, 190)), (23, (600, 100)), (24, (445, 150))])
def test_regresion_explorar_en_el_borde_con_camara_ciega_no_sale_de_la_tolerancia(seed, start):
    """Bug: los sondeos y destrabes ciegos cerca del borde podian salir de la franja de tolerancia."""
    env = SimEnv(seed, start=start)
    dist = env.track_zone()
    assert env.run_until(max_s=240) == "limite"
    assert max(dist) <= tol(env)


def test_regresion_la_caminata_no_clickea_metin_que_no_son_objetivo():
    """Bug: un clic de caminata sobre un metin de Avaricia lo atacaba."""
    env = SimEnv(25, phi=1.2, start=(450, 170))
    for x in (480, 520, 560):
        env.world.add_metin((x, 170), name=AVA)
    assert env.run_until(max_s=240) == "limite"
    assert env.world.clicks_on_metin == []
