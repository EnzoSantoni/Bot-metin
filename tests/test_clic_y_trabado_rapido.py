"""Punto de clic al centro de la piedra, reclic rapido sin panel y trabado detectado en stuck.fast_s (sin input real)."""
import copy
import logging
import re
from pathlib import Path

import cv2
import pytest

import bot.input.base as base
from bot.bot import State, _Scan
from bot.config import DEFAULTS, ConfigError, load_config, validate_config
from bot.perception import perceive
from test_bot_behavior import messages, perc_far
from test_bot_states import PLAYER, Harness, metin, perc, target_panel
from test_eleccion_y_rodeado import _surrounded_script

DATA = Path(__file__).parent / "data"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


# ---------------------------------------------------------------- punto de clic sobre capturas reales
# (piedra antes del hover, etiqueta que aparece en el hover): corrida real 20/11 12:10, ver debug/run_20261120_121005.
REAL = {"a": (1501, 364), "b": (217, 306), "c": (1035, 295)}   # base (`FarMetin.center`) de la piedra lejana de cada par


def _real(name):
    img = cv2.imread(str(DATA / name))
    assert img is not None
    return perceive(img, copy.deepcopy(DEFAULTS))


@pytest.mark.parametrize("key", sorted(REAL))
def test_el_clic_cae_en_el_cuerpo_de_la_piedra_y_no_en_su_base(key):
    stone = _real(f"real_clic_{key}_piedra_antes_del_hover.png")
    lab = _real(f"real_clic_{key}_etiqueta_en_el_hover.png")
    base_xy = REAL[key]
    far = min(stone.far_metins, key=lambda f: abs(f.center[0] - base_xy[0]) + abs(f.center[1] - base_xy[1]))
    assert far.center == base_xy
    det = min(lab.metins, key=lambda d: abs(d.click_point[0] - base_xy[0]))
    r, old = far.rect, det.click_point
    assert old[1] > r.bottom   # antes: por debajo de la piedra, en el piso
    h = Harness(lambda h, n: perc())
    x, y = h.bot._aim(det, lab)
    assert r.x <= x <= r.right and r.y <= y <= r.bottom   # ahora: dentro del cuerpo
    assert y <= far.center[1] and old[1] - y >= 20          # y mas arriba que la base y que el clic anterior


def test_con_piedra_asociada_el_clic_va_al_centro_de_su_rect():
    from test_bot_behavior import far_at
    h = Harness(lambda h, n: perc())
    p = perc_far(far=[far_at(1002, 470)])
    assert h.bot._aim(metin(click=(1000, 500)), p) == p.far_metins[0].rect.center


# ---------------------------------------------------------------- verificacion rapida del clic
def _until_second_click(h, limit=200):
    t0 = None
    for _ in range(limit):
        h.bot.step()
        if t0 is None and h.clicks() >= 1:
            t0 = h.clock.t
        if h.clicks() >= 2:
            return t0
    raise AssertionError("no hubo segundo clic")


def test_clic_sin_panel_reclickea_a_los_15_s_mejor_apuntado(caplog):
    caplog.set_level(logging.INFO)

    def script(h, n):
        xy = (500 + min(n, 8), 150)   # camina un rato y queda quieto (el reclic espera a que frene)
        if h.bot.state is State.ATACAR:
            return perc_far(coords=xy, metins=[metin()])   # sin panel
        return perc(coords=xy, metins=[metin()])
    h = Harness(script, attack={"confirm_timeout_s": 50.0}, stuck={"fast_s": 1000.0})
    t0 = _until_second_click(h)
    assert 1.5 <= h.clock.t - t0 < 2.6   # a los ~1.5 s (`attack.confirm_wait_s`), no antes ni a los 6 s
    assert any("Clic sin panel en 1.5 s: reclic mejor apuntado en (1000, 466)" in m for m in messages(caplog))
    # primer clic: etiqueta (1000, 500) corrida 24 px hacia arriba; el reclic cae en el mismo punto y sube 10 px mas
    assert h.bot._atk.aim == (1000, 466)


def test_los_reclics_rapidos_tienen_tope_y_despues_sigue_el_flujo_de_siempre(caplog):
    caplog.set_level(logging.INFO)
    xy = lambda n: (500 + min(n, 8), 150)   # camina un rato y queda quieto
    h = Harness(lambda h, n: perc_far(coords=xy(n), metins=[metin()]) if h.bot.state is State.ATACAR
                else perc(coords=xy(n), metins=[metin()]), attack={"confirm_timeout_s": 50.0}, stuck={"fast_s": 1000.0})
    for _ in range(120):
        h.bot.step()
    assert sum("Clic sin panel" in m for m in messages(caplog)) == int(h.cfg["attack"]["quick_reclicks"])
    assert h.clicks() == 1 + int(h.cfg["attack"]["quick_reclicks"])


def test_con_panel_confirmado_no_hay_reclic_rapido(caplog):
    caplog.set_level(logging.INFO)
    xy = lambda n: (500 + max(0, n - 5), 150)   # quieto al elegir (un clic nunca sale en movimiento) y despues camina
    h = Harness(lambda h, n: perc_far(coords=xy(n), panel=target_panel(1.0), metins=[metin()])
                if h.bot.state is State.ATACAR else perc(coords=xy(n), metins=[metin()]))
    for _ in range(40):
        h.bot.step()
    assert h.clicks() == 1 and not any("Clic sin panel" in m for m in messages(caplog))


def test_el_panel_que_aparece_antes_de_los_15_s_cancela_el_reclic():
    def script(h, n):
        xy = (500 + max(0, n - 5), 150)   # quieto al elegir y despues camina
        if h.bot.state is State.ATACAR:
            panel = target_panel(1.0) if h.clock.t - h.bot._atk.start_t >= 0.2 else None
            return perc_far(coords=xy, panel=panel, metins=[metin()])
        return perc(coords=xy, metins=[metin()])
    h = Harness(script)
    for _ in range(40):
        h.bot.step()
    assert h.clicks() == 1


# ---------------------------------------------------------------- trabado yendo al metin en stuck.fast_s
def _stuck_script(n_near=0, stop=12):
    def script(h, n):
        xy = (500 + min(n, stop), 150)
        m = metin(click=(1300, 500))   # lejos del personaje: todavia no llego
        if h.bot.state is State.ATACAR:
            return perc_far(coords=xy, panel=target_panel(1.0), metins=[m], n_near=n_near)
        return perc(coords=xy, metins=[m])
    return script


def _first_quiet(caplog):
    for m in messages(caplog):
        mt = re.search(r"Trabado yendo al metin \(quieto ([\d.]+) s", m)
        if mt:
            return float(mt.group(1))
    return None


def test_trabado_sin_monstruos_paso_al_costado_y_reclic_a_los_18_s(caplog):
    caplog.set_level(logging.INFO)
    h = Harness(_stuck_script(), attack={"blocked_s": 50.0, "reclick_stall_s": 50.0})
    h.run_until(lambda: h.clicks() >= 2, limit=300)
    quiet = _first_quiet(caplog)
    assert quiet is not None and 1.8 <= quiet < 2.6
    assert h.keys("space") == 0 and h.keys("a") >= 1   # sin monstruos: paso al costado, no ESPACIO
    assert any("paso al costado (A)" in m for m in messages(caplog))
    assert any(m.startswith("Reclic en") and "trabado, intento 1" in m for m in messages(caplog))


def test_trabado_con_monstruos_espacio_y_reclic_a_los_18_s(caplog):
    caplog.set_level(logging.INFO)
    h = Harness(_stuck_script(n_near=2), attack={"blocked_s": 50.0, "reclick_stall_s": 50.0})
    h.run_until(lambda: h.clicks() >= 2, limit=300)
    quiet = _first_quiet(caplog)
    assert quiet is not None and 1.8 <= quiet < 2.6
    assert h.keys("space") >= 1 and h.keys("a") == 0 and h.keys("d") == 0
    assert any("ESPACIO y reclic en el mismo metin" in m for m in messages(caplog))


def test_con_la_vida_bajando_no_hay_trabado(caplog):
    caplog.set_level(logging.INFO)

    def script(h, n):
        m = metin()
        if h.bot.state is State.ATACAR:   # quieto pero pegando: la vida baja todo el tiempo
            return perc_far(coords=(500, 150), panel=target_panel(max(0.05, 1.0 - 0.01 * n)), metins=[m])
        return perc(coords=(500, 150), metins=[m])
    h = Harness(script, attack={"scan_enabled": False})
    for _ in range(80):
        h.bot.step()
    assert not any("Trabado yendo" in m for m in messages(caplog)) and h.clicks() == 1


def test_con_la_camara_girada_no_hay_trabado(caplog):
    caplog.set_level(logging.INFO)

    def script(h, n):
        hp = max(0.05, 1.0 - 0.02 * n) if n < 6 else 0.9   # baja un rato y despues se queda quieta
        if h.bot.state is State.ATACAR:
            return perc_far(coords=(500, 150), panel=target_panel(hp), metins=[metin()])
        return perc(coords=(500, 150), metins=[metin()])
    h = Harness(script, explore={"settle_s": 1000.0})   # la compuerta de giros nunca abre: no gira de verdad
    h.run_until(lambda: h.bot._atk is not None and h.bot._atk.first_drop_t is not None)
    h.bot._scan = _Scan("right", 0.0, steps=1)   # la camara quedo girada
    a = h.bot._atk
    while h.clock.t - a.hp_down_t < 5.0 and h.bot._scan is not None:
        h.bot.step()
    assert not any("Trabado yendo" in m for m in messages(caplog)) and h.clicks() == 1


def test_llegar_al_metin_sin_golpe_todavia_no_es_trabado(caplog):
    caplog.set_level(logging.INFO)
    near = PLAYER[0] + 40, PLAYER[1] - 40   # la piedra pegada al personaje
    h = Harness(lambda h, n: perc_far(coords=(500, 150), panel=target_panel(1.0), metins=[metin(click=near)])
                if h.bot.state is State.ATACAR else perc(coords=(500, 150), metins=[metin(click=near)]),
                attack={"reclick_stall_s": 50.0, "blocked_s": 50.0, "scan_enabled": False})
    for _ in range(40):
        h.bot.step()
    assert not any("Trabado yendo" in m for m in messages(caplog))


# ---------------------------------------------------------------- trabado caminando (VOLVER, CENTRO, EXPLORAR)
def _enter_volver(h):
    h.run_until(lambda: h.bot.state is State.VOLVER)
    return h.clock.t


def test_volviendo_quieto_sin_monstruos_paso_al_costado_a_los_18_s(caplog):
    caplog.set_level(logging.INFO)
    h = Harness(_surrounded_script(n_near=0))
    t0 = _enter_volver(h)
    h.run_until(lambda: h.keys("a") >= 1, limit=100)
    assert 1.8 <= h.clock.t - t0 < 2.8
    assert h.bot.state is State.VOLVER and h.keys("space") == 0 and h.bot.stats.sidesteps == 1
    assert any("Trabado caminando (quieto" in m and "paso al costado (A)" in m for m in messages(caplog))


def test_volviendo_quieto_con_monstruos_espacio_a_los_18_s(caplog):
    caplog.set_level(logging.INFO)
    h = Harness(_surrounded_script(n_near=2))
    t0 = _enter_volver(h)
    h.run_until(lambda: h.keys("space") >= 1, limit=100)
    assert 1.8 <= h.clock.t - t0 < 2.8
    assert h.bot.state is State.VOLVER and h.keys("a") == 0 and h.bot.stats.surrounded == 1


def test_trabado_caminando_agota_las_reacciones_y_destraba():
    h = Harness(_surrounded_script(n_near=0), stuck={"window_s": 1000.0})
    h.run_until(lambda: State.DESTRABAR in h.states(), limit=1500)
    assert h.bot.stats.sidesteps == int(h.cfg["stuck"]["surrounded_max"])
    assert h.keys("a") >= 1 and h.keys("d") >= 1   # alterna A / D


def test_hover_y_barrido_de_camara_no_son_caminata():
    """Sin movimiento esperado (hover, barrido de camara, ataque) `_surrounded` no corre."""
    h = Harness(lambda h, n: perc_far(coords=(550, 150), n_near=3))
    h.run_until(lambda: h.bot.state is State.EXPLORAR)
    assert h.bot._ex.phase == "sweep" and not h.bot._walking_now()
    for _ in range(12):   # quieto en el barrido (~2 s): nada de ESPACIO ni pasos al costado
        h.bot.step()
        if h.bot._ex is None or h.bot._ex.phase != "sweep":
            break
    assert h.bot.stats.sidesteps == 0 and h.bot.stats.surrounded == 0
    for st in (State.ACERCARSE, State.ATACAR, State.BUSCAR, State.RECOGER):
        h.bot.state = st
        assert not h.bot._walking_now()


# ---------------------------------------------------------------- config
def test_config_nueva():
    a, s = DEFAULTS["attack"], DEFAULTS["stuck"]
    assert s["fast_s"] == 1.8 and "surrounded_s" not in s and "walk_stuck_s" not in a
    assert (a["confirm_wait_s"], a["quick_reclicks"], a["label_click_up_frac"]) == (1.5, 2, 0.37)
    assert "confirm_check_s" not in a and a["min_click_gap_s"] == 1.5 and a["panel_recover"] is True
    ap = DEFAULTS["approach"]
    assert ap["keys_enabled"] is True and ap["near_floor"] == 4.0 and "click_floor" not in ap and ap["max_clicks"] == 4
    for sec, key, bad in (("stuck", "fast_s", 0), ("attack", "confirm_wait_s", 0), ("attack", "quick_reclicks", -1),
                          ("attack", "min_click_gap_s", -1), ("attack", "panel_recover", "si"),
                          ("approach", "near_floor", 0), ("approach", "keys_enabled", 1), ("approach", "max_clicks", 0),
                          ("attack", "label_click_up_frac", 1.0), ("attack", "quick_up_px", "x"), ("attack", "arrive_px", -1)):
        cfg = copy.deepcopy(DEFAULTS)
        cfg[sec][key] = bad
        with pytest.raises(ConfigError):
            validate_config(cfg)


def test_las_claves_viejas_se_ignoran_con_aviso(tmp_path, caplog):
    import json
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"attack": {"walk_stuck_s": 5.0}, "stuck": {"surrounded_s": 2.0}}), encoding="utf-8")
    cfg = load_config(p)
    assert "walk_stuck_s" not in cfg["attack"] and "surrounded_s" not in cfg["stuck"]
