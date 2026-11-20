"""Pestanas Historial y Objetos y tabla en vivo de la ventana (sin mainloop). Todo en tmp_path."""
import json
import sys
import time
from datetime import datetime, timedelta

import pytest

from bot import history as hist
from bot.item_catalog import UNKNOWN_ID, ItemCatalog
from bot.ui import controller as c
from test_history import rec
from test_item_catalog import sig, text_img


def _tk_root():
    import tkinter as tk
    try:
        return tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"sin pantalla: {e}")


def _pump(root, seconds=0.1):
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        root.update()
        time.sleep(0.01)


def test_humo_pestanas_historial_objetos_y_en_vivo(tmp_path):
    from bot.ui.app import App
    cdir, hfile = tmp_path / "obj", tmp_path / "data" / "historial.jsonl"
    cat = ItemCatalog(cdir)
    pid = cat.match_or_add(sig("Perla"), text_img("Perla")).id
    now = datetime.now().replace(microsecond=0)
    hist.append_session(hfile, rec(now - timedelta(days=2), 300, objetos_por_tipo={pid: 2}, objetos=2))
    hist.append_session(hfile, rec(now - timedelta(minutes=5), 120, objetos_por_tipo={pid: 1, UNKNOWN_ID: 1}, objetos=2))
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    root = _tk_root()
    try:
        root.withdraw()
        notes, names = [], []
        app = App(root, settings_path=tmp_path / "ui.json", config_path=str(tmp_path / "config.json"),
                  command_builder=lambda o: [sys.executable, "-c", "raise SystemExit(0)"], confirm=lambda t, m: True,
                  notify=lambda t, m: notes.append((t, m)), catalog_dir=cdir, history_file=hfile,
                  ask_name=lambda cur, msg: names.pop(0))
        _pump(root)
        tabs = [app.notebook.tab(i, "text") for i in range(len(app.notebook.tabs()))]
        assert tabs == ["Control", "Metin", "Zona", "Modo bichos", "Personaje", "Habilidades", "Historial", "Objetos"]
        # historial: la mas nueva arriba, totales y detalle de la elegida
        assert app.hist_tree.get_children() == ("1", "0")
        assert "Hoy: 1 sesión" in app.hist_today.get() and "Total: 2 sesiones" in app.hist_total.get()
        app.hist_tree.selection_set("1")
        app.root.update()
        detail = app.hist_detail.get("1.0", "end")
        assert "Objeto 1: 1" in detail and "Objeto sin identificar: 1" in detail
        # objetos: catalogo con miniatura y renombrado (valida vacio y refleja el cambio en el historial)
        assert app.items_tree.get_children() == (pid,) and app.items_tree.item(pid, "values") == ("Objeto 1", "1")
        assert pid in app._thumbs
        app.items_tree.selection_set(pid)
        names += ["   "]
        assert not app.rename_selected_item() and notes and "vacio" in notes[-1][1]
        names += [None]
        assert not app.rename_selected_item()
        names += ["Perla azul"]
        assert app.rename_selected_item()
        assert ItemCatalog(cdir).name_of(pid) == "Perla azul" and app.items_tree.item(pid, "values")[0] == "Perla azul"
        assert "Perla azul: 1" in app.hist_detail.get("1.0", "end") and "Perla azul 3" in app.hist_total.get()
        # en vivo: tabla de la sesion y renombrar bloqueado mientras corre
        app.phase = c.RUNNING
        app.refresh_buttons()
        assert str(app.btn_rename.cget("state")) == "disabled"
        names += ["No debe usarse"]
        assert not app.rename_selected_item() and names == ["No debe usarse"]
        app.handle_line("@@STATUS " + json.dumps({"state": "RECOGER", "items_by_type": [
            {"id": pid, "name": "Perla azul", "count": 2}, {"id": UNKNOWN_ID, "name": "Objeto sin identificar", "count": 5}]}))
        assert [app.live_tree.item(i, "values") for i in app.live_tree.get_children()] == [
            ("Objeto sin identificar", "5"), ("Perla azul", "2")]
        app.handle_line("@@STATUS " + json.dumps({"state": "BUSCAR"}))
        assert app.live_tree.get_children() == ()
        # al terminar la corrida se recarga el historial solo
        hist.append_session(hfile, rec(now, 60))
        app._on_exit()
        assert len(app.hist_tree.get_children()) == 3 and str(app.btn_rename.cget("state")) == "normal"
        hist.append_session(hfile, rec(now + timedelta(seconds=1), 60))
        app.refresh_history()
        assert len(app.hist_tree.get_children()) == 4
    finally:
        root.destroy()


def test_ventana_sin_archivos_no_rompe(tmp_path):
    from bot.ui.app import App
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    root = _tk_root()
    try:
        root.withdraw()
        app = App(root, settings_path=tmp_path / "ui.json", config_path=str(tmp_path / "config.json"),
                  catalog_dir=tmp_path / "nada", history_file=tmp_path / "nada" / "h.jsonl")
        _pump(root)
        assert app.hist_tree.get_children() == () and app.items_tree.get_children() == ()
        assert "Hoy: 0 sesiones" in app.hist_today.get() and "Todavía no hay objetos" in app.items_state.get()
        assert not app.rename_selected_item() and not (tmp_path / "nada").exists()
    finally:
        root.destroy()
