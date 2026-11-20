"""KeyboardNav (bot/keynav.py): logica pura de cam_h (hacia donde camina W), combinaciones de teclas, giro de la vista y
velocidades en el modo teclado."""
import logging
import math

import pytest

from bot.keynav import COMBOS, KeyboardNav, combo_for_screen_angle, combo_toward_ground, is_lateral, screen_angle
from sim_bot_env import make_cfg


def nav(**kb):
    cfg = make_cfg(navigation={"mode": "teclado"})
    cfg["navigation"]["keyboard"] = dict(cfg["navigation"]["keyboard"], **kb)
    return KeyboardNav(cfg)


def walk(k, heading_deg, dist=10.0, seconds=1.0, combo="W"):
    """Un tramo con `combo` cuyo desplazamiento real apunta a `heading_deg` (angulo del juego)."""
    a = math.radians(heading_deg)
    return k.observe((dist * math.cos(a), dist * math.sin(a)), seconds, combo=combo)


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


def test_inversion_de_q_e_exige_dos_giros_consistentes(caplog):
    k = nav()
    walk(k, 0)
    k.on_turned("right", 1.0)         # se suponen +60 grados
    assert walk(k, -60) == "dudoso"   # una sola muestra al reves NO alcanza
    assert k.view_right_key == "camera_right"
    k.on_turned("right", 1.0)         # otra vez +60 supuestos desde el cam_h medido (-60)
    with caplog.at_level(logging.WARNING):
        assert walk(k, -120) == "invertido"   # la segunda vez tambien giro hacia la izquierda
    assert k.view_right_key == "camera_left"
    assert any("Q/E giran al reves" in r.message for r in caplog.records)
    assert k.plan_turn(math.radians(60))[0] == "left"      # ya con el sentido corregido


def test_un_giro_al_reves_aislado_no_invierte_q_e():
    k = nav()
    walk(k, 0)
    k.on_turned("right", 1.0)
    assert walk(k, -60) == "dudoso"
    k.on_turned("right", 1.0)
    assert walk(k, 0) == "aprendido"          # el siguiente giro fue normal (+60): se reinicia la cuenta
    k.on_turned("right", 1.0)
    assert walk(k, -60) == "dudoso" and k.view_right_key == "camera_right"


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


def test_muestra_lejana_se_descarta_y_dos_seguidas_reemplazan(caplog):
    k = nav()
    walk(k, 0)
    with caplog.at_level(logging.WARNING):
        assert walk(k, 90) == "externo"      # una muestra a mas de 60 grados de cam_h medido se descarta
        assert math.degrees(k.heading) == pytest.approx(0)
        assert walk(k, 90) == "externo"      # dos seguidas: la camara si cambio
    assert any("girada por fuera" in r.message for r in caplog.records)
    assert math.degrees(k.heading) == pytest.approx(90)
    assert walk(k, 100) == "medido"          # una variacion chica no es "externo"


def test_cam_h_medido_promedia_las_muestras_cercanas():
    k = nav()
    walk(k, 0)
    walk(k, 20)
    assert math.degrees(k.heading) == pytest.approx(10)   # EMA 0.5


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
    assert "camara ?" in k.describe() and k.state == "desconocido"
    walk(k, 45)
    assert "camara 45 (medido)" in k.describe(bearing=math.radians(135))
    assert "hacia 135" in k.describe(bearing=math.radians(135)) and "A/D normal" in k.describe()
    k.on_turned("right", 0.5)
    assert "(estimado)" in k.describe() and k.state == "estimado"


# ---------------------------------------------------------------- combinaciones de teclas (cam_h + desvio)
OFFSETS = {"W": 0, "W+D": 45, "D": 90, "S+D": 135, "S": 180, "S+A": -135, "A": -90, "W+A": -45}


@pytest.mark.parametrize("combo,off", list(OFFSETS.items()))
def test_cada_combinacion_mide_cam_h_restando_su_desvio(combo, off):
    k = nav()
    walk(k, 100 + off, combo=combo)          # cam_h real = 100
    assert math.degrees(k.heading) == pytest.approx(100)
    assert math.cos(k.offset(combo)) == pytest.approx(math.cos(math.radians(off)))
    assert math.sin(k.offset(combo)) == pytest.approx(math.sin(math.radians(off)))


@pytest.mark.parametrize("bearing", range(0, 360, 15))
def test_plan_walk_elige_la_combinacion_mas_cercana(bearing):
    k = nav()
    walk(k, 141)                               # cam_h = 141
    combo, err = k.plan_walk(math.radians(bearing))
    assert abs(math.degrees(err)) <= 22.5 + 1e-6
    best = min(OFFSETS, key=lambda c: abs((141 + OFFSETS[c] - bearing + 180) % 360 - 180))
    assert combo == best


def test_plan_walk_sin_cam_h_no_inventa_nada():
    assert nav().plan_walk(1.0) is None and nav().needs_probe("W")


def test_probe_si_cam_h_estimado_y_combinacion_lateral_sin_verificar():
    k = nav()
    walk(k, 0)
    k.on_turned("right", 0.5)
    assert k.needs_probe("W+D") and not k.needs_probe("W") and not k.needs_probe("S")
    walk(k, 30, combo="W")
    assert not k.needs_probe("W+D")            # medido otra vez


def test_a_d_al_reves_se_corrige_con_dos_muestras_seguidas(caplog):
    k = nav()
    walk(k, 0)                                 # cam_h = 0
    # mundo con A/D espejadas: W+D camina en realidad a -45 (en vez de +45)
    assert walk(k, -45, combo="W+D") == "externo" and k.side == 1      # una sola muestra no alcanza
    with caplog.at_level(logging.WARNING):
        assert walk(k, -45, combo="W+D") == "invertido"
    assert k.side == -1 and any("A/D caminan al reves" in r.message for r in caplog.records)
    assert math.degrees(k.heading) == pytest.approx(0)
    assert math.degrees(k.offset("D")) == pytest.approx(-90) and math.degrees(k.offset("S")) == pytest.approx(180)
    assert "A/D al reves" in k.describe()
    assert k.plan_walk(math.radians(45))[0] == "W+A"     # ahora W+A es hacia la derecha


def test_a_d_normales_no_se_invierten_con_ruido():
    k = nav()
    walk(k, 0)
    for noise in (-30, 25, 20, -35, 10):       # W+D normal camina a +45; el ruido lo mueve unos grados
        walk(k, 45 + noise, combo="W+D")
    assert k.side == 1


def test_una_muestra_lateral_sin_cam_h_medido_no_vota():
    k = nav()
    assert walk(k, -45, combo="W+D") == "medido" and k.side == 1      # sin cam_h previo solo se mide


def test_acumula_por_combinacion():
    k = nav()
    assert k.observe((3.0, 0.0), 0.3, combo="W") == "corto"
    assert k.observe((0.0, 3.0), 0.3, combo="D") == "corto"      # cambio de combinacion: se reinicia el acumulado
    assert k.observe((0.0, 3.0), 0.3, combo="D") == "corto"
    assert k.observe((0.0, 3.0), 0.3, combo="D") == "medido"


def test_combos_y_sectores():
    assert len(COMBOS) == 8 and is_lateral("W+D") and not is_lateral("S")
    assert combo_for_screen_angle(0) == "W" and combo_for_screen_angle(math.pi / 2) == "D"
    assert combo_for_screen_angle(math.radians(-130)) == "S+A" and combo_for_screen_angle(math.pi) == "S"
    assert combo_for_screen_angle(math.radians(40)) == "W+D" and combo_for_screen_angle(math.radians(-60)) == "W+A"


def test_combo_toward_ground_usa_el_plano_del_piso():
    cfg = make_cfg()
    shape = (1057, 1858, 3)
    feet = (929.0, 600.0)
    assert combo_toward_ground(feet, (929.0, 300.0), shape, cfg) == "W"          # arriba
    assert combo_toward_ground(feet, (1300.0, 600.0), shape, cfg) == "D"         # a la derecha, a la misma altura
    assert combo_toward_ground(feet, (929.0, 800.0), shape, cfg) == "S"          # abajo
    assert combo_toward_ground(feet, (500.0, 600.0), shape, cfg) == "A"


def test_config_por_defecto_es_teclado_y_se_valida():
    import copy

    from bot.config import DEFAULTS, ConfigError, validate_config
    assert DEFAULTS["navigation"]["mode"] == "teclado"
    kb = DEFAULTS["navigation"]["keyboard"]
    assert kb["turn_deg_per_s"] == pytest.approx(360 / (DEFAULTS["explore"]["camera_steps"] * DEFAULTS["explore"]["camera_step_s"]))
    validate_config(copy.deepcopy(DEFAULTS))
    for path, val in ((("navigation", "mode"), "mouse"), (("navigation", "keyboard", "view_right_key"), "q"),
                      (("navigation", "keyboard", "chunk_s"), 0), (("stuck", "back_s"), 0), (("stuck", "look_walk_s"), -1),
                      (("pickup", "step_s"), 0), (("explore", "edge_return_u"), -1)):
        cfg = copy.deepcopy(DEFAULTS)
        d = cfg
        for k in path[:-1]:
            d = d[k]
        d[path[-1]] = val
        with pytest.raises(ConfigError):
            validate_config(cfg)
