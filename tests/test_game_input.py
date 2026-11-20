"""Tests de bot/input/game_input.py: todo simulado (backend y ventana falsos)."""
import copy
import random
import threading

import numpy as np
import pytest

import bot.input.base as base
import bot.input.game_input as gi_mod
from bot.config import DEFAULTS
from bot.control import BotControl, PanicStop
from bot.geometry import Rect
from bot.input.fake import FakeBackend, FakeWindow
from bot.input.game_input import GameInput, InputBlockedError
from bot.vision.screen_mask import ScreenMask


class FakeKeyboard:
    def add_hotkey(self, key, cb):
        return object()

    def remove_hotkey(self, handle):
        pass


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


def make(ready=True, rect=Rect(8, 31, 1858, 1057), captured=True):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["input"].update(delay_ms=[0, 1], key_hold_ms=[0, 1], move_settle_ms=[0, 1])
    cfg["safety"].update(hard_kill_after_s=5.0)
    ctl = BotControl(cfg, keyboard=FakeKeyboard(), cursor_pos=lambda: (9999, 9999), exit_fn=lambda c: None)
    be = FakeBackend()
    win = FakeWindow(rect, ready=ready)
    ctl.add_stop_callback(be.release_all)
    if captured:
        win.capture()   # las acciones con mouse exigen una captura previa
    return GameInput(win, be, ctl, cfg, rng=random.Random(1)), be, ctl, win


def open_mask(w=1858, h=1057, blocked=None):
    m = np.zeros((h, w), dtype=np.uint8)
    if blocked is not None:
        m[blocked.y:blocked.bottom, blocked.x:blocked.right] = 255
    return ScreenMask(zones=[], mask=m)


def moves(be):
    return [e for e in be.events if e[0] == "move_to"]


def test_click_translates_with_offset_and_jitter_radius():
    g, be, _, _ = make()
    for _ in range(30):
        be.events.clear()
        assert g.click_at(500, 400, open_mask())
        _, sx, sy = moves(be)[0]
        cx, cy = sx - 8, sy - 31
        assert (cx - 500) ** 2 + (cy - 400) ** 2 <= g.cfg["input"]["click_jitter_px"] ** 2 + 2
        assert ("mouse_down", "left") in be.events and ("mouse_up", "left") in be.events
    assert be.pressed == ([], [])


def test_jitter_stays_in_allowed_area():
    g, be, _, _ = make()
    mask = open_mask(blocked=Rect(0, 0, 500, 1057))   # el borde x<500 esta prohibido; (500, 400) permitido
    for _ in range(40):
        be.events.clear()
        assert g.click_at(500, 400, mask)
        assert moves(be)[0][1] - 8 >= 500


def test_click_without_jitter_is_exact():
    g, be, _, _ = make()
    g._in["click_jitter_px"] = 0
    g.click_at(10, 20, open_mask())
    assert moves(be)[0] == ("move_to", 18, 51)


def test_click_rejected_when_mask_blocks():
    g, be, _, _ = make()
    assert g.click_at(100, 100, open_mask(blocked=Rect(50, 50, 200, 200))) is False
    assert be.events == []


def test_click_rejected_outside_client():
    g, be, _, _ = make()
    # mascara mas grande que el cliente: la mascara lo permite pero cae fuera del cliente
    mask = open_mask(w=2000, h=1200)
    assert g.click_at(1900, 100, mask) is False
    assert be.events == []


def test_click_requires_mask():
    g, be, _, _ = make()
    with pytest.raises(ValueError):
        g.click_at(10, 10, None)
    assert be.events == []


def test_not_ready_sends_nothing():
    g, be, _, _ = make(ready=False)
    assert g.click_at(500, 400, open_mask()) is False
    assert g.press("pickup") is False
    assert g.hold("camera_left", 0.1) is False
    assert g.zoom(3, open_mask()) is False
    assert be.events == []


def test_not_ready_with_panic_raises():
    g, be, ctl, win = make()
    win.ready = lambda: (ctl.panic("t"), False)[1]
    with pytest.raises(PanicStop):
        g.press("pickup")
    assert be.events == []


def test_panic_before_action():
    g, be, ctl, _ = make()
    ctl.panic("t")
    with pytest.raises(PanicStop):
        g.press("pickup")
    with pytest.raises(PanicStop):
        g.click_at(500, 400, open_mask())
    assert be.events == []


def test_panic_during_hold_releases_key():
    g, be, ctl, _ = make()
    t = threading.Timer(0.1, lambda: ctl.panic("t"))
    t.start()
    with pytest.raises(PanicStop):
        g.hold("camera_left", 5)
    t.join()
    assert be.events[0] == ("key_down", "q")
    assert set(be.events[1:]) == {("key_up", "q")}   # release_all del panico y/o el finally
    assert be.pressed == ([], [])


def test_other_exception_releases_all():
    g, be, _, _ = make()
    be.key_down("x")
    be.mouse_down("left")
    g.window.client_to_screen = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        g.click_at(500, 400, open_mask())
    assert be.pressed == ([], [])


def test_pause_waits_then_continues():
    g, be, ctl, _ = make()
    ctl.toggle_pause()
    done = threading.Event()

    def run():
        g.press("pickup")
        done.set()

    t = threading.Thread(target=run)
    t.start()
    assert not done.wait(0.2)
    assert be.events == []
    ctl.toggle_pause()
    assert done.wait(2)
    t.join()
    assert ("key_down", "z") in be.events


def test_press_single_and_combo_and_unknown():
    g, be, _, _ = make()
    g.press("pickup")
    assert be.events == [("key_down", "z"), ("key_up", "z")]
    be.events.clear()
    g.press("mount")
    assert be.events == [("key_down", "ctrl"), ("key_down", "g"), ("key_up", "g"), ("key_up", "ctrl")]
    with pytest.raises(KeyError):
        g.press("inexistente")


def test_camera_and_move_keys():
    g, be, _, _ = make()
    g.rotate_camera("right", 0.01)
    g.move_key("up", 0.01)
    assert be.events == [("key_down", "e"), ("key_up", "e"), ("key_down", "w"), ("key_up", "w")]
    with pytest.raises(ValueError):
        g.rotate_camera("up", 0.1)
    with pytest.raises(ValueError):
        g.move_key("x", 0.1)


def test_zoom_moves_cursor_to_allowed_point_before_scroll():
    g, be, _, _ = make()
    assert g.zoom(-3, open_mask())
    kinds = [e[0] for e in be.events]
    assert kinds == ["move_to", "scroll"]
    assert be.events[1] == ("scroll", -3)
    _, sx, sy = be.events[0]
    off = g.cfg["vision"]["player_offset"]
    assert (sx - 8, sy - 31) == (1858 // 2 + off[0], 1057 // 2 + off[1])


def test_zoom_skipped_if_player_point_blocked():
    g, be, _, _ = make()
    assert g.zoom(3, open_mask(blocked=Rect(0, 0, 1858, 1057))) is False
    assert be.events == []


def test_startup_check(monkeypatch):
    g, _, _, _ = make()
    monkeypatch.setattr(gi_mod, "elevation_mismatch", lambda hwnd: True)
    with pytest.raises(InputBlockedError, match="administrador"):
        g.startup_check()
    monkeypatch.setattr(gi_mod, "elevation_mismatch", lambda hwnd: False)
    g.startup_check()
    monkeypatch.setattr(gi_mod, "elevation_mismatch", lambda hwnd: None)
    g.startup_check()


def test_backend_release_all_thread_safety():
    be = FakeBackend()
    stop = threading.Event()
    errors = []

    def presser():
        try:
            while not stop.is_set():
                be.press("a")
                be.hotkey(["ctrl", "g"])
                be.click(1, 1)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def releaser():
        try:
            for _ in range(2000):
                be.release_all()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t1, t2 = threading.Thread(target=presser), threading.Thread(target=releaser)
    t1.start()
    t2.start()
    t2.join()
    stop.set()
    t1.join()
    be.release_all()
    assert errors == []
    assert be.pressed == ([], [])


# --- correcciones de la revision de seguridad (fase 5) ---
def test_panic_inside_ensure_ready_with_ready_true_sends_nothing():
    g, be, ctl, win = make()
    win.ready = lambda: (ctl.panic("F12"), True)[1]
    with pytest.raises(PanicStop):
        g.press("pickup")
    with pytest.raises(PanicStop):
        g.click_at(500, 400, open_mask())
    with pytest.raises(PanicStop):
        g.zoom(3, open_mask())
    assert be.events == []


def test_panic_before_move_in_click_and_zoom():
    g, be, ctl, win = make()
    orig = win.client_to_screen
    win.client_to_screen = lambda *a, **k: (ctl.panic("t"), orig(*a, **k))[1]
    with pytest.raises(PanicStop):
        g.click_at(500, 400, open_mask())
    assert be.events == []


def test_zoom_requires_mask():
    g, be, _, _ = make()
    with pytest.raises(ValueError):
        g.zoom(3, None)
    assert be.events == []


def test_click_and_zoom_without_capture_or_after_window_moved(caplog):
    g, be, _, win = make(captured=False)
    assert g.click_at(500, 400, open_mask()) is False
    assert g.zoom(3, open_mask()) is False
    win.capture()
    win.move_window(Rect(100, 100, 1858, 1057))
    with caplog.at_level("WARNING"):
        assert g.click_at(500, 400, open_mask()) is False
        assert g.zoom(3, open_mask()) is False
    assert any("la ventana se movió o cambió de tamaño" in r.message for r in caplog.records)
    assert be.events == []
    win.capture()
    assert g.click_at(500, 400, open_mask()) is True


def test_pause_during_hold_releases_and_represses_for_remaining_time():
    g, be, ctl, _ = make()
    clock = {"t": 0.0}
    real_sleep = ctl.sleep
    paused_events = []

    def sleep(s):
        clock["t"] += s
        if abs(clock["t"] - 0.1) < 1e-9 and not paused_events:
            paused_events.append(1)
            ctl.toggle_pause()                 # F11 (desde el "hilo de la hotkey")
            assert ("key_up", "q") == be.events[-1]
            n = len(be.events)
            ctl.toggle_pause()                 # reanuda
            assert len(be.events) == n         # nada durante la pausa
        real_sleep(0)

    ctl.sleep = sleep
    assert g.hold("camera_left", 0.3)
    downs = [i for i, e in enumerate(be.events) if e == ("key_down", "q")]
    assert len(downs) == 2
    assert be.pressed == ([], [])
    assert be.events[-1] == ("key_up", "q")


def test_pause_callback_failure_is_logged(caplog):
    g, be, ctl, _ = make()
    ctl.add_pause_callback(lambda: 1 / 0, lambda: 1 / 0)
    with caplog.at_level("ERROR"):
        ctl.toggle_pause()
        ctl.toggle_pause()
    assert sum("callback de pausa" in r.message for r in caplog.records) == 2


def test_focus_lost_during_hold_releases_key_and_waits():
    g, be, ctl, win = make()
    state = {"n": 0}

    def fg():
        state["n"] += 1
        return state["n"] != 2   # pierde el foco en el 2do tramo

    win.foreground = fg
    calls_before = win.ensure_ready_calls
    assert g.hold("move_up", 0.3)
    assert win.ensure_ready_calls == calls_before + 2   # el de _action + el de la re-espera
    assert [e for e in be.events if e[0] == "key_down"] == [("key_down", "w")] * 2
    assert be.events[:3] == [("key_down", "w"), ("key_up", "w"), ("key_down", "w")]
    assert be.pressed == ([], [])


def test_hold_zero_seconds_still_presses_once():
    g, be, _, _ = make()
    assert g.hold("move_up", 0)
    assert be.events == [("key_down", "w"), ("key_up", "w")]


def test_hover_only_moves_the_mouse_with_jitter_and_mask_check():
    g, be, _, _ = make()
    for _ in range(20):
        be.events.clear()
        assert g.hover_at(500, 400, open_mask())
        assert [e[0] for e in be.events] == ["move_to"]   # sin clic ni teclas
        _, sx, sy = be.events[0]
        assert (sx - 8 - 500) ** 2 + (sy - 31 - 400) ** 2 <= (g.cfg["input"]["click_jitter_px"] + 1) ** 2
    be.events.clear()
    assert g.hover_at(100, 100, open_mask(blocked=Rect(50, 50, 200, 200))) is False
    assert g.hover_at(1900, 100, open_mask(w=2000, h=1200)) is False   # fuera del cliente
    assert be.events == []
    with pytest.raises(ValueError):
        g.hover_at(10, 10, None)


def test_hover_not_ready_sends_nothing():
    g, be, _, _ = make(ready=False)
    assert g.hover_at(500, 400, open_mask()) is False
    assert be.events == []
