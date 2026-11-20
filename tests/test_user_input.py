"""Pausa automatica por input del usuario: detector puro, integracion con BotControl, hook con user32 falso y run_bot.
Nada instala hooks reales ni manda input."""
import copy
import ctypes
import logging
import threading

import pytest

import bot.input.base as base
import run_bot
from bot import user_input as ui
from bot.config import DEFAULTS, ConfigError, validate_config
from bot.control import BotControl, PanicStop
from bot.input.fake import FakeBackend
from bot.user_input import UserInputDetector, WindowsInputHook, vk_codes
from test_bot_states import FakeAlarm, FakeWindow, NoKeyboard, make_cfg, perc

F11, F12, KEY_W = 0x7A, 0x7B, ord("W")


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def make_detector(enabled=True, **safety):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["safety"].update(safety)
    clock = Clock()
    det = UserInputDetector(cfg, enabled=enabled, monotonic=clock, ignored_keys={F11, F12})
    return det, clock


def armed(**kw):
    det, clock = make_detector(**kw)
    det.arm()
    clock.t += 5.0   # pasada la gracia inicial
    return det, clock


# ------------------------------------------------------------------ detector puro

def test_tecla_fisica_dispara_y_la_inyectada_no():
    det, _ = armed()
    assert det.key(KEY_W, injected=True) is False
    assert det.key(KEY_W, injected=False, down=False) is False   # soltar no cuenta
    assert det.key(KEY_W, injected=False) is True


def test_f11_y_f12_se_ignoran():
    det, _ = armed()
    assert det.key(F11, False) is False and det.key(F12, False) is False
    assert det.key(KEY_W, False) is True


def test_click_y_rueda_fisicos_disparan_los_inyectados_no():
    det, _ = armed()
    assert det.mouse_button(True) is False and det.wheel(True) is False
    assert det.mouse_button(False, down=False) is False
    assert det.wheel(False) is True


def test_dispara_una_sola_vez():
    det, _ = armed()
    assert det.key(KEY_W, False) is True
    assert det.key(KEY_W, False) is False and det.mouse_button(False) is False


def test_mouse_movimientos_del_bot_no_cuentan_pero_actualizan_la_posicion():
    det, _ = armed()
    det.mouse_move(0, 0, injected=True)
    det.mouse_move(900, 500, injected=True)   # el bot salta lejos
    assert det.mouse_move(903, 502, injected=False) is False   # temblor chico desde donde dejo el bot


def test_mouse_temblor_bajo_el_umbral_se_ignora():
    det, clock = armed(user_mouse_px=10)
    det.mouse_move(100, 100, injected=False)
    for dx in (3, 6, 9):   # 3 + 3 + 3 = 9 px acumulados dentro de 0.3 s
        clock.t += 0.05
        assert det.mouse_move(100 + dx, 100, injected=False) is False


def test_mouse_acumulado_en_la_ventana_dispara():
    det, clock = armed(user_mouse_px=10)
    det.mouse_move(100, 100, injected=False)
    clock.t += 0.05
    assert det.mouse_move(106, 100, injected=False) is False
    clock.t += 0.05
    assert det.mouse_move(112, 100, injected=False) is True   # 6 + 6 = 12 >= 10


def test_mouse_lento_no_acumula_fuera_de_la_ventana():
    det, clock = armed(user_mouse_px=10, user_mouse_window_s=0.3)
    det.mouse_move(100, 100, injected=False)
    for i in range(1, 6):   # 4 px cada 0.2 s: nunca hay 10 px dentro de 0.3 s
        clock.t += 0.2
        assert det.mouse_move(100 + 4 * i, 100, injected=False) is False


def test_mouse_salto_grande_dispara():
    det, clock = armed(user_mouse_px=10)
    det.mouse_move(100, 100, injected=False)
    clock.t += 0.01
    assert det.mouse_move(300, 100, injected=False) is True


def test_gracia_tras_reanudar():
    det, clock = armed(auto_pause_grace_s=1.0)
    assert det.key(KEY_W, False) is True
    det.set_paused(True)
    det.set_paused(False)   # F11 o el boton de la interfaz
    clock.t += 0.5
    assert det.key(KEY_W, False) is False and det.mouse_button(False) is False   # el clic en la interfaz / el F11
    clock.t += 0.6
    assert det.key(KEY_W, False) is True


def test_gracia_inicial_al_armar():
    det, clock = make_detector(auto_pause_grace_s=1.0)
    det.arm()
    clock.t += 0.5
    assert det.key(KEY_W, False) is False
    clock.t += 0.6
    assert det.key(KEY_W, False) is True


def test_no_vigila_sin_armar_ni_pausado_ni_tras_desarmar():
    det, clock = make_detector()
    assert det.key(KEY_W, False) is False   # cuenta regresiva: sin armar
    det.arm()
    clock.t += 5
    det.set_paused(True)
    assert det.key(KEY_W, False) is False
    det.set_paused(False)
    clock.t += 5
    det.disarm()
    assert det.key(KEY_W, False) is False


def test_desactivado_por_config_y_por_observar():
    det, _ = armed(auto_pause_on_user_input=False)
    assert det.key(KEY_W, False) is False
    det, _ = armed(enabled=False)   # --observar
    assert det.key(KEY_W, False) is False and not det.enabled


# ------------------------------------------------------------------ integracion con BotControl

class Spy:
    def __init__(self):
        self.calls = []

    def on_pause(self):
        self.calls.append("pausa")

    def on_resume(self):
        self.calls.append("reanuda")


def control_with_detector():
    cfg = copy.deepcopy(DEFAULTS)
    clock = Clock()
    ctl = BotControl(cfg, keyboard=NoKeyboard(), cursor_pos=lambda: (9999, 9999), exit_fn=lambda c: None)
    det = UserInputDetector(cfg, monotonic=clock, ignored_keys={F11, F12})
    ctl.attach_user_input(det)
    spy = Spy()
    ctl.add_pause_callback(spy.on_pause, spy.on_resume)
    det.arm()
    clock.t += 5
    return ctl, det, clock, spy


def feed_key(ctl, det, vk=KEY_W, injected=False):
    """Lo que hace el hook: el detector decide y, si dispara, se pausa."""
    if det.key(vk, injected):
        ctl.auto_pause()


def test_auto_pausa_como_f11_y_reanuda(caplog):
    ctl, det, clock, spy = control_with_detector()
    with caplog.at_level(logging.WARNING):
        feed_key(ctl, det)
    assert ctl.is_paused and ctl.auto_paused and spy.calls == ["pausa"]
    assert "Pausa automatica: detecte mouse/teclado del usuario. F11 o la interfaz para reanudar" in caplog.text
    feed_key(ctl, det)   # ya pausado: nada nuevo
    assert spy.calls == ["pausa"]
    ctl.toggle_pause()   # F11 o el boton de la interfaz
    assert not ctl.is_paused and not ctl.auto_paused and spy.calls == ["pausa", "reanuda"]
    clock.t += 0.3
    feed_key(ctl, det)   # dentro de la gracia
    assert not ctl.is_paused
    clock.t += 1.5
    feed_key(ctl, det)
    assert ctl.is_paused and ctl.auto_paused


def test_pausa_manual_no_es_automatica_y_input_del_bot_no_pausa():
    ctl, det, _, _ = control_with_detector()
    feed_key(ctl, det, injected=True)
    assert not ctl.is_paused
    ctl.toggle_pause()
    assert ctl.is_paused and not ctl.auto_paused


def test_auto_pausa_no_impide_detener_ni_panico():
    ctl, det, _, _ = control_with_detector()
    feed_key(ctl, det)
    assert ctl.is_paused
    ctl.panic("interfaz")   # Detener desde la interfaz, ya pausado
    with pytest.raises(PanicStop):
        ctl.check()
    ctl.auto_pause()   # despues del panico no hace nada
    ctl.mark_finished()


def test_auto_pausa_desbloquea_sleep_al_reanudar():
    ctl, det, _, _ = control_with_detector()
    feed_key(ctl, det)
    done = threading.Event()

    def worker():
        ctl.sleep(0.01)
        done.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    assert not done.wait(0.2)   # bloqueado por la pausa
    ctl.toggle_pause()
    assert done.wait(2.0)


# ------------------------------------------------------------------ hook de Windows con user32 falso

class FakeUser32:
    def __init__(self):
        self.next_calls = []

    def CallNextHookEx(self, hhook, n_code, w, l):
        self.next_calls.append((n_code, w))
        return 0


_KEEP = []   # los structs tienen que seguir vivos mientras el hook lee su direccion


def kb_struct(vk, flags=0):
    s = ui._KBDLLHOOKSTRUCT(vk, 0, flags, 0, 0)
    _KEEP.append(s)
    return s


def ms_struct(x, y, flags=0):
    s = ui._MSLLHOOKSTRUCT()
    s.pt.x, s.pt.y, s.flags = x, y, flags
    _KEEP.append(s)
    return s


def make_hook(ignore_pids=(), pid_at=0, fg_pid=0):
    det, clock = armed()
    fired = threading.Event()
    u32 = FakeUser32()
    hook = WindowsInputHook(det, fired.set, ignore_pids=set(ignore_pids), user32=u32,
                            pid_at_point=lambda x, y: pid_at, foreground_pid=lambda: fg_pid)
    return hook, det, fired, u32, clock


def test_hook_teclado_fisico_dispara_e_inyectado_no():
    hook, _, fired, u32, _ = make_hook()
    hook._on_keyboard(0, ui.WM_KEYDOWN, ctypes.addressof(kb_struct(KEY_W, ui.LLKHF_INJECTED)))
    assert not fired.wait(0.1)
    hook._on_keyboard(0, ui.WM_KEYDOWN, ctypes.addressof(kb_struct(F11)))   # F11 fisico: ignorado
    assert not fired.wait(0.1)
    hook._on_keyboard(0, ui.WM_KEYDOWN, ctypes.addressof(kb_struct(KEY_W)))
    assert fired.wait(2.0)
    assert len(u32.next_calls) == 3   # siempre pasa el evento al resto de los hooks


def test_hook_ncode_negativo_no_se_procesa():
    hook, _, fired, u32, _ = make_hook()
    hook._on_keyboard(-1, ui.WM_KEYDOWN, ctypes.addressof(kb_struct(KEY_W)))
    assert not fired.wait(0.1) and u32.next_calls == [(-1, ui.WM_KEYDOWN)]


def test_hook_mouse_inyectado_no_dispara_fisico_si():
    hook, _, fired, _, clock = make_hook()
    hook._on_mouse(0, ui.WM_MOUSEMOVE, ctypes.addressof(ms_struct(100, 100, ui.LLMHF_INJECTED)))
    hook._on_mouse(0, ui.WM_MOUSEMOVE, ctypes.addressof(ms_struct(800, 600, ui.LLMHF_INJECTED)))
    hook._on_mouse(0, ui.WM_BUTTON_DOWN[0], ctypes.addressof(ms_struct(800, 600, ui.LLMHF_INJECTED)))
    assert not fired.wait(0.1)
    hook._on_mouse(0, ui.WM_BUTTON_DOWN[0], ctypes.addressof(ms_struct(800, 600)))
    assert fired.wait(2.0)


def test_hook_ignora_input_sobre_la_ventana_de_la_interfaz():
    hook, _, fired, _, _ = make_hook(ignore_pids={4242}, pid_at=4242, fg_pid=4242)
    hook._on_mouse(0, ui.WM_BUTTON_DOWN[0], ctypes.addressof(ms_struct(10, 10)))   # clic en Pausar / Detener
    hook._on_mouse(0, ui.WM_MOUSEMOVE, ctypes.addressof(ms_struct(10, 10)))
    hook._on_mouse(0, ui.WM_MOUSEMOVE, ctypes.addressof(ms_struct(400, 400)))
    hook._on_keyboard(0, ui.WM_KEYDOWN, ctypes.addressof(kb_struct(KEY_W)))
    assert not fired.wait(0.2)


def test_hook_falla_en_el_callback_no_rompe_el_hook():
    det, _ = armed()

    def boom():
        raise RuntimeError("x")

    hook = WindowsInputHook(det, boom, user32=FakeUser32(), pid_at_point=lambda x, y: 0, foreground_pid=lambda: 0)
    hook._on_keyboard(0, ui.WM_KEYDOWN, ctypes.addressof(kb_struct(KEY_W)))   # el hilo captura la excepcion


def test_vk_codes():
    assert vk_codes("f11") == {F11} and vk_codes("f12") == {F12}
    assert {0x11, 0xA2, 0xA3, F11} <= vk_codes("ctrl+f11")
    assert vk_codes("a") == {ord("A")}


# ------------------------------------------------------------------ config

def test_config_defaults_y_validacion():
    s = DEFAULTS["safety"]
    assert s["auto_pause_on_user_input"] is True and s["user_mouse_px"] == 10 and s["auto_pause_grace_s"] == 1.0
    validate_config(copy.deepcopy(DEFAULTS))
    for key, bad in (("auto_pause_on_user_input", "si"), ("user_mouse_px", -1), ("auto_pause_grace_s", "x"), ("user_mouse_window_s", 0)):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["safety"][key] = bad
        with pytest.raises(ConfigError, match=key):
            validate_config(cfg)


# ------------------------------------------------------------------ run_bot

@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


class FakeHook:
    instances = []

    def __init__(self, detector, on_trigger):
        self.detector, self.on_trigger = detector, on_trigger
        self.started_watching = None
        self.closed = False
        FakeHook.instances.append(self)

    def start(self):
        self.started_watching = self.detector.watching

    def close(self):
        self.closed = True


def run_main(argv, **kw):
    FakeHook.instances = []
    cfg = make_cfg(loop={"cycle_ms": 5})
    cfg["history"]["enabled"] = False
    ctl = BotControl(cfg, keyboard=NoKeyboard(), cursor_pos=lambda: (9999, 9999), exit_fn=lambda c: None)
    return run_bot.main(argv, cfg=cfg, window=FakeWindow(), control=ctl, alarm=FakeAlarm(), backend=FakeBackend(),
                        bot_kwargs={"perceive_fn": lambda img, c, *, full_captcha=False, parts=None: perc()},
                        input_hook=FakeHook, **kw), ctl


def test_run_bot_arma_el_detector_y_arranca_el_hook_despues_de_la_cuenta():
    code, ctl = run_main(["--sin-cuenta-regresiva", "--max-minutos", "0.01"])
    (h,) = FakeHook.instances
    assert code == 0 and h.started_watching is True and h.closed
    assert h.on_trigger == ctl.auto_pause


def test_run_bot_observar_no_vigila():
    code, _ = run_main(["--observar", "--sin-cuenta-regresiva", "--max-minutos", "0.01"])
    assert code == 0 and FakeHook.instances == []


def test_run_bot_desactivado_por_config():
    FakeHook.instances = []
    cfg = make_cfg(loop={"cycle_ms": 5})
    cfg["safety"]["auto_pause_on_user_input"] = False
    cfg["history"]["enabled"] = False
    ctl = BotControl(cfg, keyboard=NoKeyboard(), cursor_pos=lambda: (9999, 9999), exit_fn=lambda c: None)
    run_bot.main(["--sin-cuenta-regresiva", "--max-minutos", "0.01"], cfg=cfg, window=FakeWindow(), control=ctl,
                 alarm=FakeAlarm(), backend=FakeBackend(), input_hook=FakeHook,
                 bot_kwargs={"perceive_fn": lambda img, c, *, full_captcha=False, parts=None: perc()})
    assert FakeHook.instances == []


def test_status_informa_auto_paused():
    class Bot:
        class stats:
            metins = items = captchas = unstucks = 0
            elapsed_s = 0.0
        state = "BUSCAR"

    ctl, det, _, _ = control_with_detector()
    bridge = run_bot.UiBridge(Bot, ctl, DEFAULTS, stdin=iter(()))
    assert bridge.status()["auto_paused"] is False
    feed_key(ctl, det)
    assert bridge.status()["auto_paused"] is True and bridge.status()["paused"] is True
