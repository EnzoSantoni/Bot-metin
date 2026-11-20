"""Buscar el proximo metin MIENTRAS se ataca el actual: solo con la vida bajando, nunca un clic en otro metin, la camara vuelve a su
posicion original, se corta (y se desactiva) si girar corta el ataque, y despues de romper y recoger se va directo al
encontrado. Todo con input y percepcion falsos."""
import logging

import pytest

import bot.input.base as base
from bot.bot import State
from test_bot_behavior import messages
from test_bot_states import Harness, metin, perc, target_panel

A_POINT = (1000, 500)     # metin atacado (pegado al personaje)
AIM = (1000, 476)         # el clic va 24 px mas arriba, al cuerpo de la piedra
B_POINT = (300, 400)      # otro metin, lejos del personaje
RIGHT_KEY, LEFT_KEY = "e", "q"   # controls.camera_right / camera_left


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


def net_rotation(h):
    """Pasos de camara netos (+ derecha / - izquierda) mandados hasta ahora (cada paso es una pulsacion de Q o E)."""
    return sum(1 if e == ("key_down", RIGHT_KEY) else -1 if e == ("key_down", LEFT_KEY) else 0 for e in h.be.events)


def left_clicks(h):
    """(posicion de cliente, rotacion neta en ese momento) de cada clic izquierdo."""
    out, last, rot = [], None, 0
    for e in h.be.events:
        if e[0] == "move_to":
            last = (e[1] - 8, e[2] - 31)
        elif e == ("key_down", RIGHT_KEY):
            rot += 1
        elif e == ("key_down", LEFT_KEY):
            rot -= 1
        elif e == ("mouse_down", "left"):
            out.append((last, rot))
    return out


def world(h, *, drop=True, hp0=0.9, freeze_rotated=False, b_steps=3):
    """Guion: BUSCAR ve A; ATACAR con la vida bajando (o quieta). Con la camara girada `b_steps` pasos se ve B (y no A); con
    otros giros no se ve ninguno. `st["break"] = True`: el metin se rompe (pasan 20 s; panel y piedra desaparecen); despues A
    ya no esta."""
    st = {"hp": hp0, "break": False, "jumped": False}

    def script(h_, n):
        rot = net_rotation(h_)
        if h_.bot.state is State.RECOGER:
            return perc()
        panel = None
        if h_.bot.state is State.ATACAR:
            if st["break"]:
                if not st["jumped"]:
                    st["jumped"] = True
                    h_.clock.t += 20.0   # pasa el min_break_s
                return perc(panel=None, metins=[])
            if drop and not (freeze_rotated and rot != 0):
                st["hp"] = max(0.05, st["hp"] - 0.004)
            panel = target_panel(st["hp"])
        if rot == 0:
            m = [metin(A_POINT)] if h_.bot.stats.metins == 0 else []
        elif rot == b_steps:
            m = [metin(B_POINT)]
        else:
            m = []
        return perc(panel=panel, metins=m)
    return script, st


def make(**kw):
    h = Harness(None)
    script, st = world(h, **kw)
    h.script = script
    return h, st


# ------------------------------------------------------------------ solo con la vida bajando
def test_no_gira_si_la_vida_no_baja(caplog):
    caplog.set_level(logging.INFO)
    h, _ = make(drop=False)
    h.cfg["stuck"]["fast_s"] = 1000.0   # quieto y sin pegar es "trabado": aca se prueba solo que el barrido no arranca
    h.run_until(lambda: h.bot.state is State.ATACAR)
    for _ in range(100):   # ~15 s atacando con la vida quieta (a los ~20 s se descarta el metin y EXPLORAR si gira la camara)
        h.bot.step()
    assert h.bot.state is State.ATACAR
    assert net_rotation(h) == 0 and not any(e[0] == "key_down" and e[1] in (RIGHT_KEY, LEFT_KEY) for e in h.be.events)
    assert not any("Mientras ataco" in m for m in messages(caplog))


def test_no_gira_antes_de_scan_after_s():
    h, _ = make()
    h.run_until(lambda: h.bot.state is State.ATACAR)
    t0 = h.clock.t
    while h.clock.t - t0 < 4.0:
        h.bot.step()
    assert not any(e[0] == "key_down" and e[1] in (RIGHT_KEY, LEFT_KEY) for e in h.be.events)


def test_scan_enabled_false_desactiva_el_barrido():
    h = Harness(None, attack={"scan_enabled": False})
    h.script, _ = world(h)
    h.run_until(lambda: h.bot.state is State.ATACAR)
    for _ in range(300):
        h.bot.step()
    assert not any(e[0] == "key_down" and e[1] in (RIGHT_KEY, LEFT_KEY) for e in h.be.events)


# ------------------------------------------------------------------ busca, recuerda, vuelve y sin clics
def test_barrido_encuentra_otro_metin_vuelve_la_camara_y_no_hace_clic(caplog):
    caplog.set_level(logging.INFO)
    h, _ = make()
    h.run_until(lambda: h.bot._next is not None and h.bot._scan is None and net_rotation(h) == 0, limit=2000)
    n = h.bot._next
    assert n.kind == "metin_de_oscuridad" and abs(n.rel) == 3 and n.point == B_POINT
    assert any("Mientras ataco: encontre metin_de_oscuridad a 3 pasos (300, 400)" in m for m in messages(caplog))
    assert [c[0] for c in left_clicks(h)] == [AIM]          # ningun clic en otro metin
    assert net_rotation(h) == 0                                   # la camara quedo en su posicion original
    # 3 pasos de ida y 3 de vuelta, en sentidos opuestos
    keys = [e[1] for e in h.be.events if e[0] == "key_down" and e[1] in (RIGHT_KEY, LEFT_KEY)]
    assert len(keys) == 6 and keys[:3] == [keys[0]] * 3 and keys[3:] == [keys[3]] * 3 and keys[0] != keys[3]


def test_no_encuentra_nada_vuelve_igual_y_no_vuelve_a_buscar_enseguida(caplog):
    caplog.set_level(logging.INFO)
    h, _ = make(b_steps=99)
    h.run_until(lambda: any("no encontre otro metin en 12 pasos" in m for m in messages(caplog)), limit=2000)
    h.run_until(lambda: h.bot._scan is None, limit=2000)
    assert net_rotation(h) == 0 and h.bot._next is None
    n_keys = sum(1 for e in h.be.events if e[0] == "key_down" and e[1] in (RIGHT_KEY, LEFT_KEY))
    assert n_keys == 24   # 12 de ida + 12 de vuelta
    t0 = h.clock.t
    while h.clock.t - t0 < 30:   # menos de next_keep_s: no empieza otro barrido
        h.bot.step()
    assert sum(1 for e in h.be.events if e[0] == "key_down" and e[1] in (RIGHT_KEY, LEFT_KEY)) == 24


# ------------------------------------------------------------------ si girar corta el ataque
def test_si_la_vida_deja_de_bajar_al_girar_corta_vuelve_y_desactiva(caplog):
    caplog.set_level(logging.INFO)
    h, st = make(freeze_rotated=True, b_steps=99)
    h.run_until(lambda: h.bot._scan_off, limit=3000)
    assert any("Girar la camara corta el ataque: no busco mientras ataco" in r.getMessage() and r.levelno == logging.WARNING
               for r in caplog.records)
    h.run_until(lambda: net_rotation(h) == 0 and h.bot._scan is None, limit=500)
    assert all(rot == 0 for _, rot in left_clicks(h))   # ningun reclic con la camara girada
    for _ in range(700):   # con la vida bajando otra vez y mucho tiempo: no vuelve a buscar
        h.bot.step()
        assert h.bot._scan is None
        if h.bot.state is State.ATACAR:   # (las exploraciones si giran la camara: aca solo importa el barrido de ATACAR)
            assert net_rotation(h) == 0 or not h.bot._atk
    assert h.bot._scan_off and h.bot._scan is None


def test_nunca_gira_si_la_vida_no_viene_bajando_aunque_haya_scan_pendiente():
    h, _ = make(drop=False)
    h.run_until(lambda: h.bot.state is State.ATACAR)
    h.bot._atk.first_drop_t = h.clock.t - 100   # "viene pegando" desde hace rato, pero la vida esta quieta
    for _ in range(100):
        h.bot.step()
    assert net_rotation(h) == 0 and not any(e[0] == "key_down" and e[1] in (RIGHT_KEY, LEFT_KEY) for e in h.be.events)


# ------------------------------------------------------------------ despues de romper y recoger: directo al encontrado
def test_despues_de_romper_y_recoger_va_directo_al_que_encontro(caplog):
    caplog.set_level(logging.INFO)
    h, st = make()
    h.run_until(lambda: h.bot._next is not None and h.bot._scan is None and net_rotation(h) == 0, limit=3000)
    st["hp"] = 0.06      # la vida llega casi a 0 ...
    for _ in range(4):
        h.bot.step()
    st["break"] = True   # ... y el metin en curso se rompe (panel y piedra desaparecen)
    h.run_until(lambda: State.RECOGER in h.states() and h.bot.state is State.ATACAR, limit=3000)
    msgs = messages(caplog)
    assert any(m.startswith("Metin roto") for m in msgs)
    assert any("Voy al metin que encontre mientras atacaba" in m for m in msgs)
    clicks = left_clicks(h)
    assert clicks[0] == (AIM, 0) and len(clicks) == 2
    assert clicks[-1][0] == (B_POINT[0], B_POINT[1] - 24) and abs(clicks[-1][1]) == 3   # el clic al recordado, con la camara girada 3 pasos
    assert h.bot._next is None
    assert h.states().index(State.RECOGER) < len(h.states()) - 1
    assert State.EXPLORAR not in h.states()   # fue directo: sin explorar


def test_el_recuerdo_vence_a_los_next_keep_s(caplog):
    caplog.set_level(logging.INFO)
    h, _ = make()
    h.run_until(lambda: h.bot._next is not None and h.bot._scan is None, limit=2000)
    h.bot._next.t -= 61.0
    h.bot._atk = None
    h.bot._enter(State.BUSCAR, "prueba")
    h.bot.step()
    assert any("se vencio" in m for m in messages(caplog)) and h.bot._next is None


def test_si_el_recordado_no_aparece_elige_normal(caplog):
    caplog.set_level(logging.INFO)
    h, _ = make()
    h.run_until(lambda: h.bot._next is not None and h.bot._scan is None and net_rotation(h) == 0, limit=2000)
    h.bot._next.point = (1500, 900)   # el punto recordado no coincide con nada visible (a > relocate_px de B)
    h.bot._atk = None
    h.bot._cur = None
    h.bot._enter(State.BUSCAR, "prueba")
    for _ in range(40):
        h.bot.step()
        if h.bot.state is State.ATACAR:
            break
    assert any("no aparece a <=" in m and "eleccion normal" in m for m in messages(caplog))
