"""Corrida de las 21:24 (2026-11-21): bichos pegados a la montura y reclics a un punto viejo.

Capturas reales en tests/data:
  ataque_bicho_pegado_2131   21:31:25, dos soldados tocando la montura (sus nombres ~46 px debajo del nombre propio)
  ataque_punto_viejo_2129    21:29:15, la piedra lejos a la derecha (1452, 321); a las 21:30:10 se reclickeaba (428, 181)
  ataque_frase_captcha_2132  21:32:39, cartel "la siguiente frase es: - - - p - -" arriba al centro (otro tipo de captcha)
"""
import math
from pathlib import Path

import cv2
import pytest

import bot.input.base as base
from bot.bot import State
from bot.config import load_config
from bot.perception import perceive
from bot.vision.coords import Coords
from bot.vision.monsters import monsters_on_me
from test_bot_behavior import messages, perc_far
from test_bot_states import INSIDE_XY, Harness, metin, perc, target_panel
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


def test_bicho_pegado_a_la_montura_cuenta_como_encima_aunque_su_etiqueta_quede_lejos_de_la_propia(cfg):
    p = perceive(img_of("ataque_bicho_pegado_2131.png"), cfg, parts={"monsters"})
    assert p.own_label is not None and p.monsters_on_me >= 1
    assert monsters_on_me(p.monsters, p.own_label, cfg) == []      # solo por la etiqueta propia (30 px) no se veia
    libre = perceive(img_of("ataque_frase_captcha_2132.png"), cfg, parts={"monsters"})
    assert libre.monsters_on_me == 0


def test_la_piedra_se_movio_en_pantalla_el_reclic_va_a_su_posicion_actual_nunca_a_la_vieja():
    """La piedra estaba en (277, 183); 9 s despues, con la camara girada, en (1452, 321): se reclickeaba (428, 181)."""
    img = img_of("ataque_punto_viejo_2129.png")
    h = Harness(None)

    def capture(region=None):
        h.win.last_capture_rect = h.win.client_rect_on_screen()
        return img
    h.win.capture = capture
    w = {"i": 0}

    def script(h, n):
        if h.bot.state is not State.ATACAR:
            return perc(metins=[metin((277, 210))])
        w["i"] += 1
        p = perceive(img, h.cfg, parts={"metins", "far", "monsters"})   # captura de ahora: piedra lejos, a la derecha
        p.coords = Coords(INSIDE_XY[0], INSIDE_XY[1], 1.0)
        p.panel = target_panel(1.0) if w["i"] <= 2 else None
        p.monsters_on_me = 0
        return p
    h.script = script
    h.run_until(lambda: h.clicks() >= 2, limit=500)
    xy = clicks_xy(h)
    assert abs(xy[0][0] - 277) < 10
    for pt in xy[1:]:
        assert math.dist(pt, (1452, 321)) < 40, xy       # reclic sobre la piedra de ahora
        assert math.dist(pt, (428, 181)) > 500           # y no sobre un punto viejo


def test_reclic_sin_ver_la_piedra_no_clickea_un_punto_viejo(caplog):
    import logging
    caplog.set_level(logging.INFO)
    script, _ = scripted(lambda i: target_panel(1.0) if i <= 2 else None, stone=lambda i: [])   # la piedra ya no se ve
    h = Harness(None)
    h.script = script
    h.run_until(lambda: State.BUSCAR in h.states()[1:], limit=300)
    assert h.clicks() == 1       # solo el clic inicial: nada de reclics a ciegas
    assert any("no clickeo un punto viejo" in m for m in messages(caplog))


def test_si_el_personaje_camina_hacia_el_metin_no_se_reclickea_aunque_el_panel_se_pierda(caplog):
    import logging
    caplog.set_level(logging.INFO)
    w = {"i": 0}

    def script(h, n):
        if h.bot.state is not State.ATACAR:
            return perc(metins=[metin(A_POINT)])
        w["i"] += 1
        x = INSIDE_XY[0] + 3 * min(w["i"], 40)      # camina 40 ciclos y se frena
        return perc_far(coords=(x, INSIDE_XY[1]), panel=target_panel(1.0) if w["i"] <= 2 else None,
                        metins=[metin(A_POINT)])
    h = Harness(script)
    h.run_until(lambda: w["i"] >= 36, limit=800)
    assert h.clicks() == 1                          # camina: ningun reclic (el panel se perdio en el ciclo 3)
    assert any("camina hacia el metin" in m for m in messages(caplog))
    h.run_until(lambda: h.clicks() >= 2, limit=800)  # se freno: ahi si
    assert w["i"] > 40
