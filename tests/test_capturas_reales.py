"""Pruebas con capturas REALES del juego (samples/): lo que la percepcion tiene que detectar en cada una y, en la del metin
que el bot dejo pasar (corrida de las 23:48), que el bot elija el metin que tiene enfrente. Ningun cambio de deteccion o de
eleccion esta terminado si estos tests no pasan. Sin input real: el clic va a un backend falso."""
import copy

import pytest

import bot.input.base as base
from bot.bot import State
from bot.config import DEFAULTS
from bot.perception import perceive
from test_bot_states import Harness

CFG = copy.deepcopy(DEFAULTS)
CFG["target_metin_names"] = ["Metin de Oscuridad", "Metin Negro"]


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


def metins(p):
    """(etiquetas de metin objetivo, piedras sin etiqueta) detectadas."""
    return [d for d in p.metins if d.is_target], list(p.far_metins)


def near(pt, xy, r):
    return abs(pt[0] - xy[0]) <= r and abs(pt[1] - xy[1]) <= r


# ------------------------------------------------------------------ que detecta en cada captura
def test_metin_enfrente_omitido_detecta_el_de_enfrente_con_su_nombre_y_el_lejano(load_sample):
    """3 Metin de Oscuridad: uno enfrente del personaje (~1060, 440) con el nombre naranja visible sin hover y la piedra
    roja brillando, y dos lejos (uno a la izquierda, ~390, 250, y otro arriba a la derecha tapado por el texto de un NPC)."""
    p = perceive(load_sample("real_metin_enfrente_omitido.png"), CFG)
    labels, stones = metins(p)
    assert len(labels) == 1 and near(labels[0].click_point, (1075, 430), 40), labels
    assert any(near(f.center, (390, 255), 30) for f in stones), stones
    assert len(labels) + len(stones) >= 2


def test_metin_cerca_sin_seleccionar(load_sample):
    p = perceive(load_sample("metin_cerca_sin_seleccionar.png"), CFG)
    labels, stones = metins(p)
    assert len(labels) == 1 and near(labels[0].click_point, (840, 410), 40), labels
    assert not stones


def test_metin_oscuridad_a_distancia_tres_metin(load_sample):
    p = perceive(load_sample("metin_oscuridad_a_distancia.png"), CFG)
    labels, stones = metins(p)
    assert len(labels) == 1 and near(labels[0].click_point, (157, 400), 40), labels
    assert len(stones) == 2
    assert any(near(f.center, (432, 262), 30) for f in stones) and any(near(f.center, (1470, 270), 30) for f in stones)


def test_tres_metin_hover(load_sample):
    p = perceive(load_sample("real_tres_metin_hover.png"), CFG)
    labels, stones = metins(p)
    assert len(labels) == 1 and near(labels[0].click_point, (1236, 260), 40), labels
    assert len(stones) == 2
    assert any(near(f.center, (546, 275), 30) for f in stones) and any(near(f.center, (1405, 370), 30) for f in stones)


def test_items_en_piso_cinco_objetos_y_ningun_metin(load_sample):
    p = perceive(load_sample("items_en_piso.png"), CFG)
    labels, stones = metins(p)
    assert not labels and not stones
    assert len([i for i in p.items if i.kind == "item"]) == 5


# ------------------------------------------------------------------ que elige el bot
def _decide_on(img, cfg_over=None):
    """Un ciclo de decision del bot sobre la captura real: devuelve los puntos clickeados."""
    real = perceive(img, CFG)
    h = Harness(lambda h, n: real, **(cfg_over or {}))

    def capture(region=None):   # como la ventana real: la captura fija el rect al que van los clics
        h.win.last_capture_rect = h.win.client_rect_on_screen()
        return img
    h.win.capture = capture
    clicks = []
    orig = h.gi.click_at
    h.gi.click_at = lambda x, y, *a, **k: clicks.append((x, y)) or orig(x, y, *a, **k)
    h.bot.step()
    return h, clicks


def test_elige_el_metin_de_enfrente(load_sample):
    """23:48: el bot tenia un metin enfrente y no lo ataco. Con el nombre a la vista, clic directo (sin hover) en su piedra."""
    h, clicks = _decide_on(load_sample("real_metin_enfrente_omitido.png"))
    assert h.bot.state is State.ATACAR, h.seen
    assert clicks and near(clicks[0], (1070, 445), 60), clicks


@pytest.mark.parametrize("name,where", [
    ("metin_cerca_sin_seleccionar.png", (840, 420)),
    ("metin_oscuridad_a_distancia.png", (157, 400)),
    # el mas cercano es la piedra SIN nombre de la derecha: clic directo (sin hover), el panel decide
    ("real_tres_metin_hover.png", (1407, 362))])
def test_con_un_metin_a_la_vista_hace_clic_en_el_mas_cercano(load_sample, name, where):
    h, clicks = _decide_on(load_sample(name))
    assert h.bot.state is State.ATACAR and clicks, (h.seen, clicks)
    assert near(clicks[0], where, 60), clicks
