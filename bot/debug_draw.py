"""Dibujo de lo detectado sobre una captura (usado por `detect_preview.py` y por el modo debug del bot) y
`DebugRecorder`, que guarda imagenes anotadas de la corrida en `debug/run_<fecha_hora>/`. Nada de esto manda input."""
from __future__ import annotations

import logging
import math
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from bot.config import resolve_path
from bot.vision.captcha import CaptchaInfo
from bot.vision.coords import Coords
from bot.vision.death import DeathInfo
from bot.vision.far_metin import FarMetin
from bot.vision.items import ItemLabel
from bot.vision.metin import MetinDetection
from bot.vision.monsters import MonsterLabel
from bot.vision.panel import PanelInfo
from bot.vision.screen_mask import ScreenMask, anchor_rect

logger = logging.getLogger(__name__)

GREEN, RED, YELLOW, CYAN = (0, 255, 0), (0, 0, 255), (0, 255, 255), (255, 255, 0)
GRAY, ORANGE, MAGENTA = (160, 160, 160), (0, 140, 255), (255, 0, 255)
MAX_FAILS = 10   # fallos seguidos al guardar: el recorder se apaga para no llenar el log
SLUG_LEN = 40
COORD_CROP_ZOOM = 4   # ampliacion de los recortes de coordenadas fallidas


def annotate(img: np.ndarray, mask: ScreenMask, dets: list[MetinDetection],
             player: tuple[int, int], chosen: MetinDetection | None) -> np.ndarray:
    """Devuelve una copia de img con zonas, etiquetas, puntos de clic y objetivo dibujados."""
    out = img.copy()
    gray = np.full_like(out, 128)
    shaded = np.where(mask.mask[..., None] > 0, cv2.addWeighted(out, 0.4, gray, 0.6, 0), out)
    out = shaded.astype(np.uint8)
    for name, r in mask.zones:
        cv2.putText(out, name, (r.x + 3, r.y + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    for d in dets:
        r = d.label_rect
        col = GREEN if d.is_target else RED
        cv2.rectangle(out, (r.x - 2, r.y - 2), (r.right + 2, r.bottom + 2), col, 1)
        txt = f"{d.name} {d.score:.2f}" + ("" if d.allowed else " (zona)")
        cv2.putText(out, txt, (r.x, r.y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)
        cv2.drawMarker(out, d.click_point, col, cv2.MARKER_CROSS, 14, 2)
    cv2.circle(out, player, 8, CYAN, 2)
    if chosen is not None:
        cv2.line(out, player, chosen.click_point, YELLOW, 1)
        cv2.circle(out, chosen.click_point, 16, YELLOW, 2)
    return out


def panel_line(panel: PanelInfo) -> str:
    """Texto de una linea con lo leido del panel superior."""
    if not panel.present:
        return "Panel: ninguno"
    vida = (f"vida {panel.hp_current}/{panel.hp_max}" if panel.hp_current is not None else "vida ?")
    barra = f"{panel.hp_ratio:.2f}" if panel.hp_ratio is not None else "?"
    return f"Panel: {panel.kind} {panel.name} {panel.name_score:.2f} | {vida} (barra {barra})"


def draw_panel_coords(out: np.ndarray, panel: PanelInfo, coords: Coords | None) -> None:
    """Dibuja marco, nombre, barra y numero del panel y la linea de coordenadas."""
    if panel.present and panel.rect is not None:
        col = GREEN if panel.kind == "target_metin" else RED
        r = panel.rect
        cv2.rectangle(out, (r.x, r.y), (r.right, r.bottom), col, 1)
        for sub in (panel.name_rect, panel.number_rect, panel.bar_rect):
            if sub is not None:
                cv2.rectangle(out, (sub.x, sub.y), (sub.right, sub.bottom), CYAN, 1)
        cv2.putText(out, panel_line(panel)[7:], (r.x, r.bottom + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)
    if coords is not None and coords.rect is not None:
        r = coords.rect
        cv2.rectangle(out, (r.x - 2, r.y - 2), (r.right + 2, r.bottom + 2), GREEN, 1)
        cv2.putText(out, f"({coords.x}, {coords.y}) {coords.score:.2f}", (r.x - 60, r.bottom + 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, GREEN, 1, cv2.LINE_AA)


def draw_items(out: np.ndarray, items: list[ItemLabel], chosen: ItemLabel | None) -> None:
    """Objetos: verde = se recoge, gris = yang, naranja = reservado para otro; el elegido va marcado."""
    for it in items:
        col = GRAY if it.kind == "yang" else ORANGE if it.reserved_other else GREEN
        r = it.rect
        cv2.rectangle(out, (r.x - 2, r.y - 2), (r.right + 2, r.bottom + 2), col, 1)
        tag = "yang" if it.kind == "yang" else "de otro" if it.reserved_other else "tuyo" if it.reserved else "publico"
        cv2.putText(out, tag + ("" if it.allowed else " (zona)"), (r.x, r.y - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1, cv2.LINE_AA)
    if chosen is not None:
        cv2.circle(out, chosen.click_point, 14, YELLOW, 2)


def items_line(items: list[ItemLabel], chosen: ItemLabel | None) -> str:
    """Resumen de una linea de los objetos."""
    objs = [i for i in items if i.kind == "item"]
    mine = sum(i.reserved for i in objs)
    other = sum(i.reserved_other for i in objs)
    public = len(objs) - mine - other
    n_yang = sum(i.kind == "yang" for i in items)
    return (f"Objetos: {len(objs)} ({mine} tuyos, {public} publico, {other} de otros) | yang: {n_yang} | "
            f"elegido: {chosen.click_point if chosen else None}")


def draw_alerts(out: np.ndarray, cap: CaptchaInfo | None, death: DeathInfo | None) -> None:
    """Captcha: rectangulo rojo + "CAPTCHA"; muerte: rectangulo rojo en cada boton + "MUERTE"."""
    if cap is not None:
        r = cap.rect
        cv2.rectangle(out, (r.x, r.y), (r.right, r.bottom), RED, 2)
        cv2.putText(out, f"CAPTCHA {cap.score:.2f}", (r.x, max(12, r.y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, RED, 2, cv2.LINE_AA)
    if death is not None:
        rs = [r for r in (death.here_rect, death.city_rect) if r is not None]
        for r in rs:
            cv2.rectangle(out, (r.x, r.y), (r.right, r.bottom), RED, 2)
        if rs:
            cv2.putText(out, f"MUERTE {death.score:.2f}", (rs[0].x, max(12, rs[0].y - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, RED, 2, cv2.LINE_AA)


def draw_far_metins(out: np.ndarray, far: list[FarMetin]) -> None:
    """Piedras lejanas (sin etiqueta): circulo magenta en su base."""
    for f in far:
        r = f.rect
        cv2.circle(out, f.center, max(14, max(r.w, r.h) // 2), MAGENTA, 2)
        cv2.putText(out, f"lejana {f.score:.2f}" + ("" if f.allowed else " (zona)"), (r.x, max(12, r.y - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, MAGENTA, 1, cv2.LINE_AA)


def draw_monsters(out: np.ndarray, monsters: list[MonsterLabel], player: tuple[int, int], near_radius: float) -> None:
    """Monstruos en rojo (grueso si estan cerca) y el radio de "cerca" alrededor del personaje."""
    cv2.circle(out, player, int(near_radius), RED, 1)
    for m in monsters:
        near = math.dist(m.center, player) <= near_radius
        r = m.rect
        cv2.rectangle(out, (r.x - 2, r.y - 2), (r.right + 2, r.bottom + 2), RED, 2 if near else 1)
        cv2.circle(out, m.center, 5, RED, -1 if near else 1)


def draw_text_block(out: np.ndarray, lines: list[str], origin: tuple[int, int] = (10, 20)) -> None:
    """Lineas de texto sobre fondo oscuro."""
    x, y = origin
    for line in lines:
        (w, h), _ = cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(out, (x - 3, y - h - 3), (x + w + 3, y + 5), (0, 0, 0), -1)
        cv2.putText(out, line, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        y += h + 9


def annotate_perception(img: np.ndarray, p: Any, cfg: dict, lines: list[str],
                        click: tuple[int, int] | None = None) -> np.ndarray:
    """Imagen anotada de un ciclo del bot: detecciones, zona, panel, estado y clic planeado."""
    out = annotate(img, p.mask, p.metins, p.player_xy, p.target)
    draw_far_metins(out, p.far_metins)
    draw_monsters(out, p.monsters, p.player_xy, float(cfg["vision"]["monsters"]["near_radius_px"]))
    if p.items:
        draw_items(out, p.items, p.item_choice)
    if p.panel is not None:
        draw_panel_coords(out, p.panel, p.coords)
    draw_alerts(out, p.captcha, p.death)
    if click is not None:
        cv2.drawMarker(out, (int(click[0]), int(click[1])), YELLOW, cv2.MARKER_TILTED_CROSS, 24, 2)
        cv2.line(out, p.player_xy, (int(click[0]), int(click[1])), YELLOW, 1)
    draw_text_block(out, lines)
    return out


def _slug(text: str) -> str:
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")[:SLUG_LEN] or "x"


class DebugRecorder:
    """Guarda una imagen anotada por evento en `<debug.dir>/run_<fecha_hora>/NNNN_<estado>_<motivo>.jpg`, con un
    `events.log` de una linea por imagen, un tope total (`debug.max_images`) y un tope por tipo de evento
    (`debug.max_per_kind`). Nunca lanza: un error se loguea y sigue."""

    def __init__(self, cfg: dict, now_fn: Callable[[], datetime] = datetime.now) -> None:
        d = cfg["debug"]
        self.cfg = cfg
        self.enabled = bool(d["enabled"])
        self.max_images = int(d["max_images"])
        self.quality = int(d["jpeg_quality"])
        self.explore_every_s = float(d["explore_every_s"])
        self.max_per_kind = {str(k): int(v) for k, v in d["max_per_kind"].items()}
        self.per_kind: dict[str, int] = {}
        self._kind_capped: set[str] = set()
        self._base = resolve_path(d["dir"])
        self._now = now_fn
        self._dir: Path | None = None
        self.count = 0
        self._fails = 0
        self._last_explore = -math.inf
        self._capped = False
        self.save_raw = bool(d["save_raw"])
        self.max_coord_crops = int(d["max_coord_crops"])
        self.coord_crop_every_s = float(d["coord_crop_every_s"])
        self.coord_crops = 0
        self._last_crop = -math.inf
        self.max_snapshots = int(d["max_snapshots"])
        self.snapshots = 0

    @property
    def run_dir(self) -> Path | None:
        return self._dir

    def save_snapshot(self, img: np.ndarray | None, folder: str, name: str,
                      box: tuple[int, int, int, int] | None = None) -> Path | None:
        """Guarda una captura SIN dibujos en `<run>/<folder>/<HHMMSS_mmm>_<name>.png` (la hora en el nombre), entera o
        recortada a `box` (x, y, w, h; se ajusta a la imagen). Tope `debug.max_snapshots` por corrida. Solo con
        `debug.enabled`. Nunca lanza."""
        if not self.enabled or img is None or self.snapshots >= self.max_snapshots:
            return None
        try:
            if box is not None:
                ih, iw = img.shape[:2]
                x, y, w, h = box
                x0, y0, x1, y1 = max(0, int(x)), max(0, int(y)), min(iw, int(x + w)), min(ih, int(y + h))
                if x1 <= x0 or y1 <= y0:
                    return None
                img = img[y0:y1, x0:x1]
            if self._dir is None:
                self._dir = self._base / f"run_{self._now():%Y%m%d_%H%M%S}"
            out = self._dir / folder
            out.mkdir(parents=True, exist_ok=True)
            now = self._now()
            path = out / f"{now:%H%M%S}_{now.microsecond // 1000:03d}_{_slug(name)}.png"
            if not cv2.imwrite(str(path), img):
                raise OSError(f"cv2.imwrite no pudo escribir {path}")
            self.snapshots += 1
            if self.snapshots == self.max_snapshots:
                logger.warning("Debug: tope de %d capturas sueltas alcanzado", self.max_snapshots)
            return path
        except Exception:  # noqa: BLE001 - el debug nunca puede romper el loop
            logger.exception("Debug: no se pudo guardar la captura %s", name)
            return None

    def explore_due(self, t: float) -> bool:
        """True (y rearma el reloj) si pasaron `debug.explore_every_s` desde la ultima imagen periodica."""
        if not self.enabled or t - self._last_explore < self.explore_every_s:
            return False
        self._last_explore = t
        return True

    def save_coord_crop(self, img: np.ndarray, reason: str, t: float) -> Path | None:
        """Guarda la zona de coordenadas ampliada x4 de una lectura fallida o descartada en
        `<run>/coordenadas/NNNN_<motivo>.png` (tope `debug.max_coord_crops`, separacion `coord_crop_every_s`)."""
        if not self.enabled or img is None or self.coord_crops >= self.max_coord_crops                 or t - self._last_crop < self.coord_crop_every_s:
            return None
        try:
            ih, iw = img.shape[:2]
            region = anchor_rect(iw, ih, self.cfg["vision"]["coords"]["search"]).clip(iw, ih)
            if region is None:
                return None
            if self._dir is None:
                self._dir = self._base / f"run_{self._now():%Y%m%d_%H%M%S}"
            folder = self._dir / "coordenadas"
            folder.mkdir(parents=True, exist_ok=True)
            crop = img[region.y:region.bottom, region.x:region.right]
            big = cv2.resize(crop, None, fx=COORD_CROP_ZOOM, fy=COORD_CROP_ZOOM, interpolation=cv2.INTER_NEAREST)
            path = folder / f"{self.coord_crops + 1:04d}_{_slug(reason)}.png"
            if not cv2.imwrite(str(path), big):
                raise OSError(f"cv2.imwrite no pudo escribir {path}")
            self.coord_crops += 1
            self._last_crop = t
            if self.coord_crops == self.max_coord_crops:
                logger.warning("Debug: tope de %d recortes de coordenadas alcanzado", self.max_coord_crops)
            return path
        except Exception:  # noqa: BLE001 - el debug nunca puede romper el loop
            logger.exception("Debug: no se pudo guardar el recorte de coordenadas")
            return None

    def record(self, img: np.ndarray | None, p: Any, state: str, reason: str, lines: list[str] | None = None,
               click: tuple[int, int] | None = None, summary: str = "", kind: str = "") -> Path | None:
        """Guarda la imagen del evento. Devuelve la ruta o None (deshabilitado, tope total o del tipo `kind`, o error)."""
        if not self.enabled or img is None or p is None:
            return None
        try:
            if kind in self.max_per_kind and self.per_kind.get(kind, 0) >= self.max_per_kind[kind]:
                if kind not in self._kind_capped:
                    logger.warning("Debug: tope de %d imagenes de tipo %r alcanzado, no se guardan mas de ese tipo",
                                   self.max_per_kind[kind], kind)
                    self._kind_capped.add(kind)
                return None
            if self.count >= self.max_images:
                if not self._capped:
                    logger.warning("Debug: tope de %d imagenes alcanzado, no se guardan mas", self.max_images)
                    self._capped = True
                return None
            if self._dir is None:
                self._dir = self._base / f"run_{self._now():%Y%m%d_%H%M%S}"
                self._dir.mkdir(parents=True, exist_ok=True)
            n = self.count + 1
            path = self._dir / f"{n:04d}_{_slug(state)}_{_slug(reason)}.jpg"
            out = annotate_perception(img, p, self.cfg, [f"{state}: {reason}"] + list(lines or []), click)
            if not cv2.imwrite(str(path), out, [int(cv2.IMWRITE_JPEG_QUALITY), self.quality]):
                raise OSError(f"cv2.imwrite no pudo escribir {path}")
            if self.save_raw:   # captura cruda (sin dibujos) con el mismo numero: caso de prueba limpio (mismo tope: n <= max_images)
                raw = path.with_name(path.stem + "_raw.png")
                if not cv2.imwrite(str(raw), img):
                    raise OSError(f"cv2.imwrite no pudo escribir {raw}")
            with open(self._dir / "events.log", "a", encoding="utf-8") as f:
                f.write(f"{n:04d} {path.name} | {state} | {reason} | {summary}\n")
            self.count = n
            self.per_kind[kind] = self.per_kind.get(kind, 0) + 1
            self._fails = 0
            return path
        except Exception:  # noqa: BLE001 - el debug nunca puede romper el loop
            self._fails += 1
            logger.exception("Debug: no se pudo guardar la imagen")
            if self._fails >= MAX_FAILS:
                logger.error("Debug: %d fallos seguidos, se desactiva el recorder", self._fails)
                self.enabled = False
            return None
