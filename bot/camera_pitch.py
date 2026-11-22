"""Angulo vertical de la camara: reinicio con TECLAS (sin arrastres del mouse ni medir el horizonte para saber el angulo).

Datos comprobados por Enzo en el juego:
- G sube el plano de la camara (vista desde arriba, se ve el piso); T lo baja (perspectiva mas plana).
- Si se mantiene T mucho, llega a plano y la camara se ACERCA al personaje (el mismo zoom que hacia el arrastre del mouse
  contra el tope horizontal). Ese zoom NO se va al volver con G: solo se saca con la rueda hacia "lejos".

`reset()`: (a) rueda hacia "lejos" hasta el tope (`zoom_out_sign` x `zoom_out_clicks`); (b) mantener G `g_hold_s` (hasta el tope
de la vista desde arriba, con margen); (c) `t_taps` toques cortos de T (`t_tap_ms` cada uno, `t_gap_ms` entre toques) para bajar
al angulo de farmeo, NUNCA T mantenida hasta abajo; (d) rueda hacia "lejos" otra vez, por seguridad. `t_taps` se calibra con
`calibrate.py camara`.

Con `camera_pitch.enabled` false el bot no toca la camara para nada. El juego sube la camara solo al acercarse a bordes o
montanas y no la devuelve: por eso se reinicia al arrancar, tras cada metin roto, cada `every_s` y cuando la camara mira el piso
(`looks_at_floor`: bot/vision/pitch.py solo como detector de "todo piso", no como medida del angulo).
"""
from __future__ import annotations

import copy
import logging
import math
from typing import Any, Callable

from bot.vision.metin import player_point
from bot.vision.pitch import measure_horizon

logger = logging.getLogger(__name__)

TOP_ACTION = "camera_top"     # controls: G, vista desde arriba
FLAT_ACTION = "camera_flat"   # controls: T, perspectiva mas plana
BACKOFF_MAX = 8   # tope del multiplo de `min_gap_s` tras reinicios por piso que no arreglaron nada


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
    captura y percibe (para la mascara de la rueda) y una de espera."""

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.c = cfg["camera_pitch"]
        self.last_reset_t = -math.inf
        self.floor_streak = 0      # capturas seguidas mirando al piso
        self.fruitless = 0         # reinicios por piso seguidos que no sacaron la vista del piso
        self.actions = 0           # acciones mandadas (rueda, G, toques de T)
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

    # --- pasos del reinicio ---
    def wheel_out(self, perceive: Callable[[], Any], gi: Any) -> bool:
        """Rueda hacia "lejos" hasta el tope (`zoom_out_sign` x `zoom_out_clicks`) sobre el personaje. False si se corto."""
        clicks = int(self.c["zoom_out_clicks"])
        if clicks <= 0:
            return True
        cur = perceive()
        if cur is None:
            return False
        wheel = int(self.c["zoom_out_sign"]) * clicks
        if not gi.zoom(wheel, cur[1].mask):
            logger.warning("Camara: no se pudo girar la rueda (zoom lejos)")
            return False
        self.actions += 1
        return True

    def top(self, perceive: Callable[[], Any], gi: Any, sleep: Callable[[float], None]) -> bool:
        """Pasos (a) y (b): rueda lejos y G mantenida `g_hold_s` hasta el tope de la vista desde arriba. False si se corto."""
        if not self.wheel_out(perceive, gi):
            return False
        sleep(float(self.c["step_pause_s"]))
        if not gi.hold(TOP_ACTION, float(self.c["g_hold_s"])):
            return False
        self.actions += 1
        sleep(float(self.c["step_pause_s"]))
        return True

    def tap_down(self, gi: Any, sleep: Callable[[float], None], n: int) -> bool:
        """Paso (c): `n` toques cortos de T (`t_tap_ms`, `t_gap_ms` entre toques). Nunca T mantenida. False si se corto."""
        for _ in range(max(0, int(n))):
            if not gi.hold(FLAT_ACTION, float(self.c["t_tap_ms"]) / 1000):
                return False
            self.actions += 1
            sleep(float(self.c["t_gap_ms"]) / 1000)
        return True

    def run(self, perceive: Callable[[], Any], gi: Any, sleep: Callable[[float], None], taps: int | None = None) -> bool:
        """Reinicio completo (a-d) con `taps` toques de T (default `t_taps`). True si llego hasta el final."""
        n = int(self.c["t_taps"]) if taps is None else int(taps)
        if not self.top(perceive, gi, sleep) or not self.tap_down(gi, sleep, n):
            return False
        sleep(float(self.c["step_pause_s"]))
        return self.wheel_out(perceive, gi)   # (d) por seguridad: un toque de T de mas acerca la camara

    def reset(self, perceive: Callable[[], Any], gi: Any, sleep: Callable[[float], None], t: float, why: str) -> bool:
        """Reinicio de la camara (`run`). `perceive()` captura y devuelve (img, Perception) o None (ventana minimizada, captcha:
        se corta). True si llego hasta el final."""
        if not self.enabled:
            return False
        self.last_reset_t, self.floor_streak = t, 0
        c = self.c
        logger.info("Camara: reinicio (rueda %+d, G %.1f s, T x%d de %d ms, rueda %+d) [%s]",
                    int(c["zoom_out_sign"]) * int(c["zoom_out_clicks"]), float(c["g_hold_s"]), int(c["t_taps"]),
                    int(c["t_tap_ms"]), int(c["zoom_out_sign"]) * int(c["zoom_out_clicks"]), why)
        if not self.run(perceive, gi, sleep):
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
