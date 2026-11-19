"""Captura de la ventana del juego y traduccion de coordenadas cliente <-> pantalla."""
from __future__ import annotations

import ctypes
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from bot import geometry
from bot.config import DEFAULTS
from bot.geometry import Rect

logger = logging.getLogger(__name__)

try:  # pywin32 solo existe en Windows; los tests parchean los metodos que lo usan
    import pywintypes
    import win32api
    import win32con
    import win32gui
    import win32process
except ImportError:  # pragma: no cover
    pywintypes = win32api = win32con = win32gui = win32process = None  # type: ignore

LIST_HINT = r"Corré `.venv\Scripts\python.exe list_windows.py` para ver los títulos"
ADMIN_HINT = ("El juego corre como administrador y Python no: abrí la terminal como administrador "
              "(si no, Windows también bloquea los clics y teclas del bot).")
RESTORE_POLL = 0.2  # s que se espera el primer plano tras SW_RESTORE
GA_ROOT = 2  # GetAncestor: ventana raiz
UNOBSTRUCTED_MARGIN = 10  # px desde el borde del cliente para los puntos de prueba
FOCUS_POLL = 0.05 # s entre chequeos de primer plano tras un intento de activacion
STOP_POLL_S = 0.05  # las esperas largas se cortan en tramos de este tamano para atender should_stop

# Indirecciones para poder simular tiempo y sonido en los tests
_monotonic = time.monotonic
_sleep = time.sleep


def _sleep_stoppable(seconds: float, should_stop: Callable[[], bool] | None) -> bool:
    """Duerme `seconds` en tramos cortos. True si `should_stop()` corto la espera."""
    if should_stop is None:
        _sleep(seconds)
        return False
    end = _monotonic() + seconds
    while True:
        if should_stop():
            return True
        left = end - _monotonic()
        if left <= 0:
            return False
        _sleep(min(STOP_POLL_S, left))


def _beep() -> None:
    """Aviso sonoro; nunca falla."""
    try:
        import winsound
        winsound.Beep(1000, 200)
    except Exception:
        pass


def elevation_mismatch(hwnd: int) -> bool | None:
    """True si el juego corre elevado (admin) y Python no; False si no hay problema; None si no se sabe."""
    try:
        from ctypes import wintypes
        if ctypes.windll.shell32.IsUserAnAdmin():
            return False
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        u32 = ctypes.windll.user32
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        pid = wintypes.DWORD(0)
        u32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        if not pid.value:
            return None
        proc = k32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not proc:
            return True if ctypes.get_last_error() == 5 else None  # acceso denegado: probablemente elevado
        try:
            token = wintypes.HANDLE()
            if not adv.OpenProcessToken(proc, 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
                return True if ctypes.get_last_error() == 5 else None
            try:
                elevated = wintypes.DWORD(0)
                size = wintypes.DWORD(0)
                if not adv.GetTokenInformation(token, 20, ctypes.byref(elevated),  # TokenElevation
                                               ctypes.sizeof(elevated), ctypes.byref(size)):
                    return None
                return bool(elevated.value)
            finally:
                k32.CloseHandle(token)
        finally:
            k32.CloseHandle(proc)
    except Exception:
        return None


def enable_dpi_awareness() -> None:
    """Activa DPI awareness del proceso. Idempotente y sin excepciones."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:  # ya seteado (acceso denegado) o API no disponible
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def get_dpi_scale(hwnd: int) -> float:
    """Escala de Windows para la ventana (1.0 = 100%)."""
    try:
        dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
        return dpi / 96 if dpi else 1.0
    except Exception:
        return 1.0


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    client_rect_on_screen: Rect
    visible: bool
    minimized: bool


class WindowNotFoundError(Exception):
    """No se encontro la ventana del juego (o se cerro)."""


class WindowMinimizedError(Exception):
    """La ventana del juego esta minimizada."""


def _client_rect(hwnd: int) -> Rect:
    try:
        _, _, w, h = win32gui.GetClientRect(hwnd)
        x, y = win32gui.ClientToScreen(hwnd, (0, 0))
    except pywintypes.error as e:  # la ventana se cerro entre llamadas
        raise WindowNotFoundError(f"La ventana {hwnd} ya no existe: {e}") from e
    return Rect(x, y, w, h)


def list_windows(title_filter: str | None = None) -> list[WindowInfo]:
    """Ventanas visibles con titulo; filtro por substring sin distinguir mayusculas."""
    found: list[WindowInfo] = []
    flt = title_filter.lower() if title_filter else None

    def cb(hwnd: int, _) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title or (flt and flt not in title.lower()):
            return True
        try:
            found.append(WindowInfo(hwnd, title, _client_rect(hwnd), True,
                                    bool(win32gui.IsIconic(hwnd))))
        except Exception:  # la ventana desaparecio mientras se enumeraba
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return found


class GameWindow:
    """Ventana del juego. Unico lugar que traduce cliente <-> pantalla.

    mss captura los pixeles de la PANTALLA: el juego tiene que estar visible
    y sin ventanas encima.
    """

    def __init__(self, title: str, expected_size: tuple[int, int] = tuple(DEFAULTS["expected_client_size"]),
                 settle_ms: float = DEFAULTS["window"]["settle_ms"],
                 unobstructed_poll_ms: float = DEFAULTS["window"]["unobstructed_poll_ms"]) -> None:
        enable_dpi_awareness()
        self.settle_ms = settle_ms
        self.unobstructed_poll_ms = unobstructed_poll_ms
        self._was_ready = False
        self.title = title
        self.expected_size = tuple(expected_size)
        self.hwnd: int | None = None
        self._size_warned = False
        self._local = threading.local()
        self.last_capture_rect: Rect | None = None   # client_rect_on_screen usado en la ultima captura

    # --- ventana ---
    def find(self) -> WindowInfo:
        """Busca la ventana por titulo; reutiliza el hwnd mientras sea valido."""
        if self.hwnd is not None and win32gui.IsWindow(self.hwnd):
            try:
                return WindowInfo(self.hwnd, win32gui.GetWindowText(self.hwnd),
                                  _client_rect(self.hwnd), bool(win32gui.IsWindowVisible(self.hwnd)),
                                  bool(win32gui.IsIconic(self.hwnd)))
            except WindowNotFoundError:
                self.hwnd = None
        matches = list_windows(self.title)
        if not matches:
            raise WindowNotFoundError(
                f"No se encontró ninguna ventana con título que contenga {self.title!r}. {LIST_HINT}")
        if len(matches) > 1:
            logger.warning("Varias ventanas coinciden con %r: %s", self.title,
                           "; ".join(f"{m.hwnd}={m.title!r}" for m in matches))
        # Prioridad: titulo exacto (evita, p. ej., una pestaña "Metin2 - wiki" del navegador),
        # despues ventanas visibles y no minimizadas.
        wanted = self.title.strip().lower()
        chosen = min(matches, key=lambda m: (m.title.strip().lower() != wanted,
                                             not (m.visible and not m.minimized)))
        self.hwnd = chosen.hwnd
        self._size_warned = False
        return chosen

    def _hwnd(self) -> int:
        return self.find().hwnd

    def client_rect_on_screen(self) -> Rect:
        """Area de cliente en coordenadas de pantalla (se recalcula siempre)."""
        try:
            r = _client_rect(self._hwnd())
        except WindowNotFoundError:
            self.hwnd = None  # la proxima llamada vuelve a buscar
            raise
        if not self._size_warned and (r.w, r.h) != self.expected_size:
            logger.warning("Tamaño de cliente %dx%d distinto del esperado %dx%d",
                           r.w, r.h, *self.expected_size)
            self._size_warned = True
        return r

    def is_minimized(self) -> bool:
        return bool(win32gui.IsIconic(self._hwnd()))

    def is_foreground(self) -> bool:
        return win32gui.GetForegroundWindow() == self._hwnd()

    def _admin_hint(self, hwnd: int) -> str:
        return (" " + ADMIN_HINT) if elevation_mismatch(hwnd) else ""

    def _try_alt(self, hwnd: int, should_stop: Callable[[], bool] | None = None) -> None:
        """Intento A: truco ALT para esquivar el foreground lock + SetForegroundWindow."""
        if should_stop is not None and should_stop():
            return   # con panico no se manda ni ALT
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        win32gui.SetForegroundWindow(hwnd)

    def _try_attach(self, hwnd: int) -> None:
        """Intento B: acoplar la cola de input al hilo en primer plano."""
        fg = win32gui.GetForegroundWindow()
        fg_thread = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
        cur_thread = win32api.GetCurrentThreadId()
        attached = False
        try:
            if fg_thread and fg_thread != cur_thread:
                win32process.AttachThreadInput(cur_thread, fg_thread, True)
                attached = True
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        finally:
            if attached:
                try:
                    win32process.AttachThreadInput(cur_thread, fg_thread, False)
                except Exception as e:
                    logger.debug("No se pudo desacoplar el hilo: %s", e)

    @staticmethod
    def _poll_foreground(hwnd: int, duration: float,
                         should_stop: Callable[[], bool] | None = None) -> bool:
        """Espera hasta `duration` s a que `hwnd` sea la ventana en primer plano."""
        deadline = _monotonic() + duration
        while True:
            if win32gui.GetForegroundWindow() == hwnd:
                return True
            if _monotonic() >= deadline or (should_stop is not None and should_stop()):
                return False
            _sleep(FOCUS_POLL)

    def activate(self, timeout: float = 1.5, should_stop: Callable[[], bool] | None = None) -> bool:
        """Trae la ventana al frente. Devuelve si quedo en primer plano.

        La activacion de Windows es asincrona: se hace polling tras cada intento.
        """
        hwnd = self._hwnd()  # WindowNotFoundError se propaga
        stopped = should_stop or (lambda: False)
        try:
            if win32gui.GetForegroundWindow() == hwnd and not win32gui.IsIconic(hwnd):
                return True  # ya está al frente: no mandar ALT al juego
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                if self._poll_foreground(hwnd, RESTORE_POLL, should_stop):
                    return True  # el restore alcanzó: no mandar ALT
        except Exception as e:
            logger.debug("Error preparando la activación: %s", e)
        for name, attempt, share in (("A (ALT)", lambda h: self._try_alt(h, should_stop), 0.5),
                                     ("B (AttachThreadInput)", self._try_attach, 0.5)):
            if stopped():
                return False
            logger.debug("Activando ventana, intento %s", name)
            try:
                attempt(hwnd)
            except Exception as e:  # pywintypes.error: suele pasar aunque funcione a medias
                logger.debug("Intento %s lanzó: %s", name, e)
            try:
                if self._poll_foreground(hwnd, timeout * share, should_stop):
                    logger.debug("Ventana al frente con el intento %s", name)
                    return True
            except Exception as e:
                logger.debug("Error consultando el primer plano: %s", e)
        if stopped():
            return False
        logger.warning("No se pudo traer la ventana al frente.%s", self._admin_hint(hwnd))
        return False

    def wait_until_foreground(self, timeout: float | None = None, poll: float = 0.5,
                              beep_every: float = 5.0,
                              should_stop: Callable[[], bool] | None = None) -> bool:
        """Intenta activar; si no puede, avisa y espera a que el usuario ponga el juego al frente."""
        if self.activate(should_stop=should_stop):
            return True
        if should_stop is not None and should_stop():
            return False
        hwnd = self._hwnd()
        title = win32gui.GetWindowText(hwnd) or self.title
        logger.warning('El juego no está al frente. Hacé clic en la ventana "%s" para seguir.%s',
                       title, self._admin_hint(hwnd))
        start = _monotonic()
        next_beep = start
        while True:
            now = _monotonic()
            if should_stop is not None and should_stop():
                return False
            if self.is_foreground():  # sin robar el foco otra vez
                logger.info("Juego al frente, sigo.")
                return True
            if timeout is not None and now - start >= timeout:
                return False
            if now >= next_beep:
                _beep()
                next_beep = now + beep_every
            if _sleep_stoppable(poll, should_stop):
                return False

    def is_unobstructed(self) -> bool:
        """True si el juego es la ventana visible en 4 esquinas (margen), centro y medios de borde."""
        try:
            hwnd = self._hwnd()
            r = self.client_rect_on_screen()
            m = min(UNOBSTRUCTED_MARGIN, r.w // 2, r.h // 2)
            xs, ys = (r.x + m, r.x + r.w // 2, r.x + r.w - 1 - m), (r.y + m, r.y + r.h // 2, r.y + r.h - 1 - m)
            for x, y in ((x, y) for y in ys for x in xs):  # 9 puntos: esquinas, medios y centro
                h = win32gui.WindowFromPoint((x, y))
                if not h or win32gui.GetAncestor(h, GA_ROOT) != hwnd:
                    return False
            return True
        except Exception as e:  # ventana cerrada o API fallando: no se puede afirmar que este libre
            logger.debug("is_unobstructed fallo: %s", e)
            return False

    def _is_ready_now(self) -> bool:
        return not self.is_minimized() and self.is_foreground() and self.is_unobstructed()

    def ensure_ready(self, timeout: float | None = None,
                     should_stop: Callable[[], bool] | None = None) -> bool:
        """Deja el juego al frente, sin tapar y estable. Llamar antes de capturar o mandar input.

        Rapido si ya estaba listo en la llamada anterior y nada cambio; si no, activa, espera
        a que la pantalla termine de redibujarse (`settle_ms`) y confirma. False si timeout/stop.
        """
        if self._was_ready and self._is_ready_now():
            return True
        if self._was_ready:
            logger.debug("Se perdió el foco o quedó tapado el juego; espero de nuevo.")
        self._was_ready = False
        start = _monotonic()

        def expired() -> bool:
            return ((should_stop is not None and should_stop())
                    or (timeout is not None and _monotonic() - start >= timeout))

        while True:
            remaining = None if timeout is None else max(0.0, timeout - (_monotonic() - start))
            if not self.wait_until_foreground(remaining, should_stop=should_stop):
                return False
            while not self.is_unobstructed():
                if expired():
                    return False
                if _sleep_stoppable(self.unobstructed_poll_ms / 1000, should_stop):
                    return False
            logger.debug("Juego al frente; espero %d ms a que se redibuje.", self.settle_ms)
            if _sleep_stoppable(self.settle_ms / 1000, should_stop):
                return False
            if self._is_ready_now():
                self._was_ready = True
                logger.info("Juego al frente y estable, sigo.")
                return True
            logger.debug("El juego perdió el frente durante la espera; reintento.")
            if expired():
                return False

    # --- coordenadas ---
    def client_to_screen(self, x: int, y: int, strict: bool = True) -> tuple[int, int]:
        return geometry.client_to_screen(x, y, self.client_rect_on_screen(), strict)

    def screen_to_client(self, x: int, y: int) -> tuple[int, int]:
        return geometry.screen_to_client(x, y, self.client_rect_on_screen())

    # --- captura ---
    def _mss(self):
        import mss
        sct = getattr(self._local, "sct", None)
        if sct is None:
            sct = self._local.sct = mss.mss()
        return sct

    def _grab_raw(self, screen_rect: Rect) -> np.ndarray:
        """Captura cruda de mss: BGRA (h, w, 4)."""
        shot = self._mss().grab({"left": screen_rect.x, "top": screen_rect.y,
                                 "width": screen_rect.w, "height": screen_rect.h})
        return np.asarray(shot)

    def capture(self, region: Rect | None = None) -> np.ndarray:
        """Captura BGR uint8 en coordenadas de CLIENTE.

        `region` (en coords de cliente) tiene que entrar ENTERA en el area de cliente,
        asi el pixel [0, 0] de la imagen siempre es (region.x, region.y).
        """
        if self.is_minimized():
            raise WindowMinimizedError("La ventana del juego está minimizada")
        client = self.client_rect_on_screen()
        self.last_capture_rect = client
        local = Rect(0, 0, client.w, client.h)
        if region is not None:
            if region.intersect(local) != region:
                raise ValueError(f"La región {region} no entra entera en el área de cliente "
                                 f"({client.w}x{client.h})")
            local = region
        screen = Rect(local.x + client.x, local.y + client.y, local.w, local.h)
        bgra = self._grab_raw(screen)
        return np.ascontiguousarray(bgra[:, :, :3])
