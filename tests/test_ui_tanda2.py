"""Interfaz completa, tanda 2: duplicar perfiles, editor de opciones del personaje, ajustes avanzados de bichos, deteccion de
claves de config.json que pisan perfiles, comandos de calibrate (personaje y migrar), formato mm:ss y humo de tkinter.
Sin input real: los dialogos y subprocesos son falsos y todo se escribe en tmp_path."""
import json
import shutil
import sys
import time
from pathlib import Path

import pytest

import calibrate
from bot.config import ConfigError
from bot.profiles import load_profile, profile_path
from bot.ui import calibrate_runner as cr
from bot.ui import character_editor as ce
from bot.ui import overrides as ov
from bot.ui import profile_ops as po
from bot.ui import skills_editor as se
from bot.ui import validators as vd
from bot.ui.farm_mode_editor import ADVANCED_KEYS, FarmModeEditor, validate_advanced, validate_bichos

ROOT = Path(__file__).resolve().parent.parent
ZONA = "Guabina - Oscuridad y Negro"
PJ = "Farfarfa"


@pytest.fixture()
def profs(tmp_path):
    dst = tmp_path / "profiles"
    shutil.copytree(ROOT / "profiles", dst)
    return dst


# ---------------------------------------------------------------- validadores nuevos
@pytest.mark.parametrize("fn,text,ok", [
    (vd.positive_int, "1", True), (vd.positive_int, "0", False), (vd.positive_int, "2.5", False), (vd.positive_int, "x", False),
    (vd.optional_game_key, "", True), (vd.optional_game_key, "f5", True), (vd.optional_game_key, "f12", False),
    (vd.optional_game_key, "zzz", False),
    (vd.optional_file, "", True), (vd.optional_file, "no/existe.png", False), (vd.optional_file, "requirements.txt", True)])
def test_validadores_nuevos(fn, text, ok):
    assert (fn(text) is None) is ok


# ---------------------------------------------------------------- nombres y duplicado de perfiles
@pytest.mark.parametrize("name,frag", [
    ("", "Escribí"), ("  ", "Escribí"), ("-x", "guion"), ("a/b", "no válido"), ("con", "no válido"), ("x.", "no válido"),
    ("Farfarfa", "Ya existe"), ("farfarfa", "Ya existe"), ("algo.mapa", "no válido")])
def test_nombre_de_perfil_invalido(profs, name, frag):
    err = po.validate_new_profile_name("personaje", name, root=profs)
    assert err and frag in err


def test_nombre_de_perfil_valido(profs):
    assert po.validate_new_profile_name("personaje", "Otro Pj-2", root=profs) is None
    assert po.validate_new_profile_name("zona", "Zona nueva", root=profs) is None


def test_nombre_de_zona_con_mapa_guardado_se_rechaza(profs):
    (profs / "zonas" / "Fantasma.mapa.json").write_text("{}", encoding="utf-8")
    assert "mapa" in po.validate_new_profile_name("zona", "Fantasma", root=profs)


def test_duplicar_personaje_mismo_conserva_nombre_amarillo(profs):
    notes = po.duplicate_character_profile(PJ, "Farfarfa 2", True, profs)
    assert notes == []
    src, dst = load_profile("personaje", PJ, profs), load_profile("personaje", "Farfarfa 2", profs)
    assert dst == src and dst["vision"]["items"]["owner_template"] == src["vision"]["items"]["owner_template"]
    assert profile_path("personaje", PJ, profs).read_text(encoding="utf-8")   # el original no se toca


def test_duplicar_personaje_otro_pide_capturar_el_nombre(profs):
    notes = po.duplicate_character_profile(PJ, "Otro", False, profs)
    assert len(notes) == 1 and "Capturar nombre amarillo" in notes[0]
    dst = load_profile("personaje", "Otro", profs)
    assert dst["character"]["name"] == "Otro" and "vision" not in dst
    assert dst["character"]["potions"] == load_profile("personaje", PJ, profs)["character"]["potions"]


def test_duplicar_personaje_no_pisa_uno_existente(profs):
    with pytest.raises(ConfigError):
        po.duplicate_character_profile(PJ, PJ, True, profs)


def test_duplicar_zona_copia_todo_menos_el_mapa(profs):
    data = load_profile("zona", ZONA, profs)
    data["spawns"] = {"file": "profiles/zonas/otro.mapa.json"}
    data["zone"]["bichos"]["space_s"] = 3.5
    data["zone"]["farm_mode"] = "bichos"
    from bot.profiles import save_profile
    save_profile("zona", ZONA, data, profs)
    (profs / "zonas" / f"{ZONA}.mapa.json").write_text('{"spots": []}', encoding="utf-8")
    po.duplicate_zone_profile(ZONA, "Zona B", profs)
    dst = load_profile("zona", "Zona B", profs)
    assert dst["farm_zone"] == data["farm_zone"] and dst["target_metin_names"] == data["target_metin_names"]
    assert dst["zone"]["farm_mode"] == "bichos" and dst["zone"]["bichos"]["space_s"] == 3.5
    assert dst["zone"]["name"] == "Zona B" and "spawns" not in dst
    assert not (profs / "zonas" / "Zona B.mapa.json").exists()          # el mapa empieza vacio
    assert (profs / "zonas" / f"{ZONA}.mapa.json").exists()             # el de la original sigue
    from bot.profiles import list_profiles
    assert list_profiles("zona", profs) == sorted([ZONA, "Zona B"])      # los .mapa.json no cuentan como perfil


# ---------------------------------------------------------------- opciones del personaje
def test_character_editor_carga_valores_del_perfil(profs):
    ed = ce.CharacterEditor(profs)
    ed.load(PJ)
    assert ed.has_potions and not ed.dirty
    assert ed.values["dismount"] is True and ed.values["auto_revive"] is False
    assert (ed.values["hp_key"], ed.values["mp_key"]) == ("f2", "f3")
    assert ed.values["max_revives"] == "5" and ed.values["button_wait_s"] == "3"


def test_character_editor_dirty_guardar_y_bak(profs):
    ed = ce.CharacterEditor(profs)
    ed.load(PJ)
    before = load_profile("personaje", PJ, profs)
    ed.set("auto_revive", True)
    ed.set("horse_key", " f5 ")
    ed.set("max_revives", "3")
    ed.set("button_wait_s", "2,5")
    ed.set("dismount", False)
    ed.set("hp_key", "f6")
    assert ed.dirty and ed.errors() == []
    path = ed.save()
    assert not ed.dirty and path.with_name(path.name + ".bak").exists()
    after = load_profile("personaje", PJ, profs)
    assert after["revive"]["auto_revive"] is True and after["revive"]["horse_key"] == "f5"
    assert after["revive"]["max_revives"] == 3 and after["revive"]["button_wait_s"] == 2.5
    assert after["pickup"]["dismount"] is False and after["character"]["potions"]["hp_key"] == "f6"
    assert after["character"]["skills"] == before["character"]["skills"]            # el resto se conserva
    assert after["vision"] == before["vision"] and after["coords_filter"] == before["coords_filter"]


def test_character_editor_errores_y_no_toca_el_archivo(profs):
    ed = ce.CharacterEditor(profs)
    ed.load(PJ)
    raw = profile_path("personaje", PJ, profs).read_text(encoding="utf-8")
    for field, bad in (("horse_key", "f12"), ("max_revives", "-1"), ("button_wait_s", "abc"), ("hp_key", ""),
                       ("horse_item_template", "no/existe.png")):
        good = ed.values[field]
        ed.set(field, bad)
        assert ed.errors(), field
        with pytest.raises(ValueError):
            ed.save()
        ed.set(field, good)
    assert profile_path("personaje", PJ, profs).read_text(encoding="utf-8") == raw


def test_character_editor_sin_pociones_no_las_toca(profs):
    from bot.profiles import save_profile
    data = load_profile("personaje", PJ, profs)
    del data["character"]["potions"]
    save_profile("personaje", PJ, data, profs)
    ed = ce.CharacterEditor(profs)
    ed.load(PJ)
    assert not ed.has_potions
    ed.set("hp_key", "")          # invisible en la interfaz: no bloquea el guardado
    ed.set("recast_skills", False)
    ed.save()
    assert "potions" not in load_profile("personaje", PJ, profs)["character"]


def test_character_editor_bloqueado(profs):
    ed = ce.CharacterEditor(profs)
    ed.load(PJ)
    ed.locked = True
    with pytest.raises(RuntimeError):
        ed.set("dismount", False)


def test_plantilla_relativa_al_proyecto(tmp_path):
    assert ce.relative_template_path(ROOT / "templates" / "x" / "a.png", ROOT) == "templates/x/a.png"
    other = tmp_path / "b.png"
    assert ce.relative_template_path(other, ROOT) == str(other)


# ---------------------------------------------------------------- bichos avanzado
def test_bichos_avanzado_validacion():
    ok, errs = validate_advanced({"space_s": "2", "min_monsters": "1", "clear_s": "3", "pickup_every_s": "60",
                                  "pickup_max_monsters": "0", "idle_s": "20"})
    assert errs == {} and ok["pickup_max_monsters"] == 0 and isinstance(ok["min_monsters"], int)
    bad, errs = validate_advanced({"space_s": "0", "min_monsters": "0", "clear_s": "x", "pickup_every_s": "-1",
                                   "pickup_max_monsters": "-1", "idle_s": ""})
    assert bad is None and set(errs) == set(ADVANCED_KEYS)


def test_bichos_validate_con_avanzados_agrega_las_claves():
    v, errs = validate_bichos("bichos", "f4", "60", {"space_s": "1,5", "min_monsters": "2", "clear_s": "3",
                                                     "pickup_every_s": "60", "pickup_max_monsters": "1", "idle_s": "20"})
    assert errs == [] and v["space_s"] == 1.5 and v["min_monsters"] == 2 and v["lure_key"] == "f4"
    v, errs = validate_bichos("bichos", "f4", "60", {"space_s": "0"})
    assert v is None and any("space_s" in e for e in errs)


def test_bichos_editor_avanzado_dirty_guardar(profs):
    ed = FarmModeEditor(profs)
    ed.load(ZONA)
    assert ed.advanced["space_s"] == "2" and ed.advanced["min_monsters"] == "1" and not ed.dirty
    ed.set_advanced("space_s", "3")
    ed.set_advanced("idle_s", "20")
    assert ed.dirty
    ed.save()
    bi = load_profile("zona", ZONA, profs)["zone"]["bichos"]
    assert bi["space_s"] == 3 and "idle_s" not in bi and "min_monsters" not in bi   # solo lo que difiere del defecto
    ed2 = FarmModeEditor(profs)
    ed2.load(ZONA)
    assert ed2.advanced["space_s"] == "3" and not ed2.dirty
    ed2.set_advanced("min_monsters", "0")
    with pytest.raises(ValueError):
        ed2.save()
    with pytest.raises(KeyError):
        ed2.set_advanced("nada", "1")


# ---------------------------------------------------------------- config.json que pisa perfiles
RAW = {"target_metin_names": ["A"], "farm_zone": {"polygon": [[0, 0], [1, 0], [1, 1]], "outside_tolerance": 30},
       "zone": {"farm_mode": "bichos", "bichos": {"lure_key": "f5"}}, "revive": {"auto_revive": True},
       "window_title": "X", "debug": {"enabled": True}, "profiles": {"zona": "Z"}}


def test_claves_de_config_que_pisan():
    keys = ov.keys_in_config(RAW)
    names = ov.all_names(keys)
    assert names == ["target_metin_names", "farm_zone", "zone.farm_mode", "zone.bichos", "revive.auto_revive", "window_title"]
    assert "debug" not in " ".join(names) and "profiles" not in names       # general y bloque profiles no cuentan
    assert ov.keys_in_config({"debug": {"x": 1}, "profiles": {"zona": "Z"}}) == []


def test_claves_por_pestana():
    by = ov.names_by_tab(ov.keys_in_config(RAW))
    assert by == {"metin": ["target_metin_names"], "zona": ["farm_zone"], "bichos": ["zone.farm_mode", "zone.bichos"],
                  "personaje": ["revive.auto_revive"]}                       # window_title es del servidor: sin pestana
    sk = ov.names_by_tab(ov.keys_in_config({"character": {"skills": []}, "pickup": {"dismount": False}}))
    assert sk == {"habilidades": ["character.skills"], "personaje": ["pickup.dismount"]}


def test_textos_de_aviso():
    keys = ov.keys_in_config(RAW)
    assert ov.banner_text([]) == "" and ov.tab_text([]) == ""
    b = ov.banner_text(keys)
    assert "target_metin_names" in b and "farm_zone" in b and "config.json" in b
    assert "farm_zone" in ov.tab_text(["farm_zone"])
    e = ov.migration_explanation(keys, {"servidor": "S", "personaje": "P", "zona": "Z"})
    assert "config.json.bak" in e and "perfil de zona 'Z'" in e and "perfil de personaje 'P'" in e and "perfil de servidor 'S'" in e
    assert e.rstrip().endswith("¿Seguir?")


def test_leer_claves_del_archivo(tmp_path):
    p = tmp_path / "c.json"
    assert ov.read_config_keys(p) == []                     # no existe
    p.write_text("{no es json", encoding="utf-8")
    assert ov.read_config_keys(p) == []                     # roto: no revienta la ventana
    p.write_text(json.dumps(RAW), encoding="utf-8")
    assert len(ov.read_config_keys(p)) == len(ov.keys_in_config(RAW))


# ---------------------------------------------------------------- comandos de calibrate
def test_comando_personaje():
    cmd = cr.build_calibrate_command("personaje", nombre=" Zeta ", servidor="S", personaje="P", zona="Z", python="py",
                                     script="cal.py", config="c.json")
    assert cmd == ["py", "-u", "cal.py", "personaje", "Zeta", "--ui", "--config", "c.json", "--servidor", "S", "--zona", "Z"]
    assert "--personaje" not in cmd                         # el perfil puede no existir todavia
    args = calibrate.build_parser().parse_args(cmd[3:])
    assert args.cmd == "personaje" and args.nombre == "Zeta" and args.ui and args.zona == "Z"


def test_comando_personaje_nombre_invalido():
    for bad in (None, "  ", "-x"):
        with pytest.raises(ValueError):
            cr.build_calibrate_command("personaje", nombre=bad)


def test_comando_migrar():
    cmd = cr.build_calibrate_command("perfiles-migrar", servidor="S", personaje="P", zona="Z", python="py", script="cal.py")
    assert cmd == ["py", "-u", "cal.py", "perfiles", "migrar", "--ui", "--servidor", "S", "--personaje", "P", "--zona", "Z"]
    assert "--si" not in cmd                                # la confirmacion la da la persona
    args = calibrate.build_parser().parse_args(cmd[3:])
    assert args.cmd == "perfiles" and args.accion == "migrar" and args.ui and not args.si


def test_pasos_de_personaje_y_migrar():
    assert all(k in " ".join(cr.PERSONAJE_STEPS) for k in ("F9", "F12", "nombre amarillo"))
    assert "config.json.bak" in " ".join(cr.MIGRAR_STEPS)


# ---------------------------------------------------------------- mm:ss
@pytest.mark.parametrize("secs,text", [(0, "ya"), (-5, "ya"), (float("nan"), "ya"), (0.1, "00:01"), (59, "00:59"), (60, "01:00"),
                                       (65.2, "01:06"), (599, "09:59"), (3599, "59:59"), (3600, "1:00:00"), (3725, "1:02:05")])
def test_remaining_text(secs, text):
    assert se.remaining_text(secs) == text


# ---------------------------------------------------------------- humo de tkinter
def _tk_root():
    import tkinter as tk
    try:
        return tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"sin pantalla: {e}")


def _pump(root, secs=0.2):
    t0 = time.monotonic()
    while time.monotonic() - t0 < secs:
        root.update()
        time.sleep(0.01)


class FakeDialog:
    made: list = []

    def __init__(self, parent, title, steps, factory, on_finish, **kw):
        self.args = (title, steps, factory, on_finish, kw)
        FakeDialog.made.append(self.args)

    def kill(self):
        pass


def _make_app(tmp_path, root, config=None, **kw):
    from bot.ui.app import App
    profs = tmp_path / "profiles"
    shutil.copytree(ROOT / "profiles", profs)
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps(config or {}), encoding="utf-8")
    ctx = dict(confirm=lambda t, m: True, notify=lambda t, m: None, ask_save=lambda t, m: False, inform=lambda t, m: None)
    ctx.update(kw)
    return App(root, settings_path=tmp_path / "ui.json", config_path=str(cfgp), profiles_root=profs,
               command_builder=lambda o: [sys.executable, "-c", "raise SystemExit(0)"], **ctx), profs, cfgp


def _destroy(root):
    try:
        root.destroy()
    except Exception:  # noqa: BLE001
        pass


def test_humo_pestana_personaje_y_titulos(tmp_path):
    root = _tk_root()
    try:
        root.withdraw()
        app, profs, _ = _make_app(tmp_path, root)
        _pump(root)
        ct = app.char_tab
        assert ct.lists["personaje"].get(0, "end") == (PJ,) and ct.lists["zona"].get(0, "end") == (ZONA,)
        assert ct.lists["personaje"].curselection() == (0,)
        assert app.notebook.tab(app.tabs["personaje"], "text") == "Personaje"
        ct.vars["auto_revive"].set(True)
        ct.vars["max_revives"].set("abc")
        assert ct.editor.dirty and app.notebook.tab(app.tabs["personaje"], "text") == "Personaje *"
        assert not ct.fields["max_revives"].valid and ct.fields["max_revives"].msg.get()
        notes = []
        app._notify = lambda t, m: notes.append((t, m))
        assert ct.save() is False and notes and load_profile("personaje", PJ, profs)["pickup"]["dismount"] is True
        ct.vars["max_revives"].set("2")
        assert ct.save() is True and not ct.editor.dirty
        assert load_profile("personaje", PJ, profs)["revive"]["auto_revive"] is True
        assert app.notebook.tab(app.tabs["personaje"], "text") == "Personaje"
        ct.vars["horse_key"].set("f5")
        ct.discard()                                              # Descartar cambios
        assert ct.vars["horse_key"].get() == "" and not ct.editor.dirty
    finally:
        _destroy(root)


def test_humo_pestana_personaje_se_bloquea_con_el_bot_corriendo(tmp_path):
    from bot.ui import controller as ctl
    root = _tk_root()
    try:
        root.withdraw()
        app, _, _ = _make_app(tmp_path, root)
        ct = app.char_tab
        app._set_phase(ctl.RUNNING)
        assert ct.editor.locked and str(ct.buttons["crear"].cget("state")) == "disabled"
        assert str(ct.btn_save.cget("state")) == "disabled" and str(ct.fields["horse_key"].entry.cget("state")) == "disabled"
        assert str(app.banner.button.cget("state")) == "disabled"
        app._set_phase(ctl.STOPPED)
        assert not ct.editor.locked and str(ct.buttons["crear"].cget("state")) == "normal"
    finally:
        _destroy(root)


def test_humo_duplicar_personaje_y_zona_refrescan_los_selectores(tmp_path):
    root = _tk_root()
    try:
        root.withdraw()
        answers = iter([("Copia Pj", True), ("Otro Pj", False), ("Zona B", False), ("Zona C", False)])
        asked, info = [], []
        app, profs, _ = _make_app(tmp_path, root, inform=lambda t, m: info.append((t, m)),
                                  ask_profile_name=lambda title, intro, validator, **kw: (asked.append((title, kw)),
                                                                                           next(answers))[1])
        ct = app.char_tab
        ct.duplicate_character()
        assert "Copia Pj" in app.combos["personaje"].cget("values") and app.vars["personaje"].get() == PJ   # no cambia el elegido
        assert ct.lists["personaje"].get(0, "end") == ("Copia Pj", PJ) and "check_text" in asked[0][1]
        ct.duplicate_character()
        assert "capturar" in info[-1][1].lower()
        assert load_profile("personaje", "Otro Pj", profs)["character"]["name"] == "Otro Pj"
        ct.duplicate_zone()
        assert "Zona B" in app.combos["zona"].cget("values") and app.vars["zona"].get() == ZONA
        ct.new_zone()                                             # Nueva: la elige y va a la pestana Zona
        assert app.vars["zona"].get() == "Zona C" and app.notebook.select() == str(app.tabs["zona"])
        assert app.farm.zona == "Zona C" and app.metin.zona == "Zona C"
        assert "polígono" in info[-1][1]
    finally:
        _destroy(root)


def test_humo_crear_personaje_arma_la_guia_y_selecciona_el_nuevo(tmp_path, monkeypatch):
    import bot.ui.app as appmod
    FakeDialog.made = []
    monkeypatch.setattr(appmod, "CalibrateDialog", FakeDialog)
    root = _tk_root()
    try:
        root.withdraw()
        app, profs, _ = _make_app(tmp_path, root)
        app.char_tab.create_character()
        title, steps, factory, finish, kw = FakeDialog.made[-1]
        assert title == cr.PERSONAJE_TITLE and steps == cr.PERSONAJE_STEPS
        assert kw["name_validator"](PJ) and kw["name_validator"]("Nuevo Pj") is None and kw["name_validator"]("-x")
        cmd = factory("Nuevo Pj")
        assert cmd[3:5] == ["personaje", "Nuevo Pj"] and "--ui" in cmd and "--personaje" not in cmd
        app.char_tab.create_character()                           # una guia a la vez
        assert len(FakeDialog.made) == 1
        profs.joinpath("personajes", "Nuevo Pj.json").write_text(
            json.dumps(load_profile("personaje", PJ, profs)), encoding="utf-8")   # lo que haria calibrate.py
        finish(0)
        assert app.vars["personaje"].get() == "Nuevo Pj" and app._calib is None
        assert "Nuevo Pj" in app.combos["personaje"].cget("values") and app.char_tab.editor.personaje == "Nuevo Pj"
        # capturar el nombre amarillo del elegido: sin campo de nombre y con el perfil como argumento
        app.char_tab.capture_owner_name()
        title, _s, factory, finish, kw = FakeDialog.made[-1]
        assert title == cr.PERSONAJE_CAPTURE_TITLE and "name_validator" not in kw and factory("")[3:5] == ["personaje", "Nuevo Pj"]
        finish(None)
    finally:
        _destroy(root)


def test_humo_franjas_de_config_que_pisa(tmp_path):
    cfg = {"target_metin_names": ["Metin Negro"], "farm_zone": {"polygon": [[0, 0], [5, 0], [5, 5]], "outside_tolerance": 3}}
    root = _tk_root()
    try:
        root.withdraw()
        app, _, cfgp = _make_app(tmp_path, root, config=cfg)
        _pump(root)
        assert app.banner.visible and "target_metin_names" in app.banner.label.cget("text")
        assert app.strips["metin"].visible and "target_metin_names" in app.strips["metin"].label.cget("text")
        assert app.strips["zona"].visible and "farm_zone" in app.strips["zona"].label.cget("text")
        assert not app.strips["bichos"].visible and not app.strips["personaje"].visible
        cfgp.write_text("{}", encoding="utf-8")
        app.refresh_overrides()
        assert not app.banner.visible and not app.strips["metin"].visible
    finally:
        _destroy(root)


def test_humo_migrar_pide_confirmacion_y_arma_el_comando(tmp_path, monkeypatch):
    import bot.ui.app as appmod
    FakeDialog.made = []
    monkeypatch.setattr(appmod, "CalibrateDialog", FakeDialog)
    cfg = {"target_metin_names": ["Metin Negro"]}
    root = _tk_root()
    try:
        root.withdraw()
        asked = []
        decision = {"ok": False}
        app, profs, cfgp = _make_app(tmp_path, root, config=cfg, confirm=lambda t, m: (asked.append((t, m)), decision["ok"])[1])
        raw = cfgp.read_text(encoding="utf-8")
        app.migrate_config()                                      # el usuario dice que no: no se abre nada
        assert asked and "config.json.bak" in asked[0][1] and "target_metin_names" in asked[0][1] and not FakeDialog.made
        decision["ok"] = True
        app.migrate_config()
        title, steps, factory, finish, kw = FakeDialog.made[-1]
        assert title == cr.MIGRAR_TITLE and kw.get("autostart") is True
        cmd = factory("")
        assert cmd[3:5] == ["perfiles", "migrar"] and "--ui" in cmd and "--si" not in cmd
        assert cmd[cmd.index("--zona") + 1] == ZONA and cmd[cmd.index("--config") + 1] == str(cfgp)
        assert cfgp.read_text(encoding="utf-8") == raw            # la ventana no toco config.json
        app.migrate_config()                                      # con la guia abierta no se abre otra
        assert len(FakeDialog.made) == 1
        # al terminar se recarga todo: simulamos lo que haria migrar (el perfil gana la clave y config.json la pierde)
        data = load_profile("zona", ZONA, profs)
        data["target_metin_names"] = ["Metin Negro"]
        from bot.profiles import save_profile
        save_profile("zona", ZONA, data, profs)
        cfgp.write_text("{}", encoding="utf-8")
        finish(0)
        assert app._calib is None and not app.banner.visible
        assert [t.name for t in app.metin.types if t.attack] == ["Metin Negro"]
    finally:
        _destroy(root)


def test_humo_migrar_sin_nada_para_migrar_avisa(tmp_path, monkeypatch):
    import bot.ui.app as appmod
    FakeDialog.made = []
    monkeypatch.setattr(appmod, "CalibrateDialog", FakeDialog)
    root = _tk_root()
    try:
        root.withdraw()
        info = []
        app, _, _ = _make_app(tmp_path, root, inform=lambda t, m: info.append(m))
        app.migrate_config()
        assert info and "nada para migrar" in info[0] and not FakeDialog.made
    finally:
        _destroy(root)


def test_humo_bichos_avanzado_plegable_y_guardado(tmp_path):
    root = _tk_root()
    try:
        root.withdraw()
        app, profs, _ = _make_app(tmp_path, root)
        _pump(root)
        assert not app.adv_box.winfo_ismapped() and app.adv_toggle.cget("text") == "Avanzado ▸"
        app.adv_open.set(True)
        app._toggle_farm_advanced()
        _pump(root)
        assert app.adv_box.winfo_ismapped() and app.adv_toggle.cget("text") == "Avanzado ▾"
        assert set(app.farm_adv_fields) == set(ADVANCED_KEYS)
        app.farm_adv_fields["space_s"].var.set("4")
        app.farm_adv_fields["min_monsters"].var.set("0")
        assert app.farm.dirty and app.notebook.tab(app.tabs["bichos"], "text") == "Modo bichos *"
        assert not app.farm_adv_fields["min_monsters"].valid
        notes = []
        app._notify = lambda t, m: notes.append(m)
        assert app.save_farm_mode() is False and notes
        app.farm_adv_fields["min_monsters"].var.set("2")
        assert app.save_farm_mode() is True
        bi = load_profile("zona", ZONA, profs)["zone"]["bichos"]
        assert bi["space_s"] == 4 and bi["min_monsters"] == 2
        app.farm_adv_fields["space_s"].var.set("9")
        app.discard_farm_mode()                                   # Descartar cambios
        assert app.farm_adv_fields["space_s"].get() == "4" and not app.farm.dirty
    finally:
        _destroy(root)


def test_humo_habilidades_tooltips_y_restante(tmp_path):
    root = _tk_root()
    try:
        root.withdraw()
        app, _, _ = _make_app(tmp_path, root)
        app.editor.add_example(0)
        app.refresh_skills()
        assert set(app.SKILL_TIPS) == {c[0] for c in app.SKILL_COLS}
        app.handle_line('@@STATUS {"state":"x","skills":[{"name":"Aura de Espada","key":"F1","remaining_s":3725}]}')
        app.editor.locked = True
        app.refresh_skills()
        assert app.tree.item("0", "values")[-1] == "1:02:05"
        app.editor.locked = False
        app.add_example(2)
        app.discard_skills()                                      # Descartar cambios
        assert app.tree.get_children() == () and not app.editor.dirty
    finally:
        _destroy(root)


def test_humo_validated_entry_oculta_el_mensaje_sin_error():
    from tkinter import ttk
    from bot.ui.widgets import ValidatedEntry
    root = _tk_root()
    try:
        root.withdraw()
        f = ValidatedEntry(ttk.Frame(root), vd.positive_number, value="1")
        assert not f._msg_label.winfo_manager()
        f.set("x")
        assert f._msg_label.winfo_manager() and f.msg.get()
        f.set("2")
        assert not f._msg_label.winfo_manager()
    finally:
        _destroy(root)


def test_humo_scroll_y_tooltips_de_encabezado():
    from tkinter import ttk
    from bot.ui.widgets import ScrollFrame, heading_tooltips
    root = _tk_root()
    try:
        root.geometry("300x200")
        sf = ScrollFrame(root)
        sf.pack(fill="both", expand=True)
        for i in range(40):
            ttk.Label(sf.body, text=f"fila {i}").pack()
        tree = ttk.Treeview(sf.body, columns=("a", "b"), show="headings")
        tree.pack()
        tip = heading_tooltips(tree, {"a": "ayuda de a", "b": "ayuda de b"})
        _pump(root, 0.3)
        assert sf.body.winfo_reqheight() > sf.canvas.winfo_height()
        sf.canvas.yview_moveto(0.5)
        assert sf.canvas.yview()[0] > 0
        assert tip.heading_column is not None
    finally:
        _destroy(root)


def test_humo_todos_los_controles_editables_tienen_tooltip(tmp_path):
    """Cada Entry/Combobox/Checkbutton/Button de las pestanas tiene un Tooltip enganchado (bind de <Enter>)."""
    from tkinter import ttk
    import tkinter as tk
    root = _tk_root()
    try:
        root.withdraw()
        app, _, _ = _make_app(tmp_path, root, config={"target_metin_names": ["X"]})
        missing, seen = [], []

        def walk(w):
            for ch in w.winfo_children():
                if isinstance(ch, (ttk.Entry, ttk.Combobox, ttk.Checkbutton, ttk.Button, ttk.Menubutton)):
                    seen.append(ch)
                if isinstance(ch, (ttk.Entry, ttk.Combobox, ttk.Checkbutton, ttk.Button, ttk.Menubutton)) \
                        and not isinstance(ch, tk.Toplevel) and not ch.bind("<Enter>"):
                    missing.append(f"{ch.winfo_class()} {ch.cget('text') if 'text' in ch.keys() else ''} {ch}")
                walk(ch)

        walk(app.root)
        assert len(seen) > 40 and missing == []
    finally:
        _destroy(root)
