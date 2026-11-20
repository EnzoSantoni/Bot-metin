"""Correccion del angulo vertical de la camara: mide el horizonte (bot/vision/pitch.py) y, si se fue de la referencia,
arrastra con el clic derecho en vertical desde un punto libre del piso hasta volver a ella.

El sentido y la magnitud del arrastre se APRENDEN: `gain` (px de arrastre por unidad de horizon_frac de error) se ajusta
comparando la medicion antes y despues de cada arrastre (EMA); el signo inicial es una suposicion y se corrige solo.
El estado (gain) vive en el objeto: no se guarda en config.json. Config: `camera_pitch`.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Callable

from bot.vision.pitch import PitchResult, measure_horizon

logger = logging.getLogger(__name__)

MIN_DRAG_PX = 3        # un arrastre menor no mueve nada util
GAIN_LIMITS = (50.0, 5000.0)   # |gain| acotado (px por unidad de horizon_frac)
DRAG_BUTTON = "right"


def _gap(pt: tuple[int, int], rect: Any) -> float:
    """Distancia de un punto a un rectangulo (0 si esta adentro)."""
    return math.hypot(max(rect.x - pt[0], 0, pt[0] - rect.right), max(rect.y - pt[1], 0, pt[1] - rect.bottom))


class CameraPitch:
    """Estado y logica de la correccion vertical. No captura ni manda input por su cuenta: recibe `gi`, una funcion de
    captura y una de espera."""

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.c = cfg["camera_pitch"]
        self.gain = float(self.c["gain_px_per_frac"])
        self.last_check_t = -math.inf
        self.last_error: float | None = None
        self.drags = 0   # arrastres mandados (contador)
        self._warned = False

    # --- estado ---
    @property
    def ref(self) -> float | None:
        r = self.c["ref_horizon_frac"]
        return None if r is None else float(r)

    @property
    def active(self) -> bool:
        """Habilitado y con referencia calibrada. Sin referencia avisa una sola vez."""
        if not self.c["enabled"]:
            return False
        if self.ref is None:
            if not self._warned:
                self._warned = True
                logger.info("Angulo vertical sin calibrar: corre calibrate.py camara")
            return False
        return True

    def due(self, t: float) -> bool:
        """Pasaron `every_s` desde el ultimo chequeo (para el chequeo periodico de EXPLORAR)."""
        return t - self.last_check_t >= float(self.c["every_s"])

    # --- medicion ---
    def measure(self, img: Any, p: Any) -> PitchResult | None:
        """Mide el horizonte en `img` con la mascara y el personaje de la percepcion `p`. Loguea el valor."""
        res = measure_horizon(img, self.cfg, p.mask, p.player_xy)
        if res is not None:
            logger.debug("Angulo vertical: horizonte %.3f (referencia %s, confianza %.2f)", res.horizon_frac,
                         "-" if self.ref is None else f"{self.ref:.3f}", res.confidence)
        return res

    def error(self, res: PitchResult | None) -> float | None:
        """medido - referencia, o None si no hay referencia o la medicion no es confiable."""
        if res is None or self.ref is None or res.confidence < float(self.c["min_confidence"]):
            return None
        return res.horizon_frac - self.ref

    def out_of_tolerance(self, res: PitchResult | None) -> bool:
        e = self.error(res)
        return e is not None and abs(e) > float(self.c["tolerance"])

    # --- arrastre ---
    def plan_dy(self, err: float) -> int:
        """Pixeles de arrastre vertical (con signo, + = hacia abajo) para corregir `err`, acotados por `max_drag_px`."""
        mx = int(self.c["max_drag_px"])
        return int(max(-mx, min(mx, round(self.gain * err))))

    def free_point(self, p: Any) -> tuple[int, int] | None:
        """Punto del piso libre donde empezar el arrastre: permitido (el y el final de un arrastre de +-`max_drag_px`),
        a >= `drag_clear_px` de toda etiqueta de metin, monstruo, objeto o piedra lejana y del personaje."""
        h, w = p.shape[0], p.shape[1]
        clear, mx = float(self.c["drag_clear_px"]), int(self.c["max_drag_px"])
        rects = ([d.label_rect for d in p.metins] + [m.rect for m in p.monsters] + [i.rect for i in p.items]
                 + [f.rect for f in p.far_metins])
        px, py = p.player_xy
        for fy in (0.72, 0.64, 0.80, 0.56):
            for fx in (0.35, 0.65, 0.25, 0.75, 0.5, 0.15, 0.85):
                pt = (int(w * fx), int(h * fy))
                if not all(p.mask.allows(pt[0], pt[1] + k) for k in (-mx, 0, mx)):
                    continue
                gap = min([_gap(pt, r) for r in rects] + [math.hypot(pt[0] - px, pt[1] - py)])
                if gap >= clear:
                    return pt
        return None

    def learn(self, before: PitchResult, after: PitchResult, dy: int) -> bool:
        """Ajusta `gain` con lo que se movio el horizonte con el arrastre `dy`: efecto s = (despues - antes) / dy (fraccion por
        px); gain = -1 / s (para anular el error). Se ignoran las mediciones saturadas, de baja confianza o sin movimiento."""
        if dy == 0 or before.saturated or after.saturated:
            return False
        mn = float(self.c["min_confidence"])
        if before.confidence < mn or after.confidence < mn:
            return False
        move = after.horizon_frac - before.horizon_frac
        if abs(move) < float(self.c["min_gain_move_frac"]):
            logger.info("Angulo vertical: el arrastre de %+d px casi no movio el horizonte (%+.3f): no aprendo", dy, move)
            return False
        new = -dy / move
        new = math.copysign(min(max(abs(new), GAIN_LIMITS[0]), GAIN_LIMITS[1]), new)
        old = self.gain
        a = float(self.c["gain_ema"])
        self.gain = new if old * new < 0 else (1 - a) * old + a * new   # signo opuesto: se adopta el nuevo sin promediar
        logger.info("Angulo vertical: arrastre %+d px movio el horizonte %+.3f; ganancia %.0f -> %.0f px/frac", dy, move, old,
                    self.gain)
        return True

    def correct(self, p: Any, img: Any, gi: Any, capture: Callable[[], Any], sleep: Callable[[], None], why: str,
                max_steps: int | None = None) -> bool:
        """Mide y, si hace falta, arrastra hasta `max_steps` veces (con `sleep()` y una captura nueva entre medio).
        `capture()` devuelve una imagen o None (ventana minimizada, captcha: se corta). True si al final esta dentro de la
        tolerancia o no hizo falta corregir (o no se pudo medir)."""
        res = self.measure(img, p)
        e = self.error(res)
        logger.info("Angulo vertical (%s): horizonte %s, referencia %.3f (vision.ground.horizon_frac = %s)%s", why,
                    "no medible" if res is None else f"{res.horizon_frac:.3f}", self.ref or 0.0,
                    self.cfg["vision"]["ground"]["horizon_frac"],
                    "" if res is None or e is not None else " (confianza baja: no corrijo)")
        steps = int(self.c["max_steps"]) if max_steps is None else max_steps
        learned, dragged, started_saturated = False, False, res is not None and res.saturated
        for n in range(steps):
            if e is None or abs(e) <= float(self.c["tolerance"]):
                break
            dy = self.plan_dy(e)
            pt = self.free_point(p)
            if abs(dy) < MIN_DRAG_PX or pt is None:
                logger.info("Angulo vertical: no hay punto libre del piso para arrastrar" if pt is None
                            else "Angulo vertical: correccion menor a %d px", MIN_DRAG_PX)
                break
            logger.info("Angulo vertical: error %+.3f, arrastro %+d px con el clic derecho desde %s (intento %d/%d)",
                        e, dy, pt, n + 1, steps)
            if not gi.drag(DRAG_BUTTON, pt[0], pt[1], 0, dy, p.mask, float(self.c["drag_s"]), int(self.c["drag_steps"])):
                break
            self.drags += 1
            dragged = True
            sleep()
            img = capture()
            if img is None:
                return False
            after = self.measure(img, p)
            if res is not None and after is not None:
                learned = self.learn(res, after, dy) or learned
            res, e = after, self.error(after)
        ok = e is None or abs(e) <= float(self.c["tolerance"])
        if not ok and dragged and not learned and started_saturated and res is not None and res.saturated:
            # Sigue mirando el piso sin que el arrastre cambie nada medible: lo mas probable es que el sentido este al reves.
            self.gain = -self.gain
            logger.warning("Angulo vertical: sigue todo piso despues de arrastrar; doy vuelta el sentido del arrastre")
        return ok
