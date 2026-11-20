"""Interfaz completa, tanda 1: validadores, zona (escala y datos), calibrate desde la interfaz, parseo de @@STATUS y humo de
las pestanas nuevas. Sin input real: los subprocesos son scripts de Python que solo imprimen y leen stdin."""
import json
import shutil
import sys
import time
from pathlib import Path

import pytest

from bot.spawns import Spot
from bot.ui import calibrate_runner as cr
from bot.ui import controller as c
from bot.ui import validators as vd
from bot.ui import zone_view as zv

ROOT = Path(__file__).resolve().parent.parent
ZONA = "Ciudad 1"


# ---------------------------------------------------------------- validadores
@pytest.mark.parametrize("text,ok", [("f4", True), ("4", True), ("ctrl+g", True), ("F12", False), ("f11", False), ("", False),
                                     ("  ", False), ("zzz", False)])
def test_game_key(text, ok):
    assert (vd.game_key(text) is None) is ok


def test_game_key_mensajes():
    assert "F11 o F12" in vd.game_key("f12")
    assert "no existe" in vd.game_key("zzz")


@pytest.mark.parametrize("fn,text,ok", [
    (vd.positive_number, "1", True), (vd.positive_number, "0,5", True), (vd.positive_number, "0", False),
    (vd.positive_number, "-1", False), (vd.positive_number, "abc", False), (vd.positive_number, "inf", False),
    (vd.non_negative_number, "0", True), (vd.non_negative_number, "-0.1", False), (vd.non_negative_number, "", False),
    (vd.non_negative_int, "0", True), (vd.non_negative_int, "7", True), (vd.non_negative_int, "1.5", False),
    (vd.non_negative_int, "-2", False), (vd.non_negative_int, "x", False),
    (vd.non_empty, "a", True), (vd.non_empty, "   ", False)])
def test_validadores_numericos_y_texto(fn, text, ok):
    assert (fn(text) is None) is ok


# ---------------------------------------------------------------- zona: escala y dibujo
POLY = [(439.0, 141.0), (753.0, 91.0), (702.0, 203.0), (436.0, 196.0)]


def test_fit_transform_entra_con_margen_y_centrado():
    w, h, m = 640, 420, 30
    tr = zv.fit_transform(POLY, 30.0, w, h, m)
    pts = zv.polygon_to_canvas(POLY, tr)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    left, right = min(xs) - 30 * tr.scale, w - (max(xs) + 30 * tr.scale)
    top, bottom = min(ys) - 30 * tr.scale, h - (max(ys) + 30 * tr.scale)
    assert min(left, right, top, bottom) >= m - 1e-6                 # poligono + tolerancia entran con el margen
    assert left == pytest.approx(right) and top == pytest.approx(bottom)   # centrado
    assert min(left, top) == pytest.approx(m)                        # el lado limitante toca el margen (escala maxima)


def test_fit_transform_escala_uniforme_y_y_hacia_abajo():
    tr = zv.fit_transform([(0, 0), (10, 0), (10, 10)], 0, 200, 100, 10)
    assert tr.scale == pytest.approx(8.0)       # el alto limita: (100-20)/10
    (x0, y0), (x1, y1) = tr.apply(0, 0), tr.apply(10, 10)
    assert x1 - x0 == pytest.approx(80) and y1 - y0 == pytest.approx(80) and y1 > y0
    assert (x0 + x1) / 2 == pytest.approx(100)  # centrado horizontal


def test_fit_transform_degenerado():
    assert zv.fit_transform([], 5, 100, 100).scale == 1.0
    assert zv.fit_transform([(5, 5), (5, 5), (5, 5)], 0, 100, 100, 10).scale > 0   # sin extension: no divide por cero


def test_tolerance_outline_rodea_el_poligono():
    poly = [(100, 100), (200, 100), (200, 200), (100, 200)]
    cs = zv.tolerance_outline(poly, 20, 300, 300)
    assert cs
    xs = [p[0] for c_ in cs for p in c_]
    assert min(xs) <= 80 and max(xs) >= 220
    assert zv.tolerance_outline(poly, 0, 300, 300) == [] and zv.tolerance_outline(poly[:2], 5, 300, 300) == []


def test_ago_text():
    now = 1_000_000.0
    assert zv.ago_text(0, now) == "nunca"
    assert zv.ago_text(now - 10, now) == "hace menos de 1 min"
    assert zv.ago_text(now - 300, now) == "hace 5 min"
    assert zv.ago_text(now - 3 * 3600 - 600, now) == "hace 3 h 10 min"
    assert zv.ago_text(now - 2 * 3600, now) == "hace 2 h"
    assert zv.ago_text(now - 5 * 86400, now) == "hace 5 d"


def test_spot_tooltip():
    now = 1_000_000.0
    t = zv.spot_tooltip(Spot(10.4, 20.6, n=3, last_broken=now - 600), now)
    assert "3 veces" in t and "hace 10 min" in t and "(10, 21)" in t
    uno = zv.spot_tooltip(Spot(1, 2, n=1), now)
    assert "1 vez" in uno and "nunca" in uno


def test_nearest_spot():
    pts = [(10.0, 10.0), (50.0, 50.0)]
    assert zv.nearest_spot(pts, 12, 11) == 0
    assert zv.nearest_spot(pts, 48, 52) == 1
    assert zv.nearest_spot(pts, 30, 30) is None and zv.nearest_spot([], 0, 0) is None


def test_trail_solo_agrega_si_cambia_y_tiene_tope():
    t = zv.Trail(maxlen=3)
    assert t.add((1, 1)) and not t.add((1, 1)) and not t.add(None)
    for i in range(2, 7):
        t.add((i, i))
    assert list(t.points) == [(4, 4), (5, 5), (6, 6)]
    t.clear()
    assert not t.points


def test_position_text():
    assert "sin dato" in zv.position_text(None, "")
    assert "(12, 5)" in zv.position_text((12.0, 5.2), "tolerance") and "tolerancia" in zv.position_text((1, 1), "tolerance")


def test_load_zone_data_perfiles_y_mapa(tmp_path):
    profs = tmp_path / "profiles"
    shutil.copytree(ROOT / "profiles", profs)
    cfgp = tmp_path / "config.json"
    spawns = tmp_path / "mapa.json"
    spawns.write_text(json.dumps({"version": 1, "spots": [{"x": 500, "y": 150, "n": 2, "last_broken": 1.0, "last_visit": 1.0}]}),
                      encoding="utf-8")
    cfgp.write_text(json.dumps({"spawns": {"file": str(spawns)}}), encoding="utf-8")
    d = zv.load_zone_data(cfgp, "", "", ZONA, profs)
    assert not d.error and len(d.polygon) == 4 and d.tolerance == 30 and [(s.x, s.y, s.n) for s in d.spots] == [(500, 150, 2)]
    bad = zv.load_zone_data(cfgp, "", "", "NoExiste", profs)
    assert bad.error and not bad.polygon


# ---------------------------------------------------------------- @@STATUS
def test_status_pos_y_zona():
    st = c.parse_line('@@STATUS {"state":"x","pos":[12.5, 3],"zone_status":"tolerance"}').status
    assert st.pos == (12.5, 3.0) and st.zone_status == "tolerance"


@pytest.mark.parametrize("raw", [None, [], [1], [1, 2, 3], "ab", [True, 1], ["a", 1], [float("nan"), 1], {"x": 1}, 5, [None, None]])
def test_status_pos_tolerante(raw):
    assert c.parse_pos_status(raw) is None
    st = c.status_from_dict({"pos": raw, "zone_status": raw})
    assert st.pos is None and st.zone_status == ""


def test_status_sin_pos_y_zone_status_invalido():
    st = c.status_from_dict({})
    assert st.pos is None and st.zone_status == ""
    assert c.parse_zone_status("fuera") == "" and c.parse_zone_status("outside") == "outside"


def test_tab_title():
    assert c.tab_title("Metin", True) == "Metin *" and c.tab_title("Metin", False) == "Metin"


# ---------------------------------------------------------------- calibrate desde la interfaz
def test_comando_metin():
    cmd = cr.build_calibrate_command("metin", nombre=" Metin de Hielo ", servidor="S", personaje="P", zona="Z", python="py",
                                     script="cal.py")
    assert cmd == ["py", "-u", "cal.py", "metin", "Metin de Hielo", "--ui", "--en-perfil", "--servidor", "S",
                   "--personaje", "P", "--zona", "Z"]
    assert "--si" not in cmd


def test_comando_zona_con_config():
    cmd = cr.build_calibrate_command("zona", zona="Z", config="c.json", python="py", script="cal.py")
    assert cmd == ["py", "-u", "cal.py", "zona", "--ui", "--en-perfil", "--config", "c.json", "--zona", "Z"]


def test_comando_usa_gui_python_por_defecto():
    assert cr.build_calibrate_command("zona")[0] == c.gui_python()


def test_comando_invalido():
    with pytest.raises(ValueError):
        cr.build_calibrate_command("velocidad")
    with pytest.raises(ValueError):
        cr.build_calibrate_command("metin", nombre="  ")


def test_comando_parsea_con_calibrate_py():
    import calibrate
    cmd = cr.build_calibrate_command("metin", nombre="Metin de Hielo", servidor="S", personaje="P", zona="Z")
    args = calibrate.build_parser().parse_args(cmd[3:])
    assert (args.cmd, args.nombre, args.ui, args.en_perfil, args.zona) == ("metin", "Metin de Hielo", True, True, "Z")
    args = calibrate.build_parser().parse_args(cr.build_calibrate_command("zona", servidor="S")[3:])
    assert args.cmd == "zona" and args.servidor == "S"


def test_validate_metin_name():
    ex = ["Metin de Oscuridad", "Metin Negro"]
    assert cr.validate_metin_name("Metin de Hielo", ex) is None
    assert cr.validate_metin_name("  ", ex) and cr.validate_metin_name("!!!", ex)
    assert cr.validate_metin_name("metin negro", ex) and cr.validate_metin_name("-x", ex)


def test_parse_calibrate_line_y_preguntas():
    assert cr.parse_calibrate_line("@@ASK Es la etiqueta de 'X'? (s/n):\n") == ("ask", "Es la etiqueta de 'X'? (s/n):")
    assert cr.parse_calibrate_line("Vértice 1: (1, 2)\n") == ("log", "Vértice 1: (1, 2)")
    assert cr.is_yes_no("Guardar? (s/n):") and not cr.is_yes_no("Nombre:")
    assert cr.question_text("Guardar? (s/n):") == "Guardar?"
    assert cr.question_text("Nombre:") == "Nombre:"


def test_pasos_en_pantalla_mencionan_las_teclas():
    z = " ".join(cr.ZONA_STEPS)
    assert all(k in z for k in ("F9", "F8", "F10", "F12", "sentido", "margen"))
    m = " ".join(cr.METIN_STEPS)
    assert "F9" in m and "F12" in m


# ---------------------------------------------------------------- humo de tkinter
def _tk_root():
    import tkinter as tk
    try:
        return tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"sin pantalla: {e}")


def _pump(root, secs=0.3):
    t0 = time.monotonic()
    while time.monotonic() - t0 < secs:
        root.update()
        time.sleep(0.01)


def _wait(root, pred, timeout=10.0):
    t0 = time.monotonic()
    while not pred() and time.monotonic() - t0 < timeout:
        _pump(root, 0.05)
    return pred()


def _make_app(tmp_path, root, **kw):
    from bot.ui.app import App
    profs = tmp_path / "profiles"
    shutil.copytree(ROOT / "profiles", profs)
    cfgp = tmp_path / "config.json"
    cfgp.write_text("{}", encoding="utf-8")
    return App(root, settings_path=tmp_path / "ui.json", config_path=str(cfgp), profiles_root=profs,
               command_builder=lambda o: [sys.executable, "-c", "raise SystemExit(0)"],
               confirm=lambda t, m: True, notify=lambda t, m: None, ask_save=lambda t, m: False, **kw), profs


def _destroy(root):
    try:
        root.destroy()
    except Exception:  # noqa: BLE001
        pass


def test_humo_pestanas_control_zona_y_estilo(tmp_path):
    root = _tk_root()
    try:
        root.withdraw()
        app, profs = _make_app(tmp_path, root)
        _pump(root, 0.1)
        titles = [app.notebook.tab(i, "text") for i in range(len(app.notebook.tabs()))]
        assert titles == ["Control", "Metin", "Zona", "Modo bichos", "Personaje", "Habilidades", "Historial", "Objetos"]
        assert set(app.stat_vars) >= {"deaths", "lures"}
        # zona: dibuja el poligono del perfil y responde a un @@STATUS con posicion
        assert len(app._zone.polygon) == 4 and app.zone_canvas.find_all()
        app.handle_line('@@STATUS {"state":"x","metins":0,"pos":[500,150],"zone_status":"inside","deaths":2,"revives":1}')
        n0 = len(app.zone_canvas.find_all())
        app.handle_line('@@STATUS {"state":"x","metins":0,"pos":[520,160],"zone_status":"inside"}')
        assert len(app.zone_canvas.find_all()) > n0 and "(520, 160)" in app.zone_pos_var.get()   # sumo el rastro
        assert app.stat_vars["deaths"].get() == "0 (0)"
        app.handle_line('@@STATUS {"state":"x","pos":null,"zone_status":null}')
        assert app.cur_pos is None
        # selector rapido de modo: guarda en el perfil de zona
        from bot.profiles import load_profile
        app.quick_mode_var.set("Bichos")
        app._on_quick_mode()
        assert app.farm.mode == "bichos" and load_profile("zona", ZONA, profs)["zone"]["farm_mode"] == "bichos"
        # minutos validados: marca en rojo con mensaje
        app.minutes_var.set("abc")
        assert not app.minutes_field.valid and app.minutes_field.msg.get()
        assert str(app.minutes_entry.cget("style")) == "Invalid.TEntry"
        app.minutes_var.set("5")
        assert app.minutes_field.valid and app.minutes_field.msg.get() == ""
        # '*' en el titulo de la pestana Modo bichos
        app.farm_secs_var.set("90")
        assert app.notebook.tab(app.tabs["bichos"], "text") == "Modo bichos *"
    finally:
        _destroy(root)


def test_humo_tooltip_aparece_tras_el_retardo(tmp_path):
    from bot.ui.widgets import Tooltip
    import tkinter as tk
    root = _tk_root()
    try:
        root.deiconify()
        lab = tk.Label(root, text="hola")
        lab.pack()
        tip = Tooltip(lab, "ayuda", delay_ms=20)
        root.update()
        lab.event_generate("<Enter>")
        assert _wait(root, lambda: tip._tip is not None, 3.0)
        lab.event_generate("<Leave>")
        root.update()
        assert tip._tip is None
    finally:
        _destroy(root)


def test_humo_botones_de_calibrar_se_bloquean_con_el_bot_corriendo(tmp_path):
    from bot.ui import controller as ctl
    root = _tk_root()
    try:
        root.withdraw()
        app, _ = _make_app(tmp_path, root)
        assert str(app.btn_add_metin.cget("state")) == "normal" and str(app.btn_redo_zone.cget("state")) == "normal"
        app._set_phase(ctl.RUNNING)
        assert str(app.btn_add_metin.cget("state")) == "disabled" and str(app.btn_redo_zone.cget("state")) == "disabled"
        app._set_phase(ctl.STOPPED)
    finally:
        _destroy(root)


FAKE_CALIBRATE = (
    "import sys\n"
    "print('hola desde calibrate', flush=True)\n"
    "print('@@ASK Guardar? (s/n):', flush=True)\n"
    "a = sys.stdin.readline().strip()\n"
    "print('respuesta=' + a, flush=True)\n"
    "print('@@ASK Nombre:', flush=True)\n"
    "b = sys.stdin.readline().strip()\n"
    "print('nombre=' + b, flush=True)\n"
)


def test_humo_dialogo_de_calibracion_responde_preguntas():
    from bot.ui.calibrate_dialog import CalibrateDialog
    root = _tk_root()
    try:
        root.withdraw()
        done = []
        dlg = CalibrateDialog(root, "Prueba", ("1. paso uno",), lambda name: [sys.executable, "-u", "-c", FAKE_CALIBRATE],
                              done.append, name_label="Nombre:", name_validator=lambda t: cr.validate_metin_name(t, []))
        assert str(dlg.btn_start.cget("state")) == "disabled"      # nombre vacio: no deja empezar
        dlg.name_entry.set("Metin de Hielo")
        assert str(dlg.btn_start.cget("state")) == "normal"
        dlg.start()
        assert _wait(root, lambda: dlg.pending_question is not None)
        assert dlg.pending_question == "Guardar? (s/n):" and "hola desde calibrate" in dlg.output.get("1.0", "end")
        assert dlg.answer(cr.ANSWER_YES)
        assert _wait(root, lambda: dlg.pending_question is not None)
        assert dlg.pending_question == "Nombre:" and not cr.is_yes_no(dlg.pending_question)
        assert dlg.answer("Metin X")
        assert _wait(root, lambda: dlg.finished)
        out = dlg.output.get("1.0", "end")
        assert done == [0] and "respuesta=s" in out and "nombre=Metin X" in out
        dlg.close()
    finally:
        _destroy(root)


def test_humo_dialogo_cancelar_corta_el_proceso():
    from bot.ui.calibrate_dialog import CalibrateDialog
    root = _tk_root()
    try:
        root.withdraw()
        done = []
        dlg = CalibrateDialog(root, "Prueba", (), lambda n: [sys.executable, "-u", "-c", "import time; time.sleep(60)"], done.append)
        dlg.start()
        assert dlg.proc.running
        dlg.close()
        assert not dlg.proc.running and done == [None]
    finally:
        _destroy(root)


def test_humo_agregar_tipo_y_rehacer_zona_arman_el_dialogo(tmp_path, monkeypatch):
    import bot.ui.app as appmod
    made = []

    class FakeDialog:
        def __init__(self, parent, title, steps, factory, on_finish, **kw):
            made.append((title, steps, factory, on_finish, kw))

        def kill(self):
            pass

    monkeypatch.setattr(appmod, "CalibrateDialog", FakeDialog)
    root = _tk_root()
    try:
        root.withdraw()
        app, _ = _make_app(tmp_path, root)
        app.redo_zone()
        title, steps, factory, finish, _kw = made[-1]
        assert title == cr.ZONA_TITLE and steps == cr.ZONA_STEPS
        cmd = factory("")
        assert cmd[3] == "zona" and "--en-perfil" in cmd and cmd[cmd.index("--zona") + 1] == ZONA
        assert str(app.btn_redo_zone.cget("state")) == "disabled"   # una guia a la vez
        app.redo_zone()
        assert len(made) == 1
        finish(0)                                                    # al terminar: libera y redibuja
        assert app._calib is None and str(app.btn_redo_zone.cget("state")) == "normal"
        app.add_metin_type()
        title, steps, factory, finish, kw = made[-1]
        assert title == cr.METIN_TITLE and kw["name_validator"]("Metin Negro") and kw["name_validator"]("Metin de Hielo") is None
        assert factory("Metin de Hielo")[3:5] == ["metin", "Metin de Hielo"]
        n = len(app.metin.types)
        finish(0)   # al terminar se recargan los tipos
        assert len(app.metin.types) == n and app._calib is None
    finally:
        _destroy(root)
