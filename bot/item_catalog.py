"""Catalogo de tipos de objeto por firma visual de su etiqueta (no hay OCR).

Objetos con la misma etiqueta (mismo nombre) dan la misma firma y se agrupan solos; cada grupo nuevo se llama
"Objeto N" y se puede renombrar. En `dir` guarda `catalogo.json` (entradas), `<id>.png` (recorte en color de la
etiqueta, para la miniatura) y `<id>.npy` (firma). Con `dir=None` esta desactivado (no lee ni escribe).
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from bot.geometry import Rect

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
CATALOG_FILE = "catalogo.json"
UNKNOWN_ID = "sin_identificar"          # objetos que desaparecieron sin poder asociarles una etiqueta
UNKNOWN_NAME = "Objeto sin identificar"
MAX_NAME_LEN = 60
WIDTH_TOL_PX = 2                         # ancho de firma: diferencia maxima para considerar el mismo tipo


def label_signature(img_bgr: np.ndarray, rect: Rect, white_min: int = 250, text_rows: int = 13,
                    height: int = 10) -> np.ndarray | None:
    """Firma de la etiqueta de `rect`: mascara del texto blanco (mismo criterio que `vision.items`) en las ultimas
    `text_rows` filas del interior (sin borde), recortada a la caja del texto y escalada a `height` px de alto
    manteniendo la proporcion. Float32 0..1. None si no hay texto."""
    inner = img_bgr[max(rect.y + 1, 0):max(rect.bottom - 1, 0), max(rect.x + 1, 0):max(rect.right - 1, 0)]
    if inner.size == 0:
        return None
    line = inner[max(0, inner.shape[0] - text_rows):]
    mask = (line >= white_min).all(axis=2)
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    box = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1].astype(np.float32)
    w = max(1, round(box.shape[1] * height / box.shape[0]))
    return cv2.resize(box, (w, height), interpolation=cv2.INTER_AREA)


def signature_similarity(a: np.ndarray, b: np.ndarray, width_tol: int = WIDTH_TOL_PX) -> float:
    """Correlacion normalizada (-1..1) de dos firmas; 0 si el ancho difiere mas de `width_tol` px o no hay variacion.
    La mas angosta se rellena con ceros a la derecha."""
    if a.shape[0] != b.shape[0] or abs(a.shape[1] - b.shape[1]) > width_tol:
        return 0.0
    w = max(a.shape[1], b.shape[1])
    pa = np.pad(a, ((0, 0), (0, w - a.shape[1]))).ravel().astype(np.float64)
    pb = np.pad(b, ((0, 0), (0, w - b.shape[1]))).ravel().astype(np.float64)
    pa, pb = pa - pa.mean(), pb - pb.mean()
    den = float(np.linalg.norm(pa) * np.linalg.norm(pb))
    return float(pa @ pb / den) if den > 1e-9 else 0.0


@dataclass
class CatalogEntry:
    id: str
    name: str
    count: int
    width: int
    created: str


def validate_name(name: Any) -> str:
    """Nombre limpio o ValueError (vacio o demasiado largo)."""
    text = str(name).strip() if isinstance(name, str) else ""
    if not text:
        raise ValueError("El nombre no puede estar vacio.")
    if len(text) > MAX_NAME_LEN:
        raise ValueError(f"El nombre es demasiado largo (maximo {MAX_NAME_LEN} caracteres).")
    return text


class ItemCatalog:
    """Entradas {id, name, count, width, created} + firmas en memoria. `match` = correlacion minima."""

    def __init__(self, dir: Path | None, match: float = 0.88) -> None:   # noqa: A002 - mismo nombre que la config
        self.dir = Path(dir) if dir is not None else None
        self.match = float(match)
        self.entries: list[CatalogEntry] = []
        self._sigs: dict[str, np.ndarray] = {}
        self._broken = False
        self._load()

    @property
    def enabled(self) -> bool:
        return self.dir is not None

    def __len__(self) -> int:
        return len(self.entries)

    # ------------------------------------------------------------------ persistencia
    def _load(self) -> None:
        if self.dir is None or not (self.dir / CATALOG_FILE).is_file():
            return
        path = self.dir / CATALOG_FILE
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.entries = [CatalogEntry(str(d["id"]), str(d["name"]), int(d.get("count", 0)), int(d.get("width", 0)),
                                         str(d.get("created", ""))) for d in data["items"]]
        except (OSError, ValueError, KeyError, TypeError) as e:
            logger.warning("Catalogo de objetos: no se pudo leer %s (%s): empiezo vacio", path, e)
            self.entries, self._broken = [], True
            return
        for e in self.entries:
            try:
                self._sigs[e.id] = np.load(self.dir / f"{e.id}.npy")
            except (OSError, ValueError):
                logger.warning("Catalogo de objetos: falta la firma de %s (%s): no se podra reconocer", e.id, e.name)
        logger.info("Catalogo de objetos: %d tipos cargados de %s", len(self.entries), path)

    def reload(self) -> None:
        """Vuelve a leer del disco (la interfaz lo usa despues de una corrida)."""
        self.entries, self._sigs, self._broken = [], {}, False
        self._load()

    def _write_atomic(self, path: Path, data: bytes) -> None:
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def _save_index(self) -> None:
        if self.dir is None:
            return
        path = self.dir / CATALOG_FILE
        if self._broken:
            os.replace(path, path.with_name(path.name + ".bak"))
            logger.warning("Catalogo de objetos: archivo ilegible respaldado como %s.bak", path.name)
            self._broken = False
        data = {"version": FORMAT_VERSION,
                "items": [{"id": e.id, "name": e.name, "count": e.count, "width": e.width, "created": e.created}
                          for e in self.entries]}
        self._write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))

    def _save(self, new: CatalogEntry | None = None, crop_bgr: np.ndarray | None = None) -> None:
        if self.dir is None:
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            if new is not None:
                import io
                buf = io.BytesIO()
                np.save(buf, self._sigs[new.id])
                self._write_atomic(self.dir / f"{new.id}.npy", buf.getvalue())
                if crop_bgr is not None and crop_bgr.size:
                    ok, png = cv2.imencode(".png", crop_bgr)
                    if ok:
                        self._write_atomic(self.dir / f"{new.id}.png", png.tobytes())
            self._save_index()
        except OSError as e:
            logger.warning("Catalogo de objetos: no se pudo guardar en %s: %s", self.dir, e)

    # ------------------------------------------------------------------ consulta
    def get(self, item_id: str) -> CatalogEntry | None:
        return next((e for e in self.entries if e.id == item_id), None)

    def name_of(self, item_id: str) -> str:
        """Nombre actual de un id (el de un id desconocido es el id mismo)."""
        if item_id == UNKNOWN_ID:
            return UNKNOWN_NAME
        e = self.get(item_id)
        return e.name if e else item_id

    def thumbnail_path(self, item_id: str) -> Path | None:
        if self.dir is None:
            return None
        p = self.dir / f"{item_id}.png"
        return p if p.is_file() else None

    def _next_number(self) -> int:
        nums = [int(m.group(1)) for e in self.entries if (m := re.fullmatch(r"obj(\d+)", e.id))]
        return max(nums, default=0) + 1

    # ------------------------------------------------------------------ edicion
    def match_or_add(self, signature: np.ndarray, crop_bgr: np.ndarray | None = None) -> CatalogEntry:
        """Grupo de la firma (mismo ancho +-2 px y correlacion >= `match`) o uno nuevo "Objeto N". Suma 1 a su cuenta."""
        best, best_s = None, self.match
        for e in self.entries:
            sig = self._sigs.get(e.id)
            if sig is None:
                continue
            s = signature_similarity(signature, sig)
            if s >= best_s:
                best, best_s = e, s
        if best is not None:
            best.count += 1
            self._save()
            return best
        n = self._next_number()
        entry = CatalogEntry(f"obj{n:03d}", f"Objeto {n}", 1, int(signature.shape[1]), datetime.now().isoformat(timespec="seconds"))
        self.entries.append(entry)
        self._sigs[entry.id] = signature.astype(np.float32)
        self._save(entry, crop_bgr)
        logger.info("Catalogo de objetos: tipo nuevo %s (%s)", entry.id, entry.name)
        return entry

    def rename(self, item_id: str, name: str) -> CatalogEntry:
        """Cambia el nombre (se aplica tambien al historial, que guarda ids). ValueError si es invalido o no existe."""
        text = validate_name(name)
        e = self.get(item_id)
        if e is None:
            raise ValueError(f"No existe el objeto {item_id!r}.")
        e.name = text
        self._save()
        return e


@dataclass
class SeenItem:
    """Objeto visto en el piso en una captura: punto en pantalla, firma de su etiqueta y recorte en color."""
    point: tuple[float, float]
    sig: np.ndarray | None
    crop: np.ndarray | None


def vanished(prev: list[SeenItem], cur: list[SeenItem], match: float, near: tuple[float, float]) -> list[SeenItem]:
    """Objetos de `prev` que ya no estan en `cur`, del mas cercano al punto `near` (los pies) al mas lejano.
    Cada objeto actual se asocia al previo de misma firma (correlacion >= `match`), con desempate por cercania en pantalla;
    sin firma, solo se asocia con otro sin firma. Los previos sin asociar son los que desaparecieron."""
    free = list(prev)
    for c in cur:
        best, best_key = None, None
        for q in free:
            if c.sig is None or q.sig is None:
                sim = 1.0 if c.sig is None and q.sig is None else 0.0
            else:
                sim = signature_similarity(c.sig, q.sig)
            if sim < match:
                continue
            key = (-sim if sim < 0.999 else -1.0, ((c.point[0] - q.point[0]) ** 2 + (c.point[1] - q.point[1]) ** 2))
            if best_key is None or key < best_key:
                best, best_key = q, key
        if best is not None:
            free.remove(best)
    free.sort(key=lambda q: (q.point[0] - near[0]) ** 2 + (q.point[1] - near[1]) ** 2)
    return free
