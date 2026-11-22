"""Angulo vertical de la camara: reinicio desde el TOPE (no se mide el horizonte para saber el angulo: depende del terreno).

`reset()` lleva el zoom al tope "lejos" (rueda), la camara al tope horizontal (clic derecho arrastrando hacia ARRIBA mas de lo
que hace falta) y desde ahi baja una cantidad FIJA de pixeles (`from_limit_px`) hasta el angulo normal. El juego sube la camara
solo al acercarse a bordes o montanas y NO la devuelve: por eso se reinicia al arrancar, tras cada metin roto, cada `every_s` y
cuando se detecta que la camara mira el piso (`looks_at_floor`, con bot/vision/pitch.py solo como detector de "todo piso").

Un arrastre siempre mueve el mouse (>= `min_drag_px`) y no empieza ni termina sobre un monstruo, una etiqueta, un metin, un
objeto ni la interfaz: un clic derecho SIN mover sobre un monstruo lo selecciona. Config: `camera_pitch`.
"""
from __future__ import annotations

import copy
import logging
import math
from typing import Any, Callable

from bot.vision.metin import player_point
from bot.vision.pitch import measure_horizon

logger = logging.getLogger(__name__)

DRAG_BUTTON = "right"
BACKOFF_MAX = 8   # tope del multiplo de `min_gap_s` tras reinicios por piso que no arreglaron nada
# Filas (fraccion del alto) y columnas (fraccion del ancho) donde se prueba el punto inicial del arrastre.
FREE_ROWS = (0.72, 0.64, 0.80, 0.56, 0.88, 0.48, 0.40, 0.32)
FREE_COLS = (0.35, 0.65, 0.25, 0.75, 0.5, 0.15, 0.85)
PATH_SAMPLES = (0.0, 0.5, 1.0)   # puntos del recorrido (fraccion de dy) que tienen que estar permitidos y libres


def _gap(pt: tuple[int, int], rect: Any) -> float:
    """Distancia de un punto a un rectangulo (0 si esta adentro)."""
    return math.hypot(max(rect.x - pt[0], 0, pt[0] - rect.right), max(rect.y - pt[1], 0, pt[1] - rect.bottom))


def split_px(total: int, max_px: int) -> list[int]:
    """Parte `total` px en la menor cantidad de arrastres parejos de a lo sumo `max_px` (suman exactamente `total`)."""
    n = max(1, math.ceil(total / max_px))
    base, extra = divmod(int(total), n)
    return [base + (1 if i < extra else 0) for i in range(n)]


def looks_at_floor(img: Any, cfg: dict, mask: Any = None, player_xy: tuple[int, int] | None = None) -> bool:
    """La camara mira el piso: no se ve horizonte. True si la franja central es todo piso hasta el borde de arriba, o si la
    central no es medible (piso de dos tonos: ladera de montana) y las dos franjas laterales (a +-`side_offset_frac` del ancho)
    son todo piso."""
    c = cfg["camera_pitch"]
    res = measure_horizon(img, cfg, mask, player_xy)
    if res is not None and res.saturated and res.confidence >= float(c["min_confidence"]):
        return True
    if res is not None and res.confidence >= float(c["min_confidence"]):
        return False   # hay horizonte medido (o piso corto): no es piso
    side = copy.deepcopy(cfg)
    side["camera_pitch"]["strip_half_frac"] = float(c["side_half_frac"])
    w = img.shape[1]
    if player_xy is None:
        player_xy = player_point(img.shape, cfg)
    for off in (-1, 1):
        r = measure_horizon(img, side, mask, (int(player_xy[0] + off * float(c["side_offset_frac"]) * w), player_xy[1]))
        if r is None or not r.saturated or r.confidence < float(c["min_confidence"]):
            return False
    return True


class CameraPitch:
    """Estado y logica del reinicio de la camara. No captura ni manda input por su cuenta: recibe `gi`, una funcion que
    captura y percibe y una de espera."""

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.c = cfg["camera_pitch"]
        self.last_reset_t = -math.inf
        self.floor_streak = 0      # capturas seguidas mirando al piso
        self.fruitless = 0         # reinicios por piso seguidos que no sacaron la vista del piso
        self.drags = 0             # arrastres mandados
        self.resets = 0            # reinicios completos

    # --- cuando ---
    @property
    def enabled(self) -> bool:
        return bool(self.c["enabled"])

    def periodic_due(self, t: float) -> bool:
        """Pasaron `every_s` desde el ultimo reinicio (se chequea al empezar cada vuelta de busqueda)."""
        return self.enabled and t - self.last_reset_t >= float(self.c["every_s"])

    def floor_gap_s(self) -> float:
        """Minimo entre reinicios por piso: `min_gap_s`, multiplicado si los ultimos no arreglaron nada (terreno alto)."""
        return float(self.c["min_gap_s"]) * min(BACKOFF_MAX, 1 + self.fruitless)

    def observe(self, img: Any, p: Any, t: float) -> bool:
        """Cuenta capturas seguidas mirando al piso. True si hay que reiniciar: `floor_frames` seguidas y al menos
        `floor_gap_s()` desde el ultimo reinicio."""
        if not self.enabled:
            return False
        if not looks_at_floor(img, self.cfg, p.mask, p.player_xy):
            self.floor_streak = 0
            return False
        self.floor_streak += 1
        return self.floor_streak >= int(self.c["floor_frames"]) and t - self.last_reset_t >= self.floor_gap_s()

    # --- punto del arrastre ---
    def free_point(self, p: Any, dy: int) -> tuple[int, int] | None:
        """Punto del piso donde empezar un arrastre vertical de `dy` px: el inicio, el medio y el final permitidos por la
        mascara y a >= `drag_clear_px` de toda etiqueta de metin, monstruo, objeto o piedra lejana y del personaje."""
        h, w = p.shape[0], p.shape[1]
        clear = float(self.c["drag_clear_px"])
        rects = ([d.label_rect for d in p.metins] + [m.rect for m in p.monsters] + [i.rect for i in p.items]
                 + [f.rect for f in p.far_metins])
        px, py = p.player_xy
        for fy in FREE_ROWS:
            for fx in FREE_COLS:
                x, y0 = int(w * fx), int(h * fy)
                pts = [(x, y0 + int(round(dy * f))) for f in PATH_SAMPLES]
                if not all(0 <= q[1] < h and p.mask.allows(*q) for q in pts):
                    continue
                gap = min([_gap(q, r) for q in pts for r in rects] + [math.hypot(q[0] - px, q[1] - py) for q in pts])
                if gap >= clear:
                    return (x, y0)
        return None

    # --- reinicio ---
    def _drag(self, cur: tuple[Any, Any], perceive: Callable[[], Any], gi: Any, dy: int) -> tuple[Any, Any] | None:
        """Un arrastre vertical con el clic derecho (`dy` < 0 = hacia arriba) desde un punto libre de la percepcion `cur`
        (img, p). Devuelve la percepcion nueva (despues del arrastre) o None si no se pudo (se corta el reinicio)."""
        assert abs(dy) >= int(self.c["min_drag_px"])   # un arrastre sin mover seleccionaria el monstruo de abajo
        p = cur[1]
        pt = self.free_point(p, dy)
        if pt is None:
            logger.warning("Camara: no hay un punto libre del piso para arrastrar %+d px: se corta el reinicio", dy)
            return None
        if not gi.drag(DRAG_BUTTON, pt[0], pt[1], 0, dy, p.mask, float(self.c["drag_s"]), int(self.c["drag_steps"])):
            return None
        self.drags += 1
        return perceive()

    def plan_down(self, px: int) -> list[int]:
        """Arrastres (dy con signo, + = abajo) que bajan la camara `px` px en neto, cada uno de >= `min_drag_px` (y de <=
        `max_drag_px`). Menos que `min_drag_px`: baja `px + min_drag_px` y vuelve a subir `min_drag_px` (neto `px`)."""
        mn = int(self.c["min_drag_px"])
        if px <= 0:
            return []
        return split_px(px, int(self.c["max_drag_px"])) if px >= mn else [px + mn, -mn]

    def move_down(self, cur: tuple[Any, Any], perceive: Callable[[], Any], gi: Any, sleep: Callable[[float], None],
                  px: int, limit: str | None = None) -> tuple[Any, Any] | None:
        """Aleja la camara `px` px (neto) del tope `limit` hacia el angulo normal: desde el tope horizontal se arrastra hacia
        ABAJO; desde el tope "arriba", hacia ARRIBA. Devuelve la percepcion final o None si se corto."""
        sgn = 1 if (limit or self.c["limit"]) == "horizontal" else -1
        for dy in self.plan_down(px):
            cur = self._drag(cur, perceive, gi, sgn * dy)
            if cur is None:
                return None
            sleep(float(self.c["drag_pause_s"]))
        return cur

    def to_top(self, perceive: Callable[[], Any], gi: Any, sleep: Callable[[float], None], t: float, why: str,
               down_px: int = 0, limit: str | None = None,
               on_phase: Callable[[str, tuple[Any, Any]], None] | None = None) -> tuple[Any, Any] | None:
        """Reinicio en 3 pasos: (1) arrastre con el clic derecho hasta el tope `limit` ("horizontal" = hacia ARRIBA;
        "arriba" = hacia ABAJO, camara desde arriba). Ojo: seguir arrastrando contra el tope horizontal ACERCA la camara al
        personaje (comprobado por Enzo); por eso (2) DESPUES la rueda hasta el tope "lejos" (`zoom_out_sign`) recupera la
        distancia normal; (3) recien ahi se aleja `down_px` del tope hacia el angulo normal. `on_phase(nombre, (img, p))`
        recibe la captura despues de "tope" y de "rueda" (calibracion). Devuelve la ultima percepcion o None si se corto."""
        c = self.c
        lim = limit or c["limit"]
        self.last_reset_t, self.floor_streak = t, 0
        sgn = -1 if lim == "horizontal" else 1   # dy < 0 = arrastre hacia arriba
        drags = [sgn * d for d in split_px(int(c["to_limit_px"]), int(c["max_drag_px"]))]
        clicks = int(c["zoom_out_clicks"])
        wheel = int(c["zoom_out_sign"]) * clicks
        logger.info("Camara: reinicio desde el tope %s (arrastre %s %d px, despues rueda %+d para alejar "
                    "[zoom_out_sign = %d], despues %d px hacia el angulo normal) [%s]", lim,
                    "hacia arriba" if sgn < 0 else "hacia abajo", int(c["to_limit_px"]), wheel, int(c["zoom_out_sign"]),
                    down_px, why)
        cur = perceive()
        if cur is None:
            return None
        for dy in drags:
            cur = self._drag(cur, perceive, gi, dy)
            if cur is None:
                return None
            sleep(float(c["drag_pause_s"]))
        if on_phase is not None:
            on_phase("tope", cur)
        if clicks > 0:
            if not gi.zoom(wheel, cur[1].mask):
                logger.warning("Camara: no se pudo girar la rueda (zoom lejos); sigo")
            sleep(float(c["drag_pause_s"]))
            cur = perceive()   # el zoom movio todo en pantalla: se vuelven a ubicar monstruos y etiquetas
            if cur is None:
                return None
        if on_phase is not None:
            on_phase("rueda", cur)
        return self.move_down(cur, perceive, gi, sleep, down_px, lim)

    def reset(self, perceive: Callable[[], Any], gi: Any, sleep: Callable[[float], None], t: float, why: str) -> bool:
        """Reinicio desde el tope (`to_top`: arrastre al tope `limit`, rueda lejos, `from_limit_px` hacia el angulo normal). `perceive()` captura y devuelve
        (img, Perception) o None (ventana minimizada, captcha: se corta). True si llego hasta el final."""
        if self.to_top(perceive, gi, sleep, t, why, int(self.c["from_limit_px"])) is None:
            return False
        sleep(float(self.cfg["explore"]["settle_s"]))
        self.resets += 1
        if why == "piso":
            after = perceive()
            if after is not None and looks_at_floor(after[0], self.cfg, after[1].mask, after[1].player_xy):
                self.fruitless += 1
                logger.info("Camara: despues del reinicio sigue todo piso (terreno alto): el proximo reinicio por piso espera %.0f s",
                            self.floor_gap_s())
            elif after is not None:
                self.fruitless = 0
        return True
