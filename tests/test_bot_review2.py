"""Correcciones de la revision de la Fase 6b: B1-B4 (loops, guardia de zona, captcha en teclas sostenidas) e I1-I7."""
import copy
import json
import logging
import math

import pytest

import bot.input.base as base
from bot.bot import State, _Spot
from bot.geometry import Rect
from bot.tracking import StuckEpisode
from bot.vision.far_metin import FarMetin
from bot.vision.monsters import MonsterLabel
from sim_bot_env import SimEnv, make_cfg as sim_cfg
from test_bot_behavior import far_at, perc_far
from test_bot_states import Harness, make_cfg


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


def x_at_sd(env, target, y=150.0):
    lo, hi = 500.0, 900.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if env.zone.signed_distance((mid, y)) > target:
            lo = mid
        else:
            hi = mid
    return lo


def tol(env):
    return env.zone.outside_tolerance


# ---------------------------------------------------------------- B1
@pytest.mark.parametrize("seed", [1, 2, 4])
def test_b1_metin_afuera_de_la_zona_sin_camara_no_hace_loop(seed):
    env = SimEnv(seed, phi=0.5, start=(550.0, 150.0))
    env.world.start = None
    env.world.pos[:] = (x_at_sd(env, -2), 150.0)
    m = env.world.add_metin((x_at_sd(env, -45), 150.0))
    zd = env.track_zone()
    env.run_until(max_s=300)
    # modo clic sin camara ni rumbo: no se puede estimar donde esta el metin, asi que se lo cliquea; al salir de la zona mas la
    # tolerancia yendo hacia el se vuelve sin ignorar ninguna posicion (solo el panel suelta una piedra). Sin ping-pong:
    # VOLVER no ataca otro "cerca" hasta estar adentro y approach.reject_s no se cliquean metin sin posicion estimada
    attacks = [t.t for t in env.transitions if t.new == "ATACAR"]
    assert len(attacks) <= 300 / float(env.cfg["approach"]["reject_s"]) + 1
    assert all(b - a >= float(env.cfg["approach"]["reject_s"]) for a, b in zip(attacks, attacks[1:]))
    assert sum(1 for t in env.transitions if t.new == "ACERCARSE") <= 3
    assert max(zd) <= tol(env) + 25
    assert m.alive and not env.bot.finished


def test_b1_salir_de_la_zona_atacando_descarta_la_posicion():
    h = Harness(lambda h, n: perc_far())
    from test_bot_behavior import label
    from bot.bot import _Attack
    h.bot.tracker.update(__import__("bot.vision.coords", fromlist=["Coords"]).Coords(415, 170, 1.0), h.clock())
    p = perc_far(metins=[label("metin_de_oscuridad", True, click=(1000, 500))])
    h.bot.last_perception = p
    h.bot._atk = _Attack(0.0, (1000, 500), None, 0.0, 0.0, (415, 170), start_pos=(415, 170))
    h.bot.state = State.ATACAR
    h.bot.tracker.update(__import__("bot.vision.coords", fromlist=["Coords"]).Coords(300, 150, 1.0), h.clock())
    h.bot.tracker.forget_reference()
    h.bot.tracker.update(__import__("bot.vision.coords", fromlist=["Coords"]).Coords(300, 150, 1.0), h.clock())
    out = h.bot._do_atacar(p, 0.2)
    assert out is not None and out[0] is State.VOLVER and out[1] == "salio de la zona atacando"
    # sin ignorar posiciones: VOLVER no ataca otro "cerca" hasta volver adentro y por approach.reject_s no se cliquean metin
    # sin posicion estimada
    assert not h.bot.ignored and h.bot._left_zone and h.bot._blind_wait_until > 0.2


def test_b1_tramo_cortado_por_la_guardia_alimenta_a_la_camara():
    h = Harness(lambda h, n: perc_far())
    from bot.bot import _Walk
    from bot.vision.coords import Coords
    h.bot.tracker.update(Coords(550, 150, 1.0), h.clock())
    h.bot.tracker.update(Coords(565, 150, 1.0), h.clock() + 0.5)
    w = _Walk((100.0, 0.0), (550, 150), 0.0, (565, 150), 0.0, None, 50.0, probe=True)
    assert not h.bot.camera.has_a0
    h.bot._cut_walk(h.bot._img and None or perc_far(), "zona", w)
    assert h.bot.camera._segs and not h.bot.camera.ready   # un tramo guardado para aprender A0, sin invalidar nada
    h.bot.camera.update((0, 100), (0, 20))
    assert h.bot.camera.ready
    h.bot._cut_walk(perc_far(), "zona", _Walk((100.0, 0.0), (550, 150), 0.0, (565, 150), 0.0, None, 50.0, probe=True))
    assert h.bot.camera.ready   # con A0, el desplazamiento parcial re-estima el giro


# ---------------------------------------------------------------- B2
@pytest.mark.parametrize("start_sd", [-5, -15, -25])
def test_b2_destrabar_desde_el_borde_no_pasa_la_tolerancia(start_sd):
    worst = 0.0
    for seed in range(1, 5):
        env = SimEnv(seed, phi=math.pi / 2, start=(550.0, 150.0))
        env.world.pos[:] = (x_at_sd(env, start_sd), 150.0)
        zd = env.track_zone()
        env.bot._enter(State.DESTRABAR, "test")
        env.run_until(max_s=60)
        worst = max(worst, max(zd))
    assert worst <= tol(env)


def test_b2_exploracion_sin_metin_no_sale_mas_que_un_margen_chico():
    for seed in range(1, 4):
        env = SimEnv(seed, phi=0.5, start=(x_at_sd(env0 := SimEnv(0), 3), 150.0))
        zd = env.track_zone()
        env.run_until(max_s=200)
        assert max(zd) <= tol(env) * 0.5


def test_b2_safe_length_sin_piso_cerca_del_borde():
    env = SimEnv(1)
    nav = env.bot.navigator
    inside = nav.safe_length((x_at_sd(env, 40), 150.0))
    edge = nav.safe_length((x_at_sd(env, -25), 150.0))
    assert edge < inside and edge <= nav.measure_factor * nav.min_px + 1e-6


# ---------------------------------------------------------------- B3
@pytest.mark.parametrize("start_sd", [-5, -15, -22])
def test_b3_espacio_mas_w_nunca_pasa_la_tolerancia(start_sd):
    env = SimEnv(1, phi=math.pi / 2, start=(550.0, 150.0))
    env.world.pos[:] = (x_at_sd(env, start_sd), 150.0)
    env.world.surround_now(3, waves=1)
    zd = env.track_zone()
    env.bot._enter(State.DESTRABAR, "test")
    env.run_until(max_s=15)
    assert max(zd) <= tol(env)


def test_b3_w_en_tramos_cortos():
    env = SimEnv(1, phi=math.pi / 2, start=(550.0, 150.0))
    env.world.surround_now(3, waves=1)
    env.bot._enter(State.DESTRABAR, "test")
    env.run_until(cond=lambda: any(e == ("key_up", "w") for e in env.backend.events), max_s=15)
    downs = [t for t, e in zip(env.backend.event_t, env.backend.events) if e == ("key_down", "w")]
    assert len(downs) >= 2   # forward_s 1.0 en tramos de 0.3 s
    ups = [t for t, e in zip(env.backend.event_t, env.backend.events) if e == ("key_up", "w")]
    assert all(u - d <= float(env.cfg["stuck"]["forward_chunk_s"]) + 0.11 for d, u in zip(downs, ups))


# ---------------------------------------------------------------- B4
@pytest.mark.parametrize("offset", [0.2, 1.5, 2.5])
def test_b4_captcha_durante_los_holds_suena_enseguida_y_no_sale_otra_tecla(offset):
    env = SimEnv(1, phi=math.pi / 2, start=(550.0, 150.0))
    env.world.surround_now(3, waves=6)
    env.bot._enter(State.DESTRABAR, "test")
    t0 = env.world.t
    env.world.schedule_captcha(t0 + offset, 30.0)
    env.run_until(cond=lambda: any(a[1] == "start" for a in env.alarm.log), max_s=30)
    starts = [a for a in env.alarm.log if a[1] == "start"]
    assert starts and starts[0][0] - (t0 + offset) <= 0.6
    n = len(env.backend.events)
    env.run_until(max_s=3)
    assert env.bot.state is State.CAPTCHA
    assert not [e for e in env.backend.events[n:] if e[0] in ("key_down", "mouse_down")]


# ---------------------------------------------------------------- I1 / I2
def test_i1_descarte_lejano_sin_camara_no_bloquea_un_metin_visible():
    env = SimEnv(2, phi=0.3, start=(560.0, 150.0))
    env.world.add_metin((600.0, 150.0))
    env.bot.discarded.append(_Spot((9999.0, 9999.0), env.world.t + 600))
    env.bot.discarded.append(_Spot((200.0, 150.0), env.world.t + 600, unc=25.0))
    env.run_until(lambda: any(t.new == "ATACAR" for t in env.transitions), max_s=60)
    first = next(t for t in env.transitions if t.new == "ATACAR")
    assert first.t < 10


def test_i2_piedra_fantasma_no_hace_loop():
    from bot.vision.far_metin import FarMetin
    env = SimEnv(3, phi=0.3, start=(560.0, 150.0))
    ph = (620.0, 150.0)
    orig = env.perceive

    def per(img, c, **kw):
        p = orig(img, c, **kw)
        if 40 <= math.dist(ph, env.world.pos) <= 150:
            off = env.world.screen_offset_of(ph)
            fc = (round(env.backend.player[0] + off[0]), round(env.backend.player[1] + off[1]))
            if 40 <= fc[0] < 1818 and 40 <= fc[1] < 1017:
                p.far_metins = list(p.far_metins) + [FarMetin(Rect(fc[0] - 25, fc[1] - 60, 50, 70), fc, 0.9, True)]
        return p
    env.bot._perceive = per
    env.run_until(max_s=600)
    # decision simple: a la piedra fantasma se le hace clic directo; sin panel, truco y segundo clic, y se la suelta (solo esa,
    # approach.reject_s como maximo): se la vuelve a intentar de vez en cuando, nunca en bucle
    tries = [t.t for t in env.transitions if t.new == "ATACAR"]
    assert 1 <= len(tries) <= 2 + 600 / float(env.cfg["approach"]["reject_s"])
    assert not any(t.new == "ACERCARSE" for t in env.transitions)


def test_i2_tope_de_aproximaciones_por_posicion():
    h = Harness(lambda h, n: perc_far(far=[far_at(1300, 548)]))
    h.train_camera()
    from bot.bot import _FarCand
    p = perc_far(far=[far_at(1300, 548)])
    from bot.vision.coords import Coords
    h.bot.tracker.update(Coords(550, 150, 1.0), h.clock())
    c = _FarCand(p.far_metins[0], (580.0, 150.0))
    n = int(h.cfg["approach"]["max_tries_per_spot"])
    assert all(h.bot._register_approach(c.gp, c.far.center, p, 0.0) is not None for _ in range(n))
    assert h.bot._register_approach(c.gp, c.far.center, p, 0.0) is None and h.bot.ignored


# ---------------------------------------------------------------- I3 / I4
def test_i3_espacio_primero_con_monstruos_y_rota_tras_dos_fallos():
    ep = StuckEpisode(1, (0, 0), 0.0, 0.0)
    assert ep.next_strategy(True) == "ESPACIO"
    ep.start_attempt("ESPACIO", 0.0)
    assert ep.next_strategy(True) == "ESPACIO"
    ep.start_attempt("ESPACIO", 0.0)
    assert ep.next_strategy(True) in ("MIRAR", "ATRAS")


def test_i4_progreso_real_exige_no_empeorar():
    h = Harness(lambda h, n: perc_far())
    b = h.bot
    # ATACAR: se alejo 15 u del origen pero se alejo tambien del objetivo -> no cierra
    ep = StuckEpisode(1, (550, 150), 0.0, 0.0, state="ATACAR", origin_sd=60.0, goal=(580.0, 150.0), goal_dist0=30.0)
    assert not b._episode_progress(ep, (535, 150))
    assert b._episode_progress(ep, (565, 150))   # 15 u del origen y mas cerca del objetivo
    ep2 = StuckEpisode(1, (500, 150), 0.0, 0.0, state="ATACAR", origin_sd=60.0, goal=(580.0, 150.0), goal_dist0=80.0)
    assert b._episode_progress(ep2, (520, 150))
    # VOLVER: hay que mejorar la distancia firmada
    ep3 = StuckEpisode(1, (420, 170), 0.0, 0.0, state="VOLVER", metric0=b.zone.signed_distance((420, 170)))
    assert not b._episode_progress(ep3, (420, 185))   # mejoro menos que progress_tol
    assert not b._episode_progress(ep3, (405, 170))   # se alejo, pero hacia afuera
    assert b._episode_progress(ep3, (440, 170))


# ---------------------------------------------------------------- I5
def test_i5_guardia_con_umbral_fijo():
    h = Harness(lambda h, n: perc_far())
    from bot.bot import _Walk
    lim = h.bot.navigator.guard_limit()
    w = _Walk((1.0, 0.0), (0, 0), 0.0, (0, 0), 0.0, None, lim - 5, probe=False)   # arranca mas alla del umbral
    deeper = (400, 190)   # peor
    assert h.bot.zone.signed_distance(deeper) < lim - 5
    assert h.bot._walk_problem(w, deeper) == "zona"
    w2 = _Walk((1.0, 0.0), (0, 0), 0.0, (0, 0), 0.0, None, lim - 5, probe=False)
    assert h.bot._walk_problem(w2, (550, 150)) is None   # mejorando: se deja


# ---------------------------------------------------------------- I6
def test_i6_presupuesto_por_tipo_y_sin_imagen_por_parpadeo(tmp_path):
    cfg = make_cfg(debug={"enabled": True, "dir": str(tmp_path), "max_images": 100, "explore_every_s": 0.5,
                          "max_per_kind": {"descarte": 2, "estado": 3}})
    h = Harness(lambda h, n: perc_far(), cfg=cfg)
    h.bot.step()
    for i in range(10):
        h.bot._dbg("descarte", f"x{i}")
    assert h.bot.debug.per_kind["descarte"] == 2
    for i in range(10):
        h.bot._dbg("estado", f"e{i}")
    assert h.bot.debug.per_kind["estado"] <= 3
    # parpadeo del mismo motivo: una sola vez
    n0 = h.bot.debug.per_kind.get("descarte", 0)
    h2 = Harness(lambda h, n: perc_far(), cfg=make_cfg(debug={"enabled": True, "dir": str(tmp_path), "max_images": 100}))
    h2.bot.step()
    key = (("tipo", "x"), "tipo ignorado (x)")
    seen = []
    h2.bot._dbg = lambda kind, reason: seen.append(reason)
    for _ in range(5):
        h2.bot._log_reasons([key])
        h2.bot._log_reasons([])   # desaparece un ciclo
    assert len(seen) == 1 and n0 == 2


def test_i6_config_por_defecto():
    from bot.config import DEFAULTS
    d = DEFAULTS["debug"]
    assert d["explore_every_s"] == 30 and d["max_per_kind"] == {
        "estado": 150, "descarte": 40, "destrabe": 60, "ignorada": 20, "equivocada": 20, "bloqueado": 20, "explorar": 40}


# ---------------------------------------------------------------- I7
def test_i7_piedra_sin_humo_rodeada_de_monstruos_tambien_es_candidata():
    """Decision simple: sin el filtro de "rodeada de monstruos": se la cliquea y decide el panel."""
    h = Harness(lambda h, n: perc_far())
    h.bot.tracker.update(__import__('bot.vision.coords', fromlist=['Coords']).Coords(550, 150, 1.0), h.clock())
    p = perc_far(far=[FarMetin(Rect(1275, 488, 50, 70), (1300, 548), 0.9, True, smoke=False)])
    p.monsters = [MonsterLabel(Rect(1250, 500, 100, 12), (1300, 506)), MonsterLabel(Rect(1300, 560, 100, 12), (1350, 566))]
    far, _ = h.bot._far_view(p, 0.0)
    assert len(far) == 1


def test_i7_validacion_de_alt_y_minimos():
    from bot.config import DEFAULTS, ConfigError, validate_config
    for path, val in [(("far_metin", "min_core_pixels"), 0), (("monsters", "min_red_pixels"), 0),
                      (("far_metin", "alt_min_bright_px"), "x"), (("far_metin", "alt_bright_lo"), [1, 2]),
                      (("far_metin", "alt_enabled"), 1), (("far_metin", "alt_min_ground_frac"), -1)]:
        cfg = copy.deepcopy(DEFAULTS)
        cfg["vision"][path[0]][path[1]] = val
        with pytest.raises(ConfigError):
            validate_config(cfg)


def test_i7_detectores_no_fallan_con_minimos_en_cero():
    import numpy as np
    from bot.vision.far_metin import find_far_metins
    from bot.vision.monsters import find_monsters
    cfg = copy.deepcopy(__import__("bot.config", fromlist=["DEFAULTS"]).DEFAULTS)
    cfg["vision"]["far_metin"]["min_core_pixels"] = 0
    cfg["vision"]["monsters"]["min_red_pixels"] = 0
    img = np.zeros((200, 300, 3), dtype=np.uint8)
    assert find_far_metins(img, cfg) == [] and find_monsters(img, cfg) == []


# ---------------------------------------------------------------- menores
def test_claves_obsoletas_se_ignoran_con_warning(tmp_path, caplog):
    from bot.config import DEFAULTS, load_config
    data = copy.deepcopy(DEFAULTS)
    data["stuck"]["max_no_progress"] = 3
    data["volver"]["alt_targets"] = 3
    f = tmp_path / "c.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        cfg = load_config(f)
    assert "max_no_progress" not in cfg["stuck"] and "alt_targets" not in cfg["volver"]
    assert sum("obsoleta" in r.getMessage() for r in caplog.records) == 2


def test_centro_con_timeout_tiene_mensaje_propio():
    from test_bot_states import OUT_XY, perc
    h = Harness(lambda h, n: perc(coords=OUT_XY))
    h.train_camera()
    h.run_until(lambda: h.bot.finished, limit=5000)
    assert h.alarm.starts == ["no puede llegar al centro"]


def test_approaches_no_cuenta_la_vuelta_desde_destrabar():
    h = Harness(lambda h, n: perc_far())
    h.bot.state = State.DESTRABAR
    h.bot._enter(State.ACERCARSE, "vuelta del destrabe")
    assert h.bot.stats.approaches == 0
