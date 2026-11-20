"""Logica de `calibrate.py`: calibracion pasiva (lee la pantalla y escucha hotkeys; NUNCA manda teclas ni clics al juego).

Cada subcomando es una funcion `cmd_*(session, ...)` que devuelve 0 (ok) o 1 (no se guardo / no se pudo). Todo lo
externo (ventana, teclado, `input()`, reloj, lectura de coordenadas) va inyectado en `Session` para poder probarlo.
"""
from __future__ import annotations

import copy
import json
import logging
import math
import queue
import shutil
import statistics
import sys
import threading
import time
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Callable

import cv2
import numpy as np

from bot.config import ConfigError, resolve_path, save_config, validate_config, validate_farm_zone
from bot.debug_draw import annotate, draw_panel_coords
from bot.geometry import Rect
from bot.profiles import (KIND_DIRS, KINDS, apply_migration, build_config, has_path, kind_of, leaf_paths, list_profiles,
                          load_profile, merge_layers, plan_migration, profile_path, raw_without_profiles, save_profile,
                          set_path)
from bot.vision.items import find_boxes
from bot.vision.coords import read_coords
from bot.vision.metin import find_label_candidates, slugify, target_slugs
from bot.vision.panel import find_frame, read_panel
from bot.vision.pitch import draw_horizon, measure_horizon
from bot.vision.screen_mask import anchor_rect, build_screen_mask, find_ui_windows

logger = logging.getLogger(__name__)

KEY_CAPTURE, KEY_UNDO, KEY_DONE, KEY_CANCEL = "f9", "f8", "f10", "f12"
POLL_S = 0.2              # espera maxima de cada lectura de la cola de hotkeys (para atender Ctrl+C)
SPEED_MARGIN = 1.5        # max_speed propuesto = velocidad maxima medida x este factor
SPEED_WINDOW_S = 1.0      # ventana (s) con que se mide cada velocidad
SPEED_CYCLE_S = 0.1       # pausa entre lecturas de coordenadas
ZONE_IMG_MAX = (1000, 700)   # tamano maximo (ancho, alto) de la imagen de la zona
ZONE_IMG_MAX_SCALE = 8.0
ZONE_IMG_MARGIN = 15      # unidades de juego de margen alrededor de la zona + tolerancia
LABEL_CROP_MARGIN = 14    # px alrededor de la etiqueta en la vista ampliada
LABEL_CROP_ZOOM = 6
GREEN, ORANGE, CYAN, YELLOW, WHITE = (0, 255, 0), (0, 140, 255), (255, 255, 0), (0, 255, 255), (255, 255, 255)


class Cancelled(Exception):
    """El usuario cancelo (F12, Ctrl+C) o no se pudo seguir: no se guarda nada."""


@dataclass
class Change:
    """Cambio de config: `path` = claves anidadas, `new` = valor nuevo."""
    path: tuple[str, ...]
    new: Any


def _default_cursor() -> tuple[int, int]:
    """Posicion del mouse en coordenadas de PANTALLA."""
    import ctypes
    from ctypes import wintypes
    pt = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


@dataclass
class Session:
    """Dependencias de un subcomando."""
    cfg: dict
    config_path: str = "config.json"
    yes: bool = False
    window: Any = None
    keyboard: Any = None                       # modulo `keyboard` (o un falso)
    read_coords_fn: Callable = read_coords
    list_windows_fn: Callable | None = None
    cursor_fn: Callable[[], tuple[int, int]] = _default_cursor   # mouse en coords de pantalla
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    profiles_root: Any = None                  # carpeta de perfiles (None = profiles/ del proyecto)
    say: Callable[[str], None] = print
    ask: Callable[[str], str] = field(default=lambda prompt: input(prompt))
    prefer_profile: bool = False               # guardar en el perfil activo aunque config.json defina ese valor (lo usa la interfaz)

    def confirm(self, question: str) -> bool:
        """s/n por consola; con --si responde que si sin preguntar."""
        if self.yes:
            return True
        while True:
            try:
                ans = self.ask(f"{question} (s/n): ").strip().lower()
            except EOFError:
                return False
            if ans in ("s", "si", "sí", "y", "yes"):
                return True
            if ans in ("n", "no"):
                return False
            self.say("Responde s o n.")

    def grab(self, hk: "Hotkeys | None" = None) -> np.ndarray:
        """Captura el juego (espera a que quede al frente). Cancelled si se cancela o no hay juego."""
        stop = hk.cancel.is_set if hk is not None else None
        if not self.window.ensure_ready(should_stop=stop):
            raise Cancelled("No se pudo dejar el juego al frente.")
        return self.window.capture()

    def cursor_client(self) -> tuple[int, int]:
        """Posicion del mouse en coordenadas de cliente."""
        return self.window.screen_to_client(*self.cursor_fn())


class Hotkeys:
    """Escucha hotkeys (solo para que Enzo marque momentos). F12 siempre cancela. No suprime las teclas."""

    def __init__(self, kb: Any, keys: tuple[str, ...]) -> None:
        if kb is None:
            import keyboard as kb
        self._kb = kb
        self.q: queue.Queue[str] = queue.Queue()
        self.cancel = threading.Event()
        self._handles = [kb.add_hotkey(k, partial(self._on, k)) for k in (*keys, KEY_CANCEL)]

    def _on(self, key: str) -> None:
        if key == KEY_CANCEL:
            self.cancel.set()
        self.q.put(key)

    def _check(self, key: str | None) -> str | None:
        if key == KEY_CANCEL or self.cancel.is_set():
            raise Cancelled("Cancelado con F12.")
        return key

    def next(self) -> str:
        """Bloquea hasta la proxima hotkey (Ctrl+C la interrumpe)."""
        while True:
            try:
                return self._check(self.q.get(timeout=POLL_S))  # type: ignore[return-value]
            except queue.Empty:
                continue

    def poll(self) -> str | None:
        """Hotkey pendiente o None, sin bloquear."""
        try:
            return self._check(self.q.get_nowait())
        except queue.Empty:
            return self._check(None)

    def close(self) -> None:
        for h in self._handles:
            try:
                self._kb.remove_hotkey(h)
            except Exception:  # noqa: BLE001
                pass
        self._handles = []

    def __enter__(self) -> "Hotkeys":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# ----------------------------------------------------------------------------- config

def get_path(d: dict, path: tuple[str, ...]) -> Any:
    for k in path:
        d = d[k]
    return d


def _set_path(d: dict, path: tuple[str, ...], value: Any) -> None:
    for k in path[:-1]:
        if not isinstance(d.get(k), dict):
            d[k] = {}
        d = d[k]
    d[path[-1]] = copy.deepcopy(value)


def _active_profiles(s: Session) -> dict[str, str]:
    """Perfiles con que se armo s.cfg ({} si no se uso ninguno: sin carpeta profiles/)."""
    p = s.cfg.get("_profiles") or {}
    return {k: p[k] for k in KINDS if p.get(k)} if all(p.get(k) for k in KINDS) else {}


def _read_raw(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}


def _refresh_cfg(s: Session, sel: dict[str, str]) -> None:
    """Vuelve a armar s.cfg con los mismos perfiles que tenia."""
    s.cfg = build_config(s.config_path, sel.get("servidor"), sel.get("personaje"), sel.get("zona"),
                         profiles_root_dir=s.profiles_root, warn=False)


def apply_changes(s: Session, changes: list[Change]) -> bool:
    """Muestra antes -> despues, valida, pide confirmacion, hace .bak y guarda donde corresponde.

    Cada cambio va al perfil activo de su categoria (servidor, personaje o zona) o a config.json si es general o si
    config.json ya define ese valor (lo pisa: se guarda ahi y se avisa que conviene migrar). Escribe el JSON crudo de cada
    archivo (no el mezclado con DEFAULTS), asi se conservan las demas claves tal cual. La config invalida nunca se guarda.
    Devuelve True si guardo.
    """
    path = resolve_path(s.config_path)
    raw = _read_raw(path)
    sel = _active_profiles(s)
    datas = {k: load_profile(k, n, s.profiles_root) for k, n in sel.items()}
    layers = lambda d, r: [d[k] for k in KINDS if k in d] + [raw_without_profiles(r)]   # noqa: E731
    before = merge_layers(layers(datas, raw))
    new_raw, new_datas = copy.deepcopy(raw), copy.deepcopy(datas)
    dests: list[str] = []
    notes: list[str] = []
    for c in changes:
        kind = kind_of(c.path)
        if kind in datas and (s.prefer_profile or not has_path(raw, c.path)):
            set_path(new_datas[kind], c.path, c.new)
            dests.append(f"perfil de {kind} '{sel[kind]}'")
        else:
            set_path(new_raw, c.path, c.new)
            dests.append("config.json")
            if kind in datas:
                notes.append(f"Aviso: {'.'.join(c.path)} esta definido en config.json (pisa el perfil de {kind} '{sel[kind]}'): "
                             "se guarda ahi. Conviene migrar con 'calibrate.py perfiles migrar'.")
    after = merge_layers(layers(new_datas, new_raw))
    try:
        validate_config(after)
    except ConfigError as e:
        s.say(f"La config resultante es invalida, no se guarda nada: {e}")
        return False
    s.say("Cambios en la config:")
    for c, dest in zip(changes, dests):
        s.say(f"  {'.'.join(c.path)}: {get_path(before, c.path)}  ->  {get_path(after, c.path)}   [{dest}]")
    for n in dict.fromkeys(notes):
        s.say(n)
    if not s.confirm(f"Guardar en {', '.join(dict.fromkeys(dests))}?"):
        s.say("No se guardo nada.")
        return False
    saved: list[str] = []
    for kind in KINDS:
        if kind in datas and new_datas[kind] != datas[kind]:
            saved.append(str(save_profile(kind, sel[kind], new_datas[kind], s.profiles_root)))
    if "config.json" in dests:
        bak = path.with_name(path.name + ".bak")
        if path.exists():
            shutil.copy2(path, bak)
        save_config(new_raw, path)
        saved.append(str(path))
    _refresh_cfg(s, sel)
    s.say("Guardado en " + ", ".join(saved) + " (copias anteriores: .bak).")
    return True


def _debug_path(s: Session, name: str):
    p = resolve_path(s.cfg["debug"]["dir"]) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ----------------------------------------------------------------------------- ventana

def cmd_ventana(s: Session, title_filter: str | None = None) -> int:
    """Lista las ventanas, Enzo elige una y se guardan window_title y expected_client_size."""
    wins = s.list_windows_fn(title_filter) if s.list_windows_fn else []
    if not wins:
        s.say("No hay ventanas visibles" + (f" con {title_filter!r} en el titulo." if title_filter else "."))
        return 1
    for i, w in enumerate(wins, 1):
        r = w.client_rect_on_screen
        s.say(f"  {i:2d}) {w.title!r}  cliente {r.w}x{r.h}" + ("  (minimizada)" if w.minimized else ""))
    ans = s.ask("Numero de la ventana del juego (Enter = cancelar): ").strip()
    if not ans.isdigit() or not 1 <= int(ans) <= len(wins):
        s.say("Cancelado: no se eligio una ventana valida.")
        return 1
    w = wins[int(ans) - 1]
    r = w.client_rect_on_screen
    if w.minimized or r.w <= 0 or r.h <= 0:
        s.say("La ventana esta minimizada (area de cliente sin tamano): restaurala y volve a correr el comando.")
        return 1
    s.say(f"Area de cliente medida: {r.w}x{r.h}.")
    ok = apply_changes(s, [Change(("window_title",), w.title), Change(("expected_client_size",), [r.w, r.h])])
    return 0 if ok else 1


# ----------------------------------------------------------------------------- zona

def angular_order(points: list[list[int]]) -> list[list[int]]:
    """Ordena los vertices por angulo alrededor de su centroide (contorno sin cruces para formas convexas)."""
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    return sorted(points, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))


def draw_zone_image(polygon: list[list[int]], tolerance: float) -> np.ndarray:
    """Poligono dibujado a escala (x a la derecha, y hacia abajo) con la franja de tolerancia y los vertices."""
    pts = np.array(polygon, dtype=np.float64)
    pad = tolerance + ZONE_IMG_MARGIN
    x0, y0 = pts.min(axis=0) - pad
    x1, y1 = pts.max(axis=0) + pad
    scale = min(ZONE_IMG_MAX[0] / (x1 - x0), ZONE_IMG_MAX[1] / (y1 - y0), ZONE_IMG_MAX_SCALE)
    w, h = int(math.ceil((x1 - x0) * scale)), int(math.ceil((y1 - y0) * scale))
    px = [(int(round((x - x0) * scale)), int(round((y - y0) * scale))) for x, y in pts]
    poly = np.array(px, dtype=np.int32)
    img = np.full((h, w, 3), 30, np.uint8)
    fill = np.zeros((h, w), np.uint8)
    cv2.fillPoly(fill, [poly], 255)
    r = int(round(tolerance * scale))
    if r > 0:
        band = cv2.dilate(fill, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
        contours, _ = cv2.findContours(band, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(img, contours, -1, ORANGE, 2)
    overlay = img.copy()
    cv2.fillPoly(overlay, [poly], (60, 140, 60))
    img = cv2.addWeighted(overlay, 0.5, img, 0.5, 0)
    cv2.polylines(img, [poly], True, GREEN, 2)
    for i, ((x, y), (gx, gy)) in enumerate(zip(px, polygon), 1):
        cv2.circle(img, (x, y), 5, YELLOW, -1)
        cv2.putText(img, f"{i}: ({gx}, {gy})", (x + 8, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, WHITE, 1, cv2.LINE_AA)
    cx = sum(p[0] for p in polygon) / len(polygon)
    cy = sum(p[1] for p in polygon) / len(polygon)
    cv2.drawMarker(img, (int(round((cx - x0) * scale)), int(round((cy - y0) * scale))), CYAN, cv2.MARKER_CROSS, 14, 1)
    cv2.putText(img, f"Zona de farmeo (escala {scale:.1f} px/u). Naranja: tolerancia {tolerance:g} u",
                (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, WHITE, 1, cv2.LINE_AA)
    return img


def _valid_zone(polygon: list[list[int]], tol: float) -> str | None:
    """None si el poligono es valido; si no, el mensaje de error."""
    try:
        validate_farm_zone({"polygon": polygon, "outside_tolerance": tol})
    except ConfigError as e:
        return str(e)
    return None


def collect_vertices(s: Session, hk: Hotkeys) -> list[list[int]]:
    """F9 agrega el vertice (coordenadas leidas), F8 borra el ultimo, F10 termina (minimo 3)."""
    poly: list[list[int]] = []
    s.say(f"Camina hasta cada esquina de la zona. {KEY_CAPTURE.upper()} = marcar vertice, {KEY_UNDO.upper()} = borrar el "
          f"ultimo, {KEY_DONE.upper()} = terminar, {KEY_CANCEL.upper()} = cancelar.")
    while True:
        key = hk.next()
        if key == KEY_CAPTURE:
            c = s.read_coords_fn(s.grab(hk), s.cfg)
            if c is None:
                s.say("No se pudieron leer las coordenadas (¿algo las tapa?). No se agrego nada; proba de nuevo.")
            elif poly and poly[-1] == [c.x, c.y]:
                s.say(f"({c.x}, {c.y}) es igual al ultimo vertice: no se agrega (movete hasta la otra esquina).")
            else:
                poly.append([c.x, c.y])
                s.say(f"Vértice {len(poly)}: ({c.x}, {c.y})")
        elif key == KEY_UNDO:
            if poly:
                gone = poly.pop()
                s.say(f"Borrado el vértice {len(poly) + 1}: ({gone[0]}, {gone[1]}). Quedan {len(poly)}.")
            else:
                s.say("No hay vértices para borrar.")
        elif key == KEY_DONE:
            if len(poly) < 3:
                s.say(f"Hacen falta al menos 3 vértices (hay {len(poly)}).")
            else:
                return poly


def cmd_zona(s: Session, tolerancia: float | None = None) -> int:
    """Marca los vertices de la zona de farmeo caminando y guarda farm_zone.polygon."""
    tol = float(tolerancia) if tolerancia is not None else float(s.cfg["farm_zone"]["outside_tolerance"])
    with Hotkeys(s.keyboard, (KEY_CAPTURE, KEY_UNDO, KEY_DONE)) as hk:
        poly = collect_vertices(s, hk)
    err = _valid_zone(poly, tol)
    if err:
        s.say(f"El polígono no es valido: {err}")
        alt = angular_order(poly)
        if alt != poly and _valid_zone(alt, tol) is None:
            s.say(f"Orden sugerido (angular alrededor del centroide): {alt}")
            if not s.confirm("Usar ese orden?"):
                s.say("No se guardo nada.")
                return 1
            poly = alt
        else:
            s.say("Tampoco hay un orden angular valido. No se guardo nada: repeti los vértices.")
            return 1
    img_path = _debug_path(s, "calibrate_zona.png")
    cv2.imwrite(str(img_path), draw_zone_image(poly, tol))
    s.say(f"Imagen de la zona (con la tolerancia) en {img_path}")
    changes = [Change(("farm_zone", "polygon"), poly)]
    if tolerancia is not None:
        changes.append(Change(("farm_zone", "outside_tolerance"), tol))
    return 0 if apply_changes(s, changes) else 1


# ----------------------------------------------------------------------------- velocidad

def window_speeds(samples: list[tuple[float, int, int]], window_s: float = SPEED_WINDOW_S) -> list[float]:
    """Velocidades (u/s) medidas en ventanas de ~window_s. Si hay menos tiempo que una ventana, usa todo el tramo."""
    pts = sorted(samples)
    out: list[float] = []
    for i, (t0, x0, y0) in enumerate(pts):
        for t1, x1, y1 in pts[i + 1:]:
            if t1 - t0 >= window_s:
                out.append(math.hypot(x1 - x0, y1 - y0) / (t1 - t0))
                break
    if not out and len(pts) >= 2 and pts[-1][0] > pts[0][0]:
        (t0, x0, y0), (t1, x1, y1) = pts[0], pts[-1]
        out.append(math.hypot(x1 - x0, y1 - y0) / (t1 - t0))
    return out


def cmd_velocidad(s: Session, segundos: float = 10.0) -> int:
    """Lee coordenadas mientras Enzo cabalga y propone coords_filter.max_speed."""
    samples: list[tuple[float, int, int]] = []
    fails = 0
    with Hotkeys(s.keyboard, (KEY_CAPTURE,)) as hk:
        s.say(f"Parate donde puedas cabalgar en linea recta unos {segundos:g} s. Apreta {KEY_CAPTURE.upper()} y cabalga "
              f"sin parar ({KEY_CANCEL.upper()} cancela).")
        while hk.next() != KEY_CAPTURE:
            pass
        start = s.clock()
        while s.clock() - start < segundos:
            hk.poll()
            c = s.read_coords_fn(s.grab(hk), s.cfg)
            if c is None:
                fails += 1
            else:
                samples.append((s.clock(), c.x, c.y))
            s.sleep(SPEED_CYCLE_S)
    speeds = window_speeds(samples)
    s.say(f"Lecturas validas: {len(samples)}, fallidas (descartadas): {fails}.")
    if not speeds:
        s.say("No hay lecturas suficientes para medir la velocidad. No se guardo nada.")
        return 1
    top, typical = max(speeds), statistics.median(speeds)
    proposed = round(top * SPEED_MARGIN, 1)
    s.say(f"Velocidad tipica: {typical:.1f} u/s. Maxima: {top:.1f} u/s. "
          f"Propuesta: max_speed = {top:.1f} x {SPEED_MARGIN} = {proposed:g}")
    if typical > 0 and top > 2 * typical:
        s.say("Ojo: la maxima es mucho mayor que la tipica (¿una lectura mala?). Si dudas, repeti la medicion.")
    if top <= 0:
        s.say("El personaje no se movio. No se guardo nada.")
        return 1
    return 0 if apply_changes(s, [Change(("coords_filter", "max_speed"), proposed)]) else 1


# ----------------------------------------------------------------------------- metin

def label_preview(img: np.ndarray, cand: Any) -> np.ndarray:
    """Recorte de la etiqueta ampliado (arriba el color, abajo la mascara que queda como plantilla)."""
    r, m = cand.rect, LABEL_CROP_MARGIN
    x0, y0 = max(0, r.x - m), max(0, r.y - m)
    x1, y1 = min(img.shape[1], r.right + m), min(img.shape[0], r.bottom + m)
    crop = img[y0:y1, x0:x1]
    big = cv2.resize(crop, None, fx=LABEL_CROP_ZOOM, fy=LABEL_CROP_ZOOM, interpolation=cv2.INTER_NEAREST)
    mask = cv2.resize(cand.text_mask, None, fx=LABEL_CROP_ZOOM, fy=LABEL_CROP_ZOOM, interpolation=cv2.INTER_NEAREST)
    mask3 = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    w = max(big.shape[1], mask3.shape[1])
    canvas = np.zeros((big.shape[0] + mask3.shape[0] + 6, w, 3), np.uint8)
    canvas[:big.shape[0], :big.shape[1]] = big
    canvas[big.shape[0] + 6:, :mask3.shape[1]] = mask3
    return canvas


def _run_tool(main: Callable[[], None], argv: list[str]) -> str | None:
    """Corre el main() de una herramienta de tools/ con esos argumentos. None si salio bien, si no el mensaje."""
    old = sys.argv
    sys.argv = ["tool", *argv]
    try:
        main()
        return None
    except SystemExit as e:
        return None if e.code in (None, 0) else str(e.code)
    finally:
        sys.argv = old


def _save_capture(s: Session, img: np.ndarray, name: str) -> str:
    p = _debug_path(s, name)
    cv2.imwrite(str(p), img)
    return str(p)


def _pick_label(s: Session, hk: Hotkeys, name: str) -> tuple[np.ndarray, Any] | None:
    """F9 -> etiqueta naranja mas cercana al mouse, vista ampliada y confirmacion. None si se termina con F10."""
    while True:
        key = hk.next()
        if key == KEY_DONE:
            return None
        if key != KEY_CAPTURE:
            continue
        img = s.grab(hk)
        cands = find_label_candidates(img, s.cfg, build_screen_mask(img.shape, s.cfg))
        if not cands:
            s.say("No se vio ninguna etiqueta naranja. Acerca el mouse a la etiqueta y apreta F9 de nuevo.")
            continue
        cx, cy = s.cursor_client()
        cand = min(cands, key=lambda c: math.dist(c.rect.center, (cx, cy)))
        prev = _debug_path(s, "calibrate_metin.png")
        cv2.imwrite(str(prev), label_preview(img, cand))
        s.say(f"Etiqueta elegida (mouse en {(cx, cy)}): {cand.rect.w}x{cand.rect.h} en {cand.rect.center}. "
              f"Recorte ampliado en {prev}")
        ans = s.confirm(f"Es la etiqueta de {name!r}?")
        if ans:
            return img, cand
        s.say("Descartada. Apunta con el mouse a la correcta y apreta F9 otra vez (F10 = salir).")


def _save_label(s: Session, img: np.ndarray, cand: Any, name: str) -> bool:
    from tools import make_label_template
    out = resolve_path(s.cfg["vision"]["metin"]["label_templates_dir"]) / f"{slugify(name)}.png"
    if out.exists() and not s.confirm(f"Ya existe {out.name}. Pisarla?"):
        return False
    cap = _save_capture(s, img, "calibrate_metin_captura.png")
    cx, cy = cand.rect.center
    err = _run_tool(make_label_template.main, ["--image", cap, "--name", name, "--near", f"{cx},{cy}",
                                              "--config", s.config_path])
    if err:
        s.say(f"No se pudo guardar la plantilla: {err}")
    return err is None


def _save_panel_name(s: Session, hk: Hotkeys, name: str, skip_words: int | None = None, what: str = "el metin") -> None:
    """Segundo F9 (opcional): plantilla del nombre del panel con `what` seleccionado (skip_words: ver la herramienta)."""
    from tools import make_panel_name_template
    pcfg = s.cfg["vision"]["panel"]
    s.say(f"Opcional: selecciona {what} con un clic (que se vea el panel de arriba) y apreta F9 para guardar tambien la "
          "plantilla del nombre del panel. F10 = saltear.")
    while True:
        key = hk.next()
        if key == KEY_DONE:
            return
        if key != KEY_CAPTURE:
            continue
        img = s.grab(hk)
        if find_frame(img, pcfg) is None:
            s.say(f"No se ve el panel superior. Selecciona {what} y apreta F9 de nuevo (F10 = saltear).")
            continue
        out = resolve_path(pcfg["name_templates_dir"]) / f"{slugify(name)}.png"
        if out.exists() and not s.confirm(f"Ya existe {out.name}. Pisarla?"):
            return
        cap = _save_capture(s, img, "calibrate_metin_panel.png")
        extra = [] if skip_words is None else ["--skip-words", str(skip_words)]
        err = _run_tool(make_panel_name_template.main, ["--image", cap, "--name", name, "--force", *extra,
                                                        "--config", s.config_path])
        if err:
            s.say(f"No se pudo guardar la plantilla del panel: {err}")
            continue
        return


def _profile_targets(s: Session) -> list[str] | None:
    """target_metin_names del perfil de zona activo (None si no hay perfiles o no se puede leer)."""
    zona = _active_profiles(s).get("zona")
    try:
        return list(load_profile("zona", zona, s.profiles_root).get("target_metin_names") or []) if zona else None
    except ConfigError:
        return None


def cmd_metin(s: Session, name: str) -> int:
    """Agrega un tipo de metin: plantilla de la etiqueta, (opcional) del panel y target_metin_names."""
    if not slugify(name):
        s.say("El nombre del metin no puede estar vacio.")
        return 1
    with Hotkeys(s.keyboard, (KEY_CAPTURE, KEY_DONE)) as hk:
        s.say(f"Con la etiqueta naranja de {name!r} visible, pone el mouse cerca de ella y apreta {KEY_CAPTURE.upper()} "
              f"({KEY_DONE.upper()} = salir, {KEY_CANCEL.upper()} = cancelar).")
        picked = _pick_label(s, hk, name)
        if picked is None:
            s.say("Terminado sin guardar nada.")
            return 1
        if not _save_label(s, *picked, name):
            return 1
        s.say(f"Plantilla de la etiqueta guardada ({slugify(name)}.png).")
        _save_panel_name(s, hk, name)
    base = (_profile_targets(s) if s.prefer_profile else None)
    if base is None:
        base = list(s.cfg["target_metin_names"])
    if slugify(name) in {slugify(n) for n in base}:
        s.say(f"{name!r} ya esta en target_metin_names.")
        return 0
    names = base + [name]
    apply_changes(s, [Change(("target_metin_names",), names)])   # la plantilla ya esta guardada: sigue siendo exito
    return 0


# ----------------------------------------------------------------------------- personaje

OWNER_PREVIEW_MARGIN = 6


def owner_candidates(img: np.ndarray, icfg: dict) -> list[tuple[Rect, np.ndarray]]:
    """Recuadros de objetos con texto amarillo de dueno: (recuadro, mascara 0/255 del amarillo recortada a su caja).

    Misma logica que bot/vision/items.py::find_items (recuadro + franja superior amarilla), sin plantilla de por medio.
    """
    rows = int(icfg["text_rows"])
    out: list[tuple[Rect, np.ndarray]] = []
    for rect in find_boxes(img, icfg):
        inner = img[rect.y + 1:rect.bottom - 1, rect.x + 1:rect.right - 1]
        top = max(0, inner.shape[0] - rows)
        if top == 0:
            continue
        upper = cv2.inRange(inner[:top], tuple(icfg["yellow_lo"]), tuple(icfg["yellow_hi"]))
        if int(np.count_nonzero(upper)) <= icfg["yellow_noise_px"]:
            continue
        ys, xs = np.nonzero(upper)
        out.append((rect, upper[ys.min():ys.max() + 1, xs.min():xs.max() + 1]))
    return out


def owner_preview(img: np.ndarray, rect: Rect, mask: np.ndarray) -> np.ndarray:
    """Recuadro ampliado (arriba el color, abajo la mascara amarilla que queda como plantilla)."""
    m = OWNER_PREVIEW_MARGIN
    crop = img[max(0, rect.y - m):rect.bottom + m, max(0, rect.x - m):rect.right + m]
    big = cv2.resize(crop, None, fx=LABEL_CROP_ZOOM, fy=LABEL_CROP_ZOOM, interpolation=cv2.INTER_NEAREST)
    mask3 = cv2.cvtColor(cv2.resize(mask, None, fx=LABEL_CROP_ZOOM, fy=LABEL_CROP_ZOOM, interpolation=cv2.INTER_NEAREST),
                         cv2.COLOR_GRAY2BGR)
    canvas = np.zeros((big.shape[0] + mask3.shape[0] + 6, max(big.shape[1], mask3.shape[1]), 3), np.uint8)
    canvas[:big.shape[0], :big.shape[1]] = big
    canvas[big.shape[0] + 6:, :mask3.shape[1]] = mask3
    return canvas


def _pick_owner(s: Session, hk: Hotkeys, name: str) -> np.ndarray | None:
    """F9 -> texto amarillo de dueno mas cercano al mouse, vista ampliada y confirmacion. None si se termina con F10."""
    icfg = s.cfg["vision"]["items"]
    while True:
        key = hk.next()
        if key == KEY_DONE:
            return None
        if key != KEY_CAPTURE:
            continue
        img = s.grab(hk)
        cands = owner_candidates(img, icfg)
        if not cands:
            s.say("No se vio ningun objeto con nombre amarillo (reservado). Acerca el mouse al objeto y apreta F9 de nuevo.")
            continue
        cx, cy = s.cursor_client()
        rect, mask = min(cands, key=lambda c: math.dist(c[0].center, (cx, cy)))
        prev = _debug_path(s, "calibrate_personaje.png")
        cv2.imwrite(str(prev), owner_preview(img, rect, mask))
        s.say(f"Recuadro elegido (mouse en {(cx, cy)}): {rect.w}x{rect.h} en {rect.center}; texto amarillo {mask.shape[1]}x"
              f"{mask.shape[0]}. Recorte ampliado en {prev}")
        if s.confirm(f"Es el nombre amarillo de {name!r} (\"{name}'s\")?"):
            return mask
        s.say("Descartado. Apunta con el mouse al correcto y apreta F9 otra vez (F10 = salir).")


def new_character_profile(name: str, clase: str | None, owner_template: str, existing: dict | None = None) -> dict:
    """Datos del perfil: estructura del personaje por defecto con su nombre (o el existente, actualizado)."""
    from bot.config import DEFAULTS
    if existing is None:
        data = {"character": copy.deepcopy(DEFAULTS["character"]),
                "pickup": {"dismount": DEFAULTS["pickup"]["dismount"]},
                "coords_filter": {"max_speed": DEFAULTS["coords_filter"]["max_speed"]}}
    else:
        data = copy.deepcopy(existing)
    ch = data.setdefault("character", copy.deepcopy(DEFAULTS["character"]))
    ch["name"] = name
    if clase is not None:
        ch["class"] = clase
    set_path(data, ("vision", "items", "owner_template"), owner_template)
    return data


def cmd_personaje(s: Session, name: str, clase: str | None = None) -> int:
    """Crea (o actualiza) profiles/personajes/<name>.json con la plantilla del nombre amarillo "<name>'s" (F9)."""
    slug = slugify(name)
    try:
        pfile = profile_path("personaje", name, s.profiles_root)
    except ConfigError as e:
        s.say(str(e))
        return 1
    if not slug:
        s.say("El nombre del personaje no puede estar vacio.")
        return 1
    tpl_rel = f"templates/items/owner_{slug}.png"
    tpl_out = resolve_path(tpl_rel)
    with Hotkeys(s.keyboard, (KEY_CAPTURE, KEY_DONE)) as hk:
        s.say(f"Con un objeto reservado para {name!r} en el piso (se ve su nombre amarillo \"{name}'s\" encima), pone el "
              f"mouse cerca y apreta {KEY_CAPTURE.upper()} ({KEY_DONE.upper()} = salir, {KEY_CANCEL.upper()} = cancelar).")
        mask = _pick_owner(s, hk, name)
        if mask is None:
            s.say("Terminado sin guardar nada.")
            return 1
        existing = load_profile("personaje", name, s.profiles_root) if pfile.is_file() else None
        data = new_character_profile(name, clase, tpl_rel, existing)
        s.say(f"Se va a {'actualizar' if existing else 'crear'} {pfile} y guardar la plantilla {tpl_out}"
              + (" (ya existe: se pisa)." if tpl_out.exists() else "."))
        if not s.confirm("Guardar?"):
            s.say("No se guardo nada.")
            return 1
        tpl_out.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(tpl_out), mask)
        save_profile("personaje", name, data, s.profiles_root)
        s.say(f"Guardados {tpl_out.name} y el perfil {pfile.name}.")
        _save_panel_name(s, hk, name, skip_words=0, what=f"a {name} (tu personaje)")
    _offer_active(s, "personaje", name)
    return 0


def _offer_active(s: Session, kind: str, name: str) -> None:
    """Pregunta si dejar el perfil como activo en config.json (bloque profiles). Con --si no cambia nada."""
    if s.yes:
        s.say(f"Para usarlo: --{kind} \"{name}\" o el bloque profiles de config.json.")
        return
    if not s.confirm(f"Dejar a {name!r} como {kind} activo en config.json?"):
        return
    path = resolve_path(s.config_path)
    new_raw = copy.deepcopy(_read_raw(path))
    if not isinstance(new_raw.setdefault("profiles", {}), dict):
        s.say("El bloque profiles de config.json no es un objeto: no se cambia nada.")
        return
    new_raw["profiles"][kind] = name
    build_config(path, **{k: (name if k == kind else None) for k in KINDS},
                 profiles_root_dir=s.profiles_root, warn=False)   # ConfigError si el resultado no valida
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    save_config(new_raw, path)
    s.say(f"config.json: profiles.{kind} = {name!r} (copia anterior: {path.name}.bak).")


# ----------------------------------------------------------------------------- perfiles

def cmd_perfiles(s: Session, action: str) -> int:
    """`listar` (perfiles disponibles y activos) o `migrar` (de config.json a los perfiles activos)."""
    return _perfiles_listar(s) if action == "listar" else _perfiles_migrar(s)


def _perfiles_listar(s: Session) -> int:
    sel = _active_profiles(s)
    if not sel:
        s.say("No hay carpeta de perfiles: se usa DEFAULTS + config.json.")
        return 0
    for kind in KINDS:
        s.say(f"Perfiles de {kind} (profiles/{KIND_DIRS[kind]}/):")
        for n in list_profiles(kind, s.profiles_root):
            s.say(f"  {'*' if n == sel[kind] else ' '} {n}" + ("   (activo)" if n == sel[kind] else ""))
    ov = s.cfg["_profiles"].get("overrides", [])
    if ov:
        s.say("config.json pisa valores de los perfiles activos:")
        for o in ov:
            s.say(f"  {o['message']}")
    return 0


def _perfiles_migrar(s: Session) -> int:
    path = resolve_path(s.config_path)
    raw = _read_raw(path)
    sel = _active_profiles(s)
    if not sel:
        s.say("No hay carpeta de perfiles: no hay a donde migrar.")
        return 1
    plan = plan_migration(raw, sel, s.profiles_root)
    if not plan.moves:
        s.say("No hay nada para migrar: config.json no tiene valores de servidor, personaje ni zona.")
        return 0
    s.say("Plan de migracion:")
    for kind, p, value in plan.moves:
        s.say(f"  {'.'.join(p)} = {json.dumps(value, ensure_ascii=False)}  ->  perfil de {kind} '{sel[kind]}'")
    rest = [".".join(p) for p, _ in leaf_paths(raw_without_profiles(plan.new_raw))]
    s.say("Queda en config.json: " + (", ".join(rest) if rest else "(nada general)") + "; se agrega el bloque profiles "
          + json.dumps(sel, ensure_ascii=False))
    datas = {k: load_profile(k, sel[k], s.profiles_root) for k in KINDS}
    new_datas = {k: plan.profile_data.get(k, datas[k]) for k in KINDS}
    before = merge_layers([datas[k] for k in KINDS] + [raw_without_profiles(raw)])
    after = merge_layers([new_datas[k] for k in KINDS] + [raw_without_profiles(plan.new_raw)])
    if before != after:
        s.say("La migracion cambiaria la config resultante: no se hace nada.")
        return 1
    if not s.confirm("Migrar (se hace .bak de config.json y de cada perfil que cambia)?"):
        s.say("No se migro nada.")
        return 1
    written = apply_migration(plan, s.config_path, s.profiles_root)
    _refresh_cfg(s, sel)
    s.say("Migrado. Archivos escritos: " + ", ".join(str(w) for w in written) + " (copias anteriores: .bak).")
    return 0


# ----------------------------------------------------------------------------- zonas

def cmd_zonas(s: Session) -> int:
    """Guarda debug/calibrate_zonas.png con zonas excluidas, ventanas, coordenadas y panel. No modifica la config."""
    img = s.grab()
    wins = find_ui_windows(img, s.cfg)
    mask = build_screen_mask(img.shape, s.cfg, extra=wins)
    out = annotate(img, mask, [], (img.shape[1] // 2, img.shape[0] // 2), None)
    for r in wins:
        cv2.rectangle(out, (r.x, r.y), (r.right, r.bottom), YELLOW, 2)
        cv2.putText(out, "ventana detectada", (r.x + 4, r.y + 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, YELLOW, 2, cv2.LINE_AA)
    coords = s.read_coords_fn(img, s.cfg)
    panel = read_panel(img, s.cfg)
    draw_panel_coords(out, panel, coords)
    path = _debug_path(s, "calibrate_zonas.png")
    cv2.imwrite(str(path), out)
    s.say(f"Imagen {img.shape[1]}x{img.shape[0]} guardada en {path}")
    s.say(f"Ventanas detectadas: {len(wins)}. Coordenadas: " + (f"({coords.x}, {coords.y})" if coords else "no se leyeron")
          + ". Panel: " + (f"{panel.kind} {panel.name}" if panel.present else "ninguno"))
    s.say("Zonas excluidas (vision.exclusion_zones en config.json):")
    for z in s.cfg["vision"]["exclusion_zones"]:
        r = anchor_rect(img.shape[1], img.shape[0], z)
        s.say(f"  {z['name']}: anchor={z['anchor']} dx={z['dx']} dy={z['dy']} w={z['w']} h={z['h']}  "
              f"-> rect ({r.x}, {r.y}, {r.w}x{r.h})")
    return 0


# ----------------------------------------------------------------------------- camara

def cmd_camara(s: Session) -> int:
    """Mide el horizonte (angulo vertical de la camara) en varias capturas con la camara como le gusta a Enzo y guarda el
    promedio en camera_pitch.ref_horizon_frac. Pasivo: no manda nada al juego."""
    c = s.cfg["camera_pitch"]
    n = int(c["calib_samples"])
    vals: list[float] = []
    last, last_res = None, None
    for i in range(n):
        img = s.grab()
        res = measure_horizon(img, s.cfg)
        if res is not None and res.confidence >= float(c["min_confidence"]):
            vals.append(res.horizon_frac)
            last, last_res = img, res
            s.say(f"  captura {i + 1}/{n}: horizonte {res.horizon_frac:.3f} (fila {res.horizon_row}, confianza {res.confidence:.2f})")
        else:
            s.say(f"  captura {i + 1}/{n}: no se pudo medir"
                  + ("" if res is None else f" (confianza {res.confidence:.2f})"))
        if i + 1 < n:
            s.sleep(float(c["calib_pause_s"]))
    if len(vals) < max(1, (n + 1) // 2):
        s.say(f"Solo {len(vals)} de {n} capturas se pudieron medir (hace falta piso a la vista debajo del personaje). "
              "No se guardo nada.")
        return 1
    ref = round(statistics.mean(vals), 3)
    spread = max(vals) - min(vals)
    s.say(f"Horizonte medido: {ref:.3f} (promedio de {len(vals)} capturas, dispersion {spread:.3f}).")
    if spread > float(c["tolerance"]):
        s.say("Ojo: las capturas difieren mas que la tolerancia (animaciones, monstruos tapando el piso). Si dudas, repeti.")
    if any(v <= 0 for v in vals):
        s.say("Ojo: en alguna captura el piso llega hasta el borde de arriba (camara muy inclinada hacia abajo): el valor es "
              "aproximado.")
    path = _debug_path(s, "calibrate_camara.png")
    cv2.imwrite(str(path), draw_horizon(last, last_res, ref, float(c["tolerance"])))
    s.say(f"Imagen con la linea del horizonte (verde = medido, amarillo = referencia +- tolerancia) en {path}")
    return 0 if apply_changes(s, [Change(("camera_pitch", "ref_horizon_frac"), ref)]) else 1


# ----------------------------------------------------------------------------- resumen

# (grupo, [(clave, como medirla)])
SUMMARY: list[tuple[str, list[tuple[str, str]]]] = [
    ("Fase 6b: destrabe (stuck)", [
        ("stuck.space_s", "cuanto hay que mantener ESPACIO para empujar a un monstruo pegado: probalo a mano en el juego"),
        ("stuck.forward_s", "cuanto avanzar con W despues de ESPACIO para salir del grupo: a mano"),
        ("stuck.monsters_min", "monstruos cerca desde los que se considera 'rodeado': mira debug/run_*/ de un destrabe"),
        ("stuck.resolve_dist", "unidades que hay que alejarse del punto de traba para darlo por resuelto: events.log"),
        ("stuck.look_steps", "pasos de camara de la estrategia MIRAR (x look_step_s): mira el paso 1 de test_input.py"),
        ("stuck.look_step_s", "cuanto gira la camara por paso: mira el paso 1 de test_input.py"),
        ("stuck.space_fail_max", "veces que ESPACIO puede fallar seguidas antes de rotar de estrategia: events.log"),
        ("stuck.forward_chunk_s", "largo de los tramos de W/S entre chequeos de zona: ajustar si el personaje se pasa"),
    ]),
    ("Fase 6b: explorar (explore)", [
        ("explore.camera_steps", "pasos de camara por barrido: con camera_step_s deberian dar ~360 grados (paso 1 de test_input.py)"),
        ("explore.camera_step_s", "segundos de Q/E por paso: cuanto gira la camara de verdad (paso 1 de test_input.py)"),
        ("explore.border_margin", "distancia al borde de los destinos de exploracion: mira calibrate.py zona (imagen) y el debug"),
        ("explore.leg_s", "tope de tiempo de un tramo recto: con la velocidad (calibrate.py velocidad) da el largo"),
        ("explore.max_without_metin_s", "tiempo sin ver metin antes de ir al centro: ver cuanto tarda en aparecer uno"),
    ]),
    ("Fase 6b: acercarse (approach) y atacar", [
        ("approach.hover_wait_s", "espera tras mover el mouse sobre una piedra lejana hasta que aparece su etiqueta: probar en el juego"),
        ("vision.ground.horizon_frac", "horizonte para comparar distancias con perspectiva: verifica el orden con samples/real_tres_metin_hover.png"),
        ("approach.blind_player_radius", "radio (u) donde vale un descarte sin camara: a ojo con el debug"),
        ("approach.max_seconds", "tope de la caminata hacia una piedra lejana: cronometra una caminata real"),
        ("attack.blocked_s", "tiempo quieto con la vida sin bajar antes de dar ESPACIO: mira el log de ATACAR"),
        ("loop.hold_chunk_s", "largo de los tramos de teclas sostenidas; verifica que ESPACIO (soltar/apretar cada 0.5 s) empuje bien"),
    ]),
    ("Fase 6: movimiento y filtro", [
        ("coords_filter.max_speed", "calibrate.py velocidad (cabalgando en linea recta)"),
        ("navigation.settle_s", "segundos sin cambio de coordenadas para decir 'se freno': logs/bot.log"),
        ("navigation.segment_timeout_s", "tope de un tramo de caminata: logs/bot.log, tramos mas largos"),
        ("navigation.scale_hint", "unidades por pixel [menor, mayor]: la escala aprendida aparece en logs/bot.log"),
        ("navigation.avoid_metin_px", "distancia minima a un metin no objetivo al clickear el piso: a ojo con el debug"),
        ("navigation.max_walk_px", "largo maximo de un tramo en pantalla: logs/bot.log"),
        ("stuck.window_s", "ventana de 'no me muevo': mide cuanto tarda en arrancar el personaje"),
        ("stuck.min_move", "unidades minimas de movimiento en esa ventana: coordenadas con el personaje quieto (ruido)"),
        ("stuck.unstuck_radius_px", "radio en pantalla del clic de destrabe: a ojo con el debug"),
        ("volver.max_seconds", "tope de una vuelta a la zona: cronometra una vuelta desde el borde"),
    ]),
    ("Fase 6: atacar y recoger", [
        ("attack.confirm_timeout_s", "segundos desde el clic hasta que aparece el panel: logs/bot.log"),
        ("attack.hp_stall_s", "segundos sin bajar la vida para reclickear: logs/bot.log"),
        ("attack.max_seconds", "duracion maxima de un metin: cronometra uno real"),
        ("attack.discard_radius", "radio (u) para recordar un metin descartado: debug"),
        ("pickup.empty_confirm_cycles", "ciclos sin objetos antes de dar por terminado: cuanto tardan en aparecer"),
        ("pickup.max_seconds", "tope de la recogida: cronometra una real"),
        ("pickup.z_interval_s", "pausa entre Z: la mas corta con la que el juego recoge sin perder pulsaciones"),
    ]),
]


def cmd_resumen(s: Session) -> int:
    """Imprime los valores que siguen siendo estimaciones y como medir cada uno. No modifica nada."""
    s.say("Valores estimados (sin medir). Para cambiarlos edita config.json (o usa los subcomandos de calibrate.py).")
    for group, items in SUMMARY:
        s.say(f"\n{group}")
        for key, how in items:
            try:
                val = get_path(s.cfg, tuple(key.split(".")))
            except KeyError:
                val = "(no existe)"
            s.say(f"  {key} = {val}\n      como medirlo: {how}")
    s.say("\nLa camara (camera_steps x camera_step_s) se mide mirando el paso 1 de test_input.py.")
    return 0
