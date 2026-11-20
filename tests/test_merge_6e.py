"""Integracion de main (Fase 6e) con desarrollo: habilidades vs barrido de camara, revivir descarta el metin en curso,
conteo por tipo con el recoger de objeto fijo, perfil y guia de calibracion del angulo vertical."""
from bot.bot import State, _CurrentMetin, _Next, _Scan, _Attack
from bot import profiles
from bot.ui import calibrate_runner as cr
from test_bot_states import Harness, perc


def _bot():
    return Harness(lambda h, n: perc()).bot


def test_skill_safe_no_con_el_barrido_de_camara():
    b = _bot()
    b.state = State.BUSCAR
    assert b._skill_safe(0.0)
    b._scan = _Scan("left", 0.0)
    assert not b._skill_safe(0.0)


def test_revivir_descarta_metin_en_curso_y_proximo():
    b = _bot()
    b._cur = _CurrentMetin((10, 10), None, None, 0.0, 0.0, 0)
    b._next = _Next(1, (5, 5), "piedra", 1.0, 0.0)
    b._enter(State.REVIVIR, "pantalla de muerte")
    assert b._cur is None and b._next is None


def test_camera_pitch_va_al_perfil_del_personaje():
    assert profiles.kind_of(("camera_pitch", "ref_horizon_frac")) == "personaje"
    assert profiles.kind_of(("camera_pitch", "tolerance")) is None


def test_guia_calibrar_angulo_vertical():
    cmd = cr.build_calibrate_command("camara", servidor="S", personaje="P", zona="Z", python="py", script="cal.py")
    assert cmd[:4] == ["py", "-u", "cal.py", "camara"] and "--ui" in cmd and "--en-perfil" in cmd
