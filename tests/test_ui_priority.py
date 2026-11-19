"""Editor de prioridad de metin (bot/ui/priority_editor.py y su seccion en la pestana Control). Sin mainloop."""
import json
import shutil
import sys
from pathlib import Path

import pytest

from bot.profiles import load_profile
from bot.ui import controller as c
from bot.ui.priority_editor import PriorityEditor

ROOT = Path(__file__).resolve().parent.parent
ZONA = "Guabina - Oscuridad y Negro"


@pytest.fixture()
def profs(tmp_path):
    dst = tmp_path / "profiles"
    shutil.copytree(ROOT / "profiles", dst)
    return dst


def _path(profs):
    return profs / "zonas" / f"{ZONA}.json"


def _ed(profs):
    ed = PriorityEditor(profs)
    ed.load(ZONA)
    return ed


def _set_prio(profs, prio):
    data = json.loads(_path(profs).read_text(encoding="utf-8"))
    data["zone"]["metin_priority"] = prio
    _path(profs).write_text(json.dumps(data), encoding="utf-8")


def test_carga_sin_prioridad(profs):
    ed = _ed(profs)
    assert ed.names == ["Metin de Oscuridad", "Metin Negro"] and not ed.enabled and not ed.dirty
    assert ed.load("NoExiste") == []


def test_carga_con_prioridad_primero_los_guardados(profs):
    _set_prio(profs, ["metin negro"])
    ed = _ed(profs)
    assert ed.names == ["Metin Negro", "Metin de Oscuridad"] and ed.enabled and not ed.dirty


def test_mover_y_dirty(profs):
    ed = _ed(profs)
    assert ed.move(0, -1) == 0 and not ed.dirty
    assert ed.move(1, -1) == 0 and ed.names[0] == "Metin Negro" and ed.dirty


def test_guardar_conserva_el_resto_con_bak(profs):
    ed = _ed(profs)
    before = json.loads(_path(profs).read_text(encoding="utf-8"))
    ed.move(1, -1)
    ed.set_enabled(True)
    ed.save()
    after = load_profile("zona", ZONA, profs)
    assert after["zone"]["metin_priority"] == ["Metin Negro", "Metin de Oscuridad"]
    assert after["farm_zone"] == before["farm_zone"] and after["zone"]["bichos"] == before["zone"]["bichos"]
    bak = _path(profs).with_name(_path(profs).name + ".bak")
    assert json.loads(bak.read_text(encoding="utf-8")) == before
    assert not ed.dirty


def test_apagado_guarda_vacia(profs):
    _set_prio(profs, ["Metin Negro"])
    ed = _ed(profs)
    ed.set_enabled(False)
    assert ed.dirty
    ed.save()
    assert load_profile("zona", ZONA, profs)["zone"]["metin_priority"] == []
    assert not _ed(profs).enabled


def test_bloqueo_durante_la_corrida(profs):
    ed = _ed(profs)
    ed.locked = not c.button_state(c.RUNNING, False).options
    with pytest.raises(RuntimeError):
        ed.move(0, 1)
    with pytest.raises(RuntimeError):
        ed.set_enabled(True)
    ed.locked = not c.button_state(c.STOPPED, False).options
    ed.move(0, 1)


# ---------------------------------------------------------------- humo de tkinter
def _tk_root():
    import tkinter as tk
    try:
        return tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"sin pantalla: {e}")


def test_humo_seccion_prioridad(tmp_path, profs):
    from bot.ui.app import App
    root = _tk_root()
    try:
        root.withdraw()
        cfgp = tmp_path / "config.json"
        cfgp.write_text("{}", encoding="utf-8")
        asked = []
        app = App(root, settings_path=tmp_path / "ui.json", config_path=str(cfgp), profiles_root=profs,
                  command_builder=lambda o: [sys.executable, "-c", "raise SystemExit(0)"],
                  confirm=lambda t, m: True, notify=lambda t, m: None, ask_save=lambda t, m: asked.append(m) or None)
        root.update()
        assert app.prio.zona == ZONA and app.prio_list.size() == 2 and app.prio_state.get() == ""
        app.prio_var.set(True)
        app._on_prio_toggle()
        app.prio_list.selection_set(1)
        app.move_priority(-1)
        assert app.prio.names[0] == "Metin Negro" and app.prio_state.get() == "sin guardar"
        assert app.save_priority() and app.prio_state.get() == ""
        assert load_profile("zona", ZONA, profs)["zone"]["metin_priority"][0] == "Metin Negro"
        app.prio_list.selection_set(0)
        app.move_priority(1)
        assert app.prio.dirty
        app.start()
        assert asked and "prioridad" in asked[-1]
        assert app.phase == c.STOPPED   # cancelo: no inicia
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
