"""KeyboardNav (bot/keynav.py): logica pura del rumbo, giro de la vista y velocidades en el modo teclado."""
import logging
import math

import pytest

from bot.keynav import KeyboardNav, screen_angle
from sim_bot_env import make_cfg


def nav(**kb):
    cfg = make_cfg(navigation={"mode": "teclado"})
    cfg["navigation"]["keyboard"] = dict(cfg["navigation"]["keyboard"], **kb)
    return KeyboardNav(cfg)


def walk(k, heading_deg, dist=10.0, seconds=1.0):
    a = math.radians(heading_deg)
    return k.observe((dist * math.cos(a), dist * math.sin(a)), seconds)


def test_screen_angle():
    assert screen_angle((0, -100)) == pytest.approx(0)                 # arriba = frente
    assert screen_angle((100, 0)) == pytest.approx(math.pi / 2)       # derecha = +90
    assert screen_angle((-100, 0)) == pytest.approx(-math.pi / 2)
    assert abs(screen_angle((0, 100))) == pytest.approx(math.pi)


def test_rumbo_medido_del_desplazamiento():
    k = nav()
    assert k.heading is None
    assert walk(k, 123) == "medido"
    assert math.degrees(k.heading) == pytest.approx(123) and k.measured


def test_tramo_corto_se_acumula_hasta_poder_medir():
    k = nav()
    assert k.observe((3.0, 0.0), 0.3) == "corto" and k.heading is None
    assert k.observe((3.0, 0.0), 0.3) == "corto"
    assert k.observe((3.0, 0.0), 0.3) == "medido"
    assert k.heading == pytest.approx(0.0)
    assert k.speed != k.speed0       # la velocidad sale del acumulado (9 u en 0.9 s = 10 u/s)


def test_plan_turn_signo_y_segundos():
    k = nav()          # view_right_key = camera_right: 'right' gira la vista a la derecha; 60 grados/s
    assert k.plan_turn(math.radians(90)) == ("right", pytest.approx(1.5))
    assert k.plan_turn(math.radians(-30)) == ("left", pytest.approx(0.5))
    k2 = nav(view_right_key="camera_left")
    assert k2.plan_turn(math.radians(90))[0] == "left" and k2.plan_turn(math.radians(-90))[0] == "right"


def test_plan_turn_se_recorta_a_max_turn():
    k = nav(max_turn_deg=100.0)
    d, s = k.plan_turn(math.radians(170))
    assert d == "right" and s == pytest.approx(100 / 60)
    d, s = k.plan_turn(math.radians(-175))
    assert d == "left" and s == pytest.approx(100 / 60)


def test_on_turned_pasa_a_estimado_y_suma_el_giro():
    k = nav()
    walk(k, 10)
    k.on_turned("right", 0.5)          # 30 grados a la derecha
    assert not k.measured and math.degrees(k.heading) == pytest.approx(40)
    k.on_turned("left", 1.0)           # 60 grados a la izquierda
    assert math.degrees(k.heading) == pytest.approx(-20)


def test_on_turned_sin_rumbo_no_inventa_uno():
    k = nav()
    k.on_turned("right", 0.5)
    assert k.heading is None


def test_barrido_largo_invalida_el_rumbo():
    k = nav()
    walk(k, 0)
    for _ in range(12):
        k.on_turned("right", 0.5)     # 360 grados: el estimado no sirve
    assert k.heading is None and (k.pending is None or k.pending[0] is None)   # sin rumbo previo no se aprende nada
    assert walk(k, 77) == "medido" and k.rate == k.rate0 and k.view_right_key == "camera_right"


def test_inversion_de_las_teclas(caplog):
    k = nav()
    walk(k, 0)
    k.on_turned("right", 1.0)         # se supone +60 grados
    with caplog.at_level(logging.WARNING):
        assert walk(k, -60) == "invertido"   # pero la vista giro a la izquierda
    assert k.view_right_key == "camera_left"
    assert any("al reves" in r.message for r in caplog.records)
    assert math.degrees(k.heading) == pytest.approx(-60)
    assert k.plan_turn(math.radians(60))[0] == "left"      # ya con el sentido corregido


def test_giro_corto_no_se_usa_para_aprender():
    k = nav()
    walk(k, 0)
    k.on_turned("right", 0.1)         # 6 grados: ruido
    assert walk(k, 0) == "medido" and k.view_right_key == "camera_right" and k.rate == k.rate0


def test_aprende_la_velocidad_de_giro():
    k = nav()
    r0 = k.rate
    walk(k, 0)
    k.on_turned("right", 1.0)         # se suponen 60 grados...
    assert walk(k, 90) == "aprendido"        # ...pero giro 90: la tecla es mas rapida
    assert k.rate > r0 and k.rate == pytest.approx(0.5 * r0 + 0.5 * math.radians(90))
    k.on_turned("right", 1.0)
    assert math.degrees(k.heading) == pytest.approx(90 + math.degrees(k.rate))


def test_velocidad_de_giro_acotada():
    lento, rapido = nav(), nav()
    for _ in range(12):
        for k, secs in ((lento, 400.0), (rapido, 0.05)):
            walk(k, 0)
            k.pending = (0.0, math.radians(100), secs)    # 100 grados ordenados que tardaron `secs` s en la tecla
            walk(k, 100)
    assert lento.rate == pytest.approx(0.25 * lento.rate0)
    assert rapido.rate == pytest.approx(4 * rapido.rate0)


def test_velocidad_de_caminata_aprendida_con_ema_y_acotada():
    k = nav(walk_speed=15.0)
    walk(k, 0, dist=10.0, seconds=1.0)     # 10 u/s
    assert k.speed == pytest.approx(12.5)
    walk(k, 0, dist=10.0, seconds=1.0)
    assert k.speed == pytest.approx(11.25)
    for _ in range(40):
        walk(k, 0, dist=50.0, seconds=0.1)   # 500 u/s: tope 4x
    assert k.speed == pytest.approx(4 * 15.0)


def test_tramo_sin_avance_no_toca_la_velocidad():
    k = nav()
    v = k.speed
    assert k.observe((0.0, 0.0), 0.5) == "corto"
    assert k.speed == v and k.heading is None


def test_camara_girada_por_fuera(caplog):
    k = nav()
    walk(k, 0)
    with caplog.at_level(logging.WARNING):
        assert walk(k, 90) == "externo"      # sin giro del bot de por medio
    assert any("girada por fuera" in r.message for r in caplog.records)
    assert math.degrees(k.heading) == pytest.approx(90)
    assert walk(k, 100) == "medido"          # una variacion chica no es "externo"


def test_giro_pendiente_con_error_de_medicion_chico_no_invierte():
    k = nav()
    walk(k, 0)
    k.on_turned("right", 0.5)      # +30
    assert walk(k, 28) == "aprendido" and k.view_right_key == "camera_right"


def test_flip_keys_olvida_el_rumbo():
    k = nav()
    walk(k, 10)
    k.flip_keys()
    assert k.view_right_key == "camera_left" and k.heading is None


def test_describe():
    k = nav()
    assert "rumbo ?" in k.describe()
    walk(k, 45)
    assert "rumbo 45 (medido)" in k.describe()
    k.on_turned("right", 0.5)
    assert "(estimado)" in k.describe()


def test_config_por_defecto_es_teclado_y_se_valida():
    import copy

    from bot.config import DEFAULTS, ConfigError, validate_config
    assert DEFAULTS["navigation"]["mode"] == "teclado"
    kb = DEFAULTS["navigation"]["keyboard"]
    assert kb["turn_deg_per_s"] == pytest.approx(360 / (DEFAULTS["explore"]["camera_steps"] * DEFAULTS["explore"]["camera_step_s"]))
    validate_config(copy.deepcopy(DEFAULTS))
    for path, val in ((("navigation", "mode"), "mouse"), (("navigation", "keyboard", "view_right_key"), "q"),
                      (("navigation", "keyboard", "chunk_s"), 0), (("stuck", "back_s"), 0), (("stuck", "look_walk_s"), -1)):
        cfg = copy.deepcopy(DEFAULTS)
        d = cfg
        for k in path[:-1]:
            d = d[k]
        d[path[-1]] = val
        with pytest.raises(ConfigError):
            validate_config(cfg)
