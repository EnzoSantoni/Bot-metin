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
def test_acerca_gira_camina_frena_y_hace_un_clic_quieto(caplog):
    caplog.set_level(logging.INFO)
    env = kenv()
    hist, clicks = watch(env)
    env.world.add_metin((600.0, 90.0), name=OSC)   # de costado: la piedra se ve a la derecha, lejos del centro
    assert env.run_until(lambda: env.world.clicks_on_metin, max_s=120) == "cond"
    cam = [t for t, _ in key_events(env, set(env.backend.k_cam))]
    w_key = next(k for k, d in env.backend.k_move.items() if d == "up")
    walk = [t for t, _ in key_events(env, {w_key})]
    assert cam and walk and cam[0] < walk[0], "primero gira la vista (Q/E) hasta centrar la piedra y recien despues camina"
    assert len(clicks) == 1, "un solo clic en todo el acercamiento"
    t_click = clicks[0][0]
    assert not moved_before(hist, t_click, 0.4), "el clic sale con el personaje quieto"
    assert t_click - walk[-1] >= 0.4 - 1e-3, "frena (sin W) antes del clic"
    assert any("ACERCARSE -> ATACAR: quieto frente al metin" in m for m in msgs(caplog))
    assert not any(t.reason.startswith("trabado") for t in env.transitions)


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


def test_si_el_panel_no_confirma_otro_clic_quieto_despues_de_1_5_s():
    env = kenv()
    hist, clicks = watch(env)
    env.world.add_metin((600.0, 90.0), name=OSC)
    orig = env.bot._perceive

    def wrapped(img, cfg, **kw):
        p = orig(img, cfg, **kw)
        return dataclasses.replace(p, panel=None if env.world.t < 12.0 else p.panel)   # sin panel un buen rato
    env.bot._perceive = wrapped
    assert env.run_until(lambda: len(clicks) >= 2, max_s=60) == "cond"
    assert clicks[1][0] - clicks[0][0] >= 1.5 - 1e-3
    check_rhythm(hist, clicks)
    env.run_until(lambda: env.world.t > 15.0, max_s=60)
    assert len(clicks) <= 4, "tope de intentos sin confirmar"


def test_tope_de_clics_sin_confirmar_aparta_el_metin_sin_descartarlo(caplog):
    caplog.set_level(logging.INFO)
    env = kenv()
    hist, clicks = watch(env)
    env.world.add_metin((600.0, 90.0), name=OSC)
    orig = env.bot._perceive
    env.bot._perceive = lambda img, cfg, **kw: dataclasses.replace(orig(img, cfg, **kw), panel=None)   # nunca hay panel
    env.run_until(lambda: any("sin confirmar:" in t.reason for t in env.transitions), max_s=120)
    assert 1 <= len(clicks) <= 4
    check_rhythm(hist, clicks)
    assert all(s.expiry - env.world.t <= 31 for s in env.bot.discarded), "se aparta unos segundos, no 10 minutos"
    assert any("no lo descarto" in m for m in msgs(caplog))


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


def test_panel_ausente_pegado_clic_derecho_sobre_monstruo_y_sigue_atacando(caplog):
    caplog.set_level(logging.INFO)
    env = kenv(start=(560.0, 150.0))
    watch(env)
    m = env.world.add_metin((585.0, 150.0), name=OSC, dps=0.02)
    bug_panel(env, attacking=True)
    assert env.run_until(lambda: not m.alive, max_s=240) == "cond"
    assert len(right_clicks(env)) == 1, "un solo clic derecho por metin"
    assert any("El panel volvio a mostrar el metin" in x for x in msgs(caplog))
    assert len(left_click_times(env)) <= 2, "estaba atacando: no hace falta reclickear"
    assert not any("descart" in t.reason for t in env.transitions)
    # el clic derecho cae sobre el CUERPO del monstruo (debajo de su etiqueta), nunca en la etiqueta
    i = next(i for i, e in enumerate(env.backend.events) if e == ("mouse_down", "right"))
    mv = next(e for e in reversed(env.backend.events[:i]) if e[0] == "move_to")
    cx, cy = env.window.screen_to_client(mv[1], mv[2])
    assert cy > env.mon.bottom, "debajo de la etiqueta del monstruo"
    assert abs(cx - env.mon.center[0]) <= 3


def test_panel_ausente_el_panel_no_vuelve_clic_izquierdo_al_metin(caplog):
    caplog.set_level(logging.INFO)
    env = kenv(start=(560.0, 150.0))
    hist, clicks = watch(env)
    m = env.world.add_metin((585.0, 150.0), name=OSC, dps=0.02)
    bug_panel(env, attacking=False)
    assert env.run_until(lambda: not m.alive, max_s=240) == "cond"
    rc = right_clicks(env)
    assert len(rc) == 1
    after = [t for t in left_click_times(env) if t > rc[0]]
    assert after and after[0] - rc[0] >= 1.5 - 1e-3, "clic izquierdo al metin recien a los 1.5 s del derecho"
    assert any("no esta atacando, clic izquierdo al metin" in x for x in msgs(caplog))
    check_rhythm(hist, clicks)


def test_panel_ausente_sin_monstruo_cerca_no_hay_clic_derecho():
    env = kenv(start=(560.0, 150.0))
    env.world.add_metin((585.0, 150.0), name=OSC, dps=0.02)
    bug_panel(env, attacking=True, with_monster=False)
    env.run_until(lambda: env.world.t > 40.0, max_s=60)
    assert right_clicks(env) == []


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
