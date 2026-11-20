"""ATACAR caminando hacia un metin lejano, trabado con monstruos, y compuerta de los giros de busqueda."""
import logging

import pytest

import bot.input.base as base
from bot.bot import State, _Attack, _Unstuck
from bot.tracking import StuckEpisode
from sim_bot_env import SimEnv
from test_bot_behavior import far_at, messages, perc_far
from test_bot_states import Harness, metin, perc, target_panel


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


def test_caminando_no_cuenta_el_estancamiento_de_vida():
    """La vida del metin no baja mientras el personaje camina hacia el: ni reclic ni descarte."""
    def script(h, n):
        xy = (500 + n, 150)   # 1 u por ciclo: camina
        if h.bot.state is State.ATACAR:
            return perc_far(coords=xy, panel=target_panel(1.0))
        return perc(coords=xy, metins=[metin()])
    h = Harness(script, attack={"hp_stall_s": 1.0, "blocked_s": 1.0, "max_reclicks": 1})
    h.run_until(lambda: State.ATACAR in h.states())
    for _ in range(60):   # ~9 s caminando
        h.bot.step()
    assert h.bot.state is State.ATACAR and h.clicks() == 1 and not h.bot.discarded


def test_el_tope_walk_max_s_vuelve_a_contar_el_estancamiento():
    def script(h, n):
        xy = (500 + n, 150)
        if h.bot.state is State.ATACAR:
            return perc_far(coords=xy, panel=target_panel(1.0))
        return perc(coords=xy, metins=[metin()])
    h = Harness(script, attack={"hp_stall_s": 1.0, "blocked_s": 50.0, "max_reclicks": 1, "walk_max_s": 2.0})
    h.run_until(lambda: h.clicks() >= 2, limit=100)   # tras ~2 s caminando la vida "no baja" y reclickea
    assert h.bot.state is State.ATACAR


def test_quieto_sin_monstruos_sigue_el_camino_de_siempre():
    """Sin caminar el estancamiento cuenta como antes (reclic por vida sin bajar)."""
    h = Harness(lambda h, n: perc_far(panel=target_panel(1.0)) if h.bot.state is State.ATACAR else perc(metins=[metin()]),
                attack={"hp_stall_s": 1.0, "blocked_s": 50.0, "max_reclicks": 2})
    h.run_until(lambda: h.clicks() >= 2)


def test_trabado_en_el_camino_con_monstruos_espacio_y_reclic_donde_esta_ahora(caplog):
    caplog.set_level(logging.INFO)

    def label_at(n):
        return metin(click=(1000 + min(n, 20) * 5, 500))   # el metin se corre en pantalla al caminar y queda quieto
    def script(h, n):
        xy = (500 + min(n, 12), 150)   # camina 12 ciclos y despues queda quieto (trabado)
        if h.bot.state is State.ATACAR:
            return perc_far(coords=xy, panel=target_panel(1.0), n_near=3, metins=[label_at(n)])
        return perc(coords=xy, metins=[label_at(n)])
    h = Harness(script, attack={"unstick_space_s": 1.5, "blocked_s": 50.0, "hp_stall_s": 50.0})
    h.run_until(lambda: h.clicks() >= 2, limit=200)
    msgs = messages(caplog)
    assert h.keys("space") >= 1
    assert any("trabado yendo al metin" in m and "monstruos cerca: 3" in m for m in msgs)
    assert any(m.startswith("Reclic en") and "trabado en el camino" in m for m in msgs)
    last_click = [e for e in h.be.events if e[0] == "move_to"][-1]
    assert (last_click[1] - 8, last_click[2] - 31) == (1100, 500)   # donde esta el metin ahora, no donde estaba
    assert h.bot._atk is None or h.bot._atk.reclicks == 1


def test_trabado_sin_monstruos_no_usa_la_regla_del_camino(caplog):
    caplog.set_level(logging.INFO)

    def script(h, n):
        xy = (500 + min(n, 12), 150)
        if h.bot.state is State.ATACAR:
            return perc_far(coords=xy, panel=target_panel(1.0), n_near=0)
        return perc(coords=xy, metins=[metin()])
    h = Harness(script, attack={"blocked_s": 50.0, "hp_stall_s": 50.0})
    h.run_until(lambda: State.ATACAR in h.states())
    for _ in range(60):
        h.bot.step()
    assert not any("trabado yendo al metin" in m for m in messages(caplog))


def test_reclic_ubica_la_piedra_mas_cercana_al_ultimo_punto():
    h = Harness(lambda h, n: perc())
    a = _Attack(0.0, (1300, 500), None, 0.0, 0.0, None)
    stones = perc_far(far=[far_at(1500, 700), far_at(1330, 560)])
    assert h.bot._relocate(a, stones, []) == far_at(1330, 560).rect.center
    assert h.bot._relocate(a, perc_far(), []) is None


# ---------------------------------------------------------------- compuerta de los giros de busqueda
def test_barrido_nunca_gira_dos_veces_sin_settle_s_ni_settle_frames():
    env = SimEnv(1)   # sin metin: barrido de EXPLORAR
    ex = env.cfg["explore"]
    perceived: list[float] = []
    orig = env.bot._perceive

    def spy(*a, **kw):
        p = orig(*a, **kw)
        perceived.append(env.world.t)
        return p
    env.bot._perceive = spy
    env.run_until(max_s=40)
    cam = set(env.backend.k_cam)
    downs = [t for t, e in zip(env.backend.event_t, env.backend.events) if e[0] == "key_down" and e[1] in cam]
    ups = [t for t, e in zip(env.backend.event_t, env.backend.events) if e[0] == "key_up" and e[1] in cam]
    assert len(downs) >= 6
    for end, nxt in zip(ups, downs[1:]):
        if nxt - end > 5:   # otro barrido (hubo un tramo de caminata en el medio)
            continue
        assert nxt - end >= float(ex["settle_s"]) - 1e-6, (end, nxt)
        assert sum(1 for t in perceived if end < t <= nxt) >= int(ex["settle_frames"]), (end, nxt)


def test_giro_de_mirar_tambien_espera_la_compuerta():
    h = Harness(lambda h, n: perc(), explore={"settle_s": 0.6, "settle_frames": 2})
    h.bot._us, h.bot._ep = _Unstuck(back=State.BUSCAR, phase="look"), StuckEpisode(1, (550, 150), 0.0, 0.0)
    u, ep, p = h.bot._us, h.bot._ep, perc()
    h.bot._unstuck_look(p, 0.0, u, ep)
    assert u.steps == 1
    h.bot._unstuck_look(p, 0.0, u, ep)           # sin tiempo ni capturas: no gira
    assert u.steps == 1
    h.clock.t += 1.0
    h.bot._frames += 1
    h.bot._unstuck_look(p, 0.0, u, ep)           # paso el tiempo pero falta una captura
    assert u.steps == 1
    h.bot._frames += 1
    h.bot._unstuck_look(p, 0.0, u, ep)
    assert u.steps == 2
