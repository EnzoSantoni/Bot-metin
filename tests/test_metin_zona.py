"""Fase 6i: un metin nunca se va a buscar fuera de la zona (posicion conocida) ni a ciegas lejos del personaje."""
import math

import pytest

import bot.input.base as base
from bot.bot import State
from bot.candidates import CandidateMap
from test_bot_states import INSIDE_XY, PLAYER, TOL_XY, Harness, metin, perc


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


def harness(p, camera=False):
    h = Harness(lambda h, n: p, strict_zone=True)
    h.bot.tracker.update(p.coords, h.clock())
    if camera:
        h.train_camera()
    return h


def test_sin_posicion_y_lejos_no_se_va():
    p = perc(coords=INSIDE_XY, metins=[metin((300, 400))])   # piso 3.3 > blind_near_floor
    h = harness(p)
    assert not h.bot._goals(h.bot._label_view(p, 0.0)[0], [], p)
    h.bot.step()
    assert State.ATACAR not in h.states() and h.clicks() == 0


def test_sin_posicion_cerca_y_jugador_adentro_si_se_va():
    p = perc(coords=INSIDE_XY, metins=[metin((1000, 500))])   # piso 0.8
    h = harness(p)
    h.bot.step()
    assert State.ATACAR in h.states() and h.clicks() == 1


def test_sin_posicion_cerca_pero_jugador_en_la_tolerancia_no_se_va():
    p = perc(coords=TOL_XY, metins=[metin((1000, 500))])
    h = harness(p)
    h.bot.step()
    assert h.clicks() == 0


def test_sin_posicion_cerca_pero_jugador_pegado_al_borde_adentro_no_se_va():
    p = perc(coords=(442, 170), metins=[metin((1000, 500))])   # adentro pero a 4.6 u del borde (< blind_inside_margin)
    h = harness(p)
    h.bot.step()
    assert h.clicks() == 0


def test_posicion_conocida_afuera_no_se_va_y_adentro_si():
    base_xy = (550, 130)   # adentro, a ~6 u del borde de arriba (la camara entrenada: 0.2 u/px)
    afuera = perc(coords=base_xy, metins=[metin((960, 548 - 145))])   # ~22 u afuera: dentro de la tolerancia, mas que metin_outside_u
    h = harness(afuera, camera=True)
    cands, why = h.bot._label_view(afuera, 0.0)
    assert not cands and "fuera de zona" in why[0][1]
    h.bot.step()
    assert h.clicks() == 0
    adentro = perc(coords=base_xy, metins=[metin((1060, 548))])    # +20 u a la derecha: adentro
    h2 = harness(adentro, camera=True)
    h2.bot.step()
    assert State.ATACAR in h2.states() and h2.clicks() == 1


def test_candidato_666_81_fuera_de_la_zona_no_entra_a_la_lista_ni_se_elige():
    h = harness(perc(coords=INSIDE_XY), camera=True)
    assert not h.bot._metin_accepts((666, 81))
    assert h.bot._metin_accepts((666, 100)) and h.bot._metin_accepts((600, 150))
    cm = CandidateMap(180.0, 12.0, 4.0, 0.5)
    cm.add((666, 81), "piedra", False, 0.0)
    assert cm.best((600, 150), (580, 150), 1.0, h.bot._metin_accepts) is None
    # por la cosecha: jugador en (666, 130) mirando al norte (rumbo -90 grados), piedra adelante: cae afuera y no se guarda
    p = perc(coords=(666, 130), metins=[metin((960, 300))])
    h2 = harness(p)
    h2.bot.apr["blind_near_floor"] = 100.0   # aca no se prueba la regla ciega sino la de la lista
    h2.bot._harvest(p, 0.0, -math.pi / 2, (666.0, 130.0))
    assert len(h2.bot.cands) == 0
    h2.bot._harvest(p, 0.0, math.pi / 2, (666.0, 130.0))   # mirando al sur la misma vista cae adentro
    assert len(h2.bot.cands) == 1
