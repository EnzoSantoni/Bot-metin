"""Acercarse a un metin con el teclado, ritmo de clics y recuperacion del panel (mixin de `Bot`).

Regla: nunca se hace clic con el personaje en movimiento ni dos clics izquierdos a menos de `attack.min_click_gap_s`. Un metin
objetivo en pantalla y CERCA (distancia de piso <= `approach.near_floor`) se ataca con un clic directo en su cuerpo, sin girar
la vista para centrarlo. Uno lejano se alcanza asi: alinear la vista con Q/E hasta que la piedra quede cerca del centro
horizontal, caminar con W en tramos cortos re-detectando la piedra, frenar al llegar a `near_floor` y, QUIETO, hacer UN clic
en el centro del cuerpo de la piedra (nunca en el nombre ni en la franja de abajo). Sin confirmar, ATACAR reclickea quieto
(`Bot._quick_check` / `Bot._reclick`, que respetan el mismo ritmo).

Panel ausente (error del juego: atacando un metin a veces no aparece la barra de vida): `_try_recover` hace UN clic derecho
(apretar y soltar sin mover) sobre el cuerpo de un monstruo cercano; el juego muestra su nombre y vida y, si se esta atacando un
metin, el panel vuelve a mostrar la vida del metin (`_recover_check`).

Las funciones usan atributos de `Bot` (self.cfg, self.gi, self.tracker, ...). `State` y `LEG_MAX_AGE_S` se importan adentro de los
metodos (bot.bot importa este modulo).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from bot.ground import depth_px, feet_of, ground_dist, to_ground
from bot.tracking import CoordTracker
from bot.vision.far_metin import FarMetin
from bot.vision.metin import MetinDetection
from bot.vision.monsters import monsters_near

logger = logging.getLogger(__name__)

Xy = tuple[float, float]
_NO_RECOVERY = object()   # `_recover_check`: nada que resolver, el ciclo de ATACAR sigue


@dataclass
class _Tgt:
    """La piedra que se persigue, en la captura de ahora."""
    base: tuple[int, int]               # base en pantalla (para medir distancia de piso y centrar la vista)
    body: tuple[int, int]               # centro del cuerpo de la piedra (donde va el clic)
    det: MetinDetection | None = None   # etiqueta (None = solo se ve la piedra, sin nombre)
    far: FarMetin | None = None


class ApproachKeysMixin:
    # ------------------------------------------------------------------ quieto / ritmo de clics
    def _note_still(self) -> None:
        """Cuenta lecturas seguidas de coordenadas en la misma posicion (el personaje esta quieto con `approach.quiet_reads`)."""
        if self.tracker.last_outcome != CoordTracker.OK:
            return
        pos = self.tracker.position
        if pos is not None and pos == self._still_pos:
            self._still_n += 1
        elif self._still_pos is None:   # primera lectura: no hay con que compararla, se supone quieto
            self._still_pos, self._still_n = pos, int(self.apr["quiet_reads"])
        else:
            self._still_pos, self._still_n = pos, 1

    def _is_moving(self, now: float | None = None) -> bool:
        """El personaje se mueve: se mando una tecla de movimiento hace menos de `approach.settle_s` o la ultima lectura de
        coordenadas cambio (menos de `approach.quiet_reads` lecturas iguales). Sin coordenadas solo cuenta el tiempo."""
        now = self.clock() if now is None else now
        if now - self._move_cmd_t < float(self.apr["settle_s"]):
            return True
        if self.tracker.position is None:
            return False
        return self._still_n < int(self.apr["quiet_reads"])

    def _click_gate(self) -> str | None:
        """Motivo por el que NO se puede hacer un clic izquierdo de ataque ahora (None = se puede): 'movimiento' (coordenadas
        cambiando o tecla de movimiento reciente) o 'ritmo' (menos de `attack.min_click_gap_s` del clic anterior)."""
        now = self.clock()
        if self._is_moving(now):
            return "movimiento"
        if now - self._last_click_t < float(self.atk["min_click_gap_s"]):
            return "ritmo"
        return None

    def _crowded(self, p: Any) -> bool:
        """Bichos reales: nombres rojos pegados al personaje o >= `stuck.monsters_min` cerca. Condicion de ESPACIO."""
        return p.monsters_on_me > 0 or p.monsters_near >= max(1, int(self.stuck["monsters_min"]))

    def _off_name(self, point: tuple[int, int], p: Any) -> tuple[int, int]:
        """Si `point` cae sobre un nombre de metin (o en la franja de `attack.name_gap_px` debajo de el) lo baja hasta quedar justo
        debajo de esa franja (nunca se hace clic en el nombre)."""
        gap, m = int(self.atk["name_gap_px"]), 8
        for d in p.metins:
            r = d.label_rect
            if r.x - m <= point[0] < r.right + m and r.y - m <= point[1] < r.bottom + gap:
                fixed = (point[0], r.bottom + gap)
                if p.mask.allows(*fixed):
                    logger.info("Punto de clic %s sobre el nombre del metin: lo bajo a %s", point, fixed)
                    return fixed
        return point

    def _cool_down(self, gp: Xy | None, screen: tuple[int, int] | None, t: float, why: str) -> None:
        """Descarte CORTO (`approach.retry_ignore_s`) de un metin con nombre que no se pudo atacar: se elige otro sin darlo por
        perdido 10 minutos."""
        secs = float(self.apr["retry_ignore_s"])
        spots = [self._new_spot(gp, screen, t, secs / 60)]
        if gp is None:   # sin camara: ademas el lugar (el punto de pantalla se corre cuando el personaje se mueve)
            spots.append(self._new_spot(None, None, t, secs / 60, locate=False))
        self.discarded.extend(s for s in spots if s is not None)
        logger.info("Metin apartado %.0f s (%s): elijo otro, no lo descarto", secs, why)
        self._dbg("descarte", f"apartado {secs:.0f} s: {why}")

    def _give_up(self, a: Any, why: str, p: Any, t: float) -> Any:
        """Tope de clics sin confirmar: se aparta el metin un rato y se elige otro (no se descarta)."""
        from bot.bot import State
        self._cool_down(a.gp, a.click, t, why)
        self._atk = None
        return State.BUSCAR, f"sin confirmar: {why}"

    # ------------------------------------------------------------------ panel ausente: clic derecho
    def _monster_body_point(self, a: Any, p: Any) -> tuple[int, int] | None:
        """Punto en el CUERPO de un monstruo cercano (debajo de su etiqueta): el mas cercano al personaje cuyo punto este
        permitido, lejos del metin y fuera de la etiqueta propia. None si no hay."""
        cpt = self.cfg["vision"]["click_point"]
        avoid = 2.0 * float(self.atk["monster_clear_px"])
        for m in sorted(monsters_near(p.monsters, p.player_xy, cfg=self.cfg), key=lambda m: math.dist(m.center, p.player_xy)):
            body_h = max(float(cpt["body_h_min_px"]), float(cpt["body_h_k"]) * depth_px(m.rect.bottom, p.shape, self.cfg))
            pt = (m.center[0], m.rect.bottom + int(body_h * 0.5))
            if not p.mask.allows(*pt) or math.dist(pt, a.click) < avoid:
                continue
            if p.own_label is not None and p.own_label.x <= pt[0] < p.own_label.right and p.own_label.y <= pt[1] < p.own_label.bottom:
                continue
            return pt
        return None

    def _try_recover(self, a: Any, p: Any, t: float, why: str) -> bool:
        """Sin panel con el personaje quieto y pegado al metin: UN clic derecho (por metin) sobre un monstruo cercano para que el
        juego muestre su nombre y vida; si se ataca un metin el panel vuelve a mostrar el del metin (`_recover_check`). True si
        hizo el clic (el ciclo esta usado)."""
        cm = a.cm
        if (not self.atk["panel_recover"] or cm is None or cm.recovered or p.monsters_on_me > 0
                or math.dist(a.click, p.player_xy) > float(self.atk["arrive_px"]) or self._click_gate() is not None):
            return False
        pt = self._monster_body_point(a, p)
        if pt is None:
            return False   # sin monstruo cerca no hay a quien seleccionar: sigue el reclic normal
        if not self.gi.right_click(pt[0], pt[1], p.mask):
            return False
        cm.recovered = True
        a.recover_t = self._last_click_t = self.clock()
        self._planned = pt
        logger.info("Sin panel (%s) con el personaje quieto y pegado al metin: clic derecho sobre un monstruo en %s para ver si "
                    "el panel vuelve al metin (espero %.1f s)", why, pt, float(self.atk["recover_wait_s"]))
        self._dbg("recuperar", f"clic derecho en {pt}: {why}")
        return True

    def _recover_check(self, a: Any, kind: str | None, p: Any, t: float) -> Any:
        """Espera tras el clic derecho: panel con el metin -> estaba atacando (sigue el ciclo normal); pasado
        `attack.recover_wait_s` sin que vuelva -> no ataca, reclic izquierdo al metin (quieto, con el ritmo de siempre)."""
        if kind == "target_metin":
            a.recover_t = None
            logger.info("El panel volvio a mostrar el metin: estaba atacando, sigo")
            return _NO_RECOVERY
        if self.clock() - a.recover_t < float(self.atk["recover_wait_s"]):
            return None   # el panel muestra al monstruo (o nada): no cuenta como seleccion equivocada ni como panel perdido
        a.recover_t = None
        a.missing = a.gone = 0
        logger.info("El panel no volvio al metin en %.1f s: no esta atacando, clic izquierdo al metin", float(self.atk["recover_wait_s"]))
        a.pending = ("el panel no volvio tras el clic derecho", False)
        return None

    # ------------------------------------------------------------------ acercarse con teclado
    def _keys_approach(self, floor_d: float) -> bool:
        """El metin a distancia de piso `floor_d` se alcanza con teclado (alinear, W, frenar, clic quieto): modo teclado, con
        coordenadas y mas lejos que `approach.near_floor` (mas cerca: clic directo, sin girar la vista)."""
        return (self.kmode and bool(self.apr["keys_enabled"]) and floor_d > float(self.apr["near_floor"])
                and self.tracker.position is not None)

    def _start_walk(self, g: Any, ranked: list, i: int, p: Any, t: float) -> Any:
        """Etiqueta objetivo lejos: arranca ACERCARSE con teclado (None si ya se agotaron las aproximaciones a esa posicion)."""
        from bot.bot import State, _Approach
        c = g.ref
        spot = self._register_approach(c.gp, c.det.click_point, p, t)
        if spot is None:
            self._cool_down(c.gp, c.det.click_point, t, "demasiadas aproximaciones")
            return None
        self._log_choice(ranked, i)
        self._no_target_t = t
        self._apr = _Approach(t, c.gp, c.det.click_point, spot=spot, point=self._aim(c.det, p), floor_d=ranked[i][1], walk=True)
        est = "" if c.gp is None else f", est. ({c.gp[0]:.0f}, {c.gp[1]:.0f})"
        return State.ACERCARSE, f"{c.det.name} lejos (piso {ranked[i][1]:.1f}): alineo la vista, camino y clic quieto{est}"

    def _follow_target(self, p: Any, t: float, a: Any) -> tuple[_Tgt | None, Any]:
        """(piedra de ahora, transicion): la etiqueta o la piedra lejana mas cercana al ultimo punto visto (a <=
        `approach.match_px`). Etiqueta de otro tipo -> se ignora la posicion y se elige otro; etiqueta objetivo que ya no es
        elegible (descartada, fuera de zona) -> BUSCAR. Sin nada a la vista: (None, None)."""
        from bot.bot import State
        lim = float(p.shape[1]) if a.rematch else float(self.apr["match_px"])   # tras un giro la piedra salta de lugar
        labels = [d for d in p.metins if math.dist(d.click_point, a.last_screen) <= lim]
        if labels:
            d = min(labels, key=lambda d: math.dist(d.click_point, a.last_screen))
            if not d.is_target:
                gp = self._game_pos_of(d.click_point, p) or a.gp
                self._ignore_position(gp, t, f"otro tipo ({d.name})", d.click_point)
                return None, self._after_reject(p, t, f"piedra de otro tipo ({d.name}): posicion ignorada")
            if next((c for c in self._candidates(p, t) if c.det is d), None) is None:
                self._apr = None
                return None, (State.BUSCAR, f"el metin {d.name} ya no es elegible")
            a.rematch = False
            return _Tgt(d.click_point, self._aim(d, p), det=d), None
        stones = [f for f in p.far_metins if f.allowed and math.dist(f.center, a.last_screen) <= lim]
        if stones:
            f = min(stones, key=lambda f: math.dist(f.center, a.last_screen))
            a.rematch = False
            return _Tgt(f.center, f.rect.center, far=f), None
        return None, None

    def _bearing_to(self, base: tuple[int, int], p: Any) -> float:
        """Angulo (rad, positivo = derecha) que hay que girar la vista para que `base` quede de frente al personaje."""
        fx, fz = to_ground(feet_of(p.player_xy, self.cfg), p.shape, self.cfg)
        gx, gz = to_ground(base, p.shape, self.cfg)
        return math.atan2(gx - fx, gz - fz)

    def _acercarse_keys(self, p: Any, t: float) -> Any:
        """Un ciclo del acercamiento con teclado (ver el docstring del modulo). Devuelve una transicion o None. Con
        `a.walk` apagado el llamador sigue con el hover de siempre (piedra cerca y sin nombre: se lee su etiqueta)."""
        from bot.bot import LEG_MAX_AGE_S, State
        a = self._apr
        tgt, out = self._follow_target(p, t, a)
        if out is not None or self._apr is not a:
            return out
        if tgt is None:
            a.lost += 1
            if a.lost >= int(self.apr["lost_cycles"]):   # se perdio de vista (p. ej. al girar): NO se ignora
                self._apr = None
                return State.BUSCAR, "se perdio la piedra de vista"
            return None
        a.lost = 0
        a.last_screen, a.point = tgt.base, tgt.body
        a.gp = self._game_pos_of(tgt.base, p) or a.gp
        if a.gp is not None and not self._metin_accepts(a.gp):
            self._ignore_position(a.gp, t, "piedra lejana fuera de zona", a.last_screen)
            self._apr = None
            return State.BUSCAR, "piedra lejana fuera de la zona"
        if t - a.start_t > float(self.apr["max_seconds"]):
            self._cool_down(a.gp, a.last_screen, t, "tiempo maximo acercandose")
            self._apr = None
            return State.BUSCAR, "tiempo maximo acercandose a la piedra"
        floor_d = ground_dist(feet_of(p.player_xy, self.cfg), tgt.base, p.shape, self.cfg)
        a.floor_d = floor_d
        pos = self.tracker.position
        if pos is None:
            a.lost += 1
            if a.lost >= int(self.apr["lost_cycles"]):
                self._apr = None
                return State.BUSCAR, "sin coordenadas para acercarme"
            return None
        leg, self._leg = self._leg, None
        if leg is not None and t - leg.end_t <= LEG_MAX_AGE_S and self.tracker.last_outcome == CoordTracker.OK:
            res = self._after_leg(leg, pos)   # alimenta a keynav (rumbo, velocidad) y cuenta tramos sin avanzar
            if res == "cut":
                self._apr = None
                return State.BUSCAR, "acercamiento cortado: cerca del limite de la zona"
            if res == "stuck":
                if self._crowded(p):
                    logger.info("Acercandome: W sin avanzar con %d bichos cerca (%d encima): ESPACIO y sigo", p.monsters_near,
                                p.monsters_on_me)
                    self.stats.surrounded += 1
                    if not self._hold_space(float(self.stuck["surrounded_space_s"])):
                        return None
                    self.tracker.forget_reference()
                    self._zero_streak = 0
                    return None
                if floor_d > float(self.apr["stuck_click_floor"]):
                    logger.info("Acercandome: W sin avanzar y sin bichos (pared o terreno), piso %.1f: cancelo el acercamiento", floor_d)
                    self._apr = None
                    return State.BUSCAR, "trabado yendo a la piedra"
                logger.info("Acercandome: W sin avanzar y sin bichos pero a piso %.1f: hago el clic desde aca", floor_d)
                a.force_click = True
                self._zero_streak = 0
        near = floor_d <= float(self.apr["near_floor"]) or a.force_click   # cerca: clic directo, sin alinear la vista
        dx = tgt.base[0] - p.player_xy[0]
        if a.last_dx is not None:   # un giro de alineacion ya dado: si alejo la piedra del centro, Q/E esta al reves
            if abs(dx) > abs(a.last_dx) + 0.25 * float(self.apr["center_tol_px"]):
                a.turn_sign = -a.turn_sign
                logger.info("El giro alejo la piedra del centro (%.0f -> %.0f px): invierto el sentido de Q/E", a.last_dx, dx)
            a.last_dx = None
        if not near:
            return self._approach_move(p, a, pos, floor_d, dx)
        return self._approach_click(p, t, a, tgt, floor_d)

    def _approach_move(self, p: Any, a: Any, pos: tuple[int, int], floor_d: float, dx: float) -> Any:
        """Lejos: alinear la vista (Q/E) si la piedra esta de costado y si no un tramo corto de W."""
        from bot.bot import State
        tol = float(self.apr["center_tol_px"])
        if abs(dx) <= tol:
            a.turns = 0
        elif a.turns < int(self.apr["align_turns_max"]):
            if not self._turn_ready():
                return None   # se espera la imagen del giro anterior
            delta = self._bearing_to(a.last_screen, p) * a.turn_sign
            key, secs = self.keynav.plan_turn(delta)
            logger.info("Acercandome: la piedra esta a %+.0f px del centro (piso %.1f): giro la vista %.0f grados (%s %.2f s)",
                        dx, floor_d, math.degrees(delta), key, secs)
            a.turns += 1
            a.last_dx = dx
            a.rematch = True
            if abs(delta) <= self.keynav.max_turn:   # giro completo: la piedra queda de frente (misma altura en pantalla)
                a.last_screen = (p.player_xy[0], a.last_screen[1])
            self._search_turn(key, secs)
            return None
        near_f = float(self.apr["near_floor"])
        chunk = float(self.apr["walk_chunk_s"]) * (0.5 if floor_d < near_f + 2.0 else 1.0)   # tramos cortos cerca del freno
        if self._step(pos, "W", chunk, None) == "blocked":
            self._apr = None
            return State.BUSCAR, "no entra ni un tramo de W antes del limite de la zona"
        self._move_cmd_t = self.clock()
        return None

    def _approach_click(self, p: Any, t: float, a: Any, tgt: _Tgt, floor_d: float) -> Any:
        """Cerca: frenado (sin W) y, quieto y con el ritmo cumplido, UN clic en el cuerpo de la piedra de esta captura."""
        from bot.bot import State
        if self._click_gate() is not None:
            return None   # todavia frenando, o clic reciente
        if tgt.det is None:
            a.walk = False   # piedra sin nombre: el hover de siempre lee la etiqueta antes de clickear
            return None
        cand = next((c for c in self._candidates(p, t) if c.det is tgt.det), None)
        if cand is None:
            self._apr = None
            return State.BUSCAR, "el metin ya no es elegible"
        point = self._clear_point(tgt.body, p)
        if not self._click_attack(point, cand.gp, p, t, floor_d):
            return None
        logger.info("Llegue (piso %.1f): quieto, un clic en el cuerpo de %s en %s", floor_d, tgt.det.name, point)
        self._clear_tries(a)
        return State.ATACAR, f"quieto frente al metin ({tgt.det.name}): clic"
