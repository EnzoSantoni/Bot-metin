"""Fase 6b: reglas A-E del juego, episodios de traba, ACERCARSE, diagnostico y modo debug (simulador y reloj falso)."""
import logging
import math
import re

import pytest

import bot.input.base as base
from bot.bot import Bot, State, _Spot
from bot.geometry import Rect
from bot.tracking import StuckEpisode, angle_sep
from bot.vision.coords import Coords
from bot.vision.far_metin import FarMetin
from bot.vision.metin import MetinDetection
from sim_bot_env import SimEnv, make_cfg as sim_cfg
from sim_game import Box, edge_cliffs
from test_bot_states import INSIDE_XY, OUT_XY, PLAYER, TOL_XY, Harness, make_cfg, metin, perc

OSC, AVA = "metin_de_oscuridad", "metin_de_avaricia"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


def far_at(x, y, allowed=True):
    return FarMetin(Rect(x - 25, y - 60, 50, 70), (x, y), 0.9, allowed)


def perc_far(far=(), n_near=0, **kw):
    p = perc(**kw)
    p.far_metins, p.monsters_near = list(far), n_near
    return p


def messages(caplog):
    return [r.getMessage() for r in caplog.records]


# ---------------------------------------------------------------- StuckEpisode (logica pura)
def test_episodio_con_monstruos_espacio_primero_hasta_fallar_dos_veces_y_despues_rota():
    """I3: con monstruos cerca al empezar el intento, ESPACIO; la rotacion MIRAR/ATRAS solo despues de 2 ESPACIO fallidos."""
    ep = StuckEpisode(1, (0, 0), 0.0, 0.0)
    seq = []
    for _ in range(8):
        s = ep.next_strategy(True)
        ep.start_attempt(s, 0.0)
        seq.append(s)
    assert seq == ["ESPACIO", "ESPACIO", "MIRAR", "ESPACIO", "ESPACIO", "ATRAS", "ESPACIO", "ESPACIO"]
    # sin monstruos al empezar el intento no se usa ESPACIO aunque el episodio lo haya hecho antes
    assert ep.next_strategy(False) in ("MIRAR", "ATRAS")


def test_episodio_sin_monstruos_nunca_usa_espacio():
    ep = StuckEpisode(1, (0, 0), 0.0, 0.0)
    seq = []
    for _ in range(6):
        s = ep.next_strategy(False)
        ep.start_attempt(s, 0.0)
        seq.append(s)
    assert seq[0] == "MIRAR" and "ESPACIO" not in seq and all(a != b for a, b in zip(seq, seq[1:]))


def test_separacion_compara_en_el_mismo_marco_o_por_angulo_de_juego():
    ep = StuckEpisode(1, (0, 0), 0.0, 0.0)
    ep.add_dir(frame=1, screen=0.0, game=None)
    assert ep.separation(1, math.radians(30), None) == pytest.approx(math.radians(30))
    assert ep.separation(2, 0.0, None) == pytest.approx(0.0)                      # otro marco, sin angulo de juego: compara pantalla
    ep.add_dir(frame=3, screen=1.0, game=math.radians(90))
    assert ep.separation(4, 5.0, math.radians(100)) == pytest.approx(math.radians(10))
    assert angle_sep(math.radians(350), math.radians(10)) == pytest.approx(math.radians(20))


def test_mirar_elige_direcciones_separadas_al_menos_60_grados():
    h = Harness(lambda h, n: perc())
    ep = StuckEpisode(1, INSIDE_XY, 0.0, 0.0)
    picked = []
    for _ in range(4):
        a = h.bot._pick_direction(ep)
        if picked:
            assert min(angle_sep(a, b) for b in picked) >= math.radians(60) - 1e-9
        picked.append(a)
        ep.add_dir(h.bot.camera.rotations, a, None)


def test_mirar_con_camara_lista_apunta_hacia_el_interior():
    h = Harness(lambda h, n: perc(coords=(442, 168)))   # pegado al borde oeste
    h.train_camera()
    h.bot.tracker.update(Coords(442, 168, 1.0), h.clock())
    ep = StuckEpisode(1, (442, 168), 0.0, 0.0)
    for _ in range(4):
        a = h.bot._pick_direction(ep)
        game = h.bot._game_angle(a)
        end = (442 + 45 * math.cos(game), 168 + 45 * math.sin(game))
        assert h.bot.zone.signed_distance(end) >= h.bot.zone.signed_distance((442, 168))
        ep.add_dir(h.bot.camera.rotations, a, game)


# ---------------------------------------------------------------- A: rodeado -> ESPACIO primero -> se libera
def test_regla_a_rodeado_explorando_usa_espacio_primero_y_se_libera(caplog):
    caplog.set_level(logging.INFO)
    env = SimEnv(2, start=(480, 150), cfg=sim_cfg())
    m = env.world.add_metin((650, 150))
    env.world.schedule_surround(8.0, 3, 1)
    env.run_until(lambda: not m.alive, max_s=300)
    assert not m.alive and env.world.space_pushes >= 1 and env.world.surround == 0
    rod = [x for x in messages(caplog) if x.startswith("Rodeado de")]   # ESPACIO sin pasar por DESTRABAR
    assert rod and "3 monstruos sin avanzar: ESPACIO y sigo" in rod[0]
    assert env.bot.stats.surrounded >= 1


def test_regla_a_el_bot_no_avanza_mientras_esta_rodeado():
    env = SimEnv(2, start=(480, 150))
    env.world.add_metin((650, 150))
    env.world.surround_now(3, 1)
    env.world.free_until = 0.0
    p0 = tuple(env.world.pos)
    env.run_until(lambda: env.world.space_pushes >= 1, max_s=120)
    assert env.world.space_pushes >= 1
    assert math.dist(p0, tuple(env.world.pos)) < 1.0 or env.world.t > 3.0   # quieto hasta el primer ESPACIO


# ---------------------------------------------------------------- B: confirmado y bloqueado / seleccion equivocada
def test_regla_b_confirmado_bloqueado_usa_espacio_y_reclic(caplog):
    caplog.set_level(logging.INFO)
    env = SimEnv(2, start=(560, 150))
    m = env.world.add_metin((600, 150))
    env.world.schedule_surround(12.0, 3, 1)
    env.run_until(lambda: not m.alive, max_s=300)
    assert not m.alive and env.world.space_pushes >= 1
    msgs = messages(caplog)
    assert any("Trabado yendo al metin" in x and "ESPACIO y reclic" in x for x in msgs)
    # el metin sigue seleccionado y tras el empujon la vida vuelve a bajar: pegando no se reclickea
    assert any("Sin reclic (trabado" in x for x in msgs)
    assert not any(x.startswith("Reclic en") for x in msgs)
    assert len(env.world.clicks_on_metin) == 1


def test_regla_b_monstruo_seleccionado_espacio_clic_directo_sin_escape(caplog):
    caplog.set_level(logging.INFO)
    env = SimEnv(2, start=(560, 150))
    m = env.world.add_metin((600, 150))
    env.world.surround_now(3, 1)
    env.run_until(lambda: not m.alive, max_s=300)
    assert not m.alive and env.world.space_pushes >= 1
    kinds = [k for _, k, _ in env.world.selected_log]
    assert kinds[0] == "monster" and kinds[-1] == "metin"
    assert any("Seleccion equivocada con" in x for x in messages(caplog))
    assert not any(e == ("key_down", "esc") for e in env.backend.events)


def test_regla_b_monstruo_seleccionado_en_metin_con_nombre_no_se_descarta():
    """Un metin con nombre (etiqueta) no se descarta por seleccion equivocada repetida: se reintenta y, agotados los clics sin
    confirmar (`approach.max_clicks`), se aparta unos segundos (no 10 minutos) para elegir otro."""
    h = Harness(lambda h, n: perc_far(panel=__import__("bot.vision.panel", fromlist=["PanelInfo"]).PanelInfo(
        present=True, kind="other", name=None), n_near=3, metins=[metin()]) if h.bot.state is State.ATACAR
        else perc(metins=[metin()]), attack={"max_wrong_selections": 2})
    h.run_until(lambda: any(w.startswith("sin confirmar:") for _, w in h.seen), limit=400)
    assert not any("seleccion equivocada repetida" in w for _, w in h.seen)
    assert h.keys("space") >= 1 and h.keys("esc") == 0
    assert all(s.expiry - h.clock.t <= 31 for s in h.bot.discarded)   # apartado ~30 s, no el descarte de 10 minutos


def test_regla_b_clic_a_piedra_sin_nombre_que_selecciona_monstruo_se_ignora():
    """Una piedra SIN etiqueta que al clickearla muestra un monstruo si se confirma que no es un metin: se ignora esa posicion."""
    from bot.vision.panel import PanelInfo
    h = Harness(lambda h, n: perc_far(panel=PanelInfo(present=True, kind="other", name=None), metins=[metin()])
                if h.bot.state is State.ATACAR else perc(metins=[metin()]), attack={"max_wrong_selections": 2})
    h.run_until(lambda: h.bot.state is State.ATACAR)
    a = h.bot._atk
    a.stone = True   # el clic fue sobre una piedra sin etiqueta
    h.run_until(lambda: any("se selecciono otra cosa" in w or "seleccion equivocada repetida" in w for _, w in h.seen), limit=200)
    assert h.bot.ignored or h.bot.discarded


def test_bloqueo_se_detecta_quieto_o_en_circulo_pero_no_caminando_recto():
    h = Harness(lambda h, n: perc())
    b = h.bot
    from bot.bot import _Attack
    a = _Attack(0.0, (1, 1), None, 0.0, 0.0, None)
    win = float(b.atk["blocked_s"])
    a.trail = [(i * 0.5, (550, 150)) for i in range(int(win / 0.5) + 1)]
    assert b._blocked_why(a, win) == "quieto"
    ring = [(550, 150), (553, 150), (553, 153), (550, 153)] * 4
    a.trail = [(i * win / (len(ring) - 1), pt) for i, pt in enumerate(ring)]
    assert b._blocked_why(a, win) == "girando en circulo"
    a.trail = [(i * 0.5, (550 + 3 * i, 150)) for i in range(int(win / 0.5) + 1)]
    assert b._blocked_why(a, win) is None


# ---------------------------------------------------------------- C / D: pared sin monstruos, trabas repetidas
def _clicks_in_destrabar(env):
    """Posiciones de clic (pantalla) mandadas mientras el bot estaba en DESTRABAR."""
    out = []
    for t, e in zip(env.backend.event_t, env.backend.events):
        if e[0] == "move_to" and env.state_at(t) == "DESTRABAR" and (not out or out[-1][0] != (t, e[1], e[2])):
            out.append(((t, e[1], e[2]), (e[1], e[2])))   # el backend anota el move_to dos veces por clic
    return [(k[0], xy) for k, xy in out]


def test_regla_c_pared_sin_monstruos_mira_y_camina_en_direcciones_distintas(caplog):
    import numpy as np
    caplog.set_level(logging.INFO)
    env = SimEnv(3, start=(400, 150))
    env.world.obstacles.append(Box(410, -3000, 416, 3000))
    clicks = []   # (vector de pantalla, rumbo REAL en el juego segun el simulador)
    orig = env.bot._click_at_angle

    def spy(p, t, ang):
        v = orig(p, t, ang)
        if v is not None:
            g = env.world._m() @ np.asarray(v)
            clicks.append((v, math.atan2(g[1], g[0])))
        return v
    env.bot._click_at_angle = spy
    env.run_until(max_s=600)
    msgs = messages(caplog)
    assert any("estrategia MIRAR" in x and "monstruos cerca: 0" in x for x in msgs)
    assert not any("estrategia ESPACIO" in x for x in msgs)
    assert len(clicks) >= 6
    for (va, ga), (vb, gb) in zip(clicks, clicks[1:]):   # nunca dos intentos seguidos hacia el mismo lugar
        assert math.dist(va, vb) >= 15, (va, vb)
        assert angle_sep(ga, gb) >= math.radians(20), (math.degrees(ga), math.degrees(gb))
    assert len(_clicks_in_destrabar(env)) >= 3


def test_regla_d_trabas_repetidas_van_al_centro_y_el_destrabe_persiste(caplog):
    caplog.set_level(logging.INFO)
    h = Harness(lambda h, n: perc(coords=OUT_XY))    # no se mueve nunca: pared infinita
    h.train_camera()
    h.run_until(lambda: State.CENTRO in h.states())
    attempts = [x for x in messages(caplog) if x.startswith("Destrabe: episodio 1,")]
    # el contador avanza entre reentradas a VOLVER (antes era siempre "intento 1/4")
    assert [re.search(r"intento (\d)/4", x).group(1) for x in attempts] == ["1", "2", "3", "4"]
    strategies = [re.search(r"estrategia (\w+)", x).group(1) for x in attempts]
    assert all(a != b for a, b in zip(strategies, strategies[1:]))
    assert any(w.startswith("episodio de traba 1: 4 intentos") for s, w in h.seen if s is State.CENTRO)
    assert any("Voy al centro" in x for x in messages(caplog))


def test_regla_d_tres_episodios_seguidos_van_al_centro():
    env = SimEnv(3, start=(400, 150))
    env.world.obstacles.append(Box(410, -3000, 416, 3000))
    env.run_until(lambda: any(t.new == "CENTRO" for t in env.transitions), max_s=200)
    tr = next(t for t in env.transitions if t.new == "CENTRO")
    assert "3 trabas en" in tr.reason or "intentos" in tr.reason
    assert env.bot.stats.centros >= 1 and env.bot.stats.episodes >= 3


def test_escenario_del_log_real_borde_con_risco_no_repite_el_ciclo_volver_destrabar(caplog):
    """Afuera del poligono con un risco a lo largo del borde: antes VOLVER <-> DESTRABAR sin fin ('intento 1/4')."""
    caplog.set_level(logging.INFO)
    env = SimEnv(4, start=(425, 168))
    env.world.obstacles.extend(edge_cliffs(env.zone.polygon, 6.0, skip=(2,)))   # el risco deja libre el borde sur
    env.run_until(lambda: env.zone.signed_distance(tuple(env.world.pos)) > 0 or env.bot.finished, max_s=500)
    inside = env.zone.signed_distance(tuple(env.world.pos)) > 0
    assert inside or (env.bot.finished and env.bot.stats.end_reason in ("no puede volver a la zona", "no puede llegar al centro"))
    assert env.world.t < 450
    if not inside:
        assert env.bot.stats.centros >= 1
    assert env.count_unstucks() < 40


def test_centro_llega_al_centro_y_despues_barre_y_explora():
    env = SimEnv(5, start=(470.0, 160.0))
    env.bot._enter(State.CENTRO, "test")
    env.run_until(lambda: env.bot.state in (State.EXPLORAR, State.FIN), max_s=200)
    c = env.bot.navigator.return_target((470, 160))
    assert env.bot.state is State.EXPLORAR
    assert math.dist(tuple(env.world.pos), c) <= env.cfg["center"]["arrive_dist"] + 2.0
    env.run_until(lambda: env.bot._ex is not None and env.bot._ex.phase == "walk", max_s=60)
    assert sum(1 for e in env.backend.events if e in (("key_down", "q"), ("key_down", "e"))) >= env.cfg["explore"]["camera_steps"]


# ---------------------------------------------------------------- E: exploracion en tramos y barridos
def test_regla_e_destinos_bien_adentro_y_rumbos_distintos():
    env = SimEnv(6, start=(520.0, 150.0))
    env.cfg["explore"]["center_laps"] = 0   # esta regla es de los tramos al azar (el ultimo recurso, tras las vueltas en el centro)
    targets, headings = [], []
    orig = env.bot.navigator.leg_target

    def spy(pos, recent, min_sep, leg_units, visited=()):
        out = orig(pos, recent, min_sep, leg_units, visited)
        targets.append((pos, out[0]))
        headings.append(out[1])
        return out
    env.bot.navigator.leg_target = spy
    env.run_until(max_s=400)
    margin = env.cfg["explore"]["border_margin"]
    assert len(targets) >= 8
    assert all(env.zone.signed_distance(t) >= margin - 1e-6 for _, t in targets)
    # nunca dos tramos seguidos con el mismo rumbo (salvo que la franja no deje otra)
    same = sum(1 for a, b in zip(headings, headings[1:]) if angle_sep(a, b) < math.radians(10))
    assert same <= len(headings) // 3


def test_regla_e_tramo_nuevo_desde_el_borde_arranca_hacia_adentro():
    env = SimEnv(7, start=(520.0, 150.0))
    nav, zone = env.bot.navigator, env.zone
    for seed in range(40):
        nav.rng.seed(seed)
        pos = (445.0 + seed, 192.0 - seed * 0.4) if seed % 2 else (700.0 - seed, 110.0 + seed * 0.5)
        if zone.signed_distance(pos) < 2:
            continue
        tgt, _ = nav.leg_target(pos, [], math.radians(60), 45.0)
        assert zone.signed_distance(tgt) >= env.cfg["explore"]["border_margin"] - 1e-6 or zone.signed_distance(tgt) > zone.signed_distance(pos)


def test_regla_e_barrido_y_tramos_encuentran_un_metin_lejano():
    env = SimEnv(2, start=(480, 140))
    m = env.world.add_metin((720, 130))        # a 240 u: no se ve desde el principio
    env.run_until(lambda: not m.alive or any(t.new == "ATACAR" for t in env.transitions), max_s=600)
    assert any(t.new == "ATACAR" for t in env.transitions)
    assert sum(1 for e in env.backend.events if e in (("key_down", "q"), ("key_down", "e"))) >= env.cfg["explore"]["camera_steps"]
    assert not any(t.new == "ACERCARSE" for t in env.transitions)   # decision simple: clic directo, sin hover


def test_sin_ningun_objetivo_en_el_tiempo_maximo_va_al_centro():
    h = Harness(lambda h, n: perc(), explore={"max_without_metin_s": 8.0})
    h.run_until(lambda: State.CENTRO in h.states())
    assert any(s is State.CENTRO and "sin ningun objetivo" in w for s, w in h.seen)


# ---------------------------------------------------------------- ACERCARSE
def test_acercarse_sin_camara_y_jugador_fuera_no_se_acerca():
    h = Harness(lambda h, n: perc_far(coords=OUT_XY, far=[far_at(1300, 548)]))
    h.bot.step()
    assert State.ACERCARSE not in h.states() and h.clicks() == 0
    assert h.states()[0] is State.VOLVER


def hovers(h):
    """Movimientos del mouse sin clic (hover): los move_to del backend falso menos los 2 que emite cada clic."""
    return sum(1 for e in h.be.events if e[0] == "move_to") - 2 * h.clicks()


# ---------------------------------------------------------------- diagnostico (sin spam)
def label(name, target, allowed=True, click=(1000, 500)):
    return MetinDetection(Rect(click[0] - 40, click[1] - 70, 80, 10), name, 1.0, target, click, allowed)


def test_log_del_motivo_de_cada_metin_no_elegido_sin_spam(caplog):
    caplog.set_level(logging.INFO)
    h = Harness(lambda h, n: perc(metins=[label(AVA, False), label(OSC, True, allowed=False, click=(400, 300))]))
    for _ in range(12):
        h.bot.step()
    msgs = [x for x in messages(caplog) if x.startswith("No elegido:")]
    # no se decide por el nombre: la etiqueta de Avaricia se cliquea (decide el panel); el clic sobre la interfaz no se manda
    assert msgs.count("No elegido: clic no permitido (interfaz)") == 1 and not any("tipo ignorado" in x for x in msgs)


# ---------------------------------------------------------------- DebugRecorder
def debug_cfg(tmp_path, **dbg):
    return make_cfg(debug={"enabled": True, "dir": str(tmp_path), "max_images": 50, "explore_every_s": 0.5, **dbg})


def scripted(h, n):
    st = h.bot.state
    if st is State.BUSCAR and n < 3:   # (la de (400, 300) cae sobre la interfaz: "no elegido")
        return perc(metins=[metin(), label(OSC, True, allowed=False, click=(400, 300))])
    if st is State.ATACAR:
        return perc()
    return perc()


def test_debug_recorder_escribe_una_imagen_por_evento(tmp_path):
    h = Harness(scripted, cfg=debug_cfg(tmp_path))
    for _ in range(300):
        h.bot.step()
    d = h.bot.debug.run_dir
    assert d is not None and d.parent == tmp_path and d.name.startswith("run_")
    files = sorted(d.glob("*.jpg"))
    assert len(files) >= 4 and all(re.fullmatch(r"\d{4}_[a-z]+_[a-z0-9_]+\.jpg", f.name) for f in files)
    lines = (d / "events.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(files) and all(f.name in l for f, l in zip(files, lines))
    kinds = " ".join(lines)
    assert "estado pos=" in kinds and "descarte pos=" in kinds     # cambio de estado y metin no elegido
    assert "explorar pos=" in kinds                                # imagen periodica en EXPLORAR
    import cv2
    img = cv2.imread(str(files[0]))
    assert img is not None and img.shape[:2] == (1057, 1858)


def test_debug_recorder_respeta_el_tope(tmp_path):
    h = Harness(scripted, cfg=debug_cfg(tmp_path, max_images=2))
    for _ in range(40):
        h.bot.step()
    assert len(list(h.bot.debug.run_dir.glob("*.jpg"))) == 2 == h.bot.debug.count


def test_debug_recorder_deshabilitado_no_escribe(tmp_path):
    h = Harness(scripted, cfg=make_cfg(debug={"enabled": False, "dir": str(tmp_path)}))
    for _ in range(10):
        h.bot.step()
    assert list(tmp_path.iterdir()) == [] and h.bot.debug.run_dir is None


def test_debug_recorder_si_imwrite_falla_el_loop_sigue(tmp_path, monkeypatch):
    import bot.debug_draw as dd

    def boom(*a, **k):
        raise OSError("disco lleno")
    monkeypatch.setattr(dd.cv2, "imwrite", boom)
    h = Harness(scripted, cfg=debug_cfg(tmp_path))
    for _ in range(30):
        h.bot.step()     # no lanza
    assert h.bot.debug.count == 0 and h.bot.stats.elapsed_s >= 0
    monkeypatch.setattr(dd.cv2, "imwrite", lambda *a, **k: False)   # imwrite que devuelve False tampoco rompe
    for _ in range(5):
        h.bot.step()


def test_debug_recorder_falla_el_dibujo_y_el_loop_sigue(tmp_path, monkeypatch):
    import bot.debug_draw as dd
    monkeypatch.setattr(dd, "annotate_perception", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    h = Harness(scripted, cfg=debug_cfg(tmp_path))
    for _ in range(20):
        h.bot.step()
    assert h.bot.debug.count == 0


def test_detect_preview_sigue_usando_el_dibujo_comun():
    import detect_preview
    import bot.debug_draw as dd
    assert detect_preview.annotate is dd.annotate and detect_preview.draw_items is dd.draw_items


# ---------------------------------------------------------------- percepcion
def test_perceive_suma_piedras_lejanas_y_monstruos_con_las_partes_nuevas(load_sample):
    from bot.perception import perceive
    from bot.vision.monsters import count_near
    cfg = make_cfg()
    img = load_sample("real_rodeado.png")
    p = perceive(img, cfg, parts={"metins", "coords", "far", "monsters"})
    assert p.monsters_near == count_near(p.monsters, p.player_xy, cfg=cfg)
    assert all(isinstance(f, FarMetin) for f in p.far_metins)
    # la piedra de una etiqueta visible no cuenta como piedra lejana
    from bot.perception import label_exclusions
    ex = label_exclusions(p.metins, cfg)
    assert not any(r.contains(*f.center) for f in p.far_metins for r in ex)
    p2 = perceive(img, cfg, parts={"coords"})
    assert p2.far_metins == [] and p2.monsters == [] and p2.monsters_near == 0
    with pytest.raises(ValueError):
        perceive(img, cfg, parts={"lejos"})


# ---------------------------------------------------------------- config nueva
def test_config_de_comportamiento_validada():
    import copy
    from bot.config import DEFAULTS, ConfigError, validate_config
    validate_config(copy.deepcopy(DEFAULTS))
    for sec, key, val in [("stuck", "resolve_dist", 0), ("stuck", "repeat_max", 0), ("approach", "max_seconds", 0),
                          ("approach", "lost_cycles", 0), ("center", "arrive_dist", 0), ("explore", "leg_units", 0),
                          ("explore", "max_without_metin_s", 0), ("attack", "blocked_s", 0), ("debug", "max_images", 0),
                          ("debug", "jpeg_quality", "x")]:
        cfg = copy.deepcopy(DEFAULTS)
        cfg[sec][key] = val
        with pytest.raises(ConfigError):
            validate_config(cfg)
