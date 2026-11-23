"""Eleccion de metin (etiqueta antes que piedra, piedras bajas al final), hover sin nombre (sin clic, se ignora y se elige el
siguiente), clic que selecciona un monstruo, y "rodeado de monstruos sin avanzar" -> ESPACIO. Todo con input falso."""
import logging

import pytest

import bot.input.base as base
from bot.bot import State
from bot.config import ConfigError, validate_config
from bot.ground import Goal, rank_goals
from bot.vision.panel import PanelInfo
from test_bot_behavior import debug_cfg, far_at, hovers, messages, perc_far, scripted
from test_bot_states import H, INSIDE_XY, OUT_XY, PLAYER, W, Harness, make_cfg, metin, perc

SHAPE = (H, W, 3)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


def _rank(goals):
    return [(g.kind, g.point) for g, _ in rank_goals(goals, PLAYER, SHAPE, make_cfg())]


# ---------------------------------------------------------------- rank_goals
def test_etiqueta_antes_que_piedra_si_la_piedra_no_esta_mucho_mas_cerca():
    label = Goal("etiqueta", "a", (PLAYER[0] + 200, 330))
    stone = Goal("piedra", "piedra", (PLAYER[0] + 120, 380))   # algo mas cerca que la etiqueta, no < 0.4x
    r = rank_goals([stone, label], PLAYER, SHAPE, make_cfg())
    assert r[1][1] < r[0][1] or r[0][0].kind == "etiqueta"
    assert [g.kind for g, _ in r] == ["etiqueta", "piedra"]
    d = {g.kind: dist for g, dist in r}
    assert 0.4 * d["etiqueta"] <= d["piedra"] < d["etiqueta"]


def test_piedra_mucho_mas_cerca_le_gana_a_la_etiqueta():
    label = Goal("etiqueta", "a", (PLAYER[0] + 600, 260))
    stone = Goal("piedra", "piedra", (PLAYER[0] + 60, 400))
    r = rank_goals([label, stone], PLAYER, SHAPE, make_cfg())
    d = {g.kind: dist for g, dist in r}
    assert d["piedra"] < 0.4 * d["etiqueta"]
    assert [g.kind for g, _ in r] == ["piedra", "etiqueta"]


def test_piedra_baja_sin_etiqueta_va_al_final_aunque_sea_la_mas_cercana():
    low = Goal("piedra", "piedra", (PLAYER[0] + 50, 800))      # base por debajo de 0.55 del alto: cerca del personaje
    high = Goal("piedra", "piedra", (PLAYER[0] + 300, 300))
    label = Goal("etiqueta", "a", (PLAYER[0] - 500, 250))
    assert _rank([low, high, label]) == [("etiqueta", label.point), ("piedra", high.point), ("piedra", low.point)]
    assert _rank([low]) == [("piedra", low.point)]             # sin otro candidato, se considera (con hover)


# ---------------------------------------------------------------- hover sin nombre
def test_piedra_sin_nombre_sin_panel_en_2_s_la_ignora_y_va_a_la_siguiente(caplog):
    """Decision simple: clic directo (sin hover) en la piedra mas cercana; si no aparece NINGUN panel en 2 s se ignora esa
    posicion (approach.no_label_ignore_s) y se hace clic en la siguiente."""
    caplog.set_level(logging.INFO)
    near, far = far_at(1300, 548), far_at(500, 300)

    def script(h, n):
        h.clock.t += 0.3
        return perc_far(far=[near, far])
    h = Harness(script)
    h.run_until(lambda: len(h.bot.ignored) == 1, limit=60)
    exp = h.bot.ignored[0].expiry - h.clock()
    assert abs(exp - float(h.cfg["approach"]["no_label_ignore_s"])) < 5.0
    assert hovers(h) == 0 and h.clicks() == 2       # en el mismo ciclo, clic directo en la siguiente
    assert h.bot.state is State.ATACAR and State.ACERCARSE not in h.states()


def test_piedra_ignorada_por_no_tener_panel_no_se_vuelve_a_elegir():
    near = far_at(1300, 548)

    def script(h, n):
        h.clock.t += 0.3
        return perc_far(far=[near])
    h = Harness(script)
    h.run_until(lambda: h.bot.ignored, limit=60)
    for _ in range(20):
        h.bot.step()
    assert State.ACERCARSE not in h.states() and h.states().count(State.ATACAR) == 1


def test_hover_con_etiqueta_sigue_clickeando_el_metin_objetivo():
    fx = far_at(1300, 548)

    def script(h, n):
        if h.bot.state is State.ACERCARSE and hovers(h) >= 1:
            return perc(metins=[metin(click=(1300, 540))])
        return perc_far(far=[fx])
    h = Harness(script)
    h.run_until(lambda: State.ATACAR in h.states())
    assert h.clicks() == 1


# ---------------------------------------------------------------- clic que selecciona un monstruo
def test_clic_sobre_piedra_que_selecciona_monstruo_la_ignora_y_elige_la_siguiente(caplog):
    caplog.set_level(logging.INFO)
    a, b = far_at(1300, 548), far_at(500, 300)
    other = PanelInfo(present=True, kind="other", name=None)
    def script(h, n):
        h.clock.t += 0.3
        return perc_far(panel=other if h.bot.state is State.ATACAR else None, far=[a, b])
    h = Harness(script)
    h.run_until(lambda: len(h.bot.ignored) == 1, limit=60)   # clic directo a la piedra mas cercana (a): el panel es otra cosa
    assert any("el clic selecciono otra cosa (other)" in m for m in messages(caplog))
    assert h.bot._cur is None
    h.run_until(lambda: h.clicks() >= 2, limit=60)            # despues, clic directo a la siguiente (b): sin hover
    assert State.ACERCARSE not in h.states()


def test_clic_sobre_etiqueta_que_selecciona_monstruo_no_deja_metin_en_curso():
    other = PanelInfo(present=True, kind="other", name=None)

    def script(h, n):
        return perc_far(panel=other, metins=[metin()]) if h.bot.state is State.ATACAR else perc(metins=[metin()])
    h = Harness(script)
    h.run_until(lambda: State.ATACAR in h.states())
    h.bot.step()
    assert h.bot._cur is None


# ---------------------------------------------------------------- rodeado sin avanzar
def _surrounded_script(n_near=2):
    return lambda h, n: perc_far(coords=OUT_XY, n_near=n_near)


def test_rodeado_volviendo_usa_espacio_y_sigue_en_el_mismo_estado(caplog):
    caplog.set_level(logging.INFO)
    h = Harness(_surrounded_script())
    h.run_until(lambda: h.bot.state is State.VOLVER)
    h.run_until(lambda: h.keys("space") >= 1)
    assert h.bot.state is State.VOLVER and State.DESTRABAR not in h.states()
    assert any("Rodeado de 2 monstruos sin avanzar: ESPACIO y sigo" in m for m in messages(caplog))
    assert h.bot.stats.surrounded == 1
    assert "1 ESPACIO por rodeado" in h.bot.stats.summary()


def test_rodeado_repetido_sin_avanzar_termina_en_destrabar():
    h = Harness(_surrounded_script(), stuck={"window_s": 1000.0})   # sin el detector de traba de la caminata
    h.run_until(lambda: State.DESTRABAR in h.states(), limit=800)
    assert h.bot.stats.surrounded == int(h.cfg["stuck"]["surrounded_max"])
    assert h.keys("space") >= h.bot.stats.surrounded


def test_sin_monstruos_suficientes_no_se_usa_espacio_por_rodeado():
    h = Harness(_surrounded_script(n_near=1))
    for _ in range(80):
        h.bot.step()
    assert h.bot.stats.surrounded == 0


def test_rodeado_no_aplica_si_el_personaje_avanza():
    def script(h, n):
        return perc_far(coords=(300 + n, 150), n_near=3)   # se mueve de verdad (1 unidad por ciclo)
    h = Harness(script)
    for _ in range(80):
        h.bot.step()
    assert h.bot.stats.surrounded == 0


def test_rodeado_en_explorar_con_barrido_no_aplica():
    """En la fase de barrido de camara el personaje no camina: quieto es lo normal."""
    h = Harness(lambda h, n: perc_far(coords=INSIDE_XY, n_near=3))
    h.run_until(lambda: h.bot.state is State.EXPLORAR)
    assert h.bot._ex.phase == "sweep" and not h.bot._walking_now()


# ---------------------------------------------------------------- config
def test_config_nueva_validacion():
    for sec, key, bad in (("approach", "unlabeled_closer_ratio", -1), ("approach", "no_label_ignore_s", "x"),
                          ("attack", "keep_with_others_s", -1), ("stuck", "fast_s", 0),
                          ("stuck", "surrounded_max", 0), ("debug", "save_raw", 1)):
        cfg = make_cfg()
        cfg[sec][key] = bad
        with pytest.raises(ConfigError):
            validate_config(cfg)
    cfg = make_cfg()
    cfg["vision"]["far_metin"]["max_aspect"] = 0
    with pytest.raises(ConfigError):
        validate_config(cfg)
    validate_config(make_cfg())


# ---------------------------------------------------------------- debug: captura cruda
def test_debug_guarda_la_captura_cruda_png_junto_a_cada_evento(tmp_path):
    import cv2
    h = Harness(scripted, cfg=debug_cfg(tmp_path))
    for _ in range(120):
        h.bot.step()
    d = h.bot.debug.run_dir
    jpgs, raws = sorted(d.glob("*.jpg")), sorted(d.glob("*_raw.png"))
    assert jpgs and [j.stem + "_raw.png" for j in jpgs] == [r.name for r in raws]
    raw, ann = cv2.imread(str(raws[0])), cv2.imread(str(jpgs[0]))
    assert raw is not None and raw.shape == ann.shape
    assert (raw != ann).any()   # la anotada tiene dibujos; la cruda no


def test_debug_save_raw_apagado_y_tope(tmp_path):
    h = Harness(scripted, cfg=debug_cfg(tmp_path, save_raw=False))
    for _ in range(60):
        h.bot.step()
    assert not list(h.bot.debug.run_dir.glob("*_raw.png"))
    h2 = Harness(scripted, cfg=debug_cfg(tmp_path / "otro", max_images=2))
    for _ in range(60):
        h2.bot.step()
    assert len(list(h2.bot.debug.run_dir.glob("*_raw.png"))) == 2 == len(list(h2.bot.debug.run_dir.glob("*.jpg")))
