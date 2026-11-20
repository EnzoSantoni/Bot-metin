"""Revision general (noche del 2026-11-20): transiciones con captcha en la subida, topes de tiempo, tramos de teclado
descartados por habilidades y senuelo. Todo con backend falso."""
from bot.bot import State, _KeyLeg
from bot.skills import Skill
from test_bot_states import CAPTCHA, Harness, perc


def _captcha_harness():
    box = {"gone": False}
    h = Harness(lambda h, n: perc(captcha=box["cap"]()),
                bot_kwargs={"captcha_fn": lambda img, cfg: box["cap"]()})
    box["cap"] = lambda: CAPTCHA if (h.keys("g") >= 1 and not box["gone"]) else None
    return h, box


def test_captcha_al_subirse_saliendo_de_recoger_hacia_volver():
    h, box = _captcha_harness()
    h.bot.state = State.RECOGER
    h.bot._set_dismounted(True, "test")
    h.bot._enter(State.VOLVER, "test")
    assert h.bot.state is State.CAPTCHA
    assert h.bot._vl_since is None and h.bot._walk is None
    assert h.keys("g") == 1 and h.bot._dismounted is False
    box["gone"] = True
    h.run_until(lambda: h.bot.state is not State.CAPTCHA)
    assert h.bot.state is not State.VOLVER and h.bot._vl_since is None
    assert h.keys("g") == 1   # ya estaba montado: no hay un segundo Ctrl+G


def test_captcha_durante_la_subida_se_vuelve_a_montar_si_volvio_a_bajar():
    h, box = _captcha_harness()
    h.bot.state = State.RECOGER
    h.bot._set_dismounted(True, "test")
    h.bot._enter(State.BUSCAR, "test")
    assert h.bot.state is State.CAPTCHA
    assert h.keys("g") % 2 == 1   # la subida salio; el estado final no es BUSCAR
    box["gone"] = True
    h.run_until(lambda: h.bot.state is not State.CAPTCHA)


def test_tope_de_tiempo_no_corta_en_estos_estados():
    for st in (State.REVIVIR, State.RECOGER, State.CAPTCHA):
        h = Harness(lambda h, n: perc())
        h.bot.state = st
        ran = []
        h.bot.step = lambda: (ran.append(1), setattr(h.bot, "state", State.BUSCAR))
        h.bot.run(max_minutes=0)
        assert ran, f"{st.name}: el tope de tiempo corto antes de terminar"


def test_remount_quiet_nunca_manda_ctrl_g_muerto_o_reviviendo():
    for st in (State.REVIVIR, State.MUERTO):
        h = Harness(lambda h, n: perc())
        h.bot.state = st
        h.bot._set_dismounted(True, "muerte")
        h.bot._remount_quiet()
        assert h.keys("g") == 0 and h.bot._dismounted


def _leg(h):
    return _KeyLeg("W", (100, 100), 0.5, h.clock())


def test_habilidad_descarta_el_tramo_de_teclado_pendiente():
    h = Harness(lambda h, n: perc())
    h.bot._leg = _leg(h)
    sk = Skill("A", "f1", 60.0, False, 0.1)
    h.bot.skills.skills.append(sk)
    h.bot._cast_skill(sk)
    assert h.bot._leg is None


def test_senuelo_y_espacio_descartan_el_tramo_pendiente():
    h = Harness(lambda h, n: perc())
    h.bot._leg = _leg(h)
    h.bot._bi_lure()
    assert h.bot._leg is None
    h.bot._leg = _leg(h)
    h.bot._bi_fight()
    assert h.bot._leg is None


# ------------------------------------------------------------------ escritura atomica
import json
import os

import pytest

from bot import fileutil
from bot.config import DEFAULTS, save_config
from bot.profiles import save_profile
from bot.ui.controller import UiOptions, save_settings


def _boom(*a, **k):
    raise OSError("disco lleno")


def test_atomic_write_text_escribe_y_no_deja_tmp(tmp_path):
    p = tmp_path / "a.json"
    fileutil.atomic_write_text(p, "uno\n")
    fileutil.atomic_write_text(p, "dos\n")
    assert p.read_text(encoding="utf-8") == "dos\n" and [f.name for f in tmp_path.iterdir()] == ["a.json"]


def test_atomic_write_text_si_falla_el_original_queda_y_no_hay_tmp(tmp_path, monkeypatch):
    p = tmp_path / "a.json"
    p.write_text("original", encoding="utf-8")
    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        fileutil.atomic_write_text(p, "nuevo")
    assert p.read_text(encoding="utf-8") == "original" and [f.name for f in tmp_path.iterdir()] == ["a.json"]


def test_save_config_save_profile_y_save_settings_son_atomicos(tmp_path, monkeypatch):
    cfgp = tmp_path / "config.json"
    cfgp.write_text("{}", encoding="utf-8")
    setp = tmp_path / "ui.json"
    setp.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        save_config({"a": 1}, cfgp)
    assert save_settings(UiOptions(), setp) is False
    assert cfgp.read_text(encoding="utf-8") == "{}" and setp.read_text(encoding="utf-8") == "{}"
    root = tmp_path / "perfiles"
    (root / "personaje").mkdir(parents=True)
    orig = root / "personaje" / "pj.json"
    orig.write_text('{"x": 1}', encoding="utf-8")
    with pytest.raises(OSError):
        save_profile("personaje", "pj", {}, root)
    assert orig.read_text(encoding="utf-8") == '{"x": 1}'
    assert not [f for f in tmp_path.rglob("*.tmp")]


# ------------------------------------------------------------------ config.example.jsonc vs DEFAULTS
from pathlib import Path

from bot.config import EXAMPLE_ONLY_KEYS, PROJECT_ROOT
from bot.profiles import read_selection
from bot.config import ConfigError


def _strip_jsonc(text: str) -> str:
    """Saca comentarios // y /* */ respetando los textos entre comillas."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\":
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            i = text.index("*/", i) + 2
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _diff_keys(example, defaults, path=""):
    out = []
    if isinstance(defaults, dict) and isinstance(example, dict):
        for k in sorted(set(defaults) | set(example)):
            if not path and k in EXAMPLE_ONLY_KEYS:
                continue
            if k not in example:
                out.append(f"falta en el ejemplo: {path}{k}")
            elif k not in defaults:
                out.append(f"sobra en el ejemplo (no esta en DEFAULTS): {path}{k}")
            else:
                out += _diff_keys(example[k], defaults[k], f"{path}{k}.")
    return out


def test_config_example_tiene_las_mismas_claves_que_defaults():
    text = (Path(PROJECT_ROOT) / "config.example.jsonc").read_text(encoding="utf-8")
    example = json.loads(_strip_jsonc(text))
    assert "profiles" in example and EXAMPLE_ONLY_KEYS == {"profiles"}
    assert _diff_keys(example, DEFAULTS) == []


def test_profiles_del_ejemplo_se_valida_como_texto():
    assert read_selection({"profiles": {"servidor": "a", "personaje": "b", "zona": "c"}}) == {
        "servidor": "a", "personaje": "b", "zona": "c"}
    for bad in ({"servidor": 1}, {"zona": ""}, {"otro": "x"}, ["x"]):
        with pytest.raises(ConfigError):
            read_selection({"profiles": bad})


# ------------------------------------------------------------------ subproceso (BotProcess)
import sys
import time

from bot.ui import controller as ctl


def test_botprocess_kill_termina_el_proceso_y_cierra_las_tuberias_sin_dejar_hilos():
    p = ctl.BotProcess()
    p.start([sys.executable, "-u", "-c", "import time\nprint('hola', flush=True)\ntime.sleep(60)"])
    t0 = time.monotonic()
    while time.monotonic() - t0 < 10 and not any(x == "hola\n" for x in p.drain()):
        time.sleep(0.02)
    p.kill()
    assert not p.running and p.proc.stdin.closed
    assert p._reader is not None and not p._reader.is_alive()
    p.kill()   # idempotente


def test_botprocess_stop_con_proceso_que_ignora_stop_lo_mata_por_timeout():
    p = ctl.BotProcess()
    p.start([sys.executable, "-u", "-c", "import time\ntime.sleep(60)"])
    assert p.stop(grace_s=0.3) is False
    assert not p.running and not p._reader.is_alive()


# ------------------------------------------------------------------ interfaz recien abierta
def test_ventana_recien_abierta_sin_asteriscos_ni_avisos_viejos_de_pisado(tmp_path):
    import tkinter as tk

    from bot.ui.app import App
    prof = tmp_path / "profiles"
    skill = {"name": "Aura", "key": "f1", "duration_s": 60, "requires_dismount": False, "animation_s": 1.0}
    for d, name, data in (("servidores", "S", {"window_title": "w"}), ("personajes", "P", {"character": {"skills": [skill]}}),
                          ("zonas", "Z", {"farm_zone": {"polygon": [[0, 0], [1, 0], [1, 1]]}})):
        (prof / d).mkdir(parents=True, exist_ok=True)
        (prof / d / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    cfgp = tmp_path / "config.json"   # config.json pisa la zona: lo cubre la franja amarilla, no el aviso de Perfiles
    cfgp.write_text(json.dumps({"farm_zone": {"polygon": [[0, 0], [2, 0], [2, 2]]}}), encoding="utf-8")
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"sin pantalla: {e}")
    try:
        root.withdraw()
        app = App(root, settings_path=tmp_path / "ui.json", config_path=str(cfgp), profiles_root=prof,
                  catalog_dir=tmp_path / "cat", history_file=tmp_path / "h.jsonl")
        root.update()
        assert not any("*" in app.notebook.tab(t, "text") for t in app.notebook.tabs())
        assert not app.editor.dirty and app.editor.skills
        assert app._override_warnings and "pisa" not in app.warn_label.cget("text")
    finally:
        root.destroy()


# ------------------------------------------------------------------ portados desde main (33ce243)
from bot.spawns import SpawnMap  # noqa: E402


def test_tope_de_tiempo_corta_en_buscar():
    h = Harness(lambda h, n: perc())
    h.bot.state = State.BUSCAR
    ran = []
    h.bot.step = lambda: ran.append(1)
    h.bot.run(max_minutes=0)
    assert not ran and h.bot.state is State.FIN


def test_mapa_de_metin_guarda_atomico_y_no_deja_tmp(tmp_path, monkeypatch):
    p = tmp_path / "mapa.json"
    m = SpawnMap(p, 6.0, clock_wall=lambda: 1000.0)
    m.record_broken((100.0, 100.0))
    first = p.read_text(encoding="utf-8")
    monkeypatch.setattr(os, "replace", _boom)
    m.record_broken((200.0, 200.0))   # falla el guardado: WARNING, el archivo anterior queda
    assert p.read_text(encoding="utf-8") == first
    assert not list(tmp_path.glob("*.tmp"))
