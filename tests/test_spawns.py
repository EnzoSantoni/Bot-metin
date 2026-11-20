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
    assert data["version"] == 1 and len(data["spots"]) == 1
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
            return perc(panel=target_panel([0.9, 0.6, 0.3][w["atk"] - 1]) if w["atk"] <= 3 else None)
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
