"""Arreglos de la revision general (2026-11-20) portados desde desarrollo: captcha durante la subida al salir de
RECOGER, el tope de tiempo no corta en CAPTCHA ni RECOGER, nunca Ctrl+G muerto, y escritura atomica. Backend falso."""
import os

import pytest

from bot import fileutil
from bot.bot import State
from bot.config import save_config
from bot.spawns import SpawnMap
from test_bot_states import CAPTCHA, Harness, perc


def _captcha_harness():
    box = {"gone": False}
    h = Harness(lambda h, n: perc(captcha=box["cap"]()),
                bot_kwargs={"captcha_fn": lambda img, cfg: box["cap"]()})
    box["cap"] = lambda: CAPTCHA if (h.keys("g") >= 1 and not box["gone"]) else None
    return h, box


# ------------------------------------------------------------------ captcha durante la subida al salir de RECOGER
def test_captcha_al_subirse_saliendo_de_recoger_hacia_volver():
    h, box = _captcha_harness()
    h.bot.state = State.RECOGER
    h.bot._set_dismounted(True, "test")
    h.bot._enter(State.VOLVER, "test")
    assert h.bot.state is State.CAPTCHA
    assert h.bot._vl_since is None and h.bot._walk is None   # no se ejecuto el bloque de VOLVER
    assert h.keys("g") == 1 and h.bot._dismounted is False
    box["gone"] = True
    h.run_until(lambda: h.bot.state is not State.CAPTCHA)
    assert h.bot.state is not State.VOLVER and h.bot._vl_since is None
    assert h.keys("g") == 1   # ya estaba montado: no hay un segundo Ctrl+G


def test_captcha_al_subirse_saliendo_de_recoger_hacia_buscar():
    h, box = _captcha_harness()
    h.bot.state = State.RECOGER
    h.bot._set_dismounted(True, "test")
    h.bot._enter(State.BUSCAR, "test")
    assert h.bot.state is State.CAPTCHA
    assert h.keys("g") == 1
    box["gone"] = True
    h.run_until(lambda: h.bot.state is not State.CAPTCHA)
    assert h.keys("g") == 1 and h.bot._dismounted is False


# ------------------------------------------------------------------ tope de tiempo
@pytest.mark.parametrize("st", [State.RECOGER, State.CAPTCHA])
def test_tope_de_tiempo_no_corta_en_recoger_ni_captcha(st):
    h = Harness(lambda h, n: perc())
    h.bot.state = st
    ran = []
    h.bot.step = lambda: (ran.append(1), setattr(h.bot, "state", State.BUSCAR))
    h.bot.run(max_minutes=0)
    assert ran, f"{st.name}: el tope de tiempo corto antes de terminar"
    assert h.bot.state is State.FIN


def test_tope_de_tiempo_corta_en_buscar():
    h = Harness(lambda h, n: perc())
    h.bot.state = State.BUSCAR
    ran = []
    h.bot.step = lambda: ran.append(1)
    h.bot.run(max_minutes=0)
    assert not ran and h.bot.state is State.FIN


def test_remount_quiet_nunca_manda_ctrl_g_muerto():
    h = Harness(lambda h, n: perc())
    h.bot.state = State.MUERTO
    h.bot._set_dismounted(True, "muerte")
    h.bot._remount_quiet()
    assert h.keys("g") == 0 and h.bot._dismounted


# ------------------------------------------------------------------ escritura atomica
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


def test_save_config_es_atomico(tmp_path, monkeypatch):
    cfgp = tmp_path / "config.json"
    cfgp.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        save_config({"a": 1}, cfgp)
    assert cfgp.read_text(encoding="utf-8") == "{}"
    assert not list(tmp_path.glob("*.tmp"))


def test_mapa_de_metin_guarda_atomico_y_no_deja_tmp(tmp_path, monkeypatch):
    p = tmp_path / "mapa.json"
    m = SpawnMap(p, 6.0, clock_wall=lambda: 1000.0)
    m.record_broken((100.0, 100.0))
    first = p.read_text(encoding="utf-8")
    monkeypatch.setattr(os, "replace", _boom)
    m.record_broken((200.0, 200.0))   # falla el guardado: WARNING, el archivo anterior queda
    assert p.read_text(encoding="utf-8") == first
    assert not list(tmp_path.glob("*.tmp"))
