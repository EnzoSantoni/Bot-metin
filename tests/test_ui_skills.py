"""Tests de la lista editable de habilidades (bot/ui/skills_editor.py y la pestana de la ventana). Sin mainloop."""
import json
import shutil
import sys
import time
from pathlib import Path

import pytest

from bot.profiles import load_profile
from bot.ui import controller as c
from bot.ui import skills_editor as se

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def profs(tmp_path):
    dst = tmp_path / "profiles"
    shutil.copytree(ROOT / "profiles", dst)
    return dst


def _ed(profs):
    ed = se.SkillsEditor(profs)
    ed.load("Farfarfa")
    return ed


def _sk(**kw):
    return {**se.new_skill("Buff", "F5"), **kw}


# ---------------------------------------------------------------- carga y validacion

def test_carga_vacia_y_perfil_inexistente(profs):
    ed = _ed(profs)
    assert ed.skills == [] and not ed.dirty
    assert ed.load("NoExiste") == []


def test_carga_desde_perfil(profs):
    p = profs / "personajes" / "Farfarfa.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["character"]["skills"] = [_sk()]
    p.write_text(json.dumps(data), encoding="utf-8")
    assert _ed(profs).skills == [_sk()]


@pytest.mark.parametrize("patch, texto", [
    ({"key": "no_es_tecla"}, "key"),
    ({"key": "F11"}, "F11 o F12"),
    ({"key": "f12"}, "F11 o F12"),
    ({"duration_s": 0}, "duration_s"),
    ({"animation_s": -1}, "animation_s"),
    ({"name": ""}, "name"),
])
def test_validacion(patch, texto):
    errs = se.validate_skill(_sk(**patch))
    assert errs and texto in errs[0]


def test_habilidad_valida():
    assert se.validate_skill(_sk()) == []
    assert se.validate_skill(_sk(key="ctrl+1", animation_s=0)) == []


def test_aviso_choque_con_controls(profs):
    controls = se.controls_for("Guabina", profs)
    assert controls["pickup"] == "z"
    w = se.key_warnings(_sk(key="z"), controls)
    assert w and "pickup" in w[0]
    assert se.key_warnings(_sk(key="F5"), controls) == []
    assert se.key_warnings(_sk(key="???"), controls) == []     # tecla invalida: no avisa, ya es error


def test_parse_seconds():
    assert se.parse_seconds("2,5", "x") == 2.5
    with pytest.raises(ValueError):
        se.parse_seconds("abc", "La duracion")
    with pytest.raises(ValueError):
        se.parse_seconds("nan", "x")


# ---------------------------------------------------------------- edicion y guardado

def test_agregar_editar_quitar_mover(profs):
    ed = _ed(profs)
    ed.add(_sk(name="A", key="F5"))
    ed.add(_sk(name="B", key="F6"))
    ed.add(_sk(name="C", key="F7"))
    assert ed.dirty
    assert ed.move(2, -1) == 1 and [s["name"] for s in ed.skills] == ["A", "C", "B"]
    assert ed.move(0, -1) == 0 and ed.move(2, 1) == 2          # en los bordes no se mueve
    ed.update(0, _sk(name="A2", key="F8"))
    assert ed.skills[0]["name"] == "A2"
    ed.remove(1)
    assert [s["name"] for s in ed.skills] == ["A2", "B"]
    with pytest.raises(ValueError):
        ed.add(_sk(key="f11"))
    with pytest.raises(ValueError):
        ed.update(0, _sk(duration_s=0))


def test_guardar_conserva_claves_y_hace_bak(profs):
    ed = _ed(profs)
    antes = json.loads((profs / "personajes" / "Farfarfa.json").read_text(encoding="utf-8"))
    ed.add(_sk())
    path = ed.save()
    assert not ed.dirty
    despues = load_profile("personaje", "Farfarfa", profs)
    assert despues["character"]["skills"] == [_sk()]
    assert despues["pickup"] == antes["pickup"] and despues["coords_filter"] == antes["coords_filter"]
    assert despues["character"]["potions"] == antes["character"]["potions"] and despues["vision"] == antes["vision"]
    bak = path.with_name(path.name + ".bak")
    assert json.loads(bak.read_text(encoding="utf-8")) == antes


def test_guardar_rechaza_invalidas_y_no_toca_el_archivo(profs):
    ed = _ed(profs)
    ed.add_example(0)
    ed.skills[0]["duration_s"] = 0       # invalida a mano (saltea add)
    with pytest.raises(ValueError, match="Fila 1"):
        ed.save()
    assert load_profile("personaje", "Farfarfa", profs)["character"]["skills"] == []


def test_ejemplos():
    ex = se.example_skills()
    assert [(s["name"], s["key"]) for s in ex] == [("Aura de Espada", "F1"), ("Espada Espejo", "F1"), ("Defensiva", "4")]
    for s in ex:
        assert (s["duration_s"], s["animation_s"], s["requires_dismount"], s["enabled"]) == (100.0, 2.0, True, True)
        assert se.validate_skill(s) == []


def test_agregar_ejemplo(profs):
    ed = _ed(profs)
    sk = ed.add_example(2)
    assert ed.skills == [sk] and sk["name"] == "Defensiva" and ed.dirty


# ---------------------------------------------------------------- status y bloqueo

def test_parseo_skills_de_status():
    line = "@@STATUS " + json.dumps({"state": "BUSCAR", "skills_cast": 3, "skills": [
        {"name": "Aura", "key": "F1", "remaining_s": 12.3}, "basura", {"name": "X"}]})
    st = c.parse_line(line).status
    assert st.skills_cast == 3
    assert st.skills[0] == {"name": "Aura", "key": "F1", "remaining_s": 12.3}
    assert st.skills[1] == {"name": "X", "key": "", "remaining_s": 0.0} and len(st.skills) == 2
    assert c.status_from_dict({}).skills == [] and c.status_from_dict({"skills": 5}).skills == []


def test_remaining_text():
    assert se.remaining_text(0) == "ya" and se.remaining_text(-3) == "ya"
    assert se.remaining_text(0.2) == "00:01" and se.remaining_text(75) == "01:15"


def test_bloqueo_durante_la_corrida(profs):
    ed = _ed(profs)
    ed.locked = not c.button_state(c.RUNNING).options
    assert ed.locked
    for fn in (lambda: ed.add(_sk()), lambda: ed.add_example(0), lambda: ed.remove(0), lambda: ed.move(0, 1),
               lambda: ed.update(0, _sk())):
        with pytest.raises(RuntimeError):
            fn()
    ed.locked = not c.button_state(c.STOPPED).options
    ed.add(_sk())


# ---------------------------------------------------------------- humo de tkinter

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


def test_humo_ventana_habilidades(tmp_path, profs):
    from bot.ui.app import App, SkillDialog
    root = _tk_root()
    try:
        root.withdraw()
        cfgp = tmp_path / "config.json"
        cfgp.write_text("{}", encoding="utf-8")
        answers = []
        app = App(root, settings_path=tmp_path / "ui.json", config_path=str(cfgp), profiles_root=profs,
                  command_builder=lambda o: [sys.executable, "-c", "raise SystemExit(0)"],
                  confirm=lambda t, m: True, notify=lambda t, m: None, ask_save=lambda t, m: answers.append(m) or False)
        _pump(root)
        assert app.editor.personaje == "Farfarfa" and app.tree.get_children() == ()
        app.add_example(0)
        app.add_example(2)
        _pump(root)
        assert len(app.tree.get_children()) == 2 and app.editor.dirty and "sin guardar" in app.skills_state.get()
        assert app.tree.item("0", "values")[1:3] == ("Aura de Espada", "F1")
        app.tree.selection_set("1")
        app.move_skill(-1)
        assert app.editor.skills[0]["name"] == "Defensiva"
        # en vivo: tiempo restante y habilidades lanzadas, con la tabla bloqueada
        app.phase = c.RUNNING
        app.refresh_buttons()
        assert app.editor.locked and str(app.skill_buttons[0].cget("state")) == "disabled"
        app.handle_line("@@STATUS " + json.dumps({"state": "ATACAR", "skills_cast": 4, "skills": [
            {"name": "Defensiva", "key": "4", "remaining_s": 65}]}))
        assert app.stat_vars["skills_cast"].get() == "4"
        assert app.tree.item("0", "values")[-1] == "01:05"
        app.remove_skill()
        assert len(app.editor.skills) == 2                       # bloqueada: no quita
        app.phase = c.STOPPED
        app.refresh_buttons()
        assert not app.editor.locked and app.tree.item("0", "values")[-1] == ""
        # guardar
        assert app.save_skills() and not app.editor.dirty
        assert len(load_profile("personaje", "Farfarfa", profs)["character"]["skills"]) == 2
        app.root.update()
        # el dialogo valida en vivo
        dlg = SkillDialog(root, None, se.controls_for("Guabina", profs))
        assert str(dlg.btn_ok.cget("state")) == "disabled"        # nombre y tecla vacios
        dlg.vars["name"].set("X")
        dlg.vars["key"].set("f11")
        assert str(dlg.btn_ok.cget("state")) == "disabled" and "F11" in dlg.msg.cget("text")
        dlg.vars["key"].set("z")
        assert str(dlg.btn_ok.cget("state")) == "normal" and "pickup" in dlg.msg.cget("text")
        dlg.accept()
        assert dlg.result and dlg.result["key"] == "z"
        # cambios sin guardar al iniciar: se pregunta y "cancelar" no arranca nada
        app.editor.add_example(1)
        app._ask_save = lambda t, m: answers.append(m)         # devuelve None = cancelar
        app.start()
        assert answers and "iniciar" in answers[-1] and app.phase == c.STOPPED and not app.proc.running
        # cambiar de personaje con cambios sin guardar y cancelar: vuelve al anterior
        app.vars["personaje"].set("Otro")
        app._on_profile_selected("personaje")
        assert app.vars["personaje"].get() == "Farfarfa" and app.editor.dirty
    finally:
        root.destroy()
