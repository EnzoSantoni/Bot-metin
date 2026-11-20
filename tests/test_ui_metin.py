"""Pestana Metin: tipos de la zona, casilla Atacar y prioridad (bot/ui/metin_editor.py y su pestana). Sin mainloop."""
import json
import shutil
import sys
from pathlib import Path

import pytest

from bot.profiles import load_profile
from bot.ui import controller as c
from bot.ui.metin_editor import MetinEditor, metin_types, pretty_name, template_slugs

ROOT = Path(__file__).resolve().parent.parent
ZONA = "Ciudad 1"


@pytest.fixture()
def profs(tmp_path):
    dst = tmp_path / "profiles"
    shutil.copytree(ROOT / "profiles", dst)
    return dst


@pytest.fixture()
def labels(tmp_path):
    d = tmp_path / "labels"
    d.mkdir()
    for slug in ("metin_de_oscuridad", "metin_negro", "metin_de_avaricia"):
        (d / f"{slug}.png").write_bytes(b"x")
    return d


def _path(profs):
    return profs / "zonas" / f"{ZONA}.json"


def _ed(profs, labels):
    ed = MetinEditor(profs)
    ed.load(ZONA, labels)
    return ed


def _set_prio(profs, prio):
    data = json.loads(_path(profs).read_text(encoding="utf-8"))
    data["zone"]["metin_priority"] = prio
    _path(profs).write_text(json.dumps(data), encoding="utf-8")


def test_pretty_name():
    assert pretty_name("metin_de_avaricia") == "Metin de Avaricia"
    assert pretty_name("metin_negro") == "Metin Negro"


def test_template_slugs(labels, tmp_path):
    assert template_slugs(labels) == ["metin_de_avaricia", "metin_de_oscuridad", "metin_negro"]
    assert template_slugs(tmp_path / "no") == [] and template_slugs(None) == []


def test_tipos_son_objetivos_mas_plantillas(profs, labels):
    ed = _ed(profs, labels)
    assert [(t.name, t.attack, t.has_template) for t in ed.types] == [
        ("Metin de Oscuridad", True, True), ("Metin Negro", True, True), ("Metin de Avaricia", False, True)]
    assert not ed.enabled and not ed.dirty


def test_objetivo_sin_plantilla(profs, tmp_path):
    ed = _ed(profs, tmp_path / "vacia")
    assert [t.has_template for t in ed.types] == [False, False]
    assert len(ed.warnings()) == 2 and "Metin Negro" in ed.warnings()[1]


def test_zona_inexistente(profs, labels):
    ed = MetinEditor(profs)
    ed.load("NoExiste", labels)
    assert [t.name for t in ed.types] == ["Metin de Avaricia", "Metin de Oscuridad", "Metin Negro"]
    assert not any(t.attack for t in ed.types) and ed.errors()


def test_prioridad_guardada_va_primero(profs, labels):
    _set_prio(profs, ["metin negro"])
    ed = _ed(profs, labels)
    assert ed.names[:2] == ["Metin Negro", "Metin de Oscuridad"] and ed.enabled and not ed.dirty


def test_metin_types_funcion_pura(labels):
    t = metin_types(["B", "A"], ["a"], labels)
    assert [x.name for x in t][:2] == ["A", "B"]


def test_mover_toggle_y_dirty(profs, labels):
    ed = _ed(profs, labels)
    assert ed.move(0, -1) == 0 and not ed.dirty
    assert ed.move(1, -1) == 0 and ed.names[0] == "Metin Negro" and ed.dirty
    ed2 = _ed(profs, labels)
    ed2.set_attack(2, True)
    assert ed2.dirty
    ed2.set_attack(2, False)
    assert not ed2.dirty


def test_posicion_de_prioridad_y_filas(profs, labels):
    ed = _ed(profs, labels)
    assert ed.rows()[0][0] == "-"
    ed.set_enabled(True)
    ed.set_attack(0, False)
    assert [r[0] for r in ed.rows()] == ["-", "1", "-"]
    assert ed.rows()[0][1] == "☐" and ed.rows()[1][1] == "☑" and ed.rows()[2][3] == "Sí"


def test_validacion_al_menos_uno(profs, labels):
    ed = _ed(profs, labels)
    for i in range(3):
        ed.set_attack(i, False)
    assert ed.errors()
    with pytest.raises(ValueError):
        ed.save()


def test_guardar_objetivos_y_prioridad_con_bak(profs, labels):
    ed = _ed(profs, labels)
    before = json.loads(_path(profs).read_text(encoding="utf-8"))
    ed.set_attack(2, True)      # Avaricia
    ed.set_attack(0, False)     # sin Oscuridad
    ed.move(1, -1)
    ed.set_enabled(True)
    ed.save()
    after = load_profile("zona", ZONA, profs)
    assert after["target_metin_names"] == ["Metin Negro", "Metin de Avaricia"]
    assert after["zone"]["metin_priority"] == ["Metin Negro", "Metin de Avaricia"]
    assert after["farm_zone"] == before["farm_zone"] and after["zone"]["bichos"] == before["zone"]["bichos"]
    bak = _path(profs).with_name(_path(profs).name + ".bak")
    assert json.loads(bak.read_text(encoding="utf-8")) == before
    assert not ed.dirty


def test_apagado_guarda_prioridad_vacia(profs, labels):
    _set_prio(profs, ["Metin Negro"])
    ed = _ed(profs, labels)
    ed.set_enabled(False)
    assert ed.dirty
    ed.save()
    assert load_profile("zona", ZONA, profs)["zone"]["metin_priority"] == []
    assert not _ed(profs, labels).enabled


def test_sin_zona_no_guarda(labels):
    with pytest.raises(ValueError):
        MetinEditor(None).save()


def test_bloqueo_durante_la_corrida(profs, labels):
    ed = _ed(profs, labels)
    ed.locked = not c.button_state(c.RUNNING, False).options
    for call in (lambda: ed.move(0, 1), lambda: ed.set_enabled(True), lambda: ed.set_attack(0, False)):
        with pytest.raises(RuntimeError):
            call()
    ed.locked = False
    ed.move(0, 1)


def test_reload_types_descarta_cambios(profs, labels):
    ed = _ed(profs, labels)
    ed.set_attack(2, True)
    (labels / "metin_de_hielo.png").write_bytes(b"x")
    ed.reload_types(labels)
    assert not ed.dirty and ed.names[-1] == "Metin de Hielo" and not ed.types[-1].attack


# ---------------------------------------------------------------- humo de tkinter
def _tk_root():
    import tkinter as tk
    try:
        return tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"sin pantalla: {e}")


def test_humo_pestana_metin(tmp_path, profs):
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
        assert app.metin.zona == ZONA and len(app.metin_tree.get_children()) >= 2 and app.prio_state.get() == ""
        app.metin_tree.selection_set("1")
        app.prio_var.set(True)
        app._on_prio_toggle()
        app.move_priority(-1)
        assert app.metin.names[0] == "Metin Negro" and "sin guardar" in app.prio_state.get()
        assert app.notebook.tab(app.tabs["metin"], "text") == "Metin *"
        assert app.save_priority() and app.prio_state.get() == ""
        assert app.notebook.tab(app.tabs["metin"], "text") == "Metin"
        assert load_profile("zona", ZONA, profs)["zone"]["metin_priority"][0] == "Metin Negro"
        # desmarcar todo: no se puede guardar
        for i in range(len(app.metin.types)):
            app.toggle_metin(i) if app.metin.types[i].attack else None
        assert not app.save_priority()
        app.reload_metin_types()
        app.metin_tree.selection_set("0")
        app.move_priority(1)
        assert app.metin.dirty
        app.start()
        assert asked and "tipos de metin" in asked[-1]
        assert app.phase == c.STOPPED   # cancelo: no inicia
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- modo de deteccion lejana y candidatos en el estado

def test_far_mode_se_guarda_en_el_perfil_de_servidor(profs):
    from bot.ui import metin_editor as me
    assert me.load_far_mode("Guabina", profs) == "ambos"            # el valor por defecto
    path = me.save_far_mode("Guabina", "estructura", profs)
    assert path.with_name(path.name + ".bak").exists()
    assert load_profile("servidor", "Guabina", profs)["vision"]["far_metin"]["mode"] == "estructura"
    assert load_profile("servidor", "Guabina", profs)["window_title"] == "Guabina"   # el resto queda igual
    assert me.load_far_mode("Guabina", profs) == "estructura"
    with pytest.raises(ValueError):
        me.save_far_mode("Guabina", "otro", profs)
    with pytest.raises(ValueError):
        me.save_far_mode("", "color", profs)
    assert set(me.FAR_MODE_LABELS) == {"color", "estructura", "ambos"}


def test_parse_candidates_status():
    assert c.parse_candidates_status([[1, 2, "piedra"], [3.5, 4, "metin_negro"]]) == [(1.0, 2.0, "piedra"), (3.5, 4.0, "metin_negro")]
    assert c.parse_candidates_status([[1, 2], "x", [None, 1, "a"], [float("nan"), 1, "a"], [True, 1, "a"]]) == []
    assert c.parse_candidates_status(None) == [] and c.parse_candidates_status({"a": 1}) == []
    assert c.status_from_dict({"candidates": [[10, 20, "piedra"]]}).candidates == [(10.0, 20.0, "piedra")]
    assert c.status_from_dict({}).candidates == []
