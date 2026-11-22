"""Percepcion unificada: un solo objeto por ciclo con todo lo que se ve en una captura (sin input)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

from bot.geometry import Rect
from bot.vision.captcha import CaptchaInfo, find_captcha
from bot.vision.coords import Coords, read_coords
from bot.vision.death import DeathInfo, find_death
from bot.vision.far_metin import FarMetin, find_far_metins
from bot.vision.items import ItemLabel, choose_item, find_items
from bot.vision.metin import MetinDetection, choose_target, find_metins, player_point
from bot.vision.game_menu import GameMenu, find_game_menu
from bot.vision.monsters import MonsterLabel, count_near, find_monsters, find_own_label, monsters_on_me
from bot.vision.panel import PanelInfo, read_panel
from bot.vision.screen_mask import ScreenMask, build_screen_mask, find_ui_windows

ALL_PARTS = frozenset({"metins", "panel", "coords", "items", "far", "monsters"})


@dataclass
class Perception:
    """Resultado de `perceive`. Las partes salteadas quedan vacias ([]) o en None."""
    t: float
    shape: tuple[int, ...]
    mask: ScreenMask
    windows: list[Rect]
    metins: list[MetinDetection]
    target: MetinDetection | None
    panel: PanelInfo | None
    coords: Coords | None
    items: list[ItemLabel]
    item_choice: ItemLabel | None
    captcha: CaptchaInfo | None
    death: DeathInfo | None
    player_xy: tuple[int, int]
    timings: dict[str, float] = field(default_factory=dict)   # ms por parte
    far_metins: list[FarMetin] = field(default_factory=list)   # piedras sin etiqueta (parte "far")
    monsters: list[MonsterLabel] = field(default_factory=list)   # etiquetas de monstruos (parte "monsters")
    monsters_near: int = 0                                      # cuantos estan cerca del personaje
    own_label: Rect | None = None                               # etiqueta del propio personaje (nombre amarillo + Lv verde)
    monsters_on_me: int = 0                                     # etiquetas de monstruo solapadas o a <= on_me_px de la propia
    menu: GameMenu | None = None                                # menu de opciones del juego abierto (boton Cancelar)


def label_exclusions(metins: list[MetinDetection], cfg: dict) -> list[Rect]:
    """Rectangulo de cada etiqueta de metin, extendido hacia abajo hasta cubrir su piedra (no es una piedra lejana)."""
    down = int(cfg["approach"]["label_exclude_down_px"])
    return [Rect(d.label_rect.x, d.label_rect.y, d.label_rect.w, d.label_rect.h + down) for d in metins]


def perceive(img: np.ndarray, cfg: dict, *, full_captcha: bool = False,
             parts: Iterable[str] | None = None, t: float | None = None) -> Perception:
    """Analiza una captura BGR. `parts` (subconjunto de metins/panel/coords/items/far/monsters) limita lo que se calcula;
    ventanas, mascara, captcha y muerte se calculan siempre."""
    wanted = ALL_PARTS if parts is None else frozenset(parts)
    unknown = wanted - ALL_PARTS
    if unknown:
        raise ValueError(f"partes desconocidas: {sorted(unknown)}")
    timings: dict[str, float] = {}
    t0 = time.perf_counter()

    def timed(name: str, fn):
        s = time.perf_counter()
        out = fn()
        timings[name] = (time.perf_counter() - s) * 1000
        return out

    windows = timed("windows", lambda: find_ui_windows(img, cfg))
    mask = timed("mask", lambda: build_screen_mask(img.shape, cfg, extra=windows))
    player = player_point(img.shape, cfg)
    metins: list[MetinDetection] = []
    target = None
    if "metins" in wanted:
        metins = timed("metins", lambda: find_metins(img, cfg, mask))
        target = choose_target(metins, player)
    panel = timed("panel", lambda: read_panel(img, cfg)) if "panel" in wanted else None
    coords = timed("coords", lambda: read_coords(img, cfg)) if "coords" in wanted else None
    items: list[ItemLabel] = []
    choice = None
    if "items" in wanted:
        items = timed("items", lambda: find_items(img, cfg, mask))
        choice = choose_item(items, player)
    monsters: list[MonsterLabel] = []
    if "monsters" in wanted:
        monsters = timed("monsters", lambda: find_monsters(img, cfg, mask))
    far: list[FarMetin] = []
    if "far" in wanted:
        far = timed("far", lambda: find_far_metins(img, cfg, mask, label_exclusions(metins, cfg), monsters))
    captcha = timed("captcha", lambda: find_captcha(img, cfg, full=full_captcha))
    death = timed("death", lambda: find_death(img, cfg))
    own = timed("own", lambda: find_own_label(img, cfg, player)) if "monsters" in wanted else None
    menu = timed("menu", lambda: find_game_menu(img, cfg))
    timings["total"] = (time.perf_counter() - t0) * 1000
    return Perception(time.monotonic() if t is None else t, tuple(img.shape), mask, windows, metins, target,
                      panel, coords, items, choice, captcha, death, player, timings,
                      far, monsters, count_near(monsters, player, cfg=cfg), own, len(monsters_on_me(monsters, own, cfg, player)),
                      menu)
