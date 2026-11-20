"""Puente entre `SimWorld` y `Bot`: reloj unico, backend que traduce input a acciones del simulador y percepcion
armada desde el estado del mundo. Nada de input real: todo pasa por `FakeBackend`."""
from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass

import numpy as np

from bot.bot import Bot
from bot.config import DEFAULTS
from bot.control import PanicStop
from bot.geometry import Rect
from bot.input.base import parse_keys
from bot.input.fake import FakeBackend, FakeWindow
from bot.input.game_input import GameInput
from bot.perception import Perception
from bot.vision.captcha import CaptchaInfo
from bot.vision.coords import Coords
from bot.vision.death import DeathInfo
from bot.vision.far_metin import FarMetin
from bot.vision.items import ItemLabel, choose_item
from bot.vision.metin import MetinDetection, choose_target, player_point, target_slugs
from bot.vision.monsters import MonsterLabel, count_near
from bot.vision.panel import PanelInfo
from bot.vision.screen_mask import build_screen_mask
from bot.zone import FarmZone
from sim_game import SimWorld, make_a0

W, H = 1858, 1057
CLIENT = Rect(8, 31, W, H)
LABEL_W, LABEL_H = 80, 10
CAPTCHA_RECT = Rect(1600, 400, 152, 194)
DEATH_RECT = Rect(10, 10, 100, 20)
HOVER_PX = 40   # radio alrededor del cuerpo de una piedra lejana donde el mouse hace aparecer su etiqueta


class SimControl:
    """Duck typing de `BotControl` con el reloj del simulador: `sleep` avanza el mundo."""

    def __init__(self, world: SimWorld) -> None:
        self.world = world
        self._stopped = False
        self._callbacks: list = []
        self.panic_t: float | None = None
        self.panic_reason: str | None = None
        self.on_panic_index: int | None = None   # largo de `backend.events` en el momento del panico
        self.backend: FakeBackend | None = None

    @property
    def is_stopped(self) -> bool:
        return self._stopped

    @property
    def is_paused(self) -> bool:
        return False

    def add_stop_callback(self, fn) -> None:
        self._callbacks.append(fn)

    def add_pause_callback(self, on_pause, on_resume) -> None:
        pass

    def panic(self, reason: str) -> None:
        if self._stopped:
            return
        self._stopped = True
        self.panic_t, self.panic_reason = self.world.t, reason
        self.on_panic_index = len(self.backend.events) if self.backend is not None else None
        for fn in self._callbacks:
            fn()

    def check(self) -> None:
        if self._stopped:
            raise PanicStop()

    def sleep(self, seconds: float) -> None:
        self.check()
        self.world.advance(max(0.0, float(seconds)))
        self.check()


class SimAlarm:
    """Alarma falsa: anota (t, accion, motivo)."""

    def __init__(self, world: SimWorld) -> None:
        self.world = world
        self.log: list[tuple[float, str, str | None]] = []
        self.is_active = False

    def start(self, reason: str) -> None:
        self.is_active = True
        self.log.append((self.world.t, "start", reason))

    def stop(self) -> None:
        self.is_active = False
        self.log.append((self.world.t, "stop", None))


class SimBackend(FakeBackend):
    """FakeBackend que ademas mueve el simulador. Anota el tiempo simulado de cada evento en `event_t`."""

    def __init__(self, world: SimWorld, window: FakeWindow, cfg: dict) -> None:
        super().__init__()
        self.world, self.window = world, window
        self.event_t: list[float] = []
        self.player = player_point((H, W), cfg)
        c = cfg["controls"]
        self.k_space = parse_keys(c["attack_forward"])[0]
        self.k_pickup, self.k_deselect = parse_keys(c["pickup"])[-1], parse_keys(c["deselect"])[-1]
        self.k_mount = parse_keys(c["mount"])
        self.k_cam = {parse_keys(c["camera_left"])[0]: +1, parse_keys(c["camera_right"])[0]: -1}
        self.k_move = {parse_keys(c[f"move_{d}"])[0]: d for d in ("up", "down", "left", "right")}
        self.cursor = (0, 0)

    def _log(self, *ev) -> None:
        self.events.append(ev)
        self.event_t.append(self.world.t)

    def _move_to(self, x: int, y: int) -> None:
        self.cursor = (x, y)
        self._log("move_to", x, y)

    def _mouse_down(self, button: str) -> None:
        self._log("mouse_down", button)
        if button == "left":
            cx, cy = self.window.screen_to_client(*self.cursor)
            self.world.click_screen((cx - self.player[0], cy - self.player[1]))

    def _mouse_up(self, button: str) -> None:
        self._log("mouse_up", button)

    def _key_down(self, key: str) -> None:
        self._log("key_down", key)
        w = self.world
        if key == self.k_pickup:
            w.press_pickup()
        elif key == self.k_deselect:
            w.deselect()
        elif key == self.k_mount[-1] and all(k in self._keys_down for k in self.k_mount):
            w.toggle_mount()
        elif key in self.k_cam:
            w.rot_dir = self.k_cam[key]
        elif key in self.k_move:
            w.key_move(self.k_move[key])
        elif key == self.k_space:
            w.space_held = True
            if w.t - w.space_up_t > w.space_gap_tol:   # soltarla un instante entre tramos no corta el empujon
                w.space_t = 0.0

    def _key_up(self, key: str) -> None:
        self._log("key_up", key)
        if key in self.k_cam:
            self.world.rot_dir = 0
        elif key in self.k_move:
            self.world.key_move(None)
        elif key == self.k_space:
            self.world.space_held, self.world.space_up_t = False, self.world.t

    def _scroll(self, clicks: int) -> None:
        self._log("scroll", clicks)

    def input_times(self, kinds=("move_to", "mouse_down", "key_down", "scroll")) -> list[float]:
        """Tiempos simulados de los eventos que son 'input nuevo' (no sueltas)."""
        return [t for t, e in zip(self.event_t, self.events) if e[0] in kinds]


def make_cfg(**over) -> dict:
    """Config de test: tiempos de input en 0 (el simulador corre rapido)."""
    cfg = copy.deepcopy(DEFAULTS)
    cfg["navigation"]["mode"] = "clic"   # los tests de fondo son del modo clic; los del teclado usan make_cfg(navigation={"mode": "teclado"})
    cfg["input"].update(delay_ms=[0, 0], key_hold_ms=[0, 0], move_settle_ms=[0, 0], click_jitter_px=0)
    cfg["safety"].update(hard_kill_after_s=5.0, countdown_s=0)
    for sec, vals in over.items():
        cfg[sec].update(vals)
    return cfg


@dataclass
class Transition:
    t: float
    old: str
    new: str
    reason: str


class SimEnv:
    """Mundo + Bot completo. `world` se llena desde el test; `run_until` corre el bot ciclo a ciclo."""

    def __init__(self, seed: int = 0, *, phi: float | None = None, a0=None, start=(550.0, 150.0),
                 cfg: dict | None = None, coords_noise: float = 0.0, **world_kw) -> None:
        self.rng = random.Random(seed)
        self.cfg = cfg or make_cfg()
        self.zone = FarmZone.from_config(self.cfg)
        phi = self.rng.uniform(-math.pi, math.pi) if phi is None else phi
        self.world = SimWorld(make_a0(0.2, 0.7) if a0 is None else a0, phi, start,
                              rng=random.Random(seed + 1), **world_kw)
        self.window = FakeWindow(CLIENT)
        self.backend = SimBackend(self.world, self.window, self.cfg)
        self.control = SimControl(self.world)
        self.control.backend = self.backend
        self.alarm = SimAlarm(self.world)
        self.gi = GameInput(self.window, self.backend, self.control, self.cfg, rng=random.Random(seed + 2))
        self.control.add_stop_callback(self.gi.release_all)
        self.mask = build_screen_mask((H, W), self.cfg)
        self.slugs = target_slugs(self.cfg)
        self.coords_noise = coords_noise
        self._noise_rng = random.Random(seed + 3)
        self.bot = Bot(self.cfg, self.window, self.gi, self.control, self.alarm, perceive_fn=self.perceive,
                       clock=lambda: self.world.t, sleep_fn=self.control.sleep, rng=random.Random(seed + 4),
                       captcha_fn=self._peek_captcha, coords_fn=self._peek_coords)
        self.transitions: list[Transition] = []
        orig_enter = self.bot._enter

        def rec(new, reason):
            self.transitions.append(Transition(self.world.t, self.bot.state.name, new.name, reason))
            orig_enter(new, reason)
        self.bot._enter = rec
        self.panicked = False

    # --- lecturas rapidas entre tramos de una tecla sostenida (en vez de analizar la imagen negra) ---
    def _peek_captcha(self, img, cfg):
        return CaptchaInfo(CAPTCHA_RECT, 1.0) if self.world.captcha_active else None

    def _peek_coords(self, img, cfg):
        return Coords(*self.world.coords(), 1.0)

    # --- percepcion ---
    def perceive(self, img, cfg, *, full_captcha: bool = False, parts=None) -> Perception:
        w = self.world
        player = self.backend.player
        metins: list[MetinDetection] = []
        far: list[FarMetin] = []
        for m in w.metins:
            if not m.alive:
                continue
            off = w.visible_offset(m.pos)
            if off is None:   # fuera de la distancia de etiqueta: se ve la piedra, sin nombre
                foff = w.visible_offset(m.pos, w.far_dist)
                if foff is not None:
                    fc = (round(player[0] + foff[0]), round(player[1] + foff[1]))
                    if 40 <= fc[0] < W - 40 and 40 <= fc[1] < H - 40:
                        if self._hovered(fc):   # el mouse esta encima: el juego muestra su nombre aunque este lejos
                            off = foff
                        else:
                            far.append(FarMetin(Rect(fc[0] - 25, fc[1] - 60, 50, 70), fc, 0.9, self.mask.allows(*fc)))
                if off is None:
                    continue
            cp = (round(player[0] + off[0]), round(player[1] + off[1]))
            label = Rect(cp[0] - LABEL_W // 2, cp[1] - 65 - LABEL_H // 2, LABEL_W, LABEL_H)
            if label.x < 0 or label.y < 0 or label.right > W or label.bottom > H or not (0 <= cp[0] < W and 0 <= cp[1] < H):
                continue
            if self.mask.overlap_fraction(label) > cfg["vision"]["metin"]["max_zone_overlap"]:
                continue
            metins.append(MetinDetection(label, m.name, 1.0, m.name in self.slugs, cp, self.mask.allows(*cp)))
        items: list[ItemLabel] = []
        for it in w.items:
            off = w.visible_offset(it.pos)
            if off is None:
                continue
            cp = (round(player[0] + off[0]), round(player[1] + off[1]))
            rect = Rect(cp[0] - 40, cp[1] - 8, 80, 15)
            if rect.x < 0 or rect.y < 0 or rect.right > W or rect.bottom > H:
                continue
            items.append(ItemLabel(rect, it.kind, False, cp, self.mask.allows_rect(rect),
                                   0.95 if it.kind == "yang" else 0.0, reserved_other=it.reserved_other))
        monsters: list[MonsterLabel] = []
        for mp in w.monster_positions():
            moff = w.screen_offset_of(mp)
            mc = (round(player[0] + moff[0]), round(player[1] + moff[1] - 40))
            if 0 <= mc[0] < W and 0 <= mc[1] < H:
                monsters.append(MonsterLabel(Rect(mc[0] - 50, mc[1] - 6, 100, 12), mc))
        cx, cy = w.coords()
        if self.coords_noise and self._noise_rng.random() < self.coords_noise:
            cx, cy = cx + self._noise_rng.randint(-300, 300), cy + self._noise_rng.randint(-300, 300)
        return Perception(
            t=w.t, shape=(H, W, 3), mask=self.mask, windows=[], metins=metins,
            target=choose_target(metins, player), panel=self._panel(), coords=Coords(cx, cy, 1.0),
            items=items, item_choice=choose_item(items, player),
            captcha=CaptchaInfo(CAPTCHA_RECT, 1.0) if w.captcha_active else None,
            death=DeathInfo(DEATH_RECT, None, 1.0) if w.dead else None, player_xy=player,
            far_metins=far, monsters=monsters, monsters_near=count_near(monsters, player, cfg=cfg))

    def _hovered(self, base: tuple[int, int]) -> bool:
        """El cursor esta sobre el cuerpo de una piedra lejana (cuyo punto base es `base`): se ve su etiqueta."""
        cx, cy = self.window.screen_to_client(*self.backend.cursor)
        return math.hypot(cx - base[0], cy - (base[1] - 25)) <= HOVER_PX

    def _panel(self) -> PanelInfo | None:
        sel = self.world.selected
        if sel is None or self.world.captcha_active:
            return None
        rect = Rect(700, 5, 450, 60)
        kind, m = sel
        if kind == "monster":
            return PanelInfo(present=True, rect=rect, kind="other", name=None)
        return PanelInfo(present=True, rect=rect, kind="target_metin" if m.name in self.slugs else "other_metin",
                         name=m.name, hp_ratio=m.hp, hp_current=int(m.hp * m.max_hp), hp_max=m.max_hp)

    # --- ejecucion ---
    def zone_distance(self) -> float:
        """Distancia (positiva) a la que esta el personaje afuera del poligono (0 si esta adentro)."""
        return max(0.0, -self.zone.signed_distance(tuple(self.world.pos)))

    def track_zone(self) -> list[float]:
        """Registra en cada paso del simulador cuanto se alejo del poligono; devuelve la lista (el maximo = max())."""
        out: list[float] = [0.0]
        self.world.on_step.append(lambda w: out.append(self.zone_distance()))
        return out

    def run_until(self, cond=None, max_s: float = 600.0) -> str:
        """Corre ciclos hasta `cond()`, FIN, panico o `max_s` de tiempo simulado. Devuelve el motivo."""
        t_end = self.world.t + max_s
        try:
            while True:
                if cond is not None and cond():
                    return "cond"
                if self.bot.finished:
                    return "fin"
                if self.world.t >= t_end:
                    return "limite"
                self.bot.step()
        except PanicStop:
            self.panicked = True
            return "panico"

    def schedule_panic(self, t: float, reason: str = "test") -> None:
        self.world.schedule(t, lambda: self.control.panic(reason))

    def max_state_seconds(self, ignore=("CAPTCHA",)) -> dict[str, float]:
        """Mayor tiempo seguido en cada estado, segun las transiciones registradas (el ultimo tramo cuenta hasta ahora)."""
        out: dict[str, float] = {}
        cur, since = "BUSCAR", 0.0
        for tr in self.transitions + [Transition(self.world.t, cur, "FIN", "")]:
            if cur not in ignore:
                out[cur] = max(out.get(cur, 0.0), tr.t - since)
            cur, since = tr.new, tr.t
        return out

    def state_at(self, t: float) -> str:
        """Estado del bot en el tiempo simulado `t`."""
        cur = "BUSCAR"
        for tr in self.transitions:
            if tr.t > t:
                break
            cur = tr.new
        return cur

    def count_unstucks(self) -> int:
        return sum(1 for tr in self.transitions if tr.new == "DESTRABAR")
