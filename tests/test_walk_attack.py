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
    h = Harness(script, attack={"reclick_stall_s": 1.0, "blocked_s": 1.0, "max_reclicks": 1})
    h.run_until(lambda: State.ATACAR in h.states())
    for _ in range(60):   # ~9 s caminando
        h.bot.step()
    assert h.bot.state is State.ATACAR and h.clicks() == 1 and not h.bot.discarded


def test_el_tope_walk_max_s_vuelve_a_contar_el_estancamiento():
    def script(h, n):
        xy = (500 + min(n, 25), 150)   # camina ~4 s y frena: el reclic nunca sale en movimiento
        if h.bot.state is State.ATACAR:
            return perc_far(coords=xy, panel=target_panel(1.0))
        return perc(coords=xy, metins=[metin()])
    h = Harness(script, attack={"reclick_stall_s": 1.0, "blocked_s": 50.0, "max_reclicks": 1, "walk_max_s": 2.0})
    h.run_until(lambda: h.clicks() >= 2, limit=100)   # tras ~2 s caminando la vida "no baja" y reclickea
    assert h.bot.state is State.ATACAR


def test_quieto_con_vida_sin_bajar_reclickea_por_estancamiento():
    """Con la escalera de trabado apagada (stuck.fast_s enorme) la vida quieta reclick_stall_s reclickea como siempre."""
    h = Harness(lambda h, n: perc_far(panel=target_panel(1.0)) if h.bot.state is State.ATACAR else perc(metins=[metin()]),
                attack={"reclick_stall_s": 1.0, "blocked_s": 50.0, "max_reclicks": 2}, stuck={"fast_s": 1000.0})
    h.run_until(lambda: h.clicks() >= 2)


def _stuck_script(label_x=1000, n_near=0):
    """Camina 12 ciclos hacia el metin y queda quieto (trabado); el panel confirma el metin con vida entera."""
    def script(h, n):
        xy = (500 + min(n, 12), 150)
        m = metin(click=(label_x + min(n, 20) * 5, 500))   # el metin se corre en pantalla al caminar y queda quieto
        if h.bot.state is State.ATACAR:
            return perc_far(coords=xy, panel=target_panel(1.0), metins=[m], n_near=n_near)
        return perc(coords=xy, metins=[m])
    return script


def _clicks_xy(h):
    """Posiciones de cliente de los clics izquierdos (el backend anota dos move_to por clic)."""
    out, last = [], None
    for e in h.be.events:
        if e[0] == "move_to":
            last = (e[1] - 8, e[2] - 31)
        elif e == ("mouse_down", "left"):
            out.append(last)
    return out


def test_trabado_con_monstruos_a_los_18_s_espacio_y_reclic_en_el_mismo_metin(caplog):
    caplog.set_level(logging.INFO)
    h = Harness(_stuck_script(n_near=2), attack={"unstick_space_s": 1.5, "blocked_s": 50.0, "reclick_stall_s": 50.0})
    h.run_until(lambda: h.clicks() >= 2, limit=300)
    msgs = messages(caplog)
    assert h.keys("space") >= 1 and h.keys("a") == 0 and h.keys("d") == 0
    assert any("Trabado yendo al metin" in m and "intento 1/4" in m and "ESPACIO" in m for m in msgs)
    assert any(m.startswith("Reclic en") and "trabado, intento 1" in m for m in msgs)
    assert _clicks_xy(h)[-1] == (1100, 476)   # el mismo metin, donde esta ahora (al cuerpo de la piedra, 24 px arriba)
    assert h.bot._cur is not None and h.bot._cur.unstick_n == 1


def test_trabado_sin_monstruos_va_al_costado_alternando_y_reclickea_sin_cambiar_de_metin(caplog):
    caplog.set_level(logging.INFO)
    # otro metin mas cerca del personaje: no se tiene que elegir nunca
    other = metin(click=(950, 560))

    def script(h, n):
        p = _stuck_script()(h, n)
        if h.bot.state is State.ATACAR:   # aparece despues de elegir: cerca del personaje, pero no es el metin en curso
            p.metins = list(p.metins) + [other]
        return p
    h = Harness(script, attack={"unstick_space_s": 1.5, "blocked_s": 50.0, "reclick_stall_s": 50.0, "stuck_reclicks": 0})
    h.run_until(lambda: h.clicks() >= 4, limit=600)
    msgs = messages(caplog)
    assert any("paso al costado (A)" in m for m in msgs) and any("paso al costado (D)" in m for m in msgs)
    assert h.keys("a") >= 1 and h.keys("d") >= 1
    assert all(abs(x - 1100) <= 5 for x, _ in _clicks_xy(h)[1:])   # todos los reclics, al mismo metin
    assert h.states().count(State.ATACAR) == 1   # sin pasar por BUSCAR


def test_trabado_agotada_la_escalera_recien_ahi_destrabar(caplog):
    caplog.set_level(logging.INFO)
    h = Harness(_stuck_script(), attack={"blocked_s": 50.0, "reclick_stall_s": 50.0, "stuck_reclicks": 0})
    h.run_until(lambda: State.DESTRABAR in h.states(), limit=2000)
    assert h.clicks() == 1 + int(h.cfg["attack"]["max_unstick"])   # clic inicial + un reclic por intento
    assert h.bot._cur is not None   # el metin en curso no se pierde


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
