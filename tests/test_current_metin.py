"""ATACAR: roto REAL (vida baja + piedra desaparecida + tiempo), deseleccion con reclic en el mismo metin, no reclickear si
pega, punto de clic sin monstruos encima y metin en curso (no se elige otro). Todo con input y percepcion falsos."""
import logging
import math

import pytest

import bot.input.base as base
from bot.bot import State
from bot.config import DEFAULTS, ConfigError, validate_config
from bot.geometry import Rect
from bot.vision.monsters import MonsterLabel
from bot.vision.panel import PanelInfo
from test_bot_behavior import messages, perc_far
from test_bot_states import Harness, make_cfg, metin, perc, target_panel

A_POINT = (1000, 500)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


def clicks_xy(h):
    """Posiciones de cliente de los clics izquierdos (el backend anota dos move_to por clic)."""
    out, last = [], None
    for e in h.be.events:
        if e[0] == "move_to":
            last = (e[1] - 8, e[2] - 31)
        elif e == ("mouse_down", "left"):
            out.append(last)
    return out


def spy_spawns(h):
    """Anota las llamadas a `_record_spawn` (el mapa de metin solo se guarda con roturas reales)."""
    calls = []
    h.bot._record_spawn = lambda: calls.append(h.clock.t)
    return calls


def scripted(panels, *, stone=lambda i: [], jump=None):
    """Script: BUSCAR ve el metin A; en ATACAR el ciclo i (1, 2, ...) devuelve el panel `panels(i)` y `stone(i)` (metins a la
    vista); `jump(i)` = segundos de reloj que pasan antes de ese ciclo."""
    w = {"i": 0}

    def script(h, n):
        if h.bot.state is not State.ATACAR:
            return perc(metins=[metin(A_POINT)])
        w["i"] += 1
        if jump is not None:
            h.clock.t += jump(w["i"])
        return perc_far(panel=panels(w["i"]), metins=stone(w["i"]))
    return script, w


# ---------------------------------------------------------------- 0. roto real
def test_panel_que_desaparece_con_vida_alta_es_deseleccion_no_roto(caplog):
    caplog.set_level(logging.INFO)
    h = Harness(None)
    calls = spy_spawns(h)

    def panels(i):
        if i <= 3:
            return target_panel(0.64)
        return target_panel(0.6) if h.clicks() >= 2 else None   # el reclic vuelve a seleccionar el metin
    h.script, w = scripted(panels, stone=lambda i: [metin(A_POINT)])
    h.run_until(lambda: h.clicks() >= 2 and w["i"] >= 12)
    assert h.bot.state is State.ATACAR and h.bot.stats.metins == 0 and not calls
    assert any("Panel perdido con vida 64%: deseleccion, reclic en el mismo metin" in m for m in messages(caplog))
    assert clicks_xy(h) == [A_POINT, A_POINT]
    assert State.BUSCAR not in h.states()[1:]   # sin pasar por BUSCAR


def test_vida_baja_piedra_desaparecida_y_mas_de_min_break_s_es_roto(caplog):
    caplog.set_level(logging.INFO)
    h = Harness(None)
    calls = spy_spawns(h)
    h.script, _ = scripted(lambda i: target_panel(0.03) if i <= 3 else None, jump=lambda i: 20.0 if i == 4 else 0.0)
    h.run_until(lambda: State.RECOGER in h.states())
    assert h.bot.stats.metins == 1 and len(calls) == 1
    assert any(m.startswith("Metin roto (vida 3%, ") for m in messages(caplog))
    assert h.bot._cur is None


def test_vida_baja_piedra_desaparecida_pero_antes_de_min_break_s_no_es_roto(caplog):
    caplog.set_level(logging.INFO)
    h = Harness(None)
    calls = spy_spawns(h)
    h.script, w = scripted(lambda i: target_panel(0.03) if i <= 3 else None)
    h.run_until(lambda: h.clicks() >= 2)
    assert h.bot.stats.metins == 0 and not calls and State.RECOGER not in h.states()
    assert any("no cuenta como roto" in m for m in messages(caplog))


def test_vida_baja_pero_piedra_visible_espera_y_reclickea():
    h = Harness(None)
    calls = spy_spawns(h)
    h.script, w = scripted(lambda i: target_panel(0.04) if i <= 3 else None, stone=lambda i: [metin(A_POINT)],
                           jump=lambda i: 0.0)
    h.run_until(lambda: w["i"] >= 4)
    t0 = h.clock.t
    h.run_until(lambda: h.clicks() >= 2)
    assert h.clock.t - t0 >= float(h.cfg["attack"]["broken_wait_s"]) - 0.5   # espero broken_wait_s antes de reclickear
    assert h.bot.stats.metins == 0 and not calls and clicks_xy(h)[-1] == A_POINT


def test_deselecciones_agotadas_vuelve_a_buscar_con_el_metin_en_curso():
    h = Harness(None, attack={"max_deselect_reclicks": 2})
    h.script, _ = scripted(lambda i: target_panel(0.9) if i <= 3 else None, stone=lambda i: [metin(A_POINT)])
    h.run_until(lambda: State.BUSCAR in h.states())
    assert h.clicks() == 3   # clic inicial + 2 reclics
    assert h.bot._cur is not None and h.bot.stats.metins == 0


# ---------------------------------------------------------------- 2. no reclickear si pega
def test_vida_bajando_no_hay_ningun_reclic_ni_espacio():
    """Quieto 40 s con la vida bajando de a poco: ni 'bloqueado', ni trabado, ni estancamiento."""
    h = Harness(None)
    h.script, w = scripted(lambda i: target_panel(max(0.05, 1.0 - 0.003 * i)), stone=lambda i: [metin(A_POINT)])
    h.run_until(lambda: h.clock.t - 1000.0 > 40.0)
    assert h.clicks() == 1 and h.keys("space") == 0 and h.bot.state is State.ATACAR


def test_vida_quieta_reclick_stall_s_reclickea_al_punto_mas_alejado_de_los_monstruos(caplog):
    caplog.set_level(logging.INFO)
    mon = Rect(940, 480, 120, 14)   # etiquetas de monstruos justo encima del centro de la piedra

    def stone(i):
        return [metin(A_POINT)]

    def script(h, n):
        if h.bot.state is not State.ATACAR:
            return perc(metins=[metin(A_POINT)])
        p = perc_far(panel=target_panel(0.8), metins=stone(0), far=[far_at_stone()])
        p.monsters = [MonsterLabel(mon, mon.center)]
        return p
    h = Harness(script, attack={"walk_stuck_s": 1000.0, "reclick_stall_s": 5.0})
    h.run_until(lambda: h.clicks() >= 2, limit=600)
    x, y = clicks_xy(h)[-1]
    gap = math.hypot(max(mon.x - x, 0, x - mon.right), max(mon.y - y, 0, y - mon.bottom))
    assert gap > float(h.cfg["attack"]["monster_clear_px"]), (x, y, gap)
    assert any("con monstruos encima" in m for m in messages(caplog))


def far_at_stone():
    from test_bot_behavior import far_at
    return far_at(A_POINT[0], A_POINT[1] + 25)   # rect (975..1025, 465..535)


def test_clear_point_sin_monstruos_no_cambia_el_punto():
    h = Harness(lambda h, n: perc())
    assert h.bot._clear_point((1000, 500), perc_far()) == (1000, 500)
    p = perc_far(far=[far_at_stone()])
    p.monsters = [MonsterLabel(Rect(100, 100, 50, 10), (125, 105))]   # lejos
    assert h.bot._clear_point((1000, 500), p) == (1000, 500)


def test_clear_point_descarta_los_candidatos_pegados_a_una_etiqueta():
    h = Harness(lambda h, n: perc())
    p = perc_far(far=[far_at_stone()])
    mon = Rect(940, 480, 120, 14)
    p.monsters = [MonsterLabel(mon, mon.center)]
    x, y = h.bot._clear_point((1000, 500), p)
    gap = math.hypot(max(mon.x - x, 0, x - mon.right), max(mon.y - y, 0, y - mon.bottom))
    assert gap > float(h.cfg["attack"]["monster_clear_px"])


def test_seleccion_equivocada_reclickea_al_punto_despejado():
    mon = Rect(940, 480, 120, 14)

    def script(h, n):
        if h.bot.state is not State.ATACAR:
            return perc(metins=[metin(A_POINT)])
        p = perc_far(panel=PanelInfo(present=True, kind="other", name=None) if h.clicks() < 2 else target_panel(0.9),
                     n_near=2, metins=[metin(A_POINT)], far=[far_at_stone()])
        p.monsters = [MonsterLabel(mon, mon.center)]
        return p
    h = Harness(script)
    h.run_until(lambda: h.clicks() >= 2, limit=300)
    assert h.keys("space") >= 1 and h.keys("esc") >= 1
    x, y = clicks_xy(h)[-1]
    assert math.hypot(max(mon.x - x, 0, x - mon.right), max(mon.y - y, 0, y - mon.bottom)) > 25


# ---------------------------------------------------------------- 4. metin en curso
def wrong_then_view(view_after):
    """ATACAR: el clic cayo en un bicho (panel 'other', sin monstruos cerca); despues `view_after(h)` = metins a la vista."""
    def script(h, n):
        if h.bot.state is State.ATACAR:
            return perc_far(panel=PanelInfo(present=True, kind="other", name=None), metins=[metin(A_POINT)])
        return perc(metins=view_after(h))
    return script


def test_tras_clickear_un_bicho_vuelve_al_mismo_metin_aunque_haya_otro_mas_cerca(caplog):
    caplog.set_level(logging.INFO)
    other = metin((950, 560))   # mas cerca del personaje que A

    def view(h):
        return [metin(A_POINT)] if h.clicks() == 0 else [other, metin(A_POINT)]
    h = Harness(wrong_then_view(view))
    h.run_until(lambda: h.clicks() >= 2, limit=300)
    assert clicks_xy(h) == [A_POINT, A_POINT]
    assert any("Retomo el metin en curso en (1000, 500)" in m for m in messages(caplog))


def test_metin_en_curso_que_no_aparece_en_target_keep_s_se_suelta(caplog):
    caplog.set_level(logging.INFO)
    other = metin((1300, 700))   # lejos del ultimo punto de A (no es el mismo metin)

    def view(h):
        return [metin(A_POINT)] if h.clicks() == 0 else [other]   # A desaparecio de la vista
    h = Harness(wrong_then_view(view))
    h.run_until(lambda: h.bot._cur is not None and State.BUSCAR in h.states())
    t0 = h.clock.t
    h.run_until(lambda: (1300, 700) in clicks_xy(h), limit=3000)   # (EXPLORAR tambien clickea el piso mientras busca)
    assert h.clock.t - t0 >= float(h.cfg["attack"]["target_keep_s"]) - 1.0
    assert any("Abandono el metin en curso: no lo encuentro hace" in m for m in messages(caplog))
    assert (1300, 700) not in clicks_xy(h)[:-1]   # recien despues de soltarlo elige el otro


def test_metin_en_curso_se_busca_por_posicion_si_la_camara_giro(caplog):
    """Con la camara calibrada, el metin en curso se reconoce por su posicion estimada aunque la pantalla cambie."""
    caplog.set_level(logging.INFO)
    h = Harness(wrong_then_view(lambda h: [metin(A_POINT)] if h.clicks() == 0 else [metin((1040, 500))]))
    h.train_camera()
    h.run_until(lambda: h.bot._cur is not None and State.BUSCAR in h.states())
    h.bot.camera.rotations += 1   # la camara giro: el punto de pantalla ya no vale, queda la posicion estimada
    h.run_until(lambda: h.clicks() >= 2, limit=300)
    assert any("Retomo el metin en curso" in m for m in messages(caplog))


# ---------------------------------------------------------------- config
def test_config_nueva_defaults_y_validacion():
    a = DEFAULTS["attack"]
    assert (a["broken_hp_ratio"], a["broken_gone_px"], a["min_break_s"], a["broken_wait_s"]) == (0.10, 90, 15.0, 3.0)
    assert (a["max_deselect_reclicks"], a["hp_progress_s"], a["reclick_stall_s"], a["monster_clear_px"]) == (6, 4.0, 5.0, 25)
    assert (a["walk_stuck_s"], a["sidestep_s"], a["max_unstick"], a["relocate_px"], a["target_keep_s"]) == (5.0, 0.6, 4, 150, 25.0)
    for key, bad in (("min_break_s", -1), ("walk_stuck_s", 0), ("broken_hp_ratio", 2), ("clear_offsets_px", [[1]]),
                     ("target_keep_s", "x")):
        cfg = make_cfg()
        cfg["attack"][key] = bad
        with pytest.raises(ConfigError):
            validate_config(cfg)


# ---------------------------------------------------------------- simulador de punta a punta
def test_sim_clic_en_un_bicho_y_deseleccion_vuelve_al_mismo_metin_y_cuenta_una_rotura():
    from sim_bot_env import SimEnv
    env = SimEnv(2, start=(560, 150))
    a = env.world.add_metin((620, 150))
    env.world.wrong_select = 1   # el primer clic sobre A selecciona un bicho
    b_holder = []
    env.world.schedule(3.0, lambda: b_holder.append(env.world.add_metin((565, 152))))   # otro metin, mucho mas cerca
    env.world.schedule(12.0, env.world.deselect)   # a mitad del ataque se deselecciona (panel perdido con vida alta)
    env.run_until(lambda: env.bot.stats.metins >= 1, max_s=300)
    assert not a.alive and b_holder and b_holder[0].alive and b_holder[0].hp == 1.0   # B no se toco antes de romper A
    assert env.bot.stats.metins == 1
    on_a = [t for t, m in env.world.clicks_on_metin if m is a]
    assert len(on_a) >= 3   # clic al bicho, reclic en el mismo metin y reclic tras la deseleccion
    assert all(m is a for _, m in env.world.clicks_on_metin)
