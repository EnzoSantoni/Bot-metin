"""Historial de sesiones (bot/history.py), su vista pura (bot/ui/history_view.py), el protocolo con la interfaz y el
registro desde run_bot.py. Todo en tmp_path; nada de input real."""
import json
import logging
import sys
import time
from datetime import datetime, timedelta

import pytest

import bot.input.base as base
import run_bot
from bot import history as hist
from bot.bot import BotStats
from bot.control import BotControl
from bot.input.fake import FakeBackend
from bot.item_catalog import UNKNOWN_ID, ItemCatalog
from bot.ui import controller as c
from bot.ui import history_view as hv
from test_bot_states import FakeAlarm, FakeWindow, NoKeyboard, make_cfg, perc
from test_item_catalog import sig, text_img

T0 = datetime(2026, 11, 20, 14, 0, 0)


def stats(**kw):
    s = BotStats(metins=3, items=4, captchas=1, unstucks=2, approaches=5, skills_cast=6)
    s.metins_by_type = {"metin_de_oscuridad": 2, "metin_negro": 1}
    s.items_by_type = {"obj001": 3, UNKNOWN_ID: 1}
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def rec(inicio=T0, dur=600, **kw):
    r = hist.build_record(stats(), inicio, inicio + timedelta(seconds=dur), {"servidor": "S", "personaje": "P", "zona": "Z"},
                          "tiempo")
    r.update(kw)
    return r


@pytest.fixture()
def cat(tmp_path):
    c_ = ItemCatalog(tmp_path / "obj")
    c_.match_or_add(sig("Perla"), text_img("Perla"))
    return c_


# ---------------------------------------------------------------- build / append / load

def test_build_record_campos():
    r = rec()
    assert r["inicio"] == "2026-11-20T14:00:00" and r["fin"] == "2026-11-20T14:10:00" and r["duracion_s"] == 600
    assert r["perfiles"] == {"servidor": "S", "personaje": "P", "zona": "Z"} and r["motivo"] == "tiempo"
    assert (r["metins"], r["objetos"], r["captchas"], r["destrabes"], r["acercamientos"], r["habilidades"]) == (3, 4, 1, 2, 5, 6)
    assert r["metins_por_tipo"] == {"metin_de_oscuridad": 2, "metin_negro": 1} and r["objetos_por_tipo"]["obj001"] == 3


def test_append_y_load_ida_y_vuelta(tmp_path):
    p = tmp_path / "data" / "historial.jsonl"
    assert hist.load_history(p) == [] and hist.load_history(None) == []
    assert hist.append_session(p, rec()) and hist.append_session(p, rec(T0 + timedelta(hours=1)))
    assert [s["inicio"] for s in hist.load_history(p)] == ["2026-11-20T14:00:00", "2026-11-20T15:00:00"]
    assert not hist.append_session(None, rec())


def test_load_saltea_lineas_rotas(tmp_path, caplog):
    p = tmp_path / "h.jsonl"
    good = json.dumps(rec())
    p.write_text("\n".join([good, "{no es json", "[1, 2]", json.dumps({"sin": "inicio"}), json.dumps({"inicio": "ayer"}), "", good]),
                 encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        out = hist.load_history(p)
    assert len(out) == 2 and caplog.text.count("ilegible") == 4


def test_append_no_lanza_si_no_se_puede(tmp_path, caplog):
    (tmp_path / "ocupado").write_text("x", encoding="utf-8")        # un archivo donde iria la carpeta
    with caplog.at_level(logging.WARNING):
        assert not hist.append_session(tmp_path / "ocupado" / "h.jsonl", rec())
    assert "no se pudo escribir" in caplog.text


# ---------------------------------------------------------------- summarize / vista

def test_summarize_hoy_y_total_con_nombre_actual(cat):
    pid = cat.entries[0].id
    s = [rec(T0 - timedelta(days=1), 100, objetos_por_tipo={pid: 2}, objetos=2, metins=1, metins_por_tipo={"metin_negro": 1}),
         rec(T0, 600, objetos_por_tipo={pid: 3, UNKNOWN_ID: 1}), {"inicio": "rota"}]
    cat.rename(pid, "Perla azul")                       # el historial pasado toma el nombre nuevo
    total = hist.summarize(s, cat)
    assert (total.sessions, total.duration_s, total.metins, total.items) == (2, 700, 4, 6)
    assert total.items_by_name == {"Perla azul": 5, "Objeto sin identificar": 1}
    assert total.metins_by_type == {"Metin negro": 2, "Metin de oscuridad": 2}
    hoy = hist.summarize(s, cat, desde=datetime(2026, 11, 20))
    assert (hoy.sessions, hoy.metins, hoy.items_by_name["Perla azul"]) == (1, 3, 3)
    assert hist.summarize([], cat) == hist.Summary()


def test_filas_mas_nueva_arriba_y_motivo_legible():
    s = [rec(T0, 90), rec(T0 + timedelta(days=1), 3700, motivo="interfaz"), {"inicio": "rota"}]
    rows = hv.session_rows(s)
    assert [r.index for r in rows] == [1, 0]
    assert rows[0].date == "2026-11-21 14:00" and rows[0].duration == "1 h 01 min" and rows[0].reason == "detenido desde la interfaz"
    assert rows[1].duration == "1 min 30 s" and (rows[1].metins, rows[1].items) == (3, 4)
    assert hv.session_rows([]) == []


def test_detalle_y_totales_texto(cat):
    pid = cat.entries[0].id
    r = rec(objetos_por_tipo={pid: 3, UNKNOWN_ID: 1})
    d = hv.session_detail(r, cat)
    assert "S / P / Z" in d and "Metin de oscuridad: 2" in d and "Objeto 1: 3" in d and "Objeto sin identificar: 1" in d
    assert "tope de tiempo" in d and "habilidades: 6" in d
    hoy, total = hv.totals_texts([r], cat, now=T0 + timedelta(hours=1))
    assert hoy.startswith("Hoy: 1 sesión, 10 min 00 s, 3 metin, 4 objetos") and "Objeto 1 3" in total
    assert hv.totals_texts([r], cat, now=T0 + timedelta(days=2))[0].startswith("Hoy: 0 sesiones")
    assert hv.session_detail({"inicio": "2026-11-20T10:00:00"}, cat)       # sesion minima no rompe


def test_duracion_y_filas_del_catalogo(cat):
    assert [hv.format_duration(x) for x in (-5, 45, 125, 3725)] == ["0 s", "45 s", "2 min 05 s", "1 h 02 min"]
    rows = hv.catalog_rows(cat)
    assert [(r.name, r.count) for r in rows] == [("Objeto 1", 1)] and rows[0].thumbnail.endswith(".png")
    assert hv.live_item_rows([{"name": "B", "count": 1}, {"name": "A", "count": 4}]) == [("A", 4), ("B", 1)]
    assert hv.live_metin_rows({"metin_negro": 2}) == [("Metin negro", 2)]


def test_rename_item_valida(cat):
    pid = cat.entries[0].id
    assert hv.rename_item(cat, pid, "") and hv.rename_item(cat, "no", "X") and hv.rename_item(cat, pid, "y" * 99)
    assert hv.rename_item(cat, pid, " Perla ") is None and cat.name_of(pid) == "Perla"


# ---------------------------------------------------------------- protocolo con la interfaz

def test_parseo_tolerante_de_status():
    s = c.status_from_dict({"items_by_type": [{"id": "a", "name": "A", "count": 2}, {"id": "b", "count": "3"}, "x",
                                              {"id": "c", "name": "C", "count": 0}, {"count": "mal"}],
                            "metins_by_type": {"metin_negro": 2, "otro": "x", "z": 0}})
    assert s.items_by_type == [{"id": "a", "name": "A", "count": 2}, {"id": "b", "name": "b", "count": 3}]
    assert s.metins_by_type == {"metin_negro": 2}
    for bad in (None, 5, "x", {}, [None]):
        t = c.status_from_dict({"items_by_type": bad, "metins_by_type": bad})
        assert t.items_by_type == [] and t.metins_by_type == {}
    assert c.status_from_dict({}).items_by_type == []


def test_status_de_run_bot_trae_objetos_por_tipo(cat):
    class FakeBot:
        state = "RECOGER"
        stats = stats()
        catalog = cat
        skills = None

    class Ctl:
        is_paused = False

    ui = run_bot.UiBridge(FakeBot(), Ctl(), {}, iter(()))
    st = ui.status()
    pid = cat.entries[0].id
    assert {"id": pid, "name": "Objeto 1", "count": 3} in st["items_by_type"]
    assert {"id": UNKNOWN_ID, "name": "Objeto sin identificar", "count": 1} in st["items_by_type"]
    assert st["metins_by_type"] == {"metin_de_oscuridad": 2, "metin_negro": 1}
    json.dumps(st)


def test_data_paths_de_la_config(tmp_path):
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"items_catalog": {"dir": str(tmp_path / "o"), "match": 0.9},
                                "history": {"file": str(tmp_path / "h.jsonl")}}), encoding="utf-8")
    assert c.data_paths(cfgp) == (tmp_path / "o", tmp_path / "h.jsonl", 0.9)
    assert c.data_paths(tmp_path / "no_existe.json")[2] == 0.88   # sin config: defaults


# ---------------------------------------------------------------- registro desde run_bot

@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


def run_main(tmp_path, argv, stdin=None, history=True):
    """run_bot.main en este hilo con ventana, backend y alarma falsos (nada de input real)."""
    cfg = make_cfg(loop={"cycle_ms": 5})
    cfg["history"] = {"enabled": history, "file": str(tmp_path / "data" / "historial.jsonl")}
    ctl = BotControl(cfg, keyboard=NoKeyboard(), cursor_pos=lambda: (9999, 9999), exit_fn=lambda c_: None)
    code = run_bot.main(argv, cfg=cfg, window=FakeWindow(), control=ctl, alarm=FakeAlarm(), backend=FakeBackend(),
                        bot_kwargs={"perceive_fn": lambda img, c_, *, full_captcha=False, parts=None: perc(),
                                    "mount_marker": tmp_path / "a_pie.flag"}, stdin=stdin)
    return code, tmp_path / "data" / "historial.jsonl"


def test_fin_por_tiempo_queda_en_el_historial(tmp_path):
    code, path = run_main(tmp_path, ["--sin-cuenta-regresiva", "--max-minutos", "0.01"])
    (s,) = hist.load_history(path)
    assert code == 0 and s["motivo"] == "tiempo" and s["duracion_s"] >= 0 and set(s["perfiles"]) == {"servidor", "personaje", "zona"}
    assert s["metins"] == 0 and s["objetos_por_tipo"] == {} and datetime.fromisoformat(s["fin"]) >= datetime.fromisoformat(s["inicio"])


def test_detener_desde_la_interfaz_se_registra_con_su_motivo(tmp_path):
    class Stdin:
        def __iter__(self):
            yield "stop\n"
            time.sleep(30)

    code, path = run_main(tmp_path, ["--ui", "--sin-cuenta-regresiva"], stdin=Stdin())
    (s,) = hist.load_history(path)
    assert s["motivo"] == "interfaz" and code == 0


def test_observar_no_registra(tmp_path):
    code, path = run_main(tmp_path, ["--observar", "--sin-cuenta-regresiva", "--max-minutos", "0.01"])
    assert code == 0 and not path.exists() and not path.parent.exists()


def test_historial_desactivado_no_escribe(tmp_path):
    code, path = run_main(tmp_path, ["--sin-cuenta-regresiva", "--max-minutos", "0.01"], history=False)
    assert code == 0 and not path.exists()


# ---------------------------------------------------------------- metin por tipo

def test_metin_roto_se_cuenta_por_tipo_confirmado():
    from bot.bot import State
    from test_bot_states import Harness, item, metin, target_panel
    w = {"atk": 0, "done": False}
    hps = [0.9, 0.5, 0.05]   # roto real: la ultima vida leida es casi 0

    def script(h, n):
        st = h.bot.state
        if st is State.BUSCAR and not w["done"]:
            return perc(metins=[metin()])
        if st is State.ATACAR:
            w["atk"] += 1
            if w["atk"] == len(hps) + 1:
                h.clock.t += 20.0   # un metin real tarda mas de attack.min_break_s en romperse
            return perc(panel=target_panel(hps[w["atk"] - 1]) if w["atk"] <= len(hps) else None)
        if st is State.RECOGER:
            w["done"] = True
            return perc(items=[item()] if h.keys("z") < 2 else [])
        return perc()

    h = Harness(script)
    h.run_until(lambda: State.EXPLORAR in h.states())
    assert h.bot.stats.metins == 1 and h.bot.stats.metins_by_type == {"metin_de_oscuridad": 1}


def test_config_valida_las_claves_nuevas():
    import copy
    from bot.config import DEFAULTS, ConfigError, validate_config
    validate_config(copy.deepcopy(DEFAULTS))
    for sec, key, bad in (("items_catalog", "enabled", "si"), ("items_catalog", "dir", ""), ("items_catalog", "match", 0),
                          ("items_catalog", "match", 1.5), ("history", "enabled", 1), ("history", "file", " ")):
        cfg = copy.deepcopy(DEFAULTS)
        cfg[sec][key] = bad
        with pytest.raises(ConfigError, match=f"{sec}.{key}"):
            validate_config(cfg)
