"""Angulo vertical de la camara medido desde la imagen: fila del horizonte = donde termina el piso (logica pura).

Se toma el color del piso de una muestra debajo del personaje y se recorre una franja vertical central de abajo hacia
arriba: mientras la fila "parece piso" (fraccion de pixeles con tono/brillo parecidos a la muestra) sigue siendo piso; la
primera fila que deja de serlo (`run_rows` seguidas) es el horizonte. `horizon_frac` = fila / alto: mas chico = camara mas
inclinada hacia abajo (se ve mas piso); 0 = todo piso. Se excluyen la mascara de UI y un rectangulo alrededor del
personaje. Config: `camera_pitch` (parametros de la medicion).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

from bot.vision.metin import player_point
from bot.vision.screen_mask import ScreenMask, build_screen_mask, find_ui_windows

logger = logging.getLogger(__name__)

ALL_FLOOR_CONFIDENCE = 0.5   # todo piso hasta arriba: el horizonte esta por encima de la imagen (se sabe que es <= 0)


@dataclass
class PitchResult:
    """`horizon_row`: fila (cliente) donde termina el piso; `horizon_frac` = fila / alto; `confidence` 0..1."""
    horizon_row: int
    horizon_frac: float
    confidence: float
    floor_rows: int = 0          # filas de piso contiguas desde la muestra hacia arriba
    coherence: float = 0.0       # fraccion de la muestra que coincide con el color modelo (calidad del modelo)
    saturated: bool = False      # todo piso hasta el borde de arriba (horizon_frac = 0 no es una medida exacta)


def _hue_diff(h: np.ndarray, ref: float) -> np.ndarray:
    """Diferencia circular de tono (OpenCV: 0..179)."""
    d = np.abs(h.astype(np.float32) - ref)
    return np.minimum(d, 180.0 - d)


def _circular_mean_hue(h: np.ndarray) -> float:
    ang = h.astype(np.float64) * (np.pi / 90.0)
    return float((np.degrees(np.arctan2(np.sin(ang).mean(), np.cos(ang).mean())) / 2.0) % 180.0)


def _valid_area(shape: tuple[int, ...], cfg: dict, mask: ScreenMask, player: tuple[int, int]) -> np.ndarray:
    """True donde el pixel se puede usar: fuera de la UI y del rectangulo del personaje."""
    h, w = shape[:2]
    ok = mask.mask[:h, :w] == 0
    hw, up, down = (int(v) for v in cfg["camera_pitch"]["player_box_px"])
    ok[max(0, player[1] - up):max(0, player[1] + down), max(0, player[0] - hw):max(0, player[0] + hw)] = False
    return ok


def measure_horizon(img: np.ndarray, cfg: dict, mask: ScreenMask | None = None,
                    player_xy: tuple[int, int] | None = None) -> PitchResult | None:
    """Mide la fila del horizonte en una captura BGR. None si no hay muestra de piso utilizable (confianza 0)."""
    c = cfg["camera_pitch"]
    h, w = img.shape[:2]
    if mask is None:
        mask = build_screen_mask(img.shape, cfg, extra=find_ui_windows(img, cfg))
    player = player_xy or player_point(img.shape, cfg)
    x0 = max(0, int(player[0] - float(c["strip_half_frac"]) * w))
    x1 = min(w, int(player[0] + float(c["strip_half_frac"]) * w))
    valid = _valid_area(img.shape, cfg, mask, player)[:, x0:x1]
    hsv = cv2.cvtColor(img[:, x0:x1], cv2.COLOR_BGR2HSV)
    # Muestra del piso: debajo del personaje (sin su rectangulo ni la UI).
    s0, s1 = max(0, player[1] + int(c["sample_top_px"])), min(h, player[1] + int(c["sample_bottom_px"]))
    if s1 - s0 < 4:
        return None
    sv = valid[s0:s1]
    if int(sv.sum()) < int(c["min_valid_px"]) * 4:
        return None
    sample = hsv[s0:s1][sv]
    ref_h = _circular_mean_hue(sample[:, 0])
    ref_v = float(np.median(sample[:, 2]))
    floor = _floor_like(hsv, c, ref_h, ref_v)
    coherence = float(floor[s0:s1][sv].mean())
    if coherence < float(c["row_floor_frac"]):
        return PitchResult(0, 0.0, 0.0, 0, coherence)   # la muestra no es un piso uniforme: no se confia
    counts = valid.sum(axis=1)
    frac = np.where(counts >= int(c["min_valid_px"]), (floor & valid).sum(axis=1) / np.maximum(counts, 1), np.nan)
    need, run, y = int(c["run_rows"]), 0, s0 - 1
    thr = float(c["row_floor_frac"])
    top = s0   # fila mas alta (hasta ahora) que es piso
    while y >= 0:
        f = frac[y]
        if np.isnan(f):   # fila sin datos suficientes (UI, personaje): ni suma ni corta
            y -= 1
            continue
        if f >= thr:
            run, top = 0, y
        else:
            run += 1
            if run >= need:
                break
        y -= 1
    saturated = y < 0
    floor_rows = s0 - top
    if saturated:
        top = 0
    conf = ALL_FLOOR_CONFIDENCE if saturated else 1.0
    conf = min(conf, coherence / 0.9 if coherence < 0.9 else 1.0)
    if floor_rows < int(c["min_floor_rows"]) and not saturated:
        conf = min(conf, 0.3)   # casi no hay piso para comparar: la medida es dudosa
    return PitchResult(int(top), top / h, float(conf), int(floor_rows), coherence, saturated)


def _floor_like(hsv: np.ndarray, c: dict, ref_h: float, ref_v: float) -> np.ndarray:
    """Pixeles con tono y brillo parecidos al piso y algo de saturacion (el cielo/niebla es gris y desaturado)."""
    return ((_hue_diff(hsv[:, :, 0], ref_h) <= float(c["tol_hue"]))
            & (np.abs(hsv[:, :, 2].astype(np.float32) - ref_v) <= float(c["tol_val"]))
            & (hsv[:, :, 1] >= int(c["min_sat"])))


def draw_horizon(img: np.ndarray, res: PitchResult | None, ref_frac: float | None = None,
                 tolerance: float = 0.0) -> np.ndarray:
    """Copia de `img` con la linea del horizonte medido (verde) y, si hay, la de referencia (amarillo) con su tolerancia."""
    out = img.copy()
    h, w = out.shape[:2]
    if ref_frac is not None:
        for f, th in ((ref_frac, 2), (ref_frac - tolerance, 1), (ref_frac + tolerance, 1)):
            if tolerance > 0 or th == 2:
                y = int(round(f * h))
                cv2.line(out, (0, y), (w, y), (0, 255, 255), th)
    if res is not None:
        cv2.line(out, (0, res.horizon_row), (w, res.horizon_row), (0, 255, 0), 2)
        txt = f"horizonte {res.horizon_frac:.3f} (fila {res.horizon_row}) conf {res.confidence:.2f}"
        cv2.putText(out, txt, (20, max(30, res.horizon_row - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2,
                    cv2.LINE_AA)
    return out
