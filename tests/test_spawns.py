"""Tests de bot/spawns.py y de su uso en el bot (EXPLORAR hacia puntos conocidos). Todo en tmp_path."""
import json
import logging
from pathlib import Path

from bot.bot import State
from bot.spawns import SpawnMap
from test_bot_states import INSIDE_XY, Harness, metin, perc, target_panel

ALL = lambda xy: True  # noqa: E731


class Wall:
    """Reloj de pared falso (epoch s)."""
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def make_map(tmp_path, wall=None, **kw):
    return SpawnMap(tmp_path / "mapa.json", kw.pop("merge_u", 6.0), clock_wall=wall or Wall(), **kw)


def test_une_puntos_cercanos_con_promedio_ponderado(tmp_path):
    m = make_map(tmp_path)
    m.record_broken((100.0, 100.0))
    m.record_broken((104.0, 100.0))
    assert len(m) == 1 and m.spots[0].n == 2 and abs(m.spots[0].x - 102.0) < 1e-9
    m.record_broken((130.0, 100.0))
    assert len(m) == 2


def test_persiste_y_recarga(tmp_path):
    w = Wall()
    m = make_map(tmp_path, w)
    m.record_broken((100.0, 50.0))
    data = json.loads((tmp_path / "mapa.json").read_text(encoding="utf-8"))
    assert data["version"] == 2 and len(data["spots"]) == 1
    assert not list(tmp_path.glob("*.tmp"))
    m2 = make_map(tmp_path, w)
    assert len(m2) == 1 and m2.spots[0].xy == (100.0, 50.0) and m2.spots[0].last_broken == w.t


def test_archivo_roto_se_respalda_y_empieza_vacio(tmp_path, caplog):
    f = tmp_path / "mapa.json"
    f.write_text("{esto no es json", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        m = make_map(tmp_path)
    assert len(m) == 0 and any(r.levelno == logging.WARNING for r in caplog.records)
    assert f.read_text(encoding="utf-8") == "{esto no es json"   # sin pisar hasta el primer guardado
    m.record_broken((1.0, 2.0))
    assert (tmp_path / "mapa.json.bak").read_text(encoding="utf-8") == "{esto no es json"
    assert len(make_map(tmp_path)) == 1


def test_desactivado_no_lee_ni_escribe(tmp_path):
    m = SpawnMap(None, 6.0)
    m.record_broken((1.0, 1.0))
    assert len(m) == 1 and list(tmp_path.iterdir()) == []


def test_solo_lectura_no_escribe(tmp_path):
    make_map(tmp_path).record_broken((1.0, 1.0))
    before = (tmp_path / "mapa.json").read_text(encoding="utf-8")
    m = SpawnMap(tmp_path / "mapa.json", 6.0, read_only=True)
    m.record_broken((50.0, 50.0))
    assert (tmp_path / "mapa.json").read_text(encoding="utf-8") == before


def test_elegibilidad_por_respawn_revisit_y_zona(tmp_path):
    w = Wall()
    m = make_map(tmp_path, w, respawn_s=240, revisit_s=120)
    s = m.record_broken((10.0, 10.0))
    assert m.next_target((0, 0), ALL) is None                 # recien roto: no reaparecio
    assert 239 < m.seconds_until_ready(ALL) <= 240
    w.t += 239
    assert m.next_target((0, 0), ALL) is None
    w.t += 2
    assert m.next_target((0, 0), ALL) is s
    assert m.next_target((0, 0), lambda xy: False) is None     # fuera de la zona
    m.mark_visited(s)
    assert m.next_target((0, 0), ALL) is None                  # visitado hace poco
    w.t += 121
    assert m.next_target((0, 0), ALL) is s


def test_recorrido_visita_el_mas_cercano_y_luego_el_siguiente(tmp_path):
    w = Wall()
    m = make_map(tmp_path, w)
    a, b, c = (m.record_broken(p) for p in ((10.0, 0.0), (40.0, 0.0), (100.0, 0.0)))
    w.t += 1000
    pos = (0.0, 0.0)
    order = []
    for _ in range(3):
        s = m.next_target(pos, ALL)
        order.append(s)
        m.mark_visited(s)
        pos = s.xy
    assert order == [a, b, c] and m.next_target(pos, ALL) is None


# ---------------------------------------------------------------- bot

def spawn_harness(tmp_path, script, wall=None, **over):
    wall = wall or Wall()
    sm = SpawnMap(tmp_path / "mapa.json", 6.0, clock_wall=wall)
    h = Harness(script, bot_kwargs={"spawn_map": sm}, **over)
    return h, sm, wall


def test_bot_guarda_el_punto_al_romper_un_metin(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    w = {"atk": 0}

    def script(h, n):
        st = h.bot.state
        if st is State.BUSCAR:
            return perc(metins=[metin()])
        if st is State.ATACAR:
            w["atk"] += 1
            if w["atk"] == 4:
                h.clock.t += 20.0   # un metin real tarda mas de attack.min_break_s en romperse
            return perc(panel=target_panel([0.9, 0.5, 0.05][w["atk"] - 1]) if w["atk"] <= 3 else None)
        return perc()

    h, sm, _ = spawn_harness(tmp_path, script)
    h.run_until(lambda: State.RECOGER in h.states())
    assert len(sm) == 1
    assert abs(sm.spots[0].x - INSIDE_XY[0]) < 3 and abs(sm.spots[0].y - INSIDE_XY[1]) < 3
    assert (tmp_path / "mapa.json").is_file()
    assert any("Mapa de metin: guardo punto" in r.message for r in caplog.records)


def _explorar_hasta_tramo(h):
    h.run_until(lambda: h.bot._ex is not None and h.bot._ex.phase == "walk" and h.bot._ex.target is not None)


def test_explorar_va_al_punto_conocido_en_vez_de_al_azar(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    wall = Wall()
    sm = SpawnMap(tmp_path / "mapa.json", 6.0, clock_wall=wall)
    far = sm.record_broken((700.0, 120.0))
    near = sm.record_broken((600.0, 160.0))
    wall.t += 1000
    h, _, _ = spawn_harness(tmp_path, lambda h, n: perc(), wall=wall)
    _explorar_hasta_tramo(h)
    assert h.bot._ex.spot.xy == near.xy and h.bot._ex.target == near.xy and far.xy != near.xy
    assert any("Mapa de metin: voy al punto" in r.message for r in caplog.records)


def test_explorar_al_llegar_o_agotar_el_tiempo_marca_visitado(tmp_path):
    wall = Wall()
    sm = SpawnMap(tmp_path / "mapa.json", 6.0, clock_wall=wall)
    sm.record_broken((600.0, 160.0))
    wall.t += 1000
    visited_before = sm.spots[0].last_visit
    h, sm2, _ = spawn_harness(tmp_path, lambda h, n: perc(), wall=wall, spawns={"max_walk_s": 1.0})
    _explorar_hasta_tramo(h)
    assert h.bot._ex.spot is not None
    h.run_until(lambda: sm2.spots[0].last_visit > visited_before)
    h.run_until(lambda: h.bot._ex.phase == "sweep")   # sigue con el barrido de camara
    assert sm2.next_target(INSIDE_XY, ALL) is None


def test_explorar_al_azar_si_no_hay_punto_listo(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    wall = Wall()
    sm = SpawnMap(tmp_path / "mapa.json", 6.0, clock_wall=wall)
    sm.record_broken((600.0, 160.0))      # recien roto: no reaparecio
    h, _, _ = spawn_harness(tmp_path, lambda h, n: perc(), wall=wall)
    _explorar_hasta_tramo(h)
    assert h.bot._ex.spot is None and h.bot.zone.accepts(h.bot._ex.target)
    assert any("ningun punto listo" in r.message for r in caplog.records)


def test_explorar_sin_mapa_no_cambia(tmp_path):
    h = Harness(lambda h, n: perc())      # spawns.enabled False en make_cfg
    _explorar_hasta_tramo(h)
    assert h.bot._ex.spot is None and h.bot.spawns.path is None


# ---------------------------------------------------------------------- nombre del mapa y migracion
def _sp(**kw):
    from bot.config import DEFAULTS
    return {**DEFAULTS["spawns"], **kw}


def test_guarda_el_nombre_del_mapa(tmp_path):
    m = make_map(tmp_path, map_name="Ciudad 1")
    m.record_broken((10.0, 10.0))
    data = json.loads((tmp_path / "mapa.json").read_text(encoding="utf-8"))
    assert data["mapa"] == "Ciudad 1" and data["version"] == 2


def test_lee_archivo_version_1_sin_mapa(tmp_path, caplog):
    (tmp_path / "mapa.json").write_text(json.dumps({"version": 1, "spots": [{"x": 1, "y": 2, "n": 3}]}), encoding="utf-8")
    with caplog.at_level(logging.INFO):
        m = make_map(tmp_path)
    assert len(m) == 1 and m.spots[0].n == 3
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert "Mapa de metin (Ciudad 1): 1 puntos cargados" in caplog.text


def test_avisa_si_el_nombre_no_coincide(tmp_path, caplog):
    (tmp_path / "mapa.json").write_text(json.dumps({"version": 2, "mapa": "Otro", "spots": [{"x": 1, "y": 2}]}), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        m = make_map(tmp_path)
    assert len(m) == 1   # no bloquea
    assert any(r.levelno == logging.WARNING and "Otro" in r.getMessage() for r in caplog.records)


def test_migra_el_mapa_viejo(tmp_path, monkeypatch, caplog):
    import bot.spawns as sp_mod
    new = tmp_path / "Ciudad 1.mapa.json"
    old = tmp_path / "mapa_metin.json"
    old.write_text(json.dumps({"version": 1, "spots": [{"x": 5, "y": 6}]}), encoding="utf-8")
    monkeypatch.setattr(sp_mod, "resolve_path", lambda p: new)
    with caplog.at_level(logging.INFO):
        m = sp_mod.spawn_map_from_config(_sp())
    assert len(m) == 1 and new.is_file() and not old.exists()
    assert (tmp_path / "mapa_metin.json.migrado").is_file()
    assert "migro data/mapa_metin.json a data/Ciudad 1.mapa.json" in caplog.text


def test_no_migra_si_el_nuevo_existe(tmp_path, monkeypatch):
    import bot.spawns as sp_mod
    new = tmp_path / "Ciudad 1.mapa.json"
    old = tmp_path / "mapa_metin.json"
    new.write_text(json.dumps({"version": 2, "mapa": "Ciudad 1", "spots": []}), encoding="utf-8")
    old.write_text(json.dumps({"version": 1, "spots": [{"x": 5, "y": 6}]}), encoding="utf-8")
    monkeypatch.setattr(sp_mod, "resolve_path", lambda p: new)
    m = sp_mod.spawn_map_from_config(_sp())
    assert len(m) == 0 and old.is_file() and not (tmp_path / "mapa_metin.json.migrado").exists()


def test_file_explicito_no_migra(tmp_path):
    import bot.spawns as sp_mod
    explicito = tmp_path / "mio.json"
    old = tmp_path / "mapa_metin.json"
    old.write_text(json.dumps({"version": 1, "spots": [{"x": 5, "y": 6}]}), encoding="utf-8")
    m = sp_mod.spawn_map_from_config(_sp(file=str(explicito)))
    assert len(m) == 0 and not explicito.exists() and old.is_file()


def test_validate_config_exige_map_name():
    import copy
    import pytest
    from bot.config import DEFAULTS, ConfigError, validate_config
    cfg = copy.deepcopy(DEFAULTS)
    cfg["spawns"]["map_name"] = " "
    with pytest.raises(ConfigError, match="map_name"):
        validate_config(cfg)


# ---------------------------------------------------------------------- hora desconocida (0) y regla del centro
def test_hora_desconocida_es_elegible_y_el_log_dice_que_no_se_sabe(tmp_path):
    wall = Wall()
    m = make_map(tmp_path, wall)
    m.spots.append(__import__("bot.spawns", fromlist=["Spot"]).Spot(100.0, 100.0))     # last_broken = last_visit = 0
    assert m.next_target((90.0, 100.0), ALL) is m.spots[0]
    assert m.seconds_until_ready(ALL) == 0.0
    txt = m.spots[0].ages(wall.t)
    assert "?" in txt and "hora desconocida" in txt and "29921" not in txt
    m.mark_visited(m.spots[0])                      # visitado ahora: la revisita si espera (la rotura sigue desconocida)
    assert m.next_target((90.0, 100.0), ALL) is None and 0 < m.seconds_until_ready(ALL) <= m.revisit_s
    wall.t += m.revisit_s + 1
    assert m.next_target((90.0, 100.0), ALL) is m.spots[0]
    assert "hora desconocida" in m.spots[0].ages(wall.t).split("visto")[0]
    assert "min" in m.spots[0].ages(wall.t).split("visto")[1]


def test_explorar_con_punto_sin_hora_va_a_el_y_no_loguea_una_edad_absurda(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    wall = Wall()
    (tmp_path / "mapa.json").write_text(json.dumps({"version": 2, "mapa": "Ciudad 1",
                                                    "spots": [{"x": 600.0, "y": 160.0, "n": 1, "last_broken": 0, "last_visit": 0}]}),
                                        encoding="utf-8")
    h, _, _ = spawn_harness(tmp_path, lambda h, n: perc(), wall=wall)
    _explorar_hasta_tramo(h)
    assert h.bot._ex.spot is not None and h.bot._ex.target == (600.0, 160.0)
    msg = next(r.message for r in caplog.records if "Mapa de metin: voy al punto" in r.message)
    assert "hora desconocida" in msg and "29921" not in msg


def test_sin_punto_listo_va_al_centro_y_camina_hasta_llegar_antes_de_buscar(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    wall = Wall()
    sm = SpawnMap(tmp_path / "mapa.json", 6.0, clock_wall=wall)
    sm.record_broken((600.0, 160.0))      # recien roto: no reaparecio
    h, _, _ = spawn_harness(tmp_path, lambda h, n: perc(), wall=wall)
    _explorar_hasta_tramo(h)
    x = h.bot._ex
    center = h.bot.navigator.return_target(h.bot.tracker.position)
    if x.center:
        assert x.target == center and x.spot is None
        assert any("voy al centro" in r.message for r in caplog.records)
    else:                                  # ya estaba en el centro: busca de nuevo desde ahi (nada de tramos al azar todavia)
        assert any("busco de nuevo desde aqui" in r.message for r in caplog.records)
