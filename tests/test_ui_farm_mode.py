"""Editor del modo de farmeo (bot/ui/farm_mode_editor.py y su seccion en la pestana Control). Sin mainloop."""
import json
import shutil
import sys
from pathlib import Path

import pytest

from bot.profiles import load_profile
from bot.ui import controller as c
from bot.ui.farm_mode_editor import FarmModeEditor, validate_bichos

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
    ed = FarmModeEditor(profs)
    ed.load(ZONA)
    return ed


# ---------------------------------------------------------------- validacion pura
def test_validar_ok():
    v, errs = validate_bichos("bichos", " F4 ", "45")
    assert errs == [] and v == {"farm_mode": "bichos", "lure_key": "F4", "lure_every_s": 45.0}
    assert validate_bichos("metin", "f5", "7,5")[0]["lure_every_s"] == 7.5


@pytest.mark.parametrize("mode,key,secs,frag", [
    ("pescar", "f4", "60", "Metin o Bichos"),
    ("bichos", "", "60", "Falta la tecla"),
    ("bichos", "f4", "0", "mayores que 0"),
    ("bichos", "f4", "-3", "mayores que 0"),
    ("bichos", "f4", "abc", "tiene que ser un número"),
    ("bichos", "f4", "nan", "finito"),
    ("bichos", "f12", "60", "F11 o F12"),
    ("bichos", "f11", "60", "F11 o F12"),
    ("bichos", "zz", "60", "tecla"),
])
def test_validar_errores(mode, key, secs, frag):
    v, errs = validate_bichos(mode, key, secs)
    assert v is None and errs and any(frag in e for e in errs), errs


# ---------------------------------------------------------------- editor
def test_carga_del_perfil(profs):
    ed = _ed(profs)
    assert (ed.mode, ed.lure_key, ed.seconds) == ("metin", "f4", "60") and not ed.dirty
    ed.load("NoExiste")
    assert (ed.mode, ed.lure_key, ed.seconds) == ("metin", "f4", "60") and not ed.dirty


def test_dirty_y_guardar_conserva_el_resto_con_bak(profs):
    ed = _ed(profs)
    before = json.loads(_path(profs).read_text(encoding="utf-8"))
    ed.set_values("bichos", "f5", "45")
    assert ed.dirty
    ed.save()
    assert not ed.dirty
    after = load_profile("zona", ZONA, profs)
    assert after["zone"]["farm_mode"] == "bichos" and after["zone"]["bichos"]["lure_key"] == "f5"
    assert after["zone"]["bichos"]["lure_every_s"] == 45 and set(after["zone"]["bichos"]) == set(before["zone"]["bichos"])
    assert after["farm_zone"] == before["farm_zone"] and after["zone"]["metin_priority"] == before["zone"]["metin_priority"]
    bak = _path(profs).with_name(_path(profs).name + ".bak")
    assert json.loads(bak.read_text(encoding="utf-8")) == before
    assert _ed(profs).mode == "bichos"


def test_guardar_invalido_no_toca_el_archivo(profs):
    ed = _ed(profs)
    raw = _path(profs).read_text(encoding="utf-8")
    ed.set_values("bichos", "f12", "60")
    assert ed.errors()
    with pytest.raises(ValueError):
        ed.save()
    assert _path(profs).read_text(encoding="utf-8") == raw and ed.dirty


def test_sin_zona_no_guarda(tmp_path):
    with pytest.raises(ValueError):
        FarmModeEditor(tmp_path).save()


def test_bloqueo_durante_la_corrida(profs):
    ed = _ed(profs)
    ed.locked = not c.button_state(c.RUNNING, False).options
    with pytest.raises(RuntimeError):
        ed.set_values("bichos", "f4", "60")
    ed.locked = not c.button_state(c.STOPPED, False).options
    ed.set_values("bichos", "f4", "60")


# ---------------------------------------------------------------- humo de tkinter
def _tk_root():
    import tkinter as tk
    try:
        return tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"sin pantalla: {e}")


def test_humo_seccion_modo_de_farmeo(tmp_path, profs):
    from bot.ui.app import App
    root = _tk_root()
    try:
        root.withdraw()
        cfgp = tmp_path / "config.json"
        cfgp.write_text("{}", encoding="utf-8")
        asked, notified = [], []
        app = App(root, settings_path=tmp_path / "ui.json", config_path=str(cfgp), profiles_root=profs,
                  command_builder=lambda o: [sys.executable, "-c", "raise SystemExit(0)"],
                  confirm=lambda t, m: True, notify=lambda t, m: notified.append(m),
                  ask_save=lambda t, m: asked.append(m) or None)
        root.update()
        assert app.farm.zona == ZONA and app.farm_mode_var.get() == "Metin" and app.farm_state.get() == ""
        app.farm_mode_var.set("Bichos")
        app.farm_secs_var.set("0")
        assert app.farm.mode == "bichos" and app.farm_state.get() == "sin guardar"
        assert not app.save_farm_mode() and notified   # segundos invalidos: avisa y no guarda
        app.farm_secs_var.set("30")
        assert app.save_farm_mode() and app.farm_state.get() == ""
        assert load_profile("zona", ZONA, profs)["zone"]["farm_mode"] == "bichos"
        app.farm_key_var.set("f6")
        app.start()
        assert asked and "modo de farmeo" in asked[-1]
        assert app.phase == c.STOPPED   # cancelo: no inicia
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
