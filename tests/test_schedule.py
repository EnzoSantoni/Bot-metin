"""Parada programada (hora y tope de metin): parseo, Bot.run, run_bot, historial, @@END y comando de la interfaz."""
import copy
import json
import logging
from argparse import Namespace
from datetime import datetime, timedelta

import pytest

import bot.input.base as base
import run_bot
from bot import history as hist
from bot.bot import State
from bot.config import DEFAULTS, ConfigError, validate_config
from bot.control import BotControl
from bot.input.fake import FakeBackend
from bot.schedule import next_stop, parse_hhmm
from bot.ui import controller as c
from bot.ui import validators as vd
from test_bot_states import FakeAlarm, FakeWindow, Harness, NoKeyboard, item, make_cfg, metin, perc, target_panel

NOW = datetime(2026, 11, 20, 22, 30, 15)


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


# ------------------------------------------------------------------ HH:MM

@pytest.mark.parametrize("text,expected", [("23:30", (23, 30)), ("7:05", (7, 5)), (" 00:00 ", (0, 0)), ("23:59", (23, 59))])
def test_parse_hhmm_valido(text, expected):
    assert parse_hhmm(text) == expected


@pytest.mark.parametrize("text", ["", "24:00", "12:60", "abc", "1230", "12:5", "12:30:00", "-1:30", "12.30"])
def test_parse_hhmm_invalido(text):
    with pytest.raises(ValueError):
        parse_hhmm(text)


def test_next_stop_hoy_y_manana():
    t, tomorrow = next_stop("23:00", NOW)
    assert t == datetime(2026, 11, 20, 23, 0) and tomorrow is False
    t, tomorrow = next_stop("22:00", NOW)   # ya paso hoy: es manana
    assert t == datetime(2026, 11, 21, 22, 0) and tomorrow is True
    t, tomorrow = next_stop("22:30", NOW)   # 22:30:15 ya paso el minuto 22:30
    assert tomorrow is True and t.day == 21
    t, _ = next_stop("00:10", datetime(2026, 12, 31, 23, 50))   # cruza el fin de anio
    assert t == datetime(2027, 1, 1, 0, 10)


def test_resolve_schedule_loguea_que_es_manana(caplog):
    cfg = copy.deepcopy(DEFAULTS)
    args = Namespace(parar_a="22:00", parar_tras_metin=None)
    with caplog.at_level(logging.INFO, logger="run_bot"):
        stop_at, n = run_bot.resolve_schedule(cfg, args, NOW)
    assert stop_at == datetime(2026, 11, 21, 22, 0) and n == 0
    assert "MANANA" in caplog.text and "22:00" in caplog.text


def test_resolve_schedule_linea_de_comandos_pisa_la_config(caplog):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["schedule"] = {"stop_at": "23:00", "stop_after_metins": 50}
    assert run_bot.resolve_schedule(cfg, Namespace(parar_a=None, parar_tras_metin=None), NOW) == (datetime(2026, 11, 20, 23, 0), 50)
    assert run_bot.resolve_schedule(cfg, Namespace(parar_a="23:45", parar_tras_metin=0), NOW) == (datetime(2026, 11, 20, 23, 45), 0)
    cfg["schedule"] = {"stop_at": "", "stop_after_metins": 0}
    assert run_bot.resolve_schedule(cfg, Namespace(parar_a=None, parar_tras_metin=None), NOW) == (None, 0)


def test_argumentos_invalidos_de_la_linea_de_comandos(capsys):
    for argv in (["--parar-a", "25:00"], ["--parar-a", "abc"], ["--parar-tras-metin", "-3"], ["--parar-tras-metin", "x"]):
        with pytest.raises(SystemExit) as e:
            run_bot.main(argv + ["--observar"])
        assert e.value.code == 2
    assert "HH:MM" in capsys.readouterr().err


def test_config_schedule_validacion():
    cfg = copy.deepcopy(DEFAULTS)
    assert cfg["schedule"] == {"stop_at": "", "stop_after_metins": 0}
    validate_config(cfg)
    cfg["schedule"] = {"stop_at": "23:30", "stop_after_metins": 10}
    validate_config(cfg)
    for sched in ({"stop_at": "25:00", "stop_after_metins": 0}, {"stop_at": 5, "stop_after_metins": 0},
                  {"stop_at": "", "stop_after_metins": -1}, {"stop_at": "", "stop_after_metins": 1.5}):
        bad = copy.deepcopy(DEFAULTS)
        bad["schedule"] = sched
        with pytest.raises(ConfigError, match="schedule"):
            validate_config(bad)


# ------------------------------------------------------------------ Bot.run

def ciclo_script(w):
    """BUSCAR -> ATACAR -> (metin roto) -> RECOGER con 2 pulsaciones de Z -> BUSCAR."""
    hps = [0.9, 0.5, 0.05]   # roto real: la ultima vida leida es casi 0

    def script(h, n):
        st = h.bot.state
        if st is State.BUSCAR and not w.get("done"):
            return perc(metins=[metin()])
        if st is State.ATACAR:
            w["atk"] = w.get("atk", 0) + 1
            if w["atk"] == len(hps) + 1:
                h.clock.t += 20.0   # un metin real tarda mas de attack.min_break_s en romperse
            return perc(panel=target_panel(hps[w["atk"] - 1]) if w["atk"] <= len(hps) else None)
        if st is State.RECOGER:
            w["done"] = True
            w["recoger_z"] = h.keys("z")
            return perc(items=[item()] if h.keys("z") < 2 else [])
        return perc()
    return script


def test_tope_de_metin_no_corta_el_recoger():
    w = {}
    h = Harness(ciclo_script(w))
    stats = h.bot.run(stop_after_metins=1)
    assert stats.end_reason == "metin programados" and stats.metins == 1 and h.bot.finished
    states = h.states()
    assert states[:3] == [State.ATACAR, State.RECOGER, State.BUSCAR] and states[-1] is State.FIN
    assert h.keys("z") == 2   # termino de recoger antes de parar


def test_tope_de_metin_mayor_no_para():
    w = {}
    h = Harness(ciclo_script(w))
    h.run_until(lambda: h.bot.stats.metins == 1 and h.bot.state is State.EXPLORAR)
    assert h.bot._scheduled_stop(None, 2) is None and h.bot._scheduled_stop(None, 1) == "metin programados"
    assert h.bot._scheduled_stop(None, 0) is None   # 0 = sin tope


def test_hora_programada_termina_con_su_motivo():
    wall = {"now": datetime(2026, 11, 20, 22, 0)}
    h = Harness(lambda h, n: perc(), bot_kwargs={"wall_clock": lambda: wall["now"]})
    stop_at = datetime(2026, 11, 20, 22, 5)
    steps = {"n": 0}
    orig = h.bot.step

    def step():
        steps["n"] += 1
        if steps["n"] == 4:
            wall["now"] = stop_at   # pasa la hora
        orig()
    h.bot.step = step
    stats = h.bot.run(stop_at=stop_at)
    assert stats.end_reason == "hora programada" and steps["n"] == 4 and h.bot.finished


def test_hora_programada_espera_captcha_recoger_y_revivir():
    wall = {"now": datetime(2026, 11, 20, 23, 0)}
    h = Harness(lambda h, n: perc(), bot_kwargs={"wall_clock": lambda: wall["now"]})
    stop_at = datetime(2026, 11, 20, 22, 0)   # ya cumplida
    for st in (State.CAPTCHA, State.RECOGER, State.REVIVIR):
        h.bot.state = st
        assert h.bot._scheduled_stop(stop_at, 0) is None
    h.bot.state = State.BUSCAR
    assert h.bot._scheduled_stop(stop_at, 0) == "hora programada"
    assert h.bot._scheduled_stop(wall["now"] + timedelta(hours=1), 0) is None


def test_tope_de_tiempo_tiene_prioridad_sobre_la_parada_programada():
    h = Harness(lambda h, n: perc(), bot_kwargs={"wall_clock": lambda: datetime(2030, 1, 1)})
    stats = h.bot.run(max_minutes=0.0, stop_at=datetime(2026, 1, 1))
    assert stats.end_reason == "tiempo"


# ------------------------------------------------------------------ historial y @@END

def test_motivos_en_historial_y_etiquetas():
    assert hist.reason_label("hora programada") == "hora programada"
    assert hist.reason_label("metin programados") == "metin programados"


def run_main(tmp_path, argv, wall_clock):
    cfg = make_cfg(loop={"cycle_ms": 5})
    cfg["history"] = {"enabled": True, "file": str(tmp_path / "historial.jsonl")}
    ctl = BotControl(cfg, keyboard=NoKeyboard(), cursor_pos=lambda: (9999, 9999), exit_fn=lambda c_: None)
    code = run_bot.main(argv, cfg=cfg, window=FakeWindow(), control=ctl, alarm=FakeAlarm(), backend=FakeBackend(),
                        bot_kwargs={"perceive_fn": lambda img, c_, *, full_captcha=False, parts=None: perc(),
                                    "mount_marker": tmp_path / "a_pie.flag", "wall_clock": wall_clock},
                        stdin=iter(()), input_hook=None)
    return code, tmp_path / "historial.jsonl"


def test_hora_programada_en_historial_resumen_y_end(tmp_path, capsys):
    code, path = run_main(tmp_path, ["--ui", "--sin-cuenta-regresiva", "--parar-a", "23:59"],
                          wall_clock=lambda: datetime.now() + timedelta(days=3))   # el reloj del bot ya paso la hora
    out = capsys.readouterr().out
    (s,) = hist.load_history(path)
    ends = [json.loads(ln[len("@@END "):]) for ln in out.splitlines() if ln.startswith("@@END ")]
    assert code == 0 and s["motivo"] == "hora programada"
    assert ends[0]["reason"] == "hora programada" and "motivo de fin: hora programada" in ends[0]["summary"]


# ------------------------------------------------------------------ interfaz: opciones y comando

def opts(**kw):
    return c.UiOptions(servidor="S", personaje="P", zona="Z", **kw)


def test_comando_con_parada_programada():
    cmd = c.build_command(opts(stop_at="23:30", stop_after_metins=40), python="py", script="run_bot.py")
    assert cmd[cmd.index("--parar-a") + 1] == "23:30" and cmd[cmd.index("--parar-tras-metin") + 1] == "40"
    plain = c.build_command(opts(), python="py", script="run_bot.py")
    assert "--parar-a" not in plain and "--parar-tras-metin" not in plain


def test_validar_opciones_de_parada():
    assert c.validate_options(opts(stop_at="23:30", stop_after_metins=3)) == []
    assert c.validate_options(opts(stop_at="")) == []
    assert any("HH:MM" in e for e in c.validate_options(opts(stop_at="25:00")))
    assert any("metin" in e for e in c.validate_options(opts(stop_after_metins=-2)))


def test_parse_y_validadores_de_la_interfaz():
    assert c.parse_stop_metins("") == 0 and c.parse_stop_metins(" 12 ") == 12
    for bad in ("x", "-1", "1.5"):
        with pytest.raises(ValueError):
            c.parse_stop_metins(bad)
    assert c.normalize_hhmm("7:05") == "07:05" and c.normalize_hhmm("") == ""
    with pytest.raises(ValueError):
        c.normalize_hhmm("99:99")
    assert vd.optional_hhmm("") is None and vd.optional_hhmm("23:30") is None
    assert vd.optional_hhmm("23h") and vd.optional_hhmm("24:00")


def test_ajustes_guardados_y_estadisticas_en_vivo(tmp_path):
    p = tmp_path / "ui.json"
    o = opts(stop_at="23:30", stop_after_metins=40)
    assert c.save_settings(o, p)
    back = c.load_settings(p)
    assert back.stop_at == "23:30" and back.stop_after_metins == 40
    p.write_text(json.dumps({"stop_at": "basura", "stop_after_metins": -4}), encoding="utf-8")
    back = c.load_settings(p)
    assert back.stop_at == "" and back.stop_after_metins == 0
    assert c.schedule_text(o, 15) == "Para a las 23:30 | Faltan 25 metin"
    assert c.schedule_text(o, 99) == "Para a las 23:30 | Faltan 0 metin"
    assert c.schedule_text(opts(stop_after_metins=5), 2) == "Faltan 3 metin"
    assert c.schedule_text(opts(), 2) == ""


def test_cartel_de_pausa_y_status():
    assert c.paused_text(False, False) == "" and c.paused_text(True, False) == "PAUSADO"
    assert c.paused_text(True, True) == "PAUSADO (automático)"
    st = c.parse_line('@@STATUS {"paused": true, "auto_paused": true}').status
    assert st.paused and st.auto_paused
    assert c.parse_line('@@STATUS {"paused": true}').status.auto_paused is False
