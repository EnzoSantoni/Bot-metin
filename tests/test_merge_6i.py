"""Integracion de main (Fases 6h y 6i) con desarrollo: habilidades vs acercamiento por teclado, clics y reinicio de camara;
reinicio de camara vs REVIVIR; habilidades y vida cerca de romper."""
from bot.bot import State, _Attack
from bot.camera_pitch import CameraPitch
from test_bot_states import Harness, perc


def _bot():
    b = Harness(lambda h, n: perc()).bot
    b.state = State.BUSCAR
    return b


def test_skill_safe_no_durante_el_acercamiento_con_teclado():
    b = _bot()
    assert b._skill_safe(100.0)
    b._apr = object()
    assert not b._skill_safe(100.0)


def test_skill_safe_no_entre_el_clic_y_la_confirmacion():
    b = _bot()
    gap = float(b.cfg["attack"]["min_click_gap_s"])
    b._last_click_t = 100.0
    assert not b._skill_safe(100.0 + gap / 2)
    assert b._skill_safe(100.0 + gap + 0.01)


def test_skill_safe_no_con_el_metin_por_romperse():
    b = _bot()
    b.state = State.ATACAR
    a = b._atk = _Attack.__new__(_Attack)
    a.confirmed, a.pending, a.last_click_t = True, None, -1e9
    a.last_hp = 0.9
    assert b._skill_safe(100.0)
    a.last_hp = float(b.cfg["skill_timing"]["near_break_hp"])
    assert not b._skill_safe(100.0)


def test_reinicio_de_camara_nunca_en_revivir_ni_acercandose():
    b = _bot()
    b.cfg["camera_pitch"]["enabled"] = True
    b.pitch = CameraPitch(b.cfg)
    called = []
    b.pitch.reset = lambda *a, **k: called.append(a)
    for st in (State.REVIVIR, State.ACERCARSE, State.ATACAR, State.RECOGER, State.CAPTCHA):
        b.state = st
        assert b._pitch_reset("prueba") is False
    assert called == []
    b.state = State.BICHOS   # bichos: el reinicio de camara no molesta
    b._pitch_reset("arranque")
    assert len(called) == 1


def test_despues_de_revivir_hay_reinicio_de_camara():
    b = _bot()
    why = []
    b._pitch_reset = lambda w: why.append(w) or False
    b.state = State.REVIVIR
    b._enter(State.BUSCAR, "revivi")
    assert why == ["revivido"]
