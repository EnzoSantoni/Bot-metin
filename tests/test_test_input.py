"""Tests de test_input.py con --simular: nada real."""
import copy

import pytest

import bot.input.base as base
import test_input as ti
from bot.config import DEFAULTS
from bot.control import BotControl
from bot.input.fake import FakeBackend, FakeWindow


class FakeKeyboard:
    def add_hotkey(self, key, cb):
        return object()

    def remove_hotkey(self, handle):
        pass


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)
    for name in ("CAMERA_HOLD_S", "MOUNT_WAIT_S", "WALK_HOLD_S", "STEP_WAIT_S"):
        monkeypatch.setattr(ti, name, 0.0)


def make_cfg():
    cfg = copy.deepcopy(DEFAULTS)
    cfg["input"].update(delay_ms=[0, 1], key_hold_ms=[0, 1], move_settle_ms=[0, 1])
    cfg["safety"].update(countdown_s=0, hard_kill_after_s=5.0)
    return cfg


def run(args, backend=None):
    cfg = make_cfg()
    be = backend or FakeBackend()
    ctl = BotControl(cfg, keyboard=FakeKeyboard(), cursor_pos=lambda: (9999, 9999), exit_fn=lambda c: None)
    code = ti.main(args, cfg=cfg, control=ctl, backend=be, window=FakeWindow())
    return code, be, ctl


def keys(be):
    return [e[1] for e in be.events if e[0] == "key_down"]


def test_all_steps():
    code, be, _ = run(["--simular"])
    assert code == 0
    kd = [e for e in be.events if e[0] in ("key_down", "scroll", "mouse_down")]
    assert kd == [("key_down", "q"), ("key_down", "e"), ("scroll", 3), ("scroll", -3),
                  ("key_down", "z"), ("key_down", "ctrl"), ("key_down", "g"),
                  ("key_down", "ctrl"), ("key_down", "g"), ("key_down", "w"),
                  ("mouse_down", "left"), ("key_down", "esc")]
    assert be.pressed == ([], [])


def test_solo_teclas():
    code, be, _ = run(["--simular", "--solo", "teclas"])
    assert code == 0
    assert keys(be) == ["z", "ctrl", "g", "ctrl", "g", "w", "esc"]
    assert not any(e[0] in ("scroll", "mouse_down") for e in be.events)


def test_solo_camara():
    code, be, _ = run(["--simular", "--solo", "camara"])
    assert code == 0
    assert keys(be) == ["q", "e"]
    assert [e for e in be.events if e[0] == "scroll"] == [("scroll", 3), ("scroll", -3)]   # primero aleja (zoom_out_sign 1: adelante)


def test_solo_mouse():
    code, be, _ = run(["--simular", "--solo", "mouse"])
    assert code == 0
    assert [e[0] for e in be.events] == ["move_to", "move_to", "mouse_down", "mouse_up"] or \
        [e[0] for e in be.events][-2:] == ["mouse_down", "mouse_up"]
    assert keys(be) == []


def test_main_simular_without_injection(capsys, monkeypatch):
    monkeypatch.setattr(ti, "load_config", lambda path: make_cfg())
    monkeypatch.setattr("bot.log.setup_logging", lambda cfg: None)
    assert ti.main(["--simular", "--solo", "teclas"]) == 0
    out = capsys.readouterr().out
    assert "[simulado]" in out and "Paso 3" in out


def test_panic_midway_stops_and_releases():
    class PanicBackend(FakeBackend):
        ctl = None

        def _key_down(self, key):
            super()._key_down(key)
            if key == "z":
                self.ctl.panic("simulado")

    be = PanicBackend()
    cfg = make_cfg()
    ctl = BotControl(cfg, keyboard=FakeKeyboard(), cursor_pos=lambda: (9999, 9999), exit_fn=lambda c: None)
    be.ctl = ctl
    code = ti.main(["--simular"], cfg=cfg, control=ctl, backend=be, window=FakeWindow())
    assert code == 0
    assert keys(be) == ["q", "e", "z"]            # nada despues del panico
    assert be.pressed == ([], [])
    assert ctl.is_stopped
