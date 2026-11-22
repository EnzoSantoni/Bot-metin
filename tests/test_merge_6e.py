"""Integracion de main (Fase 6e) con desarrollo: habilidades vs barrido de camara, revivir descarta el metin en curso,
conteo por tipo con el recoger de objeto fijo, perfil y guia de calibracion del angulo vertical."""
import pytest

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
    assert profiles.kind_of(("camera_pitch", "from_limit_px")) == "personaje"
    assert profiles.kind_of(("camera_pitch", "to_limit_px")) is None
    assert profiles.kind_of(("camera_pitch", "ref_horizon_frac")) is None   # obsoleta (reemplazada por el reinicio desde el tope)


def test_sentidos_de_teclas_son_del_servidor():
    assert profiles.kind_of(("navigation", "keyboard", "view_right_key")) == "servidor"
    assert profiles.kind_of(("navigation", "keyboard", "ad_side")) == "servidor"
    assert profiles.kind_of(("navigation", "keyboard", "lateral")) is None


def test_guia_calibrar_camara_desde_el_tope():
    cmd = cr.build_calibrate_command("camara-tope", servidor="S", personaje="P", zona="Z", python="py", script="cal.py")
    assert cmd[:4] == ["py", "-u", "cal.py", "camara-tope"] and "--ui" in cmd and "--en-perfil" in cmd
    with pytest.raises(ValueError):
        cr.build_calibrate_command("camara")   # el subcomando viejo ya no existe
