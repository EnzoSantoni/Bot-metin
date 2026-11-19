"""Tests de bot/control.py: todo simulado (sin hotkeys ni cursor reales)."""
import copy
import logging
import threading
import time

import pytest

from bot.config import DEFAULTS
from bot.control import BotControl, PanicStop


class FakeKeyboard:
    def __init__(self):
        self.hotkeys = {}
        self.removed = []

    def add_hotkey(self, key, cb):
        handle = object()
        self.hotkeys[key] = (handle, cb)
        return handle

    def remove_hotkey(self, handle):
        self.removed.append(handle)


def make(cursor=(500, 500), **safety):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["safety"].update(corner_poll_ms=10, hard_kill_after_s=0.2, **safety)
    kb = FakeKeyboard()
    pos = {"p": cursor}
    exits = []
    ctl = BotControl(cfg, keyboard=kb, cursor_pos=lambda: pos["p"], exit_fn=exits.append)
    return ctl, kb, pos, exits


def test_f12_panic_makes_check_raise():
    ctl, kb, _, _ = make()
    ctl.start()
    try:
        ctl.check()
        kb.hotkeys["f12"][1]()
        assert ctl.is_stopped
        with pytest.raises(PanicStop):
            ctl.check()
    finally:
        ctl.mark_finished()
        ctl.close()


def test_corner_panics_within_polls():
    ctl, _, pos, _ = make()
    ctl.start()
    try:
        pos["p"] = (3, 4)
        time.sleep(0.1)
        assert ctl.is_stopped
    finally:
        ctl.mark_finished()
        ctl.close()


def test_f11_toggles_pause():
    ctl, kb, _, _ = make()
    ctl.start()
    try:
        kb.hotkeys["f11"][1]()
        assert ctl.is_paused
        kb.hotkeys["f11"][1]()
        assert not ctl.is_paused
    finally:
        ctl.close()


def test_check_blocks_while_paused_then_resumes():
    ctl, _, _, _ = make()
    ctl.toggle_pause()
    done = threading.Event()
    threading.Thread(target=lambda: (ctl.check(), done.set()), daemon=True).start()
    assert not done.wait(0.1)
    ctl.toggle_pause()
    assert done.wait(1.0)


def test_check_raises_if_panic_during_pause():
    ctl, _, _, _ = make()
    ctl.toggle_pause()
    result = []

    def run():
        try:
            ctl.check()
        except PanicStop:
            result.append("panic")

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.05)
    ctl.panic("test")
    t.join(1.0)
    ctl.mark_finished()
    assert result == ["panic"]


def test_sleep_wakes_on_panic():
    ctl, _, _, _ = make()
    threading.Timer(0.05, ctl.panic, args=("test",)).start()
    t0 = time.monotonic()
    with pytest.raises(PanicStop):
        ctl.sleep(10)
    assert time.monotonic() - t0 < 0.2
    ctl.mark_finished()


def test_sleep_does_not_count_paused_time():
    ctl, _, _, _ = make()
    threading.Timer(0.05, ctl.toggle_pause).start()
    threading.Timer(0.3, ctl.toggle_pause).start()
    t0 = time.monotonic()
    ctl.sleep(0.2)
    assert time.monotonic() - t0 >= 0.43   # 0.05 antes de la pausa + 0.15 tras reanudar (a los 0.3)


def test_countdown_ticks_and_cancel():
    ctl, _, _, _ = make()
    ticks = []
    ctl.countdown(2, on_tick=ticks.append)
    assert ticks == [2, 1]

    threading.Timer(0.1, ctl.panic, args=("test",)).start()
    with pytest.raises(PanicStop):
        ctl.countdown(10, on_tick=ticks.append)
    assert len(ticks) < 6
    ctl.mark_finished()


def test_stop_callbacks_run_even_if_one_fails(caplog):
    ctl, _, _, _ = make()
    called = []

    def bad():
        raise RuntimeError("boom")

    ctl.add_stop_callback(lambda: called.append(1))
    ctl.add_stop_callback(bad)
    ctl.add_stop_callback(lambda: called.append(2))
    with caplog.at_level(logging.WARNING):
        ctl.panic("test")
        ctl.panic("otra vez")   # idempotente
    ctl.mark_finished()
    assert called == [1, 2]


def test_hard_kill_if_not_finished():
    ctl, _, _, exits = make()
    ctl.panic("test")
    time.sleep(0.5)
    assert exits == [1]


def test_no_hard_kill_if_finished():
    ctl, _, _, exits = make()
    ctl.panic("test")
    ctl.mark_finished()
    time.sleep(0.5)
    assert exits == []


def test_close_unregisters_and_is_idempotent():
    ctl, kb, _, _ = make()
    ctl.start()
    handles = [h for h, _ in kb.hotkeys.values()]
    ctl.close()
    ctl.close()
    assert kb.removed == handles


# --- corte forzado (revision de seguridad fase 5) ---
def test_hard_kill_even_if_stop_callback_hangs(monkeypatch):
    import bot.control as control_mod
    monkeypatch.setattr(control_mod, "HARD_KILL_RELEASE_JOIN_S", 0.05)
    ctl, _, _, exits = make()
    gate = threading.Event()
    ctl.add_stop_callback(lambda: gate.wait(3.0))      # cuelga el callback de parada
    t = threading.Thread(target=lambda: ctl.panic("test"), daemon=True)
    t0 = time.monotonic()
    t.start()
    while not exits and time.monotonic() - t0 < 2:
        time.sleep(0.01)
    gate.set()
    assert exits == [1]
    assert 0.15 <= time.monotonic() - t0 < 1.0         # ~hard_kill_after_s (0.2) + join corto


def test_hard_kill_releases_before_exit():
    from bot.input.fake import FakeBackend
    ctl, _, _, _ = make()
    be = FakeBackend()
    be.key_down("w")
    order = []
    ctl._exit_fn = lambda c: order.append(("exit", be.pressed))
    ctl._callbacks.append(be.release_all)
    ctl._hard_kill()                                    # el corte llama a release_all antes de exit_fn
    assert order == [("exit", ([], []))]
    assert ("key_up", "w") in be.events


def test_close_cancels_kill_timer():
    ctl, _, _, exits = make()
    ctl.panic("test")
    ctl.close()
    time.sleep(0.5)
    assert exits == []
