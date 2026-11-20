"""Tests de bot/ui/controller.py y de la ventana (sin mainloop). No se lanza el bot real."""
import json
import sys
import time
from pathlib import Path

import pytest

from bot.ui import controller as c

FAKE_BOT = Path(__file__).resolve().parent / "fake_ui_bot.py"
PROFILES = {"servidor": ["A", "B"], "personaje": ["P"], "zona": ["Z1", "Z2"]}


# ---------------------------------------------------------------- linea de comando

def test_build_command_basico():
    o = c.UiOptions(servidor="Guabina", personaje="Farfarfa", zona="Zona X")
    cmd = c.build_command(o, python="py", script="run_bot.py")
    assert cmd[:4] == ["py", "-u", "run_bot.py", "--ui"]
    assert ["--servidor", "Guabina"] == cmd[cmd.index("--servidor"):cmd.index("--servidor") + 2]
    assert "Zona X" in cmd and "--sin-debug" in cmd and "--debug" not in cmd
    assert "--max-minutos" not in cmd and "--observar" not in cmd


def test_build_command_opciones():
    o = c.UiOptions(servidor="S", personaje="P", zona="Z", max_minutes=2.5, debug=True, observar=True)
    cmd = c.build_command(o, python="py", script="r.py", config="c.json")
    assert cmd[cmd.index("--max-minutos") + 1] == "2.5"
    assert "--debug" in cmd and "--sin-debug" not in cmd and "--observar" in cmd
    assert cmd[cmd.index("--config") + 1] == "c.json"
    assert "--sin-cuenta-regresiva" not in cmd


def test_gui_python_pythonw(tmp_path):
    (tmp_path / "python.exe").write_text("")
    (tmp_path / "pythonw.exe").write_text("")
    assert c.gui_python(str(tmp_path / "pythonw.exe")) == str(tmp_path / "python.exe")
    assert c.gui_python("x/python.exe") == "x/python.exe"


# ---------------------------------------------------------------- validacion

@pytest.mark.parametrize("v,ok", [(0, True), (0.5, True), (30, True), (-1, False), (float("nan"), False), (float("inf"), False)])
def test_validate_minutes(v, ok):
    o = c.UiOptions(servidor="S", personaje="P", zona="Z", max_minutes=v)
    assert (c.validate_options(o) == []) is ok


def test_validate_falta_perfil():
    assert any("zona" in e for e in c.validate_options(c.UiOptions(servidor="S", personaje="P")))


@pytest.mark.parametrize("t,v", [("0", 0.0), (" 12,5 ", 12.5), ("3", 3.0)])
def test_parse_minutes_ok(t, v):
    assert c.parse_minutes(t) == v


@pytest.mark.parametrize("t", ["", "abc", "-2", "nan", "inf"])
def test_parse_minutes_mal(t):
    with pytest.raises(ValueError):
        c.parse_minutes(t)


# ---------------------------------------------------------------- parseo

def test_parse_status():
    p = c.parse_line('@@STATUS {"state": "ATACAR", "metins": 3, "elapsed_s": 75.2, "paused": true, "profiles": {"zona": "Z"}}\n')
    assert p.kind == "status" and p.status.state == "ATACAR" and p.status.metins == 3 and p.status.paused
    assert p.status.profiles == {"zona": "Z"} and c.format_elapsed(p.status.elapsed_s) == "01:15"


def test_parse_end():
    p = c.parse_line('@@END {"reason": "tiempo", "summary": "x"}')
    assert p.kind == "end" and p.end["reason"] == "tiempo"


@pytest.mark.parametrize("line", ["@@STATUS {roto", "@@STATUS []", "@@END", "@@STATUS ", "@@END 5", ""])
def test_parse_malformadas_no_rompen(line):
    p = c.parse_line(line)
    assert p.kind == "log"


def test_parse_status_valores_raros():
    p = c.parse_line('@@STATUS {"metins": "x", "elapsed_s": null, "profiles": 3, "countdown": []}')
    assert p.kind == "status" and p.status.metins == 0 and p.status.elapsed_s == 0 and p.status.profiles == {}


def test_parse_log_niveles():
    assert c.parse_line("2024-01-01 10:00:00 [WARNING] run_bot: x").level == "WARNING"
    assert c.parse_line("2024 [ERROR] x: y").level == "ERROR"
    assert c.parse_line("Arrancando en 3...").level == "INFO"
    assert c.parse_line("Falta una plantilla: x").level == "ERROR"


# ---------------------------------------------------------------- botones

def test_botones():
    b = c.button_state(c.STOPPED)
    assert b.start and not b.pause and not b.stop and b.options
    b = c.button_state(c.RUNNING)
    assert not b.start and b.pause and b.stop and b.pause_label == "Pausar" and not b.options
    assert c.button_state(c.RUNNING, paused=True).pause_label == "Reanudar"
    b = c.button_state(c.STOPPING)
    assert not (b.start or b.pause or b.stop)


# ---------------------------------------------------------------- ajustes

def test_settings_ida_y_vuelta(tmp_path):
    p = tmp_path / "ui.json"
    o = c.UiOptions("B", "P", "Z2", 15.0, True, True, True, "500x600+10+20")
    assert c.save_settings(o, p)
    assert c.load_settings(p, PROFILES) == o


def test_settings_corrupto_o_ausente(tmp_path):
    p = tmp_path / "ui.json"
    p.write_text("{no es json", encoding="utf-8")
    o = c.load_settings(p, PROFILES)
    assert (o.servidor, o.personaje, o.zona, o.max_minutes, o.debug) == ("A", "P", "Z1", 0.0, False)
    assert c.load_settings(tmp_path / "nada.json", PROFILES).servidor == "A"
    p.write_text("[1, 2]", encoding="utf-8")
    assert c.load_settings(p, PROFILES).zona == "Z1"


def test_settings_valores_invalidos(tmp_path):
    p = tmp_path / "ui.json"
    p.write_text(json.dumps({"servidor": "B", "max_minutes": -4, "debug": "si", "geometry": "basura", "zona": 7}), encoding="utf-8")
    o = c.load_settings(p, PROFILES)
    assert o.servidor == "B" and o.max_minutes == 0.0 and o.debug is False and o.geometry == "" and o.zona == "Z1"


def test_settings_perfil_que_ya_no_existe(tmp_path):
    p = tmp_path / "ui.json"
    p.write_text(json.dumps({"servidor": "Borrado", "personaje": "P", "zona": "Z2"}), encoding="utf-8")
    o = c.load_settings(p, PROFILES)
    assert o.servidor == "A" and o.zona == "Z2"
    assert c.load_settings(p, {"servidor": [], "personaje": [], "zona": []}).servidor == ""


def test_save_settings_no_lanza(tmp_path):
    assert c.save_settings(c.UiOptions(), tmp_path / "no" / "existe" / "ui.json") is False


def test_ui_settings_en_gitignore():
    assert "ui_settings.json" in (Path(__file__).resolve().parent.parent / ".gitignore").read_text()


# ---------------------------------------------------------------- avisos de pisado

def _mk_profiles(root: Path):
    for kind, d, name, data in (("servidor", "servidores", "S", {"window_title": "w"}),
                                ("personaje", "personajes", "P", {"character": {}}),
                                ("zona", "zonas", "Z", {"farm_zone": {"polygon": [[0, 0], [1, 0], [1, 1]]}})):
        (root / d).mkdir(parents=True, exist_ok=True)
        (root / d / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")


def test_avisos_de_pisado(tmp_path):
    prof = tmp_path / "profiles"
    _mk_profiles(prof)
    cfg = tmp_path / "config.json"
    o = c.UiOptions("S", "P", "Z")
    cfg.write_text(json.dumps({"farm_zone": {"polygon": [[0, 0], [2, 0], [2, 2]]}}), encoding="utf-8")
    w = c.override_warnings(o, cfg, prof)
    assert len(w) == 1 and "farm_zone" in w[0] and "'Z'" in w[0]
    cfg.write_text("{}", encoding="utf-8")
    assert c.override_warnings(o, cfg, prof) == []


def test_avisos_perfil_inexistente(tmp_path):
    prof = tmp_path / "profiles"
    _mk_profiles(prof)
    cfg = tmp_path / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    w = c.override_warnings(c.UiOptions("S", "P", "NoExiste"), cfg, prof)
    assert len(w) == 1 and "Config inválida" in w[0]


# ---------------------------------------------------------------- integracion con el bot falso

def _collect(bp: c.BotProcess, until, timeout=10.0):
    """Junta lineas parseadas hasta que `until(parsed_list)` sea verdadero."""
    out, t0 = [], time.monotonic()
    while time.monotonic() - t0 < timeout:
        for line in bp.drain():
            if line is None:
                return out
            out.append(c.parse_line(line))
        if until(out):
            return out
        time.sleep(0.02)
    pytest.fail(f"timeout esperando: {[p.text for p in out][-5:]}")


def test_integracion_start_pause_stop_end():
    bp = c.BotProcess()
    bp.start([sys.executable, "-u", str(FAKE_BOT)])
    try:
        got = _collect(bp, lambda ps: any(p.kind == "status" and p.status.phase == "running" for p in ps))
        assert any(p.kind == "log" and p.level == "WARNING" for p in got)       # log + linea @@ rota
        assert any(p.kind == "status" and p.status.phase == "countdown" and p.status.countdown == 3 for p in got)
        assert bp.send(c.CMD_PAUSE)
        got = _collect(bp, lambda ps: any(p.kind == "status" and p.status.paused for p in ps))
        assert bp.send(c.CMD_PAUSE)
        _collect(bp, lambda ps: any(p.kind == "status" and not p.status.paused and p.status.phase == "running" for p in ps[-3:]))
        assert bp.stop(5.0) is True
        rest = _collect(bp, lambda ps: False)           # hasta el cierre del flujo (None)
        assert any(p.kind == "end" and p.end["reason"] == "panico" for p in rest)
        assert bp.returncode == 0 and not bp.running
        assert bp.send(c.CMD_PAUSE) is False
    finally:
        bp.kill()


def test_integracion_crash():
    bp = c.BotProcess()
    bp.start([sys.executable, "-u", str(FAKE_BOT), "crash"])
    got = _collect(bp, lambda ps: False)
    bp.wait_closed()
    assert bp.returncode == 1 and got[0].kind == "log" and got[0].level == "ERROR"


def test_integracion_corte_forzado():
    bp = c.BotProcess()
    bp.start([sys.executable, "-u", str(FAKE_BOT), "hang"])
    try:
        _collect(bp, lambda ps: any(p.kind == "status" for p in ps))
        assert bp.stop(0.5) is False      # ignora `stop`: se lo mata
        assert not bp.running
    finally:
        bp.kill()


# ---------------------------------------------------------------- humo de tkinter (sin mainloop)

def _tk_root():
    import tkinter as tk
    try:
        return tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"sin pantalla: {e}")


def _pump(root, seconds=0.0, until=None):
    t0 = time.monotonic()
    while True:
        root.update()
        if (until is not None and until()) or time.monotonic() - t0 >= seconds:
            return
        time.sleep(0.01)


def test_humo_ventana_y_flujo_con_bot_falso(tmp_path):
    from bot.ui.app import App
    root = _tk_root()
    try:
        root.withdraw()
        notes = []
        cfgp = tmp_path / "config.json"
        cfgp.write_text("{}", encoding="utf-8")
        prof = tmp_path / "profiles"
        _mk_profiles(prof)
        app = App(root, settings_path=tmp_path / "ui.json", config_path=str(cfgp), profiles_root=prof,
                  command_builder=lambda o: [sys.executable, "-u", str(FAKE_BOT)],
                  confirm=lambda t, m: True, notify=lambda t, m: notes.append((t, m)))
        _pump(root, 0.1)
        assert app.phase == c.STOPPED and str(app.btn_stop.cget("state")) == "disabled"
        app.minutes_var.set("-3")
        app.start()
        assert app.phase == c.STOPPED and notes                                  # opciones invalidas: no arranca
        app.minutes_var.set("0")
        app.start()
        assert app.phase == c.RUNNING and str(app.btn_start.cget("state")) == "disabled"
        _pump(root, 10, until=lambda: app.stat_vars["metins"].get() not in ("-", "0"))
        assert app.stat_vars["state"].get() == "ATACAR"
        app.toggle_pause()
        _pump(root, 10, until=lambda: app.paused)
        assert app.paused and app.paused_label.cget("text") == "PAUSADO" and app.btn_pause.cget("text") == "Reanudar"
        app.stop()
        assert app.phase == c.STOPPING
        _pump(root, 10, until=lambda: app.phase == c.STOPPED)
        assert app.phase == c.STOPPED and "panico" in app.info_var.get() and not notes[1:]
        assert "WARNING" in app.log.tag_names() and "algo raro" in app.log.get("1.0", "end")
        app.on_close()
        assert json.loads((tmp_path / "ui.json").read_text())["servidor"] == "S"
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass


def test_humo_error_de_arranque_avisa(tmp_path):
    from bot.ui.app import App
    root = _tk_root()
    try:
        root.withdraw()
        notes = []
        prof = tmp_path / "profiles"
        _mk_profiles(prof)
        cfgp = tmp_path / "config.json"
        cfgp.write_text("{}", encoding="utf-8")
        app = App(root, settings_path=tmp_path / "ui.json", config_path=str(cfgp), profiles_root=prof,
                  command_builder=lambda o: [sys.executable, "-u", str(FAKE_BOT), "crash"],
                  notify=lambda t, m: notes.append((t, m)))
        app.start()
        _pump(root, 10, until=lambda: app.phase == c.STOPPED and notes)
        assert notes and "plantilla" in notes[0][1] and "plantilla" in app.log.get("1.0", "end")
        app.on_close()
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass


def test_humo_log_limitado(tmp_path):
    from bot.ui.app import LOG_MAX_LINES, App
    root = _tk_root()
    try:
        root.withdraw()
        app = App(root, settings_path=tmp_path / "ui.json", config_path=str(tmp_path / "x.json"),
                  profiles_root=tmp_path / "nada")
        for i in range(LOG_MAX_LINES + 50):
            app._append_log(f"linea {i}", "ERROR" if i % 2 else "INFO")
        lines = app.log.get("1.0", "end-1c").splitlines()
        assert len(lines) == LOG_MAX_LINES and lines[-1] == f"linea {LOG_MAX_LINES + 49}"
        app.on_close()
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
