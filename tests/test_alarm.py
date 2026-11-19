"""Alarma sonora con winsound simulado (nunca suena de verdad)."""
import threading
import time

import pytest

import bot.alarm as alarm_mod
from bot.alarm import Alarm
from bot.config import ConfigError, DEFAULTS, validate_config
import copy


class FakeWinsound:
    def __init__(self, fail_beep=False):
        self.beeps = []
        self.message_beeps = 0
        self.fail_beep = fail_beep

    def Beep(self, freq, ms):
        if self.fail_beep:
            raise RuntimeError("sin placa de sonido")
        self.beeps.append((freq, ms))

    def MessageBeep(self, *a):
        self.message_beeps += 1


def make(monkeypatch, fake=None, **over):
    fake = fake or FakeWinsound()
    monkeypatch.setattr(alarm_mod, "winsound", fake)
    cfg = {"alarm": {"freq": 1000, "beep_ms": 1, "pause_ms": 5, "max_seconds": 0, **over}}
    return Alarm(cfg), fake


def wait_for(cond, timeout=2.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.005)
    return False


def test_start_no_bloquea_y_stop_corta(monkeypatch):
    a, fake = make(monkeypatch)
    t0 = time.monotonic()
    a.start("captcha")
    assert time.monotonic() - t0 < 0.5
    assert a.is_active
    assert wait_for(lambda: len(fake.beeps) >= 2)
    assert fake.beeps[0] == (1000, 1)
    a.stop()
    assert not a.is_active
    n = len(fake.beeps)
    time.sleep(0.05)
    assert len(fake.beeps) == n


def test_start_idempotente(monkeypatch):
    a, _ = make(monkeypatch)
    before = threading.active_count()
    a.start("x")
    a.start("y")
    a.start("z")
    assert threading.active_count() == before + 1
    a.stop()


def test_stop_sin_start_no_falla(monkeypatch):
    a, _ = make(monkeypatch)
    a.stop()
    assert not a.is_active


def test_max_seconds(monkeypatch):
    a, fake = make(monkeypatch, max_seconds=10)
    clock = {"t": 0.0}
    monkeypatch.setattr(alarm_mod, "_monotonic", lambda: clock["t"])
    a.start("muerte")
    assert wait_for(lambda: len(fake.beeps) >= 1)
    assert a.is_active
    clock["t"] = 11.0
    assert wait_for(lambda: not a.is_active)


def test_reinicio_despues_de_stop(monkeypatch):
    a, fake = make(monkeypatch)
    a.start("a")
    a.stop()
    a.start("b")
    assert a.is_active
    a.stop()


def test_carrera_stop_start(monkeypatch):
    """stop() en otro hilo + start() 20 ms despues (el hilo viejo esta en un beep de 300 ms): no quedan dos."""
    class SlowWinsound(FakeWinsound):
        def Beep(self, freq, ms):
            time.sleep(ms / 1000)

    a, _ = make(monkeypatch, SlowWinsound(), beep_ms=300)
    a.start("a")
    time.sleep(0.05)
    t = threading.Thread(target=a.stop)
    t.start()
    time.sleep(0.02)
    a.start("b")
    t.join()
    time.sleep(0.8)
    vivos = [x for x in threading.enumerate() if x.name == "alarm" and x.is_alive()]
    assert len(vivos) <= 1
    a.stop()
    time.sleep(0.4)
    assert not [x for x in threading.enumerate() if x.name == "alarm" and x.is_alive()]


def test_fallback_message_beep(monkeypatch):
    a, fake = make(monkeypatch, FakeWinsound(fail_beep=True))
    a.beep_once()
    assert fake.message_beeps == 1
    a.start("x")
    assert wait_for(lambda: fake.message_beeps >= 3)
    a.stop()


def test_validacion_config():
    cfg = copy.deepcopy(DEFAULTS)
    validate_config(cfg)
    for key, bad in (("freq", 10), ("freq", 40000), ("beep_ms", 0), ("pause_ms", -1), ("max_seconds", -5)):
        c = copy.deepcopy(cfg)
        c["alarm"][key] = bad
        with pytest.raises(ConfigError):
            validate_config(c)
