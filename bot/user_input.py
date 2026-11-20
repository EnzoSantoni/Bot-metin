"""Pausa automatica: distingue el input fisico del usuario del que inyecta el bot.

`UserInputDetector` es una clase pura: se alimenta con eventos (cada uno trae el flag "inyectado" que Windows pone a
todo lo que viene de SendInput, o sea pydirectinput) y devuelve True cuando hay que pausar. `WindowsInputHook` es la
capa delgada con ctypes (WH_KEYBOARD_LL / WH_MOUSE_LL) que le pasa los eventos reales; los tests no la usan con hooks
reales, solo con un user32 falso.
"""
from __future__ import annotations

import ctypes
import logging
import math
import os
import threading
import time
from collections import deque
from ctypes import wintypes
from typing import Any, Callable

logger = logging.getLogger(__name__)


class UserInputDetector:
    """Decide si un evento de input es del usuario y hay que pausar. No toca el sistema.

    Ciclo: `arm()` al terminar la cuenta regresiva, `set_paused(True/False)` cuando el bot se pausa / reanuda
    (al reanudar empieza la gracia). Los eventos `key`, `mouse_move`, `mouse_button` y `wheel` devuelven True una sola
    vez: despues el detector queda "pausado" hasta que `set_paused(False)` lo rearme.
    """

    def __init__(self, cfg: dict, *, enabled: bool = True, monotonic: Callable[[], float] = time.monotonic,
                 ignored_keys: set[int] | None = None) -> None:
        s = cfg["safety"]
        self.enabled = bool(enabled and s["auto_pause_on_user_input"])
        self._px = float(s["user_mouse_px"])
        self._window_s = float(s["user_mouse_window_s"])
        self._grace_s = float(s["auto_pause_grace_s"])
        self._now = monotonic
        self.ignored_keys: set[int] = set(ignored_keys or ())   # codigos virtuales (F11 / F12): ya tienen su manejo
        self._armed = False
        self._paused = False
        self._grace_until = 0.0
        self._last_pos: tuple[int, int] | None = None
        self._moves: deque[tuple[float, float]] = deque()      # (instante, distancia) de movimientos fisicos
        self._lock = threading.Lock()

    # --- estado ---
    def arm(self) -> None:
        """Empieza a vigilar (despues de la cuenta regresiva); da la gracia inicial."""
        with self._lock:
            self._armed = True
            self._paused = False
            self._grace_until = self._now() + self._grace_s
            self._moves.clear()

    def disarm(self) -> None:
        with self._lock:
            self._armed = False

    def set_paused(self, paused: bool) -> None:
        """El bot se pauso (por F11, interfaz o pausa automatica) o se reanudo. Al reanudar: gracia y se limpia el temblor."""
        with self._lock:
            self._paused = paused
            if not paused:
                self._grace_until = self._now() + self._grace_s
                self._moves.clear()

    @property
    def watching(self) -> bool:
        return self.enabled and self._armed and not self._paused

    def _trigger(self) -> bool:
        """Con el lock tomado: True si hay que pausar (y deja el detector en pausa para no repetir)."""
        if not self.watching or self._now() < self._grace_until:
            return False
        self._paused = True
        self._moves.clear()
        return True

    # --- eventos ---
    def key(self, vk: int, injected: bool, down: bool = True) -> bool:
        """Tecla. Solo cuentan las pulsaciones (down) fisicas; F11/F12 se ignoran."""
        if injected or not down or vk in self.ignored_keys:
            return False
        with self._lock:
            return self._trigger()

    def mouse_button(self, injected: bool, down: bool = True) -> bool:
        if injected or not down:
            return False
        with self._lock:
            return self._trigger()

    def wheel(self, injected: bool) -> bool:
        if injected:
            return False
        with self._lock:
            return self._trigger()

    def mouse_move(self, x: int, y: int, injected: bool) -> bool:
        """Movimiento. Los inyectados solo actualizan la posicion; los fisicos suman distancia en `user_mouse_window_s`."""
        with self._lock:
            last, self._last_pos = self._last_pos, (x, y)
            if injected or last is None or not self.watching:
                return False
            t = self._now()
            self._moves.append((t, math.hypot(x - last[0], y - last[1])))
            while self._moves and t - self._moves[0][0] > self._window_s:
                self._moves.popleft()
            if sum(d for _, d in self._moves) < self._px:
                return False
            return self._trigger()


# ----------------------------------------------------------------------------- hooks de Windows

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_QUIT = 0x0012
WM_USER = 0x0400
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
WM_MOUSEMOVE = 0x0200
WM_BUTTON_DOWN = (0x0201, 0x0204, 0x0207, 0x020B)   # izquierdo, derecho, medio, X
WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x020A, 0x020E
LLKHF_INJECTED, LLKHF_LOWER_IL_INJECTED = 0x10, 0x02
LLMHF_INJECTED, LLMHF_LOWER_IL_INJECTED = 0x01, 0x02
GA_ROOT = 2

_LRESULT = ctypes.c_ssize_t
_HOOKPROC = ctypes.WINFUNCTYPE(_LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


_NAMED_VK = {"space": 0x20, "esc": 0x1B, "escape": 0x1B, "enter": 0x0D, "return": 0x0D, "tab": 0x09, "backspace": 0x08,
             "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12, "pause": 0x13, "capslock": 0x14,
             "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24, "left": 0x25, "up": 0x26, "right": 0x27,
             "down": 0x28, "insert": 0x2D, "delete": 0x2E, "del": 0x2E}


def vk_codes(spec: str) -> set[int]:
    """'f11' / 'ctrl+f12' -> codigos virtuales de Windows (los modificadores incluyen sus variantes izq./der.)."""
    out: set[int] = set()
    for name in (p.strip().lower() for p in spec.split("+")):
        if len(name) >= 2 and name[0] == "f" and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
            out.add(0x6F + int(name[1:]))
        elif name in _NAMED_VK:
            out.add(_NAMED_VK[name])
            if name in ("shift", "ctrl", "control", "alt"):
                out |= {"shift": {0xA0, 0xA1}, "ctrl": {0xA2, 0xA3}, "control": {0xA2, 0xA3}, "alt": {0xA4, 0xA5}}[name]
        elif len(name) == 1 and name.isalnum():
            out.add(ord(name.upper()))
    return out


def _real_user32() -> Any:
    """user32 con argtypes/restype correctos para 64 bits (punteros y HWND no caben en un int de 32 bits)."""
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    u.CallNextHookEx.restype = _LRESULT
    u.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
    u.SetWindowsHookExW.restype = wintypes.HHOOK
    u.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    u.WindowFromPoint.argtypes = [wintypes.POINT]
    u.WindowFromPoint.restype = wintypes.HWND
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    u.GetAncestor.restype = wintypes.HWND
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    u.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
    u.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    return u


class WindowsInputHook:
    """Hooks de bajo nivel de teclado y mouse en un hilo propio con su bucle de mensajes. Delgada y sin logica de decision.

    `on_trigger` se llama en un hilo aparte (los hooks tienen un tiempo maximo y pausar suelta teclas con SendInput).
    `ignore_pids`: eventos cuyo destino es una ventana de esos procesos (la interfaz) no cuentan.
    """

    def __init__(self, detector: UserInputDetector, on_trigger: Callable[[], None], *, ignore_pids: set[int] | None = None,
                 user32: Any = None, pid_at_point: Callable[[int, int], int] | None = None,
                 foreground_pid: Callable[[], int] | None = None) -> None:
        self._det, self._on_trigger = detector, on_trigger
        self._ignore = set(ignore_pids or ())
        self._u32 = user32 if user32 is not None else _real_user32()
        self._pid_at_point = pid_at_point or self._real_pid_at_point
        self._foreground_pid = foreground_pid or self._real_foreground_pid
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._hooks: list[Any] = []
        self._procs: list[Any] = []    # referencias: si el GC suelta el callback, Windows revienta

    # --- decision de a quien pertenece el evento ---
    def _real_pid_at_point(self, x: int, y: int) -> int:
        hwnd = self._u32.WindowFromPoint(wintypes.POINT(x, y))
        return self._pid_of(hwnd)

    def _real_foreground_pid(self) -> int:
        return self._pid_of(self._u32.GetForegroundWindow())

    def _pid_of(self, hwnd: Any) -> int:
        if not hwnd:
            return 0
        root = self._u32.GetAncestor(hwnd, GA_ROOT) or hwnd
        pid = wintypes.DWORD(0)
        self._u32.GetWindowThreadProcessId(root, ctypes.byref(pid))
        return int(pid.value)

    def _own_window(self, pid: int) -> bool:
        return pid in self._ignore or pid == os.getpid()

    # --- callbacks de los hooks (testeables: reciben el lParam como direccion) ---
    def _fire(self) -> None:
        threading.Thread(target=self._safe_trigger, name="auto-pause", daemon=True).start()

    def _safe_trigger(self) -> None:
        try:
            self._on_trigger()
        except Exception:  # noqa: BLE001
            logger.exception("Fallo la pausa automatica")

    def _on_keyboard(self, n_code: int, w_param: int, l_param: int) -> int:
        try:
            if n_code >= 0:
                ev = _KBDLLHOOKSTRUCT.from_address(l_param)
                injected = bool(ev.flags & (LLKHF_INJECTED | LLKHF_LOWER_IL_INJECTED))
                down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
                if not injected and down and self._own_window(self._foreground_pid()):
                    pass   # tecleando en la interfaz: no cuenta
                elif self._det.key(int(ev.vkCode), injected, down):
                    self._fire()
        except Exception:  # noqa: BLE001 - un hook nunca debe lanzar
            logger.exception("Error en el hook de teclado")
        return self._u32.CallNextHookEx(None, n_code, w_param, l_param)

    def _on_mouse(self, n_code: int, w_param: int, l_param: int) -> int:
        try:
            if n_code >= 0:
                ev = _MSLLHOOKSTRUCT.from_address(l_param)
                injected = bool(ev.flags & (LLMHF_INJECTED | LLMHF_LOWER_IL_INJECTED))
                x, y = int(ev.pt.x), int(ev.pt.y)
                own = not injected and self._own_window(self._pid_at_point(x, y))   # sobre la ventana de la interfaz
                if w_param == WM_MOUSEMOVE:
                    hit = self._det.mouse_move(x, y, injected or own)
                elif w_param in WM_BUTTON_DOWN:
                    hit = False if own else self._det.mouse_button(injected, True)
                elif w_param in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
                    hit = False if own else self._det.wheel(injected)
                else:
                    hit = False
                if hit:
                    self._fire()
        except Exception:  # noqa: BLE001
            logger.exception("Error en el hook de mouse")
        return self._u32.CallNextHookEx(None, n_code, w_param, l_param)

    # --- ciclo de vida ---
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="user-input-hook", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)

    def _run(self) -> None:
        u32 = self._u32
        try:
            try:
                self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
                u32.PeekMessageW(ctypes.byref(wintypes.MSG()), None, WM_USER, WM_USER, 0)   # crea la cola de mensajes
                for kind, fn in ((WH_KEYBOARD_LL, self._on_keyboard), (WH_MOUSE_LL, self._on_mouse)):
                    proc = _HOOKPROC(fn)
                    self._procs.append(proc)
                    h = u32.SetWindowsHookExW(kind, proc, None, 0)
                    if not h:
                        logger.warning("No se pudo instalar el hook %s: la pausa automatica por input no funciona", kind)
                    else:
                        self._hooks.append(h)
            finally:
                self._ready.set()
            if not self._hooks:
                return
            msg = wintypes.MSG()
            while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                u32.TranslateMessage(ctypes.byref(msg))
                u32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._unhook_all()

    def _unhook_all(self) -> None:
        """Desinstala los hooks aunque el bucle de mensajes haya fallado."""
        for h in self._hooks:
            try:
                self._u32.UnhookWindowsHookEx(h)
            except Exception:  # noqa: BLE001
                logger.exception("No se pudo desinstalar un hook")
        self._hooks.clear()

    def close(self) -> None:
        """Saca los hooks (el hilo los desinstala al salir del bucle). Idempotente."""
        t, self._thread = self._thread, None
        if t is None:
            return
        if self._thread_id:
            self._u32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        t.join(timeout=1.0)
