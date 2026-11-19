import numpy as np
import pytest

from bot.capture import GameWindow, WindowMinimizedError
from bot.geometry import Rect


@pytest.fixture
def gw(monkeypatch):
    g = GameWindow("x", (200, 100))
    monkeypatch.setattr(g, "client_rect_on_screen", lambda: Rect(8, 31, 200, 100))
    monkeypatch.setattr(g, "is_minimized", lambda: False)
    return g


def test_coords(gw):
    assert gw.client_to_screen(10, 20) == (18, 51)
    assert gw.screen_to_client(18, 51) == (10, 20)
    with pytest.raises(ValueError):
        gw.client_to_screen(200, 0)


def test_capture_bgra_to_bgr_and_region(gw, monkeypatch):
    calls = []

    def fake_grab(screen_rect):
        calls.append(screen_rect)
        bgra = np.zeros((screen_rect.h, screen_rect.w, 4), np.uint8)
        bgra[..., 0], bgra[..., 1], bgra[..., 2], bgra[..., 3] = 10, 20, 30, 255
        return bgra

    monkeypatch.setattr(gw, "_grab_raw", fake_grab)
    full = gw.capture()
    assert full.shape == (100, 200, 3) and full.dtype == np.uint8
    assert tuple(full[0, 0]) == (10, 20, 30)
    assert calls[-1] == Rect(8, 31, 200, 100)
    part = gw.capture(Rect(150, 40, 50, 60))  # entra justo en el area de cliente
    assert part.shape == (60, 50, 3)
    assert calls[-1] == Rect(158, 71, 50, 60)
    for bad in (Rect(190, 90, 50, 50), Rect(-10, 0, 50, 50), Rect(300, 0, 10, 10)):
        with pytest.raises(ValueError):  # nunca devolver una imagen corrida respecto de region
            gw.capture(bad)


def test_find_prefers_exact_title(monkeypatch):
    import bot.capture as cap
    from bot.capture import WindowInfo

    r = Rect(0, 0, 10, 10)
    wins = [WindowInfo(1, "Metin2 - wiki - Chrome", r, True, False),
            WindowInfo(2, "METIN2", r, True, True),
            WindowInfo(3, "Metin2", r, True, False)]
    monkeypatch.setattr(cap, "list_windows", lambda title: wins)
    assert GameWindow("metin2").find().hwnd == 3


def test_capture_channel_order(gw, monkeypatch):
    raw = np.zeros((100, 200, 4), np.uint8)
    raw[..., 2] = 255  # rojo en BGRA
    monkeypatch.setattr(gw, "_grab_raw", lambda r: raw[:r.h, :r.w])
    assert tuple(gw.capture()[0, 0]) == (0, 0, 255)


def test_minimized(gw, monkeypatch):
    monkeypatch.setattr(gw, "is_minimized", lambda: True)
    with pytest.raises(WindowMinimizedError):
        gw.capture()


# --- activacion / primer plano (win32 simulado) ---
import logging
from types import SimpleNamespace

import bot.capture as cap

HW = 42


class FakeWin:
    """Simula win32: `fg_fn(fake)` decide cual es la ventana en primer plano."""

    def __init__(self):
        self.t = 0.0
        self.calls: list[str] = []
        self.fg = 0
        self.on_fg_query = None  # callable(fake) opcional
        self.beeps = 0
        self.set_fg_raises = False
        self.set_fg_effect = None  # callable(fake, attached) tras SetForegroundWindow
        self.attached = False
        self.iconic = False

    def sleep(self, s):
        self.t += s

    def GetForegroundWindow(self):
        if self.on_fg_query:
            self.on_fg_query(self)
        return self.fg

    def IsIconic(self, h):
        return self.iconic

    def ShowWindow(self, h, c):
        self.calls.append("restore")

    def GetWindowText(self, h):
        return "Guabina"

    def BringWindowToTop(self, h):
        self.calls.append("top")

    def SetForegroundWindow(self, h):
        self.calls.append("setfg_attached" if self.attached else "setfg")
        if self.set_fg_effect:
            self.set_fg_effect(self)
        if self.set_fg_raises:
            raise RuntimeError("SetForegroundWindow fallo")


@pytest.fixture
def fw(monkeypatch):
    f = FakeWin()
    f.fg = 7  # otra ventana
    gwin = SimpleNamespace(GetForegroundWindow=f.GetForegroundWindow, IsIconic=f.IsIconic,
                           ShowWindow=f.ShowWindow, GetWindowText=f.GetWindowText,
                           BringWindowToTop=f.BringWindowToTop,
                           SetForegroundWindow=f.SetForegroundWindow)
    api = SimpleNamespace(keybd_event=lambda *a: f.calls.append("alt"),
                          GetCurrentThreadId=lambda: 1)

    def attach(a, b, flag):
        f.attached = flag
        f.calls.append(f"attach_{flag}")

    proc = SimpleNamespace(GetWindowThreadProcessId=lambda h: (2, 99), AttachThreadInput=attach)
    con = SimpleNamespace(SW_RESTORE=9, VK_MENU=0x12, KEYEVENTF_KEYUP=2)
    monkeypatch.setattr(cap, "win32gui", gwin)
    monkeypatch.setattr(cap, "win32api", api)
    monkeypatch.setattr(cap, "win32process", proc)
    monkeypatch.setattr(cap, "win32con", con)
    monkeypatch.setattr(cap, "_sleep", f.sleep)
    monkeypatch.setattr(cap, "_monotonic", lambda: f.t)
    monkeypatch.setattr(cap, "_beep", lambda: setattr(f, "beeps", f.beeps + 1))
    monkeypatch.setattr(cap, "elevation_mismatch", lambda h: False)
    g = GameWindow("Guabina", (200, 100))
    monkeypatch.setattr(g, "_hwnd", lambda: HW)
    return g, f


def test_activate_already_foreground(fw):
    g, f = fw
    f.fg = HW
    assert g.activate() is True
    assert f.calls == []  # sin ALT ni nada


def test_activate_minimized_restores(fw):
    g, f = fw
    f.iconic = True
    f.set_fg_effect = lambda f: setattr(f, "fg", HW)
    assert g.activate() is True
    assert f.calls[0] == "restore"


def test_activate_restore_sin_alt(fw):
    g, f = fw
    f.iconic = True
    f.on_fg_query = lambda f: setattr(f, "fg", HW) if "restore" in f.calls else None
    assert g.activate() is True
    assert f.calls == ["restore"]  # sin keybd_event


def test_activate_async(fw):
    g, f = fw
    n = {"q": 0}

    def on_q(f):
        if "setfg" in f.calls:
            n["q"] += 1
            if n["q"] >= 3:
                f.fg = HW

    f.on_fg_query = on_q
    assert g.activate() is True
    assert "attach_True" not in f.calls  # alcanzo con el intento A


def test_activate_a_fails_b_works(fw):
    g, f = fw
    f.set_fg_effect = lambda f: setattr(f, "fg", HW) if f.attached else None
    assert g.activate() is True
    assert f.calls.count("alt") == 2  # ALT press+release, una sola vez
    assert "setfg_attached" in f.calls and f.calls[-1] == "attach_False"


def test_activate_b_detaches_on_error(fw):
    g, f = fw
    f.set_fg_raises = True
    assert g.activate() is False
    assert f.calls.count("attach_True") == 1 and f.calls.count("attach_False") == 1
    assert not f.attached


def test_activate_all_fail_warns(fw, caplog):
    g, f = fw
    with caplog.at_level(logging.WARNING, logger="bot.capture"):
        assert g.activate(timeout=1.0) is False
    assert any("No se pudo traer" in r.message for r in caplog.records)
    assert f.t >= 1.0 - 1e-6


def test_wait_user_brings_it(fw, caplog):
    g, f = fw
    n = {"q": 0}

    def on_q(f):
        n["q"] += 1
        if n["q"] > 40:
            f.fg = HW

    f.on_fg_query = on_q
    with caplog.at_level(logging.INFO, logger="bot.capture"):
        assert g.wait_until_foreground(poll=0.5, beep_every=5.0) is True
    assert f.beeps >= 1
    msgs = [r.message for r in caplog.records]
    assert sum('hacé clic en la ventana "Guabina"'.lower() in m.lower() for m in msgs) == 1
    assert "Juego al frente, sigo." in msgs


def test_wait_timeout(fw):
    g, f = fw
    assert g.wait_until_foreground(timeout=3.0, poll=0.5, beep_every=1.0) is False
    assert f.beeps >= 3


def test_wait_should_stop(fw):
    g, f = fw
    assert g.wait_until_foreground(poll=0.5, should_stop=lambda: f.t >= 2.0) is False


def test_wait_admin_message(fw, monkeypatch, caplog):
    g, f = fw
    monkeypatch.setattr(cap, "elevation_mismatch", lambda h: True)
    with caplog.at_level(logging.WARNING, logger="bot.capture"):
        g.wait_until_foreground(timeout=1.0)
    assert any("corre como administrador" in r.message for r in caplog.records)


def test_elevation_mismatch_never_raises():
    assert cap.elevation_mismatch(0) in (True, False, None)


# --- is_unobstructed / ensure_ready ---
@pytest.fixture
def ready(fw, monkeypatch):
    g, f = fw
    f.covered = lambda pt: False  # True -> otra ventana (99) en ese punto
    f.on_sleep = None
    f.set_fg_effect = lambda f: setattr(f, "fg", HW)
    orig_sleep = f.sleep

    def sleep(s):
        orig_sleep(s)
        if f.on_sleep:
            f.on_sleep(s)

    monkeypatch.setattr(cap, "_sleep", sleep)
    gw_ns = cap.win32gui
    gw_ns.WindowFromPoint = lambda pt: 99 if f.covered(pt) else HW
    gw_ns.GetAncestor = lambda h, flag: h
    monkeypatch.setattr(g, "client_rect_on_screen", lambda: Rect(8, 31, 200, 100))
    return g, f


def test_unobstructed_libre(ready):
    g, f = ready
    assert g.is_unobstructed() is True


def test_unobstructed_tapado_en_un_punto(ready):
    g, f = ready
    f.covered = lambda pt: pt == (8 + 200 - 1 - 10, 31 + 100 - 1 - 10)  # esquina inferior derecha
    assert g.is_unobstructed() is False


def test_ensure_ready_espera_settle(ready):
    g, f = ready
    assert g.ensure_ready(timeout=10) is True
    assert f.t >= 0.5 - 1e-6


def test_ensure_ready_espera_a_que_deje_de_estar_tapado(ready):
    g, f = ready
    f.covered = lambda pt: f.t < 1.0
    assert g.ensure_ready(timeout=10) is True
    assert f.t >= 1.5 - 1e-6  # destapado a t=1.0 + settle


def test_ensure_ready_pierde_foco_en_confirmacion(ready):
    g, f = ready
    fired = []

    def on_sleep(s):
        if s == 0.5 and not fired:  # justo durante el settle
            fired.append(1)
            f.fg = 7

    f.on_sleep = on_sleep
    assert g.ensure_ready(timeout=10) is True
    assert f.t >= 1.0 - 1e-6  # dos esperas de settle


def test_ensure_ready_rapido_sin_cambios(ready):
    g, f = ready
    assert g.ensure_ready(timeout=10) is True
    t0, calls = f.t, list(f.calls)
    for _ in range(3):
        assert g.ensure_ready(timeout=10) is True
    assert f.t == t0 and f.calls == calls  # no duerme ni vuelve a activar


def test_ensure_ready_perdida_entre_llamadas(ready):
    g, f = ready
    assert g.ensure_ready(timeout=10) is True
    f.fg = 7
    t0 = f.t
    assert g.ensure_ready(timeout=10) is True
    assert f.t - t0 >= 0.5 - 1e-6


def test_ensure_ready_should_stop(ready):
    g, f = ready
    f.set_fg_effect = None
    assert g.ensure_ready(should_stop=lambda: f.t >= 2.0) is False


def test_ensure_ready_timeout(ready):
    g, f = ready
    f.set_fg_effect = None
    assert g.ensure_ready(timeout=3.0) is False
    f.set_fg_effect = lambda f: setattr(f, "fg", HW)
    f.covered = lambda pt: True  # siempre tapado
    assert g.ensure_ready(timeout=2.0) is False


# --- ensure_ready / activate interrumpibles (revision de seguridad fase 5) ---
def test_activate_should_stop_sends_no_alt(fw):
    g, f = fw
    assert g.activate(should_stop=lambda: True) is False
    assert f.calls == []                       # ni ALT ni SetForegroundWindow


def test_try_alt_no_alt_if_stopped(fw):
    g, f = fw
    g._try_alt(HW, lambda: True)
    assert f.calls == []


def test_ensure_ready_panic_during_activate(ready):
    g, f = ready
    f.set_fg_effect = None                     # nunca queda al frente
    assert g.ensure_ready(should_stop=lambda: f.t >= 0.2) is False
    assert f.t < 0.5                           # corta en tramos cortos, no espera 1.5 s


def test_ensure_ready_panic_during_settle(ready):
    g, f = ready
    assert g.ensure_ready(should_stop=lambda: f.t >= 0.1) is False   # settle_ms = 500
    assert 0.1 <= f.t < 0.2 + 1e-6             # tramos de STOP_POLL_S, no los 500 ms


def test_ensure_ready_panic_during_unobstructed_wait(ready):
    g, f = ready
    f.covered = lambda pt: True
    assert g.ensure_ready(should_stop=lambda: f.t >= 0.1) is False
    assert f.t < 0.3


def test_capture_saves_last_capture_rect(gw, monkeypatch):
    monkeypatch.setattr(gw, "_grab_raw", lambda r: np.zeros((r.h, r.w, 4), dtype=np.uint8))
    assert gw.last_capture_rect is None
    gw.capture()
    assert gw.last_capture_rect == Rect(8, 31, 200, 100)
