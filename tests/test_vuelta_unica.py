"""UNA vuelta de camara con lista de candidatos en el mapa (simulador con camara en perspectiva y rumbo del minimapa).

Escenarios: elige el mas cercano y gira directo sin segunda vuelta; empate -> el mas cerca del centro de la zona; un metin
encontrado mientras ataca se recupera por su posicion en el mapa despues de girar; vuelta sin nada -> punto del mapa de metin o
centro; candidato que no aparece tras girar -> camina hacia el; Q/E al reves se corrige con el rumbo medido."""
import logging
import math

import pytest

import bot.input.base as base
from bot.spawns import Spot, SpawnMap
from sim_bot_env import SimEnv, make_cfg

OSC, NEG = "metin_de_oscuridad", "metin_negro"
K = 13.0                    # unidades del juego por unidad de piso del simulador (igual a explore.ground_scale_u)
FACING_WEST = 3 * math.pi / 2   # phi del simulador: el frente de la pantalla mira al oeste (rumbo = phi - 90 grados)
FACING_EAST = math.pi / 2
FACING_NORTH = 0.0


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


def env_for(phi, start=(560.0, 150.0), right_key_ok=True, **kw):
    cfg = make_cfg()
    # la vuelta con lista de candidatos y el barrido mientras ataca estan apagados por defecto (decision simple): aca se prueban
    cfg["explore"]["candidate_list"] = True
    cfg["attack"]["scan_enabled"] = True
    if right_key_ok:   # en el simulador la tecla camera_left sube el rumbo: con esto Q/E ya estan bien supuestas
        cfg["navigation"]["keyboard"]["view_right_key"] = "camera_left"
    return SimEnv(3, phi=phi, start=start, cfg=cfg, heading=True, pinhole_k=K, **kw)


def msgs(caplog):
    return [r.getMessage() for r in caplog.records]


def count(caplog, text):
    return sum(1 for m in msgs(caplog) if text in m)


def laps(caplog):
    return count(caplog, " grados en ")


def test_una_sola_vuelta_elige_el_mas_cercano_y_gira_directo(caplog):
    caplog.set_level(logging.INFO)
    env = env_for(FACING_WEST)
    near = env.world.add_metin((650.0, 150.0), name=OSC)     # 90 u al este (piso ~7: mas lejos que approach.near_floor)
    env.world.add_metin((680.0, 175.0), name=OSC)            # 125 u
    env.world.add_metin((700.0, 130.0), name=NEG)            # 145 u
    assert env.run_until(lambda: env.world.clicks_on_metin, max_s=200) == "cond"
    assert env.world.clicks_on_metin[0][1] is near
    ms = msgs(caplog)
    lap = [m for m in ms if m.startswith("Vuelta de busqueda: ") and "candidatos (mejor" in m]
    # las piedras lejanas (> near_floor) no se confirman con hover durante la vuelta: quedan como piedras sin confirmar
    assert len(lap) == 1 and "3 candidatos" in lap[0] and "en (650, 150) del mapa" in lap[0]
    assert not any("hover para leer el nombre" in m for m in ms)
    assert laps(caplog) == 1   # una vuelta, no mas
    assert count(caplog, "Giro hacia el candidato en (650, 150) del mapa") == 1
    assert not any("sin nueva vuelta" in m for m in ms)
    # la escala del juego se estima bien: el candidato quedo a 90 u +- 15 %
    first = next(m for m in ms if m.startswith("Candidato: ") and " en (650" in m)
    d = int(first.split(" a ")[1].split(" u")[0])
    assert 77 <= d <= 103


def test_empate_de_distancia_gana_el_mas_cerca_del_centro(caplog):
    caplog.set_level(logging.INFO)
    env = env_for(FACING_NORTH, start=(520.0, 160.0))   # los dos metin quedan a los costados: no se ven al empezar
    east = env.world.add_metin((590.0, 160.0), name=OSC)     # 70 u, hacia el centro de la zona (598, 156)
    env.world.add_metin((450.0, 160.0), name=OSC)            # 70 u, hacia el borde oeste
    assert env.run_until(lambda: env.world.clicks_on_metin, max_s=200) == "cond"
    assert env.world.clicks_on_metin[0][1] is east


def test_metin_encontrado_mientras_ataca_se_recupera_por_posicion_en_el_mapa(caplog):
    caplog.set_level(logging.INFO)
    env = env_for(FACING_EAST)
    a = env.world.add_metin((590.0, 150.0), name=OSC, dps=0.012)   # a la vista, se rompe en ~80 s
    b = env.world.add_metin((510.0, 175.0), name=OSC)               # atras de la camara: solo se ve girando
    assert env.run_until(lambda: not b.alive, max_s=600) == "cond"
    ms = msgs(caplog)
    assert not a.alive
    assert any(m.startswith("Mientras ataco: encontre") and "del mapa" in m for m in ms)
    first_b = next(i for i, m in enumerate(ms) if m.startswith("Mientras ataco: encontre"))
    broke = next(i for i, m in enumerate(ms) if m.startswith("Metin roto"))
    assert first_b < broke
    # despues de romper y recoger va directo a B girando hacia su posicion: sin vuelta nueva y sin "eleccion normal"
    after = ms[broke:]
    assert any("Lista de candidatos (sin nueva vuelta)" in m for m in after)
    assert any("Giro hacia el candidato" in m for m in after)
    assert not any(" grados en " in m for m in after) and not any("eleccion normal" in m for m in ms)
    assert [m for _, m in env.world.clicks_on_metin][:2] == [a, b] or b in [m for _, m in env.world.clicks_on_metin]


def test_vuelta_sin_nada_va_al_centro_de_la_zona(caplog):
    caplog.set_level(logging.INFO)
    env = env_for(FACING_WEST, start=(560.0, 160.0))
    center = env.bot.navigator.return_target((560.0, 160.0))
    env.run_until(lambda: any("voy al centro" in m for m in msgs(caplog)), max_s=120)
    assert laps(caplog) <= 1 and any("0 candidatos" in m for m in msgs(caplog))
    d0 = math.dist(env.world.pos, center)
    env.run_until(lambda: math.dist(env.world.pos, center) < d0 - 25, max_s=120)
    assert math.dist(env.world.pos, center) < d0 - 25


def test_vuelta_sin_nada_va_al_punto_listo_del_mapa_de_metin(caplog):
    caplog.set_level(logging.INFO)
    sm = SpawnMap(None, 6.0, respawn_s=0.0, revisit_s=0.0, clock_wall=lambda: 1e6)
    sm.spots.append(Spot(650.0, 150.0))
    env = env_for(FACING_WEST, start=(560.0, 160.0), bot_kw={"spawn_map": sm})
    env.run_until(lambda: any("voy al punto (650, 150)" in m for m in msgs(caplog)), max_s=120)
    assert any("voy al punto (650, 150)" in m for m in msgs(caplog))
    assert not any("voy al centro" in m for m in msgs(caplog))
    env.run_until(lambda: env.world.pos[0] > 610, max_s=120)
    assert env.world.pos[0] > 610


def test_candidato_no_visible_tras_girar_camina_hacia_el_sin_nueva_vuelta(caplog):
    caplog.set_level(logging.INFO)
    env = env_for(FACING_WEST, start=(540.0, 150.0))
    m = env.world.add_metin((705.0, 150.0), name=OSC)        # a 165 u: mas lejos que far_dist, el simulador no lo muestra
    env.bot.cands.add((705.0, 150.0), "piedra", False, 0.0)   # pero el bot lo recuerda en el mapa
    assert env.run_until(lambda: env.world.clicks_on_metin, max_s=200) == "cond"
    assert env.world.clicks_on_metin[0][1] is m
    ms = msgs(caplog)
    assert any("Candidato no visible tras girar: camino hacia" in x for x in ms)
    assert not any(" grados en " in x for x in ms)


def test_si_q_e_estan_al_reves_lo_llega_igual_a_un_candidato(caplog):
    caplog.set_level(logging.INFO)
    env = env_for(FACING_NORTH, start=(540.0, 150.0), right_key_ok=False)   # hay que girar 90 grados: el signo importa
    m = env.world.add_metin((620.0, 150.0), name=OSC)
    env.bot.cands.add((620.0, 150.0), "metin_de_oscuridad", True, 0.0)
    assert env.run_until(lambda: env.world.clicks_on_metin, max_s=200) == "cond"
    assert env.world.clicks_on_metin[0][1] is m


def test_q_e_al_reves_se_determina_una_vez_con_el_cono_y_queda_fijo(caplog, tmp_path):
    """Q/E al reves: tres giros leidos con el cono lo invierten UNA vez, se guarda en el archivo de estado y nunca mas se invierte."""
    from bot.keynav import SensesStore
    caplog.set_level(logging.INFO)
    store = SensesStore(tmp_path / "sentidos.json")
    env = env_for(FACING_NORTH, start=(540.0, 150.0), right_key_ok=False, bot_kw={"senses": store})
    bot = env.bot
    bot._img = object()    # la imagen no importa: el cono del simulador sale del mundo
    bot._heading()         # primera lectura: cam_h medido
    for i in range(3):
        bot._rotate_camera("right", 1.0)
        bot._frames += 1   # llego una captura nueva despues del giro
        bot._heading()
    assert bot.keynav.locked_q and bot.keynav.view_right_key == "camera_left"
    assert count(caplog, "Q/E giran al reves") == 1
    assert store.load() == {"view_right_key": "camera_left"}
    for i in range(4):     # fijo: ya con el sentido bien, nada mas se invierte
        bot._rotate_camera("left", 1.0)
        bot._frames += 1
        bot._heading()
    assert bot.keynav.view_right_key == "camera_left" and count(caplog, "Q/E giran al reves") == 1


def test_sentidos_guardados_se_reusan_en_la_proxima_corrida(tmp_path):
    from bot.keynav import SensesStore
    store = SensesStore(tmp_path / "sentidos.json")
    store.save({"view_right_key": "camera_left"})
    env = env_for(FACING_NORTH, right_key_ok=False, bot_kw={"senses": store})
    assert env.bot.keynav.view_right_key == "camera_left" and env.bot.keynav.locked_q


def test_sin_cono_el_rumbo_sale_del_movimiento_medido(caplog):
    """Sin cono legible, `_heading` devuelve el cam_h de keynav (movimiento medido por coordenadas) y no inventa uno."""
    caplog.set_level(logging.INFO)
    env = SimEnv(3, phi=FACING_WEST, heading=False, start=(560.0, 150.0))
    bot = env.bot
    bot._img = object()
    assert bot._heading() is None
    bot.keynav.observe((10.0, 0.0), 1.0)      # un tramo de W hacia el este
    assert bot._heading() == pytest.approx(0.0)
    assert count(caplog, "No se lee el cono") == 1      # el aviso va cada 30 s como maximo


def test_candidatos_vencidos_no_cuentan(caplog):
    caplog.set_level(logging.INFO)
    env = env_for(FACING_WEST)
    env.bot.cands.add((600.0, 150.0), "metin_de_oscuridad", True, -1000.0)   # visto hace mucho
    env.run_until(lambda: any("Vuelta de busqueda" in m for m in msgs(caplog)), max_s=60)
    assert not any("sin nueva vuelta" in m for m in msgs(caplog))


def test_sin_rumbo_legible_vuelve_al_comportamiento_anterior():
    env = SimEnv(3, phi=FACING_WEST, heading=False, start=(560.0, 150.0))
    m = env.world.add_metin((600.0, 150.0), name=OSC)
    assert env.run_until(lambda: not m.alive, max_s=400) == "cond"
    assert len(env.bot.cands) == 0
