"""Acercarse quieto y un clic: alinear la vista con Q/E, W hasta cerca, frenar, un clic en el cuerpo, esperar a confirmar; ritmo
de clics (nunca en movimiento ni dos a menos de `attack.min_click_gap_s`); clic nunca en el nombre; metin con nombre no se
descarta por seleccion equivocada; panel ausente -> clic derecho sobre un monstruo; ESPACIO solo con bichos. Todo contra el
simulador (input falso)."""
import dataclasses
import logging
import math
from pathlib import Path

import cv2
import pytest

import bot.input.base as base
from bot.bot import _Attack, _CurrentMetin, _Surround
from bot.geometry import Rect
from bot.perception import perceive
from bot.vision.click_point import stone_rect_near
from bot.vision.monsters import MonsterLabel
from bot.vision.panel import PanelInfo
from sim_bot_env import SimEnv, make_cfg

OSC = "metin_de_oscuridad"
K = 13.0   # unidades del juego por unidad de piso del simulador
FACING_NORTH = 0.0
DATA = Path(__file__).parent / "data"
EPS = 1e-6


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


def kenv(phi=FACING_NORTH, start=(560.0, 150.0), seed=3, mode="teclado", **kw):
    cfg = make_cfg(navigation={"mode": mode})
    cfg["approach"].update(blind_near_floor=1000.0, blind_inside_margin=-1000.0)   # sin camara calibrada (teclado) igual se acerca
    cfg["navigation"]["keyboard"]["view_right_key"] = "camera_left"   # en el simulador camera_left gira la vista a la derecha
    return SimEnv(seed, phi=phi, start=start, cfg=cfg, heading=True, pinhole_k=K, **kw)


def msgs(caplog):
    return [r.getMessage() for r in caplog.records]


def watch(env):
    """Historial de posiciones del personaje (cada subpaso) y de cada clic izquierdo (t, posicion al clic)."""
    hist: list[tuple[float, tuple[float, float]]] = []
    clicks: list[tuple[float, tuple[float, float]]] = []
    env.world.on_step.append(lambda w: hist.append((w.t, (float(w.pos[0]), float(w.pos[1])))))
    orig = env.world.click_screen

    def spy(vec):
        clicks.append((env.world.t, (float(env.world.pos[0]), float(env.world.pos[1]))))
        return orig(vec)
    env.world.click_screen = spy
    return hist, clicks


def moved_before(hist, t, win=0.1):
    """El personaje cambio de lugar en los `win` s anteriores a `t` (dos lecturas iguales de coordenadas = quieto)."""
    now = next((p for ts, p in reversed(hist) if ts <= t + EPS), None)
    old = next((p for ts, p in reversed(hist) if ts <= t - win + EPS), None)
    return now is not None and old is not None and math.dist(now, old) > 0.05


def key_events(env, names):
    b = env.backend
    return [(b.event_t[i], e[1]) for i, e in enumerate(b.events) if e[0] == "key_down" and e[1] in names]


def check_rhythm(hist, clicks, gap=1.5):
    for i, (t, _) in enumerate(clicks):
        assert not moved_before(hist, t), f"clic {i} a t={t:.2f} con el personaje en movimiento"
        if i:
            assert t - clicks[i - 1][0] >= gap - 1e-3, f"clics {i - 1} y {i} a {t - clicks[i - 1][0]:.2f} s"


# ---------------------------------------------------------------- acercamiento
def test_metin_cerca_en_pantalla_clic_directo_sin_girar_la_vista(caplog):
    """Metin objetivo en pantalla a piso ~3 (<= approach.near_floor), de costado: clic directo en su cuerpo, sin Q/E para
    centrarlo ni ACERCARSE (antes giraba la vista y lo perdia)."""
    caplog.set_level(logging.INFO)
    env = kenv()
    hist, clicks = watch(env)
    env.world.add_metin((580.0, 115.0), name=OSC)   # ~40 u, a la derecha del centro
    assert env.run_until(lambda: env.world.clicks_on_metin, max_s=60) == "cond"
    assert not key_events(env, set(env.backend.k_cam)), "no gira la vista para centrarlo"
    assert not any("Acercandome:" in m for m in msgs(caplog))
    assert not any(tr.new == "ACERCARSE" for tr in env.transitions)
    assert len(clicks) == 1 and not moved_before(hist, clicks[0][0], 0.4)


@pytest.mark.parametrize("mode", ["teclado", "clic"])
def test_ritmo_de_clics_en_todos_los_caminos(mode):
    env = kenv(mode=mode, start=(560.0, 150.0))
    hist, clicks = watch(env)
    m = env.world.add_metin((585.0, 150.0), name=OSC, dps=0.02)
    env.world.wrong_select = 3   # tres clics seleccionan un monstruo: hay que reclickear
    assert env.run_until(lambda: not m.alive, max_s=240) == "cond"
    assert len(clicks) >= 4
    check_rhythm(hist, clicks)


def test_metin_con_nombre_no_se_descarta_por_seleccion_equivocada(caplog):
    caplog.set_level(logging.INFO)
    env = kenv(start=(560.0, 150.0))
    hist, clicks = watch(env)
    m = env.world.add_metin((585.0, 150.0), name=OSC, dps=0.02)
    env.world.wrong_select = 5   # mas que `attack.max_wrong_selections`
    assert env.run_until(lambda: not m.alive, max_s=240) == "cond"
    ms = msgs(caplog)
    assert not any("seleccion equivocada repetida (" in t.reason for t in env.transitions)
    assert not any("metin descartado" in x.lower() for x in ms)
    assert any("no lo descarto, me reacomodo y reintento" in x for x in ms)
    check_rhythm(hist, clicks)


# ---------------------------------------------------------------- panel ausente: clic derecho
def right_clicks(env):
    b = env.backend
    return [b.event_t[i] for i, e in enumerate(b.events) if e == ("mouse_down", "right")]


def left_click_times(env):
    b = env.backend
    return [b.event_t[i] for i, e in enumerate(b.events) if e == ("mouse_down", "left")]


def bug_panel(env, attacking: bool, with_monster: bool = True):
    """Error del juego: no hay panel (aunque se este atacando) hasta que el bot hace clic derecho sobre un monstruo. Despues
    el panel muestra al monstruo ~0.5 s y, si `attacking`, vuelve al metin; si no, queda el monstruo hasta el proximo clic
    izquierdo. Con `with_monster` hay un nombre rojo cerca del personaje."""
    orig = env.bot._perceive

    def wrapped(img, cfg, **kw):
        p = orig(img, cfg, **kw)
        px, py = p.player_xy
        monsters, near = p.monsters, p.monsters_near
        if with_monster:   # del lado opuesto a la piedra, a ~110 px del personaje (dentro de vision.monsters.near_radius_px)
            a = env.bot._atk
            side = -1 if a is not None and a.click[0] > px else 1
            r = Rect(px + side * 100 - 50, py - 60, 100, 12)
            env.mon = r
            monsters, near = [MonsterLabel(r, r.center)], 1
        rc = right_clicks(env)
        panel = None
        if rc:
            now = env.world.t
            lefts = [t for t in left_click_times(env) if t > rc[0]]
            if now - rc[0] < 0.5 or not (attacking or lefts):
                panel = PanelInfo(present=True, rect=Rect(700, 5, 450, 60), kind="other")
            else:
                panel = p.panel
        return dataclasses.replace(p, panel=panel, monsters=monsters, monsters_near=near)
    env.bot._perceive = wrapped


def test_panel_ausente_sin_monstruo_cerca_el_truco_va_sobre_el_metin_y_sigue_atacando():
    """Ningun panel tras el clic (error del juego) y ningun monstruo cerca: el truco del clic derecho va sobre el metin mismo
    ("un monstruo o metin cercano"), una sola vez; el panel vuelve y se sigue atacando, sin soltar la piedra."""
    env = kenv(start=(560.0, 150.0))
    env.world.add_metin((585.0, 150.0), name=OSC, dps=0.02)
    bug_panel(env, attacking=True, with_monster=False)
    env.run_until(lambda: env.world.t > 40.0, max_s=60)
    assert len(right_clicks(env)) == 1 and not env.bot.ignored
    assert env.bot._atk is not None and env.bot._atk.confirmed


def test_panel_recover_apagado_no_hace_clic_derecho():
    env = kenv(start=(560.0, 150.0))
    env.cfg["attack"]["panel_recover"] = False
    env.world.add_metin((585.0, 150.0), name=OSC, dps=0.02)
    bug_panel(env, attacking=True)
    env.run_until(lambda: env.world.t > 40.0, max_s=60)
    assert right_clicks(env) == []


# ---------------------------------------------------------------- punto de clic sobre la piedra real
def real_scene(name):
    img = cv2.imread(str(DATA / name))
    assert img is not None
    env = SimEnv(1, cfg=make_cfg())
    p = perceive(img, env.cfg, full_captcha=False)
    env.bot._img = img
    return env, img, p


@pytest.mark.parametrize("name", ["ataque_sin_panel_pegado.png", "ataque_trabado_al_costado.png"])
def test_clic_en_el_cuerpo_de_la_piedra_nunca_en_el_nombre_ni_debajo_de_el(name):
    env, img, p = real_scene(name)
    det = next(d for d in p.metins if d.is_target)
    gap = int(env.cfg["attack"]["name_gap_px"])
    rect = stone_rect_near(img, det.click_point, env.cfg)
    assert rect is not None
    pt = env.bot._off_name(env.bot._clear_point(env.bot._aim(det, p), p), p)
    r = det.label_rect
    assert pt[1] >= r.bottom + gap, "debajo de la franja del nombre"
    assert rect.x - 12 <= pt[0] < rect.right + 12 and rect.y - 12 <= pt[1] < rect.bottom + 12, "sobre el cuerpo de la piedra"
    # el punto de antes (click_point corrido 0.37 click_dy arriba) caia en la franja entre el nombre y la piedra
    old = (det.click_point[0], det.click_point[1] - round(0.37 * env.cfg["vision"]["metin"]["click_dy"]))
    assert old[1] < rect.y, "el punto viejo quedaba por encima de la piedra"


def test_un_punto_sobre_el_nombre_se_baja_debajo_de_la_franja():
    env, img, p = real_scene("ataque_sin_panel_pegado.png")
    det = next(d for d in p.metins if d.is_target)
    r = det.label_rect
    fixed = env.bot._off_name((r.center[0], r.center[1]), p)
    assert fixed[1] >= r.bottom + int(env.cfg["attack"]["name_gap_px"])


# ---------------------------------------------------------------- ESPACIO solo con bichos
def make_p(env, on_me=0, near=0):
    img = cv2.imread(str(DATA / "ataque_sin_panel_pegado.png"))
    p = perceive(img, env.cfg, full_captcha=False)
    return dataclasses.replace(p, monsters=[], monsters_near=near, monsters_on_me=on_me)


@pytest.mark.parametrize("on_me,near,expected", [(0, 0, False), (0, 1, False), (0, 2, True), (1, 0, True)])
def test_crowded_solo_con_bichos_reales(on_me, near, expected):
    env = SimEnv(1, cfg=make_cfg())
    assert env.bot._crowded(make_p(env, on_me, near)) is expected


def test_trabado_caminando_con_0_monstruos_no_aprieta_espacio(caplog):
    caplog.set_level(logging.INFO)
    env = kenv()
    bot = env.bot
    bot._walking_now = lambda: True
    p = make_p(env, 0, 0)
    bot.tracker._pos, bot.tracker._good_t = (500, 150), env.world.t
    bot._sur = _Surround((500, 150), env.world.t - 10.0)
    assert bot._surrounded(p, env.world.t) is True   # reacciona (desvio), pero sin ESPACIO
    assert not any(e == ("key_down", env.backend.k_space) for e in env.backend.events)
    assert not any("Rodeado de" in m for m in msgs(caplog))


def test_bloqueado_girando_en_circulo_sin_bichos_no_aprieta_espacio(caplog):
    caplog.set_level(logging.INFO)
    env = kenv()
    bot = env.bot
    p = make_p(env, 0, 0)
    a = _Attack(0.0, (900, 400), None, 0.0, 0.0, None)
    a.cm = _CurrentMetin((900, 400), None, OSC, 0.0, 0.0, 0)
    bot._atk = a
    bot._unblock(a, "girando en circulo", 6.0, p)
    assert not any(e == ("key_down", env.backend.k_space) for e in env.backend.events)
    assert not any("Bloqueado atacando" in m and "ESPACIO" in m for m in msgs(caplog))


# ---------------------------------------------------------------- el sentido de Q/E no se invierte a mitad de corrida
def test_un_giro_que_aleja_la_piedra_no_invierte_q_e_y_vuelve_a_medir(caplog):
    """Antes: si un giro de alineacion alejaba la piedra del centro se invertia el sentido de Q/E. Ahora el sentido es fijo:
    el proximo giro se calcula de nuevo con la piedra de esta captura (hacia donde esta)."""
    from bot.bot import _Approach
    from test_bot_states import PLAYER, Harness, metin, perc
    caplog.set_level(logging.INFO)
    h = Harness(lambda h, n: perc())
    h.bot.tracker.update(perc().coords, h.clock())
    far_pt = (PLAYER[0] + 590, 290)   # lejos (piso > near_floor) y a la derecha del centro
    p = perc(metins=[metin(click=far_pt)])
    a = _Approach(h.clock(), None, far_pt, spot=None, point=far_pt, floor_d=7.0, walk=True)
    a.last_dx, a.rematch = -299.0, True   # el giro anterior la dejo a -299 px; ahora aparece a +590
    h.bot._apr = a
    h.bot._acercarse_keys(p, h.clock())
    ms = msgs(caplog)
    assert any("vuelvo a medir el giro" in m for m in ms) and not any("invierto" in m for m in ms)
    turn = next(m for m in ms if m.startswith("Acercandome: la piedra esta a +"))
    assert float(turn.split("giro la vista ")[1].split(" grados")[0]) > 0   # hacia la derecha, donde esta la piedra
    assert not hasattr(a, "turn_sign")
