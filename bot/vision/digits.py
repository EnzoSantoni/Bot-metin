"""Lectura de lineas de texto de la interfaz (numeros) por plantillas de caracter.

El texto de la interfaz es de color (casi) uniforme y sin antialiasing: se binariza por color, se
segmenta en componentes conexas y cada una se compara contra las plantillas de `GlyphSet`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from bot.config import resolve_path
from bot.geometry import Rect

logger = logging.getLogger(__name__)

# Caracter -> nombre de archivo seguro (los digitos se llaman como el digito).
CHAR_FILES: dict[str, str] = {".": "dot", "/": "slash", "(": "lparen", ")": "rparen", ",": "comma"}
_FILE_CHARS = {v: k for k, v in CHAR_FILES.items()}


def char_filename(ch: str) -> str:
    """'(' -> 'lparen.png', '7' -> '7.png'."""
    return f"{CHAR_FILES.get(ch, ch)}.png"


def _tight(crop: np.ndarray) -> np.ndarray:
    """Recorte bool ajustado a los pixeles encendidos (vacio si no hay ninguno)."""
    ys, xs = np.nonzero(crop)
    if len(xs) == 0:
        return crop[:0, :0]
    return crop[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def glyph_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Jaccard (0..1) entre dos recortes bool alineados arriba-izquierda; 0 si difieren >1 px de tamano."""
    if a.size == 0 or b.size == 0 or abs(a.shape[0] - b.shape[0]) > 1 or abs(a.shape[1] - b.shape[1]) > 1:
        return 0.0
    hh, ww = max(a.shape[0], b.shape[0]), max(a.shape[1], b.shape[1])
    pa = np.zeros((hh, ww), bool)
    pb = np.zeros((hh, ww), bool)
    pa[:a.shape[0], :a.shape[1]] = a
    pb[:b.shape[0], :b.shape[1]] = b
    union = np.count_nonzero(pa | pb)
    return float(np.count_nonzero(pa & pb) / union) if union else 0.0


@dataclass
class GlyphSet:
    """Plantillas {caracter: recorte bool ajustado}."""
    chars: dict[str, np.ndarray] = field(default_factory=dict)

    @classmethod
    def load(cls, directory: str | Path) -> "GlyphSet":
        """Carga un PNG binario por caracter (`0.png`, `dot.png`, `slash.png`...). Carpeta vacia -> set vacio."""
        d = resolve_path(directory)
        chars: dict[str, np.ndarray] = {}
        for p in sorted(d.glob("*.png")) if d.is_dir() else []:
            ch = _FILE_CHARS.get(p.stem, p.stem)
            if len(ch) != 1:
                logger.warning("Plantilla de caracter ignorada (nombre no valido): %s", p)
                continue
            img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            if img is None:
                raise FileNotFoundError(f"No se pudo leer la plantilla {p}")
            tight = _tight(img > 127)
            if tight.size == 0:
                logger.warning("Plantilla de caracter ignorada (sin pixeles de texto): %s", p)
                continue
            chars[ch] = tight
        return cls(chars)

    def classify(self, crop: np.ndarray) -> tuple[str, float]:
        """(mejor caracter, similitud). ('?', 0.0) si no hay plantillas."""
        best_ch, best = "?", 0.0
        for ch, tpl in self.chars.items():
            s = glyph_similarity(crop, tpl)
            if s > best:
                best_ch, best = ch, s
        return best_ch, best

    def classify_ranked(self, crop: np.ndarray) -> tuple[str, float, float]:
        """(mejor caracter, su similitud, similitud del segundo mejor). ('?', 0, 0) si no hay plantillas."""
        best_ch, best, second = "?", 0.0, 0.0
        for ch, tpl in self.chars.items():
            s = glyph_similarity(crop, tpl)
            if s > best:
                best_ch, best, second = ch, s, best
            elif s > second:
                second = s
        return best_ch, best, second

    def accept(self, crop: np.ndarray, min_score: float, min_margin: float) -> tuple[str, float]:
        """(caracter, similitud) solo si pasa `min_score` y le saca `min_margin` al segundo; si no ('?', similitud)."""
        ch, best, second = self.classify_ranked(crop)
        if best >= min_score and best - second >= min_margin:
            return ch, best
        return "?", best

    @property
    def max_width(self) -> int:
        return max((t.shape[1] for t in self.chars.values()), default=0)


_glyph_cache: dict[str, GlyphSet] = {}


def get_glyphs(section: dict) -> GlyphSet:
    """GlyphSet de la seccion de config (con cache por carpeta)."""
    key = section["dir"]
    if key not in _glyph_cache:
        _glyph_cache[key] = GlyphSet.load(key)
        if not _glyph_cache[key].chars:
            logger.warning("No hay plantillas de caracteres en %s (tools/make_glyph_templates.py)", key)
    return _glyph_cache[key]


@dataclass(frozen=True)
class TextRead:
    """Resultado de leer una linea: texto ('?' en lo que no se reconocio), peor confianza y cajas."""
    text: str
    min_score: float
    boxes: list[Rect] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)
    crops: list[np.ndarray] = field(default_factory=list, repr=False, compare=False)  # bitmaps bool por caracter


def text_mask(crop_bgr: np.ndarray, section: dict) -> np.ndarray:
    """Mascara 0/255 de los pixeles del color del texto (`color` +- `color_tol` por canal)."""
    c = np.array(section["color"], dtype=np.int32)
    tol = int(section["color_tol"])
    return cv2.inRange(crop_bgr, np.clip(c - tol, 0, 255).astype(np.uint8),
                       np.clip(c + tol, 0, 255).astype(np.uint8))


def segment_glyphs(mask: np.ndarray) -> list[tuple[Rect, np.ndarray]]:
    """Componentes conexas (8) de la mascara, ordenadas de izquierda a derecha: (caja, recorte bool)."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out = []
    for k in range(1, n):
        x, y, w, h, _a = (int(v) for v in stats[k])
        out.append((Rect(x, y, w, h), labels[y:y + h, x:x + w] == k))
    out.sort(key=lambda c: (c[0].x, c[0].y))
    return out


def peel_glyphs(crop: np.ndarray, glyphs: GlyphSet) -> list[tuple[int, int, np.ndarray]]:
    """Separa caracteres pegados: [(dx, dy, recorte)] de izquierda a derecha, relativos al recorte.

    Va "pelando" el caracter de la izquierda: la plantilla mas grande que entra completa (como subconjunto de
    pixeles) pegada al borde izquierdo. Funciona aunque se solapen columnas (ej. la barra '/' y el digito
    siguiente). Si ninguna plantilla entra, lo que queda sale como una pieza sin reconocer. No distingue
    ambiguedades (un 8 tapado que "entra" un 9): por eso `read_text_line` solo pela componentes mas anchas
    que cualquier glifo; las de ancho de un solo caracter pasan por `GlyphSet.accept` (con margen).
    """
    order = [t for t in sorted(glyphs.chars.values(), key=lambda t: -int(np.count_nonzero(t))) if t.size]
    cur = crop.copy()
    out: list[tuple[int, int, np.ndarray]] = []
    while cur.any():
        cols = np.nonzero(cur.any(axis=0))[0]
        x0 = int(cols[0])
        found = None
        for t in order:
            h, w = t.shape
            if x0 + w > cur.shape[1] or h > cur.shape[0]:
                continue
            for dy in range(cur.shape[0] - h + 1):
                win = cur[dy:dy + h, x0:x0 + w]
                if np.count_nonzero(t & ~win) or not (t & win[:, :w]).any(axis=0)[0]:
                    continue  # tiene que entrar completa y cubrir la columna izquierda del resto
                rest = cur.copy()
                rest[dy:dy + h, x0:x0 + w] &= ~t
                rc = np.nonzero(rest.any(axis=0))[0]
                if len(rc) == 0 or int(rc[0]) >= x0 + w - 2:
                    found = (dy, t, rest)
                    break
            if found:
                break
        if found is None:
            ys, xs = np.nonzero(cur)
            out.append((int(xs.min()), int(ys.min()), cur[ys.min():ys.max() + 1, xs.min():xs.max() + 1]))
            break
        dy, t, cur = found
        out.append((x0, dy, t))
    return out


def read_text_line(crop_bgr: np.ndarray, glyphs: GlyphSet, cfg_section: dict) -> TextRead:
    """Lee una linea de texto: binariza, segmenta, clasifica. Lo que no alcanza `min_score` va como '?'.

    Una componente que no se reconoce (caracteres pegados) se separa con `peel_glyphs`.
    """
    mask = text_mask(crop_bgr, cfg_section)
    min_score = float(cfg_section["min_score"])
    min_margin = float(cfg_section["min_margin"])
    items: list[tuple[Rect, np.ndarray, float | None]] = []   # (caja, bitmap, score si ya se conoce)
    for rect, comp in segment_glyphs(mask):
        tight = _tight(comp)
        if glyphs.accept(tight, min_score, min_margin)[0] != "?" or tight.shape[1] <= glyphs.max_width:
            items.append((rect, tight, None))   # un solo caracter (o ilegible): no se intenta separar
            continue
        for dx, dy, piece in peel_glyphs(comp, glyphs):
            items.append((Rect(rect.x + dx, rect.y + dy, piece.shape[1], piece.shape[0]), piece, None))
    items.sort(key=lambda it: it[0].x)
    max_gap = int(cfg_section["max_gap"])
    max_gap_comma = int(cfg_section["max_gap_after_comma"])   # la coma lleva un espacio detras
    chars, scores, boxes, crops = [], [], [], []
    for box, tight, _s in items:
        if boxes:
            gap = box.x - boxes[-1].right
            limit = max_gap_comma if chars[-1] == "," else max_gap
            if gap > limit:   # hueco demasiado ancho: falta un caracter (tapado o ilegible) -> '?'
                chars.append("?")
                scores.append(0.0)
                boxes.append(Rect(boxes[-1].right, box.y, gap, box.h))
                crops.append(np.zeros((0, 0), bool))
        ch, s = glyphs.accept(tight, min_score, min_margin)
        chars.append(ch)
        scores.append(s)
        boxes.append(box)
        crops.append(tight)
    return TextRead("".join(chars), min(scores, default=0.0), boxes, scores, crops)
