"""Tests de los backends de input: todo simulado (nada se manda al sistema)."""
import copy
import types

import pytest

from bot.config import DEFAULTS, ConfigError, validate_config
from bot.input import base, pydirect
from bot.input.base import InputBackend, parse_keys
from bot.input.fake import FakeBackend
from bot.input.pydirect import PyDirectInputBackend


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


def test_parse_keys():
    assert parse_keys("ctrl+g") == ["ctrl", "g"]
    assert parse_keys(" F1 ") == ["f1"]
    with pytest.raises(ValueError):
        parse_keys("ctrl+")
    with pytest.raises(ValueError):
        parse_keys("")


def test_fake_records():
    b = FakeBackend()
    b.click(10, 20, "right", hold_s=0)
    b.press("z", 0)
    b.scroll(-2)
    assert b.events == [("move_to", 10, 20), ("mouse_down", "right"), ("mouse_up", "right"),
                        ("key_down", "z"), ("key_up", "z"), ("scroll", -2)]
    assert b.pressed == ([], [])


def test_hotkey_releases_in_reverse_order():
    b = FakeBackend()
    b.hotkey(["ctrl", "shift", "g"], 0)
    assert [e for e in b.events if e[0] == "key_up"] == [("key_up", "g"), ("key_up", "shift"), ("key_up", "ctrl")]


class FailingUp(FakeBackend):
    """Falla al soltar una vez para simular un error a mitad de camino."""

    def __init__(self):
        super().__init__()
        self.fail_up_once = True

    def _key_up(self, key):
        if self.fail_up_once:
            self.fail_up_once = False
            raise OSError("fallo")
        super()._key_up(key)


def test_release_all_after_press_failure():
    b = FailingUp()
    with pytest.raises(OSError):
        b.press("z", 0)
    assert b.pressed[0] == ["z"] or b.pressed[0] == []   # key_up fallo: la cuenta no miente
    b.key_down("a")
    b.mouse_down("left")
    b.release_all()
    assert b.pressed == ([], [])
    assert ("key_up", "a") in b.events and ("mouse_up", "left") in b.events


def test_release_all_when_down_fails_midway():
    class DownFails(FakeBackend):
        def _key_down(self, key):
            raise OSError("fallo")

    b = DownFails()
    with pytest.raises(OSError):
        b.press("z", 0)
    assert b.pressed == ([], [])   # el finally suelta
    assert ("key_up", "z") in b.events


def test_release_all_never_raises():
    class AllFail(FakeBackend):
        def _key_up(self, key):
            raise OSError("fallo")

    b = AllFail()
    b.key_down("a")
    b.release_all()   # no lanza
    assert b.pressed == ([], [])


class FakePDI:
    def __init__(self):
        self.PAUSE = 1
        self.FAILSAFE = True
        self.KEYBOARD_MAPPING = {k: i for i, k in enumerate(
            ["ctrlleft", "g", "t", "space", "z", "f1", "f11", "f12", "q", "e", "w", "a", "s", "d", "escape"])}
        self.calls = []

    def keyDown(self, k): self.calls.append(("keyDown", k))
    def keyUp(self, k): self.calls.append(("keyUp", k))
    def mouseDown(self, button): self.calls.append(("mouseDown", button))
    def mouseUp(self, button): self.calls.append(("mouseUp", button))
    def moveTo(self, x, y): self.calls.append(("moveTo", x, y))


@pytest.fixture
def pdi(monkeypatch):
    fake = FakePDI()
    monkeypatch.setattr(pydirect, "pydirectinput", fake)
    return fake


def test_pydirect_sets_flags_and_normalizes(pdi):
    b = PyDirectInputBackend(copy.deepcopy(DEFAULTS))
    assert pdi.PAUSE == 0 and pdi.FAILSAFE is False
    b.hotkey(parse_keys("ctrl+g"), 0)
    b.press("esc", 0)
    b.click(5, 6, hold_s=0)
    assert pdi.calls == [("keyDown", "ctrlleft"), ("keyDown", "g"), ("keyUp", "g"), ("keyUp", "ctrlleft"),
                         ("keyDown", "escape"), ("keyUp", "escape"),
                         ("moveTo", 5, 6), ("mouseDown", "left"), ("mouseUp", "left")]


def test_pydirect_invalid_key_raises(pdi):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["controls"]["pickup"] = "teclaquenoexiste"
    with pytest.raises(ValueError, match="teclaquenoexiste"):
        PyDirectInputBackend(cfg)


def test_pydirect_scroll_uses_sendinput(pdi, monkeypatch):
    sent = []

    class FakeUser32:
        def SendInput(self, n, ptr, size):
            sent.append(ptr._obj.mi.mouseData)
            return 1

    monkeypatch.setattr(pydirect, "_user32", lambda: FakeUser32())
    b = PyDirectInputBackend(copy.deepcopy(DEFAULTS))
    b.scroll(2)
    b.scroll(-1)
    assert sent == [120, 120, (-120) & 0xFFFFFFFF]


def test_config_defaults_validate_and_rules():
    cfg = copy.deepcopy(DEFAULTS)
    validate_config(cfg)
    bad = copy.deepcopy(cfg)
    bad["safety"]["pause_key"] = "F12"
    with pytest.raises(ConfigError):
        validate_config(bad)
    bad = copy.deepcopy(cfg)
    bad["input"]["delay_ms"] = [200, 100]
    with pytest.raises(ConfigError):
        validate_config(bad)
    bad = copy.deepcopy(cfg)
    bad["controls"]["buff"] = ""
    with pytest.raises(ConfigError):
        validate_config(bad)
    bad = copy.deepcopy(cfg)
    bad["safety"]["corner_px"] = -1
    with pytest.raises(ConfigError):
        validate_config(bad)


def _cfg_with(section, key, value):
    cfg = copy.deepcopy(DEFAULTS)
    cfg[section][key] = value
    return cfg


@pytest.mark.parametrize("section,key,value", [
    ("controls", "pickup", "ctrl+"),
    ("controls", "pickup", "+z"),
    ("controls", "pickup", "f99"),
    ("controls", "mount", "ctrl+teclarara"),
    ("controls", "buff", "f12"),            # choca con panic_key
    ("controls", "buff", "F11"),            # choca con pause_key
    ("controls", "buff", "ctrl+f12"),
    ("safety", "panic_key", "f13"),
    ("safety", "pause_key", "shift+"),
    ("safety", "hard_kill_after_s", 0),
])
def test_config_rejects_bad_controls_safety(section, key, value):
    with pytest.raises(ConfigError):
        validate_config(_cfg_with(section, key, value))


def test_config_requires_all_actions():
    for action in ("attack_forward", "pickup", "mount", "buff", "camera_left", "camera_right",
                   "move_up", "move_left", "move_down", "move_right", "deselect"):
        cfg = copy.deepcopy(DEFAULTS)
        del cfg["controls"][action]
        with pytest.raises(ConfigError, match=action):
            validate_config(cfg)


def test_valid_keys_cover_defaults_and_pydirect_aliases():
    from bot.input.base import VALID_KEYS
    assert set(pydirect.KEY_ALIASES) <= VALID_KEYS
    for spec in list(DEFAULTS["controls"].values()) + ["f11", "f12"]:
        assert set(parse_keys(spec)) <= VALID_KEYS


def test_release_all_no_se_intercala_con_key_down_lento():
    """Carrera: release_all (hilo del panico) llega mientras la primitiva key_down esta en curso.
    Con anotar + mandar atomico, release_all espera y suelta la tecla DESPUES de apretarse."""
    import threading
    import time

    from bot.input.fake import FakeBackend

    class Slow(FakeBackend):
        def _key_down(self, key):
            time.sleep(0.05)
            super()._key_down(key)

    b = Slow()
    t = threading.Thread(target=b.key_down, args=("w",))
    t.start()
    time.sleep(0.01)
    b.release_all()
    t.join()
    downs = [i for i, e in enumerate(b.events) if e == ("key_down", "w")]
    ups = [i for i, e in enumerate(b.events) if e == ("key_up", "w")]
    assert downs and ups and ups[-1] > downs[-1]
    assert b.pressed == ([], [])
