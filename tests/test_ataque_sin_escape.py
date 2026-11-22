"""ATACAR sin Escape, bichos encima por los nombres, piedra tapada, menu de opciones y retomar tras captcha.

Capturas reales en tests/data (prueba de Enzo del 2026-11-21, recortadas en negro desde y=760):
  ataque_tapada_2105          21:05:5x, metin de Oscuridad a la vista con 5 bichos sobre el personaje (6 veces "Panel perdido")
  ataque_hover_2105           el momento anterior: el metin recien clickeado, sin bichos encima
  ataque_rodeado_2105         el personaje rodeado de 5 monstruos (VOLVER trabado)
  ataque_menu_opciones        menu de opciones del juego abierto (lo abria el Escape del bot)
"""
import copy
import logging
import math
from pathlib import Path

import cv2
import pytest

import bot.input.base as base
from bot.bot import State
from bot.config import ConfigError, load_config, validate_config
from bot.geometry import Rect
from bot.perception import perceive
from bot.vision.click_point import click_candidates, monster_column, point_is_free, stone_rect_near
from bot.vision.coords import Coords
from bot.vision.game_menu import GameMenu, find_game_menu
from bot.vision.monsters import MonsterLabel, find_own_label, monsters_on_me
from test_bot_behavior import messages
from test_bot_states import CAPTCHA, INSIDE_XY, Harness, metin, perc, target_panel
from test_current_metin import A_POINT, clicks_xy, scripted

DATA = Path(__file__).resolve().parent / "data"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


@pytest.fixture(scope="module")
def cfg():
    return load_config()


def img_of(name):
    img = cv2.imread(str(DATA / name))
    assert img is not None, name
    return img


# ---------------------------------------------------------------- bichos encima (nombres rojos pegados al amarillo)
def test_etiqueta_propia_y_bichos_encima_en_capturas_reales(cfg):
    for name, expected in (("ataque_tapada_2105.png", lambda n: n >= 2), ("ataque_rodeado_2105.png", lambda n: n >= 3),
                           ("ataque_hover_2105.png", lambda n: n == 0)):
        p = perceive(img_of(name), cfg, parts={"monsters"})
        assert p.own_label is not None, name
        cx, cy = p.own_label.center
        assert abs(cx - p.player_xy[0]) < 40 and 100 < p.player_xy[1] - cy < 200, (name, p.own_label)   # arriba del personaje
        assert expected(p.monsters_on_me), (name, p.monsters_on_me)
        assert p.monsters_on_me <= p.monsters_near + 4   # los de encima son un subconjunto de la zona del personaje


def test_bichos_encima_solo_cuentan_los_pegados_a_la_etiqueta_propia(cfg):
    own = Rect(886, 415, 65, 7)
    pegado = MonsterLabel(Rect(900, 405, 100, 7), (950, 408))                  # solapado
    a_20 = MonsterLabel(Rect(886, 442, 100, 7), (936, 445))                    # 20 px debajo
    lejos = MonsterLabel(Rect(886, 500, 100, 7), (936, 503))
    assert monsters_on_me([pegado, a_20, lejos], own, cfg) == [pegado, a_20]
    assert monsters_on_me([pegado], None, cfg) == []                           # sin etiqueta propia no se sabe


def test_etiqueta_propia_no_confunde_con_nombres_rojos(cfg):
    img = img_of("ataque_hover_2105.png")
    img[380:450, 880:960] = (30, 30, 30)    # se borra la etiqueta propia
    assert find_own_label(img, cfg, (929, 548)) is None


# ---------------------------------------------------------------- menu de opciones
def test_menu_de_opciones_detectado_en_captura_real_y_no_en_las_demas(cfg):
    m = find_game_menu(img_of("ataque_menu_opciones.png"), cfg)
    assert m is not None and m.score > 0.95
    assert abs(m.cancel_point[0] - 928) <= 3 and abs(m.cancel_point[1] - 693) <= 3   # centro del boton Cancelar
    for name in ("ataque_tapada_2105.png", "ataque_hover_2105.png", "ataque_rodeado_2105.png"):
        assert find_game_menu(img_of(name), cfg) is None, name
    off = copy.deepcopy(cfg)
    off["vision"]["game_menu"]["enabled"] = False
    assert find_game_menu(img_of("ataque_menu_opciones.png"), off) is None
    p = perceive(img_of("ataque_menu_opciones.png"), cfg)
    assert p.menu is not None and p.menu.cancel_point == m.cancel_point


MENU = GameMenu(Rect(842, 684, 173, 18), (928, 693), 1.0)


def test_menu_abierto_se_cierra_con_clic_en_cancelar_y_no_se_hace_nada_mas():
    def script(h, n):
        p = perc(metins=[metin()])           # hay un metin a la vista: con el menu abierto NO se lo clickea
        if h.clicks() == 0:
            p.menu = MENU
        return p
    h = Harness(script)
    h.run_until(lambda: h.clicks() >= 1)
    assert clicks_xy(h) == [(928, 693)] and h.keys("esc") == 0
    h.bot.step()
    h.bot.step()
    assert h.bot.state is State.ATACAR   # ya cerrado: el bot sigue con su trabajo


def test_menu_que_no_se_cierra_usa_la_tecla_de_cierre_tras_los_clics(caplog):
    caplog.set_level(logging.INFO)

    def script(h, n):
        p = perc(metins=[metin()])
        p.menu = MENU
        return p
    h = Harness(script)
    h.run_until(lambda: h.keys("esc") >= 1)
    assert h.clicks() == int(h.cfg["vision"]["game_menu"]["close_clicks"])
    assert set(clicks_xy(h)) == {(928, 693)} and h.bot.state is State.BUSCAR
    assert any("sigue abierto" in m for m in messages(caplog))


# ---------------------------------------------------------------- nunca Escape
def test_deselecciones_agotadas_y_descarte_nunca_aprietan_escape():
    script, _ = scripted(lambda i: target_panel(0.64) if i <= 2 else None, stone=lambda i: [metin(A_POINT)])
    h = Harness(None, attack={"max_deselect_reclicks": 2})
    h.script = script
    h.run_until(lambda: len(h.bot.discarded) >= 1 or State.EXPLORAR in h.states(), limit=900)
    assert h.keys("esc") == 0


def test_config_conserva_deselect_pero_el_bot_no_lo_usa(cfg):
    import bot.bot as botmod
    src = Path(botmod.__file__).read_text(encoding="utf-8")
    assert 'press("deselect")' not in src and "controls[\"deselect\"]" not in src
    assert cfg["controls"]["deselect"] == "esc"      # por compatibilidad


# ---------------------------------------------------------------- bichos encima: ESPACIO antes del reclic
def _on_me_script(on_me):
    def make(i):
        return target_panel(0.64) if i <= 2 else None
    script, w = scripted(make, stone=lambda i: [metin(A_POINT)])

    def wrapped(h, n):
        p = script(h, n)
        p.monsters_on_me = on_me
        return p
    return wrapped


def test_panel_perdido_con_bichos_encima_hace_espacio_antes_del_reclic(caplog):
    caplog.set_level(logging.INFO)
    h = Harness(_on_me_script(3))
    h.run_until(lambda: h.clicks() >= 2, limit=300)
    assert h.keys("space") >= 1 and h.keys("esc") == 0
    assert any("Bichos encima (3" in m for m in messages(caplog))


def test_panel_perdido_sin_bichos_encima_reclickea_sin_espacio():
    h = Harness(_on_me_script(0))
    h.run_until(lambda: h.clicks() >= 2, limit=300)
    assert h.keys("space") == 0 and h.keys("esc") == 0


def test_trabado_yendo_al_metin_con_bichos_encima_usa_espacio(caplog):
    caplog.set_level(logging.INFO)

    def script(h, n):
        if h.bot.state is not State.ATACAR:
            return perc(metins=[metin(A_POINT)])
        p = perc(metins=[metin(A_POINT)], panel=None)
        p.monsters_on_me, p.monsters_near = 2, 0     # sin zona de cerca: solo los nombres pegados al propio
        return p
    h = Harness(script, stuck={"fast_s": 1.0})
    h.run_until(lambda: any("ESPACIO y reclic en el mismo metin" in m for m in messages(caplog)), limit=400)
    assert h.keys("space") >= 1 and h.keys("esc") == 0


# ---------------------------------------------------------------- piedra tapada: punto sobre la parte visible
def _tapada(cfg):
    img = img_of("ataque_tapada_2105.png")
    p = perceive(img, cfg)
    d = p.metins[0]
    aim = (d.click_point[0], d.click_point[1] - 24)
    return img, p, aim, stone_rect_near(img, aim, cfg)


def test_cuerpo_de_la_piedra_medido_en_la_captura_de_las_21_05(cfg):
    img, p, aim, r = _tapada(cfg)
    assert r is not None and 25 <= r.w <= 60 and 25 <= r.h <= 60
    assert r.x <= aim[0] < r.right and r.y <= aim[1] < r.bottom
    assert point_is_free(img, aim, p.monsters, p.own_label, cfg)   # esta piedra no estaba tapada: el punto de siempre sirve


def test_candidatos_estan_dentro_de_la_piedra_y_fuera_de_las_siluetas(cfg):
    img, p, aim, r = _tapada(cfg)
    cands = click_candidates(img, r, p.monsters, p.own_label, cfg)
    assert len(cands) >= 5
    for c in cands:
        x, y = c.point
        assert r.x <= x < r.right and r.y <= y < r.bottom and c.stone_frac >= cfg["vision"]["click_point"]["min_stone_frac"]
        assert not any(monster_column(m.rect, img.shape, cfg).x <= x < monster_column(m.rect, img.shape, cfg).right and
                       monster_column(m.rect, img.shape, cfg).y <= y < monster_column(m.rect, img.shape, cfg).bottom
                       for m in p.monsters)


def test_con_un_tronco_sobre_la_mitad_izquierda_el_punto_cae_en_la_derecha(cfg):
    img, p, aim, r = _tapada(cfg)
    img[r.y - 2:r.bottom + 2, r.x - 2:r.x + r.w // 2] = (150, 150, 150)   # tronco gris: baja saturacion
    cands = click_candidates(img, r, p.monsters, p.own_label, cfg)
    assert cands and cands[0].point[0] >= r.x + r.w // 2 and cands[0].stone_frac > 0.9
    assert all(c.point[0] > r.x + r.w // 4 for c in cands)       # ninguno sobre la parte tapada


def test_con_un_bicho_encima_de_la_piedra_el_punto_evita_su_columna(cfg):
    img, p, aim, r = _tapada(cfg)
    # un monstruo cuya etiqueta queda justo arriba-izquierda de la piedra: su silueta (columna) tapa la mitad izquierda
    lab = Rect(r.x - 60, r.y - 14, 70, 7)
    mon = MonsterLabel(lab, lab.center)
    col = monster_column(lab, img.shape, cfg)
    assert col.bottom > r.y + r.h // 2                            # la columna baja hasta la piedra
    cands = click_candidates(img, r, [mon], p.own_label, cfg)
    assert cands
    for c in cands:
        assert not (col.x <= c.point[0] < col.right and col.y <= c.point[1] < col.bottom)
    assert cands[0].point[0] > col.right - 1


# ---------------------------------------------------------------- segundo punto tras dos perdidas con vida 100%
def test_dos_panel_perdidos_con_vida_100_el_tercer_clic_va_a_otro_punto_de_la_piedra(cfg, caplog):
    caplog.set_level(logging.INFO)
    img = img_of("ataque_tapada_2105.png")
    h = Harness(None)

    def capture(region=None):
        h.win.last_capture_rect = h.win.client_rect_on_screen()
        return img
    h.win.capture = capture
    w = {"i": 0}

    def script(h, n):
        p = perceive(img, h.cfg, parts={"metins", "far", "monsters"})
        p.coords = Coords(INSIDE_XY[0], INSIDE_XY[1], 1.0)
        if h.bot.state is State.ATACAR:
            w["i"] += 1
            if w["i"] <= 2:
                p.panel = target_panel(1.0)    # el clic selecciono el metin ...
            p.monsters_on_me = 0               # (sin ESPACIO: aca se prueba solo el cambio de punto)
        return p
    h.script = script
    h.run_until(lambda: h.clicks() >= 4, limit=600)
    xy = clicks_xy(h)
    r = stone_rect_near(img, (1001, 307), cfg)
    assert xy[0] == xy[1], xy                                  # el primer reclic repite el punto (una sola perdida)
    assert xy[2] != xy[1], xy                                  # tras 2 perdidas seguidas: otro punto
    assert all(r.x <= x < r.right and r.y <= y < r.bottom for x, y in xy), (xy, r)   # todos sobre la piedra
    assert any("intento 1" in m for m in messages(caplog))
    assert h.keys("esc") == 0


def test_clear_point_con_intento_rota_entre_los_candidatos(cfg):
    img, p, aim, r = _tapada(cfg)
    h = Harness(None)
    h.bot._img = img
    pts = [h.bot._clear_point(aim, p, a) for a in range(4)]
    assert pts[0] == aim
    assert len({pts[1], pts[2], pts[3]}) == 3 and aim not in pts[1:]
    assert all(r.x <= x < r.right and r.y <= y < r.bottom for x, y in pts)


# ---------------------------------------------------------------- retomar tras captcha
FIRST, NEAR_NOW, OTHER = (1387, 370), (938, 488), (400, 300)   # el mismo metin, antes y despues de caminar hacia el


def _captcha_script(after, state):
    """BUSCAR ve el metin lejos -> ATACAR (confirmado) -> captcha `state['cycles']` ciclos -> `after(h, state)` decide que se ve."""
    def script(h, n):
        st = h.bot.state
        if st is State.CAPTCHA or (st is State.ATACAR and state["captcha"]):
            if state["cycles"] > 0:
                state["cycles"] -= 1
                p = perc(metins=[], panel=None, captcha=CAPTCHA)
                return p
            state["captcha"] = False
            return perc(metins=after(h, state))
        if st is State.ATACAR:
            state["atk"] += 1
            if state["atk"] >= 3 and state["captcha"] is None:
                state["captcha"] = True
            return perc(metins=[metin(FIRST)], panel=target_panel(0.8))
        if state["resumed"] is None:
            return perc(metins=[metin(FIRST)])
        return perc(metins=after(h, state))
    return script


def test_tras_el_captcha_vuelve_al_metin_que_atacaba_aunque_se_corrio_en_pantalla(caplog):
    caplog.set_level(logging.INFO)
    state = {"cycles": 4, "captcha": None, "atk": 0, "resumed": False}
    h = Harness(_captcha_script(lambda h, s: [metin(OTHER), metin(NEAR_NOW)], state))
    h.train_camera()
    h.run_until(lambda: State.CAPTCHA in h.states(), limit=300)
    assert h.bot._resume is not None and h.bot._cur is not None
    n0 = h.clicks()
    h.run_until(lambda: h.clicks() > n0, limit=300)
    assert clicks_xy(h)[-1][0] == pytest.approx(NEAR_NOW[0], abs=2)     # el que estaba mas cerca = el mismo metin, no el lejano
    assert any("Retomo el metin en curso" in m for m in messages(caplog))
    assert not any("Abandono el metin en curso" in m for m in messages(caplog))
    assert h.keys("esc") == 0


def test_tras_el_captcha_no_suelta_el_metin_a_los_8_s_aunque_haya_otro_objetivo(caplog):
    caplog.set_level(logging.INFO)
    state = {"cycles": 3, "captcha": None, "atk": 0, "resumed": False}
    h = Harness(_captcha_script(lambda h, s: [metin(OTHER)], state))
    h.run_until(lambda: State.CAPTCHA in h.states(), limit=300)
    h.run_until(lambda: h.bot.state is State.BUSCAR and h.bot._resume is not None and h.bot._resume.until > 0, limit=300)
    t_exit = h.clock.t
    keep_others = float(h.cfg["attack"]["keep_with_others_s"])
    keep_after = float(h.cfg["attack"]["after_captcha_keep_s"])
    h.run_until(lambda: h.clock.t - t_exit > keep_others + 4, limit=2000)
    assert h.bot._cur is not None
    assert not any("Abandono el metin en curso" in m for m in messages(caplog))
    # pasada la ventana (sin verlo) se lo suelta como siempre: por tiempo normal (target_keep_s)
    h.run_until(lambda: h.bot._cur is None or h.clock.t - t_exit > keep_after + float(h.cfg["attack"]["target_keep_s"]) + 5,
                limit=4000)
    assert h.bot._cur is None


def test_captcha_sin_ataque_no_deja_metin_a_retomar():
    state = {"cycles": 2, "captcha": None, "atk": 0, "resumed": None}

    def script(h, n):
        if h.bot.state is State.CAPTCHA:
            if state["cycles"] > 0:
                state["cycles"] -= 1
                return perc(captcha=CAPTCHA)
            return perc()
        return perc(captcha=CAPTCHA) if n >= 1 else perc()
    h = Harness(script)
    h.run_until(lambda: State.CAPTCHA in h.states(), limit=100)
    assert h.bot._resume is None


# ---------------------------------------------------------------- config
def test_config_nueva_validada(cfg):
    validate_config(cfg)
    for path, bad in ((("vision", "game_menu", "search"), [0.7, 0.5, 0.3, 0.8]),
                      (("vision", "own_label", "search"), [10, 0, -10, 5]),
                      (("vision", "click_point", "grid"), []),
                      (("vision", "monsters", "on_me_px"), -1),
                      (("attack", "lost_swap_after"), 0),
                      (("attack", "after_captcha_keep_s"), -1)):
        c = copy.deepcopy(cfg)
        d = c
        for k in path[:-1]:
            d = d[k]
        d[path[-1]] = bad
        with pytest.raises(ConfigError):
            validate_config(c)
    assert cfg["attack"]["after_captcha_keep_s"] == 20.0 and cfg["vision"]["monsters"]["on_me_px"] == 30
    assert math.isclose(cfg["vision"]["game_menu"]["min_score"], 0.8)
