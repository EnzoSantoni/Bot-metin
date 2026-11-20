"""Tests de los perfiles (bot/profiles.py), del armado del cfg y de los subcomandos de calibrate.py que los usan."""
import copy
import json
import logging
import shutil
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

import calibrate
from bot import calibration as cal
from bot import profiles as pf
from bot.config import DEFAULTS, PROJECT_ROOT, ConfigError, load_config, validate_config
from bot.vision.items import _owner_cache, find_items
from test_calibration import CLIENT, ORIGIN, QUAD, SAMPLES, FakeKeyboard, ScriptedWindow  # noqa: F401

DATA = Path(__file__).parent / "data"
REAL_PROFILES = PROJECT_ROOT / "profiles"
NEW_SECTIONS = ("server", "character", "zone", "_profiles", "skill_timing", "items_catalog", "history", "revive", "schedule")   # schedule: pausa automatica y parada programada; skill_timing: Fase 11; items_catalog e history: Fase 12; revive: Fase 14
ZONA = "Ciudad 1"


def write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def root(tmp_path):
    """Copia de los perfiles reales: ningun test escribe en profiles/ del proyecto."""
    r = tmp_path / "profiles"
    shutil.copytree(REAL_PROFILES, r)
    return r

NEW_SAFETY_KEYS = ("auto_pause_on_user_input", "user_mouse_px", "user_mouse_window_s", "auto_pause_grace_s")   # pausa automatica


def build(path, root, **kw):
    return pf.build_config(path, profiles_root_dir=root, **kw)


def strip_new(cfg: dict) -> dict:
    out = {k: v for k, v in cfg.items() if k not in NEW_SECTIONS}
    out["safety"] = {k: v for k, v in out["safety"].items() if k not in NEW_SAFETY_KEYS}
    return out


def same_as_main(cfg: dict, baseline: dict) -> dict:
    """Excepcion documentada a "igual que main": `spawns.file`. main usa data/Ciudad 1.mapa.json; desarrollo deriva el mapa
    de la zona elegida (profiles/zonas/<zona>.mapa.json). `spawns.map_name` (el nombre de la zona) coincide con el de main.
    Se verifica la derivacion y se compara el resto tal cual."""
    assert baseline["spawns"]["file"] == "data/Ciudad 1.mapa.json" and baseline["spawns"]["map_name"] == ZONA
    assert cfg["spawns"]["file"] == pf.spawns_file_for(ZONA) == f"profiles/zonas/{ZONA}.mapa.json"
    out = strip_new(cfg)
    out["spawns"] = {**out["spawns"], "file": baseline["spawns"]["file"]}
    return out


# ------------------------------------------------------------------ igualdad con el cfg de antes de los perfiles

def test_cfg_igual_al_de_antes_con_el_config_de_enzo(tmp_path):
    p = tmp_path / "config.json"
    shutil.copy(DATA / "config_enzo.json", p)
    baseline = json.loads((DATA / "cfg_baseline_enzo.json").read_text(encoding="utf-8"))
    cfg = load_config(p)
    assert same_as_main(cfg, baseline) == baseline
    assert set(baseline) == set(strip_new(cfg))
    for sec in ("server", "character", "zone"):    # las secciones nuevas traen los valores por defecto de los perfiles
        assert {k: v for k, v in cfg[sec].items() if k != "name"} == {k: v for k, v in DEFAULTS[sec].items() if k != "name"}


def test_cfg_igual_al_de_antes_con_config_vacio(tmp_path):
    p = write(tmp_path / "config.json", {})
    baseline = json.loads((DATA / "cfg_baseline_empty.json").read_text(encoding="utf-8"))
    cfg = load_config(p)
    # unica diferencia con DEFAULTS puros: el perfil Guabina fija el titulo de la ventana
    assert cfg["window_title"] == "Guabina" and baseline["window_title"] == "Metin2"
    baseline["window_title"] = "Guabina"
    assert same_as_main(cfg, baseline) == baseline
    assert cfg["_profiles"]["overrides"] == []


def test_load_config_es_build_config_con_la_seleccion_de_config_json(tmp_path):
    p = write(tmp_path / "config.json", {"profiles": {"zona": ZONA}})
    a, b = load_config(p), pf.build_config(p)
    assert a == b and a["_profiles"]["servidor"] == "Guabina"
    assert "profiles" not in a


def test_perfiles_reales_validan_y_tienen_su_categoria():
    for kind in pf.KINDS:
        names = pf.list_profiles(kind)
        assert names, kind
        for n in names:
            pf.load_profile(kind, n)
    assert pf.list_profiles("zona") == [ZONA]


# ------------------------------------------------------------------ mapa de metin por zona

def test_mapa_derivado_del_nombre_de_la_zona(tmp_path, root):
    write(root / "zonas" / "Otra.json", {"zone": {"name": "Otra"}})
    p = write(tmp_path / "config.json", {})
    assert build(p, root)["spawns"]["file"] == f"profiles/zonas/{ZONA}.mapa.json"
    assert build(p, root, zona="Otra")["spawns"]["file"] == "profiles/zonas/Otra.mapa.json"
    assert not list(root.glob("zonas/*.mapa.json"))   # derivar no escribe nada


def test_mapa_explicito_en_el_perfil_de_zona(tmp_path, root):
    write(root / "zonas" / "Otra.json", {"spawns": {"file": "data/otro.json"}})
    cfg = build(write(tmp_path / "config.json", {}), root, zona="Otra")
    assert cfg["spawns"]["file"] == "data/otro.json" and cfg["spawns"]["enabled"] is True


def test_mapa_en_config_json_gana(tmp_path, root):
    p = write(tmp_path / "config.json", {"spawns": {"file": "data/mio.json"}})
    assert build(p, root)["spawns"]["file"] == "data/mio.json"


def test_spawns_es_categoria_de_zona(root):
    assert pf.kind_of(("spawns", "file")) == "zona"
    pf.validate_profile_data("zona", {"spawns": {"file": "x.json"}})
    with pytest.raises(ConfigError):
        pf.validate_profile_data("servidor", {"spawns": {"file": "x.json"}})


def test_el_mapa_no_se_lista_como_perfil(root):
    write(root / "zonas" / f"{ZONA}.mapa.json", {"version": 1, "spots": []})
    assert pf.list_profiles("zona") == [ZONA]


def test_mapa_de_zona_ignorado_por_git():
    gi = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "profiles/zonas/*.mapa.json" in gi


# ------------------------------------------------------------------ precedencia, avisos, seleccion

def test_precedencia_defaults_servidor_personaje_zona_config(tmp_path, root):
    write(root / "servidores" / "S.json", {"window_title": "S", "controls": {"pickup": "x"}})
    write(root / "personajes" / "P.json", {"coords_filter": {"max_speed": 50.0}, "character": {"name": "P"}})
    write(root / "zonas" / "Z.json", {"target_metin_names": ["Metin Negro"], "zone": {"name": "Z"}})
    p = write(tmp_path / "config.json", {"coords_filter": {"slack": 9.0}, "window_title": "Cfg"})
    cfg = build(p, root, servidor="S", personaje="P", zona="Z")
    assert cfg["window_title"] == "Cfg"                       # config.json gana sobre el servidor
    assert cfg["controls"]["pickup"] == "x" and cfg["controls"]["mount"] == "ctrl+g"   # merge profundo
    assert cfg["coords_filter"]["max_speed"] == 50.0 and cfg["coords_filter"]["slack"] == 9.0
    assert cfg["target_metin_names"] == ["Metin Negro"]
    assert cfg["character"]["name"] == "P" and cfg["character"]["potions"]["hp_key"] == "f2"
    assert cfg["_profiles"] | {"overrides": []} == {"servidor": "S", "personaje": "P", "zona": "Z", "overrides": []}


def test_aviso_de_pisado_con_valor_distinto_y_con_el_mismo(tmp_path, root, caplog):
    p = write(tmp_path / "config.json", {"farm_zone": {"polygon": QUAD, "outside_tolerance": 30},
                                         "window_title": "Guabina", "debug": {"enabled": True}})
    with caplog.at_level(logging.WARNING, logger="bot.profiles"):
        cfg = build(p, root)
    msgs = [r.getMessage() for r in caplog.records if r.name == "bot.profiles"]
    assert f"config.json pisa farm_zone del perfil de zona '{ZONA}'" in msgs     # valor distinto: sin la nota
    same = [m for m in msgs if "window_title" in m]
    assert len(same) == 1 and "con el mismo valor" in same[0] and "perfil de servidor 'Guabina'" in same[0]
    assert not any("debug" in m for m in msgs)            # debug es general: no pisa nada
    assert len(msgs) == 2                                  # una sola vez por seccion
    ov = {o["section"]: o for o in cfg["_profiles"]["overrides"]}
    assert set(ov) == {"farm_zone", "window_title"}
    assert ov["farm_zone"]["same"] is False and ov["farm_zone"]["paths"] == ["farm_zone.polygon", "farm_zone.outside_tolerance"]
    assert ov["window_title"]["same"] is True and ov["window_title"]["kind"] == "servidor"
    assert cfg["farm_zone"]["polygon"] == QUAD             # y config.json gana


def test_warn_false_no_loguea(tmp_path, root, caplog):
    p = write(tmp_path / "config.json", {"window_title": "Guabina"})
    with caplog.at_level(logging.WARNING, logger="bot.profiles"):
        cfg = build(p, root, warn=False)
    assert not caplog.records and cfg["_profiles"]["overrides"]


def test_seleccion_por_argumentos_y_por_config_json(tmp_path, root):
    write(root / "zonas" / "Otra.json", {"zone": {"name": "Otra", "farm_mode": "bichos"}})
    write(root / "personajes" / "Sura.json", {"character": {"name": "Sura", "class": "sura"}})
    p = write(tmp_path / "config.json", {"profiles": {"zona": "Otra", "personaje": "Sura"}})
    cfg = build(p, root)
    assert (cfg["_profiles"]["zona"], cfg["_profiles"]["personaje"], cfg["_profiles"]["servidor"]) == ("Otra", "Sura", "Guabina")
    assert cfg["zone"]["farm_mode"] == "bichos" and cfg["character"]["class"] == "sura"
    cfg = build(p, root, zona=ZONA, personaje="Farfarfa")      # los argumentos pisan el bloque
    assert cfg["zone"]["farm_mode"] == "metin" and cfg["character"]["name"] == "Farfarfa"
    assert "profiles" not in cfg


@pytest.mark.parametrize("block", [[], {"zona": ""}, {"otro": "x"}, {"zona": 3}])
def test_bloque_profiles_invalido(tmp_path, root, block):
    p = write(tmp_path / "config.json", {"profiles": block})
    with pytest.raises(ConfigError, match="profiles"):
        build(p, root)


def test_perfil_inexistente_lista_los_disponibles(tmp_path, root):
    p = write(tmp_path / "config.json", {})
    with pytest.raises(ConfigError) as e:
        build(p, root, zona="Nope")
    assert "Nope" in str(e.value) and ZONA in str(e.value)
    write(root / "servidores" / "Otro.json", {})
    with pytest.raises(ConfigError, match="Guabina.*Otro|Otro.*Guabina"):
        build(p, root, servidor="Nope")


def test_perfil_con_clave_de_otra_categoria(tmp_path, root):
    write(root / "zonas" / "Mala.json", {"controls": {"pickup": "x"}})
    p = write(tmp_path / "config.json", {})
    with pytest.raises(ConfigError, match="la clave 'controls' no va en un perfil de zona; va en el de servidor"):
        build(p, root, zona="Mala")
    write(root / "servidores" / "Mala.json", {"vision": {"items": {"owner_template": "x.png"}}})
    with pytest.raises(ConfigError, match="'vision.items.owner_template' no va en un perfil de servidor; va en el de personaje"):
        build(p, root, servidor="Mala")
    write(root / "personajes" / "Mala.json", {"attack": {"max_seconds": 5}})
    with pytest.raises(ConfigError, match="'attack' es configuracion general"):
        build(p, root, personaje="Mala")
    with pytest.raises(ConfigError):
        pf.save_profile("zona", "X", {"farm_zone": {}, "safety": {}}, root)
    assert not (root / "zonas" / "X.json").exists()


@pytest.mark.parametrize("name", ["", "  ", "../x", "a/b", "a\\b", "a..b", "x:y", "a?", "con", "x.", " x", 'a"b', "a|b", "a*", "a\nb"])
def test_nombres_invalidos(root, name):
    with pytest.raises(ConfigError):
        pf.profile_path("zona", name, root)


def test_nombres_validos_y_tipo_invalido(root):
    assert pf.profile_path("zona", ZONA, root).name == f"{ZONA}.json"
    assert pf.profile_path("personaje", "Mi Char-2", root).parent.name == "personajes"
    with pytest.raises(ConfigError):
        pf.profile_path("clan", "x", root)


def test_save_y_load_profile_con_bak(root):
    data = {"zone": {"name": "N", "farm_mode": "metin"}}
    path = pf.save_profile("zona", "Nueva - Zona", data, root)
    assert pf.load_profile("zona", "Nueva - Zona", root) == data
    assert not path.with_name(path.name + ".bak").exists()
    pf.save_profile("zona", "Nueva - Zona", {"zone": {"name": "M"}}, root)
    assert json.loads(path.with_name(path.name + ".bak").read_text(encoding="utf-8")) == data
    assert "Nueva - Zona" in pf.list_profiles("zona", root)


def test_save_profile_conserva_utf8(root):
    pf.save_profile("zona", "Ñandú", {"zone": {"name": "Ñandú"}}, root)
    assert "Ñandú" in pf.profile_path("zona", "Ñandú", root).read_text(encoding="utf-8")


def test_carpeta_profiles_ausente_funciona_como_antes(tmp_path):
    p = write(tmp_path / "config.json", {"window_title": "X"})
    cfg = pf.build_config(p, profiles_root_dir=tmp_path / "no_hay")
    assert cfg["window_title"] == "X" and cfg["_profiles"]["servidor"] is None
    assert {k: v for k, v in cfg.items() if k != "_profiles"} == pf._merge(DEFAULTS, {"window_title": "X"})
    with pytest.raises(ConfigError, match="carpeta de perfiles"):
        pf.build_config(p, zona="Z", profiles_root_dir=tmp_path / "no_hay")


def test_bot_no_recorre_las_claves_del_cfg(tmp_path):
    """`_profiles` no rompe a quien arma teclas, mascaras ni la validacion."""
    p = write(tmp_path / "config.json", {})
    cfg = load_config(p)
    validate_config(cfg)
    assert "_profiles" in cfg


# ------------------------------------------------------------------ validacion de las secciones nuevas

def changed(path, value):
    cfg = copy.deepcopy(DEFAULTS)
    d = cfg
    for k in path[:-1]:
        d = d[k]
    d[path[-1]] = value
    return cfg


SKILL = {"name": "Aura de Espada", "key": "f1", "duration_s": 60, "requires_dismount": True, "animation_s": 1.5, "enabled": True}


def test_defaults_de_las_secciones_nuevas_validan():
    validate_config(copy.deepcopy(DEFAULTS))
    cfg = changed(("character", "skills"), [SKILL, {**SKILL, "name": "Defensiva", "key": "4", "requires_dismount": False}])
    validate_config(cfg)
    validate_config(changed(("server", "pickup_mode"), "todo_junto"))
    validate_config(changed(("zone", "farm_mode"), "bichos"))
    validate_config(changed(("character", "class"), "guerrero"))


@pytest.mark.parametrize("path,value", [
    (("server", "pickup_mode"), "de_a_dos"),
    (("server", "captcha_type"), ""),
    (("server", "name"), 3),
    (("character", "class"), 3),
    (("character", "potions", "hp_key"), "f99"),
    (("character", "potions", "mp_key"), ""),
    (("character", "potions", "hp_key"), "f12"),          # tecla de panico
    (("character", "potions", "auto"), "si"),
    (("character", "skills"), {}),
    (("character", "skills"), [{**SKILL, "key": "tecla_rara"}]),
    (("character", "skills"), [{**SKILL, "duration_s": -1}]),
    (("character", "skills"), [{**SKILL, "animation_s": "x"}]),
    (("character", "skills"), [{**SKILL, "enabled": 1}]),
    (("character", "skills"), [{**SKILL, "name": " "}]),
    (("character", "skills"), [{k: v for k, v in SKILL.items() if k != "requires_dismount"}]),
    (("zone", "farm_mode"), "pescar"),
    (("zone", "metin_priority"), "Metin Negro"),
    (("zone", "metin_priority"), [""]),
    (("zone", "bichos", "lure_key"), "zz"),
    (("zone", "bichos", "lure_every_s"), -5),
    (("zone", "bichos", "lure_every_s"), True),
])
def test_validacion_de_las_secciones_nuevas(path, value):
    with pytest.raises(ConfigError):
        validate_config(changed(path, value))


# ------------------------------------------------------------------ perfiles migrar (ida y vuelta)

class Ctx:
    """Sesion de calibrate con config.json y perfiles en una carpeta temporal."""

    def __init__(self, tmp_path, root, raw, answers=(), yes=False, images=(), keys=(), **kw):
        self.path = write(tmp_path / "config.json", raw)
        self.root = root
        self.out: list[str] = []
        answers = list(answers)

        def ask(prompt):
            self.out.append(prompt)
            return answers.pop(0) if answers else "n"
        self.s = cal.Session(cfg=pf.build_config(self.path, profiles_root_dir=root, warn=False), config_path=str(self.path),
                             yes=yes, profiles_root=root, say=self.out.append, ask=ask,
                             window=ScriptedWindow(list(images) or [np.zeros((10, 10, 3), np.uint8)]),
                             keyboard=FakeKeyboard(keys), **kw)
        self.s.cfg["debug"]["dir"] = str(tmp_path / "debug")

    def raw(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def prof(self, kind, name):
        return json.loads(pf.profile_path(kind, name, self.root).read_text(encoding="utf-8"))


ENZO_RAW = json.loads((DATA / "config_enzo.json").read_text(encoding="utf-8"))


def test_migrar_ida_y_vuelta(tmp_path, root, caplog):
    raw = {**ENZO_RAW, "controls": {"pickup": "x"}, "pickup": {"dismount": True, "max_seconds": 15.0},
           "coords_filter": {"max_speed": 44.0}, "attack": {"max_seconds": 99.0}}
    c = Ctx(tmp_path, root, raw, answers=["s"])
    before = pf.build_config(c.path, profiles_root_dir=root, warn=False)
    assert before["_profiles"]["overrides"]            # antes de migrar hay avisos
    assert cal.cmd_perfiles(c.s, "migrar") == 0
    after = pf.build_config(c.path, profiles_root_dir=root)
    assert strip_new_keep(after) == strip_new_keep(before)
    assert after["server"] == {**before["server"]} and after["character"] == before["character"]
    assert after["_profiles"]["overrides"] == []        # sin avisos de pisado
    new_raw = c.raw()
    assert new_raw == {"debug": ENZO_RAW["debug"], "log": ENZO_RAW["log"], "pickup": {"max_seconds": 15.0},
                       "attack": {"max_seconds": 99.0},
                       "profiles": {"servidor": "Guabina", "personaje": "Farfarfa", "zona": ZONA}}
    assert c.prof("servidor", "Guabina")["window_title"] == "Guabina"
    assert c.prof("servidor", "Guabina")["controls"]["pickup"] == "x"
    assert c.prof("personaje", "Farfarfa")["coords_filter"]["max_speed"] == 44.0
    assert c.prof("personaje", "Farfarfa")["pickup"] == {"dismount": True}
    assert c.prof("zona", ZONA)["farm_zone"] == ENZO_RAW["farm_zone"]
    for f in (c.path, pf.profile_path("servidor", "Guabina", root), pf.profile_path("zona", ZONA, root),
              pf.profile_path("personaje", "Farfarfa", root)):
        assert f.with_name(f.name + ".bak").exists()
    assert json.loads(c.path.with_name("config.json.bak").read_text(encoding="utf-8")) == raw


def strip_new_keep(cfg):
    return {k: v for k, v in cfg.items() if k != "_profiles"}


def test_migrar_responder_n_no_toca_nada(tmp_path, root):
    c = Ctx(tmp_path, root, ENZO_RAW, answers=["n"])
    original = c.path.read_text(encoding="utf-8")
    assert cal.cmd_perfiles(c.s, "migrar") == 1
    assert c.path.read_text(encoding="utf-8") == original
    assert not c.path.with_name("config.json.bak").exists()
    assert any("Plan de migracion" in line for line in c.out)


def test_migrar_sin_nada_que_migrar(tmp_path, root):
    c = Ctx(tmp_path, root, {"debug": {"enabled": True}})
    assert cal.cmd_perfiles(c.s, "migrar") == 0
    assert c.raw() == {"debug": {"enabled": True}}


def test_migrar_va_al_perfil_elegido_por_argumento(tmp_path, root):
    write(root / "zonas" / "Otra.json", {"zone": {"name": "Otra"}})
    p = write(tmp_path / "config.json", {"farm_zone": ENZO_RAW["farm_zone"]})
    s = cal.Session(cfg=pf.build_config(p, zona="Otra", profiles_root_dir=root, warn=False), config_path=str(p), yes=True,
                    profiles_root=root, say=lambda m: None)
    assert cal.cmd_perfiles(s, "migrar") == 0
    assert json.loads(p.read_text(encoding="utf-8")) == {"profiles": {"servidor": "Guabina", "personaje": "Farfarfa", "zona": "Otra"}}
    assert pf.load_profile("zona", "Otra", root)["farm_zone"] == ENZO_RAW["farm_zone"]


def test_listar_muestra_perfiles_activos_y_pisados(tmp_path, root):
    write(root / "zonas" / "Otra.json", {})
    c = Ctx(tmp_path, root, ENZO_RAW)
    assert cal.cmd_perfiles(c.s, "listar") == 0
    text = "\n".join(c.out)
    assert f"* {ZONA}" in text and "  Otra" in text and "* Farfarfa" in text and "config.json pisa farm_zone" in text


# ------------------------------------------------------------------ destino de los subcomandos

@pytest.fixture
def imgs(load_sample):
    return [load_sample(n) for n in SAMPLES]


def test_zona_guarda_en_el_perfil_de_zona(tmp_path, root, imgs):
    c = Ctx(tmp_path, root, {"debug": {"enabled": True}}, answers=["s"], images=imgs, keys=["f9"] * 4 + ["f10"])
    assert cal.cmd_zona(c.s, tolerancia=40) == 0
    assert c.raw() == {"debug": {"enabled": True}}                   # config.json intacto
    z = c.prof("zona", ZONA)
    assert z["farm_zone"]["polygon"] == QUAD and z["farm_zone"]["outside_tolerance"] == 40
    assert z["zone"]["name"] == ZONA and "target_metin_names" in z    # el resto del perfil se conserva
    assert pf.profile_path("zona", ZONA, root).with_name(ZONA + ".json.bak").exists()
    assert c.s.cfg["farm_zone"]["polygon"] == QUAD and c.s.cfg["_profiles"]["zona"] == ZONA
    assert any("[perfil de zona" in line for line in c.out)


def test_zona_pisada_en_config_json_se_guarda_ahi_y_avisa(tmp_path, root, imgs):
    c = Ctx(tmp_path, root, ENZO_RAW, answers=["s"], images=imgs, keys=["f9"] * 4 + ["f10"])
    zona_json = pf.profile_path("zona", ZONA, root).read_text(encoding="utf-8")
    assert cal.cmd_zona(c.s) == 0
    assert c.raw()["farm_zone"]["polygon"] == QUAD
    assert pf.profile_path("zona", ZONA, root).read_text(encoding="utf-8") == zona_json
    assert any("pisa el perfil de zona" in line and "perfiles migrar" in line for line in c.out)


def test_velocidad_guarda_en_el_perfil_de_personaje(tmp_path, root):
    t = [0.0]
    n = [0]

    def fake_read(img, cfg):
        n[0] += 1
        return SimpleNamespace(x=400 + n[0], y=200)

    c = Ctx(tmp_path, root, {}, answers=["s"], keys=["f9"], read_coords_fn=fake_read,
            clock=lambda: t[0], sleep=lambda d: t.__setitem__(0, t[0] + d))
    assert cal.cmd_velocidad(c.s, 10) == 0
    assert c.raw() == {}
    assert c.prof("personaje", "Farfarfa")["coords_filter"]["max_speed"] == 15.0


def test_ventana_guarda_en_el_perfil_de_servidor(tmp_path, root):
    wins = [SimpleNamespace(title="Guabina 2", client_rect_on_screen=cal.Rect(0, 0, 1900, 1000), minimized=False)]
    c = Ctx(tmp_path, root, {}, answers=["1", "s"], list_windows_fn=lambda f: wins)
    assert cal.cmd_ventana(c.s) == 0
    assert c.raw() == {}
    sv = c.prof("servidor", "Guabina")
    assert sv["window_title"] == "Guabina 2" and sv["expected_client_size"] == [1900, 1000]
    assert sv["server"]["name"] == "Guabina" and "controls" in sv
    assert c.s.cfg["window_title"] == "Guabina 2"


def test_metin_guarda_plantillas_y_la_lista_en_el_perfil_de_zona(tmp_path, root, load_sample):
    from bot.config import DEFAULTS as D
    from bot.vision.metin import find_metins
    img = load_sample("real_metin_negro_seleccionado.png")
    cx, cy = [d for d in find_metins(img, D) if d.name == "metin_negro"][0].label_rect.center
    c = Ctx(tmp_path, root, {"vision": {"metin": {"label_templates_dir": str(tmp_path / "labels")},
                                        "panel": {"name_templates_dir": str(tmp_path / "panel_names")}}},
            answers=["s", "s"], images=[img], keys=["f9", "f9"],
            cursor_fn=lambda: (cx + ORIGIN[0], cy + ORIGIN[1]))
    assert cal.cmd_metin(c.s, "Metin de Prueba") == 0
    assert (tmp_path / "labels" / "metin_de_prueba.png").exists()
    assert c.prof("zona", ZONA)["target_metin_names"] == ["Metin de Oscuridad", "Metin Negro", "Metin de Prueba"]
    assert "target_metin_names" not in c.raw()


def test_sin_carpeta_de_perfiles_todo_va_a_config_json(tmp_path, imgs):
    p = write(tmp_path / "config.json", {})
    out: list[str] = []
    cfg = pf.build_config(p, profiles_root_dir=tmp_path / "no_hay")
    s = cal.Session(cfg=cfg, config_path=str(p), yes=True, profiles_root=tmp_path / "no_hay", say=out.append,
                    window=ScriptedWindow(imgs), keyboard=FakeKeyboard(["f9"] * 4 + ["f10"]))
    s.cfg["debug"]["dir"] = str(tmp_path / "debug")
    assert cal.cmd_zona(s) == 0
    assert json.loads(p.read_text(encoding="utf-8"))["farm_zone"]["polygon"] == QUAD
    assert cal.cmd_perfiles(s, "migrar") == 1


# ------------------------------------------------------------------ calibrate personaje

@pytest.fixture
def piso(load_sample):
    return load_sample("items_en_piso.png")


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    """Las rutas relativas de calibration (templates/...) caen en tmp_path, no en el proyecto."""
    real = cal.resolve_path
    monkeypatch.setattr(cal, "resolve_path", lambda p: Path(p) if Path(p).is_absolute() else tmp_path / p)
    return real


def owner_box_center(img, cfg):
    return [i for i in find_items(img, cfg) if i.reserved][0].rect.center


def test_personaje_guarda_plantilla_y_perfil(tmp_path, root, piso, in_tmp):
    c = Ctx(tmp_path, root, {}, answers=["s", "s", "s"], images=[piso], keys=["f9", "f10"])
    cx, cy = owner_box_center(piso, c.s.cfg)
    c.s.cursor_fn = lambda: (cx + ORIGIN[0], cy + ORIGIN[1])
    assert cal.cmd_personaje(c.s, "Testchar", "sura") == 0
    tpl = cv2.imread(str(tmp_path / "templates" / "items" / "owner_testchar.png"), cv2.IMREAD_GRAYSCALE)
    real = cv2.imread(str(in_tmp("templates/items/owner.png")), cv2.IMREAD_GRAYSCALE)
    assert np.array_equal(tpl, real)                     # el mismo "Farfarfa's" amarillo del proyecto
    prof = c.prof("personaje", "Testchar")
    assert prof["character"] == {**DEFAULTS["character"], "name": "Testchar", "class": "sura"}
    assert prof["vision"]["items"]["owner_template"] == "templates/items/owner_testchar.png"
    assert prof["pickup"] == {"dismount": True} and prof["coords_filter"] == {"max_speed": 30.0}
    assert (tmp_path / "debug" / "calibrate_personaje.png").exists()
    assert c.raw()["profiles"] == {"personaje": "Testchar"}            # lo dejo activo (3er "s")
    assert c.path.with_name("config.json.bak").exists()
    cfg = pf.build_config(c.path, profiles_root_dir=root, warn=False)
    assert cfg["character"]["name"] == "Testchar" and cfg["_profiles"]["personaje"] == "Testchar"


def test_personaje_plantilla_reconoce_los_objetos_reservados(tmp_path, root, piso, in_tmp):
    c = Ctx(tmp_path, root, {}, yes=True, images=[piso], keys=["f9", "f10"])
    cx, cy = owner_box_center(piso, c.s.cfg)
    c.s.cursor_fn = lambda: (cx + ORIGIN[0], cy + ORIGIN[1])
    assert cal.cmd_personaje(c.s, "Testchar") == 0
    cfg = pf.build_config(c.path, personaje="Testchar", profiles_root_dir=root, warn=False)
    cfg["vision"]["items"]["owner_template"] = str(tmp_path / cfg["vision"]["items"]["owner_template"])
    _owner_cache.clear()
    assert [i.reserved for i in find_items(piso, cfg)].count(True) == 4
    assert c.raw() == {}                                  # --si: no cambia el personaje activo
    assert any("--personaje" in line for line in c.out)


def test_personaje_rechazar_el_recorte_no_guarda(tmp_path, root, piso, in_tmp):
    c = Ctx(tmp_path, root, {}, answers=["n"], images=[piso], keys=["f9", "f10"])
    cx, cy = owner_box_center(piso, c.s.cfg)
    c.s.cursor_fn = lambda: (cx + ORIGIN[0], cy + ORIGIN[1])
    assert cal.cmd_personaje(c.s, "Testchar") == 1
    assert not (tmp_path / "templates").exists()
    assert "Testchar" not in pf.list_profiles("personaje", root)


def test_personaje_sin_objeto_reservado_sigue_esperando(tmp_path, root, piso, in_tmp):
    c = Ctx(tmp_path, root, {}, answers=["s", "s", "n"], images=[np.zeros_like(piso), piso], keys=["f9", "f9", "f10"])
    cx, cy = owner_box_center(piso, c.s.cfg)
    c.s.cursor_fn = lambda: (cx + ORIGIN[0], cy + ORIGIN[1])
    assert cal.cmd_personaje(c.s, "Testchar") == 0
    assert any("No se vio ningun objeto" in line for line in c.out)
    assert "Testchar" in pf.list_profiles("personaje", root) and c.raw() == {}


def test_personaje_elige_el_recuadro_mas_cercano_al_mouse(tmp_path, root, piso, in_tmp):
    cands = cal.owner_candidates(piso, DEFAULTS["vision"]["items"])
    assert len(cands) == 4
    c = Ctx(tmp_path, root, {}, answers=["s", "s", "n"], images=[piso], keys=["f9", "f10"])
    rect = cands[-1][0]
    c.s.cursor_fn = lambda: (rect.center[0] + ORIGIN[0], rect.center[1] + ORIGIN[1])
    assert cal.cmd_personaje(c.s, "Testchar") == 0
    assert any(f"en {rect.center}" in line for line in c.out)


def test_personaje_cancelado_con_f12_no_guarda(tmp_path, root, piso, in_tmp):
    c = Ctx(tmp_path, root, {}, images=[piso], keys=["f12"])
    with pytest.raises(cal.Cancelled):
        cal.cmd_personaje(c.s, "Testchar")
    assert "Testchar" not in pf.list_profiles("personaje", root)


@pytest.mark.parametrize("name", ["../x", "a/b", ""])
def test_personaje_nombre_invalido(tmp_path, root, name):
    c = Ctx(tmp_path, root, {})
    assert cal.cmd_personaje(c.s, name) == 1


# ------------------------------------------------------------------ CLI

def test_parser_perfiles_y_personaje():
    p = calibrate.build_parser()
    a = p.parse_args(["personaje", "Mi Char", "--clase", "sura", "--zona", "Z", "--servidor", "S", "--personaje", "P"])
    assert (a.cmd, a.nombre, a.clase, a.zona, a.servidor, a.personaje) == ("personaje", "Mi Char", "sura", "Z", "S", "P")
    assert p.parse_args(["perfiles", "migrar"]).accion == "migrar"
    assert p.parse_args(["perfiles", "listar", "--si"]).si is True
    with pytest.raises(SystemExit):
        p.parse_args(["perfiles", "borrar"])
    assert p.parse_args(["zona"]).zona is None


def test_main_perfiles_listar(tmp_path, capsys):
    p = write(tmp_path / "config.json", ENZO_RAW)
    assert calibrate.main(["perfiles", "listar", "--config", str(p)]) == 0
    out = capsys.readouterr().out
    assert "config.json pisa valores de los perfiles activos" in out and "config.json pisa farm_zone" in out and f"* {ZONA}" in out


def test_main_perfil_inexistente(tmp_path, capsys):
    p = write(tmp_path / "config.json", {})
    assert calibrate.main(["perfiles", "listar", "--config", str(p), "--zona", "Nope"]) == 1
    assert "Disponibles" in capsys.readouterr().out


def test_run_bot_loguea_los_perfiles(tmp_path, caplog):
    import run_bot
    cfg = pf.build_config(write(tmp_path / "config.json", {"window_title": "Guabina"}), warn=False)
    with caplog.at_level(logging.INFO, logger="run_bot"):
        run_bot.log_profiles(cfg)
    text = caplog.text
    assert f"Perfiles: servidor=Guabina, personaje=Farfarfa, zona={ZONA}" in text
    assert "config.json pisa window_title del perfil de servidor 'Guabina'" in text


# ------------------------------------------------------------------ renombre de la zona: "Guabina - Oscuridad y Negro" -> "Ciudad 1"

OLD_ZONA = "Guabina - Oscuridad y Negro"


@pytest.fixture
def alias_log(caplog):
    pf._alias_logged.clear()
    caplog.set_level(logging.INFO, logger="bot.profiles")
    return caplog


def test_listado_de_perfiles_muestra_ciudad_1_y_no_el_viejo(root):
    zonas = pf.list_profiles("zona", root)
    assert "Ciudad 1" in zonas and OLD_ZONA not in zonas
    assert pf.load_profile("zona", "Ciudad 1", root)["zone"]["name"] == "Ciudad 1"
    assert pf.DEFAULT_SELECTION["zona"] == "Ciudad 1"


def test_alias_en_config_json_y_linea_de_comandos(tmp_path, root, alias_log):
    p = write(tmp_path / "config.json", {"profiles": {"zona": OLD_ZONA}})
    cfg = build(p, root)
    assert cfg["_profiles"]["zona"] == "Ciudad 1" and cfg["zone"]["name"] == "Ciudad 1"
    assert pf.resolve_selection({}, zona=OLD_ZONA)["zona"] == "Ciudad 1"           # --zona con el nombre viejo
    assert build(write(tmp_path / "otro.json", {}), root, zona=OLD_ZONA)["_profiles"]["zona"] == "Ciudad 1"
    avisos = [r.getMessage() for r in alias_log.records if "ahora se llama" in r.getMessage()]
    assert avisos == [f"El perfil de zona '{OLD_ZONA}' ahora se llama 'Ciudad 1'"]   # una sola vez
    assert pf.resolve_selection({"profiles": {"zona": "Otra"}})["zona"] == "Otra"    # los demas nombres no se tocan


def test_alias_en_los_ajustes_de_la_interfaz(tmp_path, root):
    from bot.ui import controller as ctl
    s = tmp_path / "ui_settings.json"
    s.write_text(json.dumps({"servidor": "Guabina", "personaje": "Farfarfa", "zona": OLD_ZONA}), encoding="utf-8")
    o = ctl.load_settings(s, ctl.available_profiles(root))
    assert o.zona == "Ciudad 1"
    assert json.loads(s.read_text(encoding="utf-8"))["zona"] == OLD_ZONA     # el archivo se actualiza al guardar
    assert ctl.save_settings(o, s)
    assert json.loads(s.read_text(encoding="utf-8"))["zona"] == "Ciudad 1"


def test_map_name_sale_del_perfil_de_zona(tmp_path, root):
    p = write(tmp_path / "config.json", {})
    assert build(p, root)["spawns"]["map_name"] == "Ciudad 1"
    otra = pf.load_profile("zona", ZONA, root)
    pf.save_profile("zona", "Otra", otra, root)
    assert build(p, root, zona="Otra")["spawns"]["map_name"] == "Otra"
    otra["spawns"] = {"map_name": "Mapa X"}
    pf.save_profile("zona", "Otra", otra, root)
    assert build(p, root, zona="Otra")["spawns"]["map_name"] == "Mapa X"          # lo definido en el perfil manda


def _zmap(tmp_path, name):
    return tmp_path / "profiles" / "zonas" / f"{name}{pf.MAP_SUFFIX}"


def test_migracion_del_mapa_desde_el_nombre_viejo(tmp_path):
    from bot.spawns import migrate_zone_map
    old, new = _zmap(tmp_path, OLD_ZONA), _zmap(tmp_path, "Ciudad 1")
    old.parent.mkdir(parents=True)
    old.write_text('{"viejo": 1}', encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "Ciudad 1.mapa.json").write_text('{"data": 1}', encoding="utf-8")
    (tmp_path / "data" / "mapa_metin.json").write_text('{"legacy": 1}', encoding="utf-8")
    assert migrate_zone_map(new) is True
    assert json.loads(new.read_text(encoding="utf-8")) == {"viejo": 1}             # 1) el mapa con el nombre viejo de la zona
    assert not old.exists() and old.with_name(old.name + ".migrado").exists()
    assert (tmp_path / "data" / "Ciudad 1.mapa.json").exists()                      # las otras fuentes no se tocan
    assert migrate_zone_map(new) is False                                           # ya existe: no pisa nada


def test_migracion_del_mapa_orden_de_las_fuentes_en_data(tmp_path):
    from bot.spawns import migrate_zone_map
    new = _zmap(tmp_path, "Ciudad 1")
    (tmp_path / "data").mkdir()
    assert migrate_zone_map(new) is False and not new.exists()                      # sin fuentes no pasa nada
    legacy = tmp_path / "data" / "mapa_metin.json"
    legacy.write_text('{"legacy": 1}', encoding="utf-8")
    named = tmp_path / "data" / "Ciudad 1.mapa.json"
    named.write_text('{"data": 1}', encoding="utf-8")
    assert migrate_zone_map(new) is True
    assert json.loads(new.read_text(encoding="utf-8")) == {"data": 1}               # 2) data/<zona>.mapa.json antes que 3) mapa_metin.json
    assert legacy.exists() and named.with_name(named.name + ".migrado").exists()
    new.unlink()
    named.with_name(named.name + ".migrado").unlink()
    assert migrate_zone_map(new) is True
    assert json.loads(new.read_text(encoding="utf-8")) == {"legacy": 1}
    assert legacy.with_name("mapa_metin.json.migrado").exists()


def test_spawn_map_from_config_migra_el_mapa_de_la_zona(tmp_path):
    from bot.spawns import spawn_map_from_config
    old = _zmap(tmp_path, OLD_ZONA)
    old.parent.mkdir(parents=True)
    old.write_text(json.dumps({"version": 1, "spots": [{"x": 450, "y": 150, "n": 2, "last_broken": 1.0, "last_visit": 0.0}]}),
                   encoding="utf-8")
    sp = {**DEFAULTS["spawns"], "file": str(_zmap(tmp_path, "Ciudad 1")), "map_name": "Ciudad 1"}
    sm = spawn_map_from_config(sp)
    assert [(s.x, s.y) for s in sm.spots] == [(450, 150)]
    assert _zmap(tmp_path, "Ciudad 1").exists()
