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


def test_sin_posicion_y_lejos_tambien_se_va():
    """Sin la regla ciega: un metin a la vista sin posicion conocida se cliquea igual (piso 5.0), decide el panel."""
    p = perc(coords=INSIDE_XY, metins=[metin((200, 350))])
    h = harness(p)
    assert h.bot._goals(h.bot._label_view(p, 0.0)[0], [], p)
    h.bot.step()
    assert State.ATACAR in h.states() and h.clicks() == 1


def test_sin_posicion_cerca_y_jugador_adentro_si_se_va():
    p = perc(coords=INSIDE_XY, metins=[metin((1000, 500))])   # piso 0.8
    h = harness(p)
    h.bot.step()
    assert State.ATACAR in h.states() and h.clicks() == 1


def test_sin_posicion_cerca_y_jugador_en_la_tolerancia_si_se_va():
    """Sin estimar la posicion del metin no se lo puede dar por claramente afuera: se cliquea."""
    p = perc(coords=TOL_XY, metins=[metin((1000, 500))])
    h = harness(p)
    h.bot.step()
    assert h.clicks() == 1


def test_sin_posicion_cerca_y_jugador_pegado_al_borde_adentro_si_se_va():
    """22:22:14: metin a piso 1.3 descartado por "jugador sin margen adentro de la zona". A piso <= near_floor se ataca si
    el metin queda adentro de la zona mas outside_tolerance (sin rumbo: el peor caso)."""
    p = perc(coords=(442, 170), metins=[metin((1000, 500))])   # adentro pero a 4.6 u del borde (< blind_inside_margin)
    h = harness(p)
    h.bot.step()
    assert State.ATACAR in h.states() and h.clicks() == 1


def test_sin_posicion_a_piso_3_3_y_jugador_adentro_si_se_va():
    p = perc(coords=INSIDE_XY, metins=[metin((300, 400))])   # piso 3.3: > blind_near_floor pero <= near_floor
    h = harness(p)
    h.bot.step()
    assert State.ATACAR in h.states() and h.clicks() == 1


def test_posicion_conocida_afuera_no_se_va_y_adentro_si():
    base_xy = (550, 130)   # adentro, a ~6 u del borde de arriba (la camara entrenada: 0.2 u/px)
    afuera = perc(coords=base_xy, metins=[metin((960, 548 - 215))])   # ~36 u afuera: mas que outside_tolerance (30)
    h = harness(afuera, camera=True)
    cands, why = h.bot._label_view(afuera, 0.0)
    assert not cands and "afuera de la zona mas la tolerancia" in why[0][1]
    h.bot.step()
    assert h.clicks() == 0
    adentro = perc(coords=base_xy, metins=[metin((1060, 548))])    # +20 u a la derecha: adentro
    h2 = harness(adentro, camera=True)
    h2.bot.step()
    assert State.ATACAR in h2.states() and h2.clicks() == 1


def test_candidato_666_81_fuera_de_la_zona_no_entra_a_la_lista_ni_se_elige():
    h = harness(perc(coords=INSIDE_XY), camera=True)
    assert not h.bot._metin_accepts((666, 55))   # claramente afuera de la zona mas la tolerancia (30)
    assert h.bot._metin_accepts((666, 81)) and h.bot._metin_accepts((666, 100)) and h.bot._metin_accepts((600, 150))
    cm = CandidateMap(180.0, 12.0, 4.0, 0.5)
    cm.add((666, 55), "piedra", False, 0.0)
    assert cm.best((600, 150), (580, 150), 1.0, h.bot._metin_accepts) is None
    # por la cosecha: jugador en (666, 130) mirando al norte (rumbo -90 grados), piedra adelante: cae afuera y no se guarda
    p = perc(coords=(666, 130), metins=[metin((960, 300))])
    h2 = harness(p)
    h2.bot.apr["blind_near_floor"] = 100.0   # aca no se prueba la regla ciega sino la de la lista
    h2.bot._harvest(p, 0.0, -math.pi / 2, (666.0, 130.0))
    assert len(h2.bot.cands) == 0
    h2.bot._harvest(p, 0.0, math.pi / 2, (666.0, 130.0))   # mirando al sur la misma vista cae adentro
    assert len(h2.bot.cands) == 1


def test_posicion_conocida_cerca_y_en_la_tolerancia_si_se_va():
    """Metin a piso 2.1 y ~22 u afuera del borde: mas que metin_outside_u (15) pero adentro de la tolerancia (30)."""
    p = perc(coords=(550, 130), metins=[metin((960, 548 - 145))])
    h = harness(p, camera=True)
    cands, _ = h.bot._label_view(p, 0.0)
    assert cands
    h.bot.step()
    assert State.ATACAR in h.states() and h.clicks() == 1


def test_coordenadas_perdidas_un_momento_metin_cerca_con_la_ultima_posicion_adentro_si_se_va():
    """22:26:45: "metin_negro: sin camara y jugador fuera de la zona" con las coordenadas perdidas (la ultima, adentro)."""
    h = Harness(lambda h, n: perc(coords=None, metins=[metin((1000, 500))]), strict_zone=True)
    h.bot.tracker.update(perc(coords=INSIDE_XY).coords, h.clock())
    h.clock.t += 60   # la posicion quedo vieja: tracker.position es None
    assert h.bot.tracker.position is None
    h.bot.step()
    assert State.ATACAR in h.states() and h.clicks() == 1


def test_coordenadas_perdidas_y_metin_lejos_tambien_es_candidato():
    """Sin estimar la posicion del metin no se lo descarta (solo se descarta lo CLARAMENTE afuera de la zona)."""
    h = Harness(lambda h, n: perc(coords=None, metins=[metin((200, 350))]), strict_zone=True)
    h.bot.tracker.update(perc(coords=INSIDE_XY).coords, h.clock())
    h.clock.t += 60
    cands, _ = h.bot._label_view(perc(coords=None, metins=[metin((200, 350))]), h.clock.t)
    assert len(cands) == 1
