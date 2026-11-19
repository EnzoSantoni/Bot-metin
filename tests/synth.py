"""Utilidades para armar imagenes sinteticas del panel y las coordenadas sobre samples reales."""
import copy

import cv2
import numpy as np

from bot.config import DEFAULTS
from bot.vision.digits import get_glyphs
from bot.vision.panel import read_panel

# Desplazamiento vertical de cada caracter respecto del tope de la linea (medido en las samples reales).
PANEL_DY = {"digit": 1, "/": 0, ".": 6}            # relativo a number_rect.y + 3
COORDS_DY = {"digit": 1, "(": 0, ")": 0, ",": 6}   # relativo a coords.rect.y


def make_cfg(targets=None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    if targets is not None:
        cfg["target_metin_names"] = list(targets)
    return cfg


def fill_rows(img, x0, y0, x1, y1, bg_x):
    """Borra el rectangulo [x0,x1) x [y0,y1) copiando, fila por fila, el color de la columna `bg_x`."""
    for y in range(y0, y1):
        img[y, x0:x1] = img[y, bg_x]


def draw_text(img, text, glyphs, color, x, y, dy_table, gap=1, space=3, dot_extra=1):
    """Dibuja `text` con plantillas; (x, y) es la esquina superior izquierda de la linea. Devuelve x final."""
    cur = x
    for ch in text:
        if ch == " ":
            cur += space
            continue
        t = glyphs.chars[ch]
        key = ch if ch in dy_table else "digit"
        if ch == ".":
            cur += dot_extra
        yy = y + dy_table[key]
        region = img[yy:yy + t.shape[0], cur:cur + t.shape[1]]
        region[t] = np.array(color, np.uint8)
        cur += t.shape[1] + gap
    return cur


def text_width(text, glyphs, gap=1, space=3, dot_extra=1):
    return sum(space if c == " " else glyphs.chars[c].shape[1] + gap + (dot_extra if c == "." else 0)
               for c in text) - gap


def set_panel_hp_text(img, text, cfg, gap=1):
    """Reemplaza el numero de vida del panel por `text` (centrado donde estaba el original)."""
    pi = read_panel(img, cfg)
    nr = pi.number_rect
    sec = cfg["vision"]["glyphs"]["panel"]
    g = get_glyphs(sec)
    fill_rows(img, nr.x, nr.y, nr.right, nr.bottom, nr.x)
    w = text_width(text, g, gap)
    draw_text(img, text, g, sec["color"], nr.x + (nr.w - w) // 2, nr.y + 3, PANEL_DY, gap)
    return pi


def set_bar_ratio(img, bar_rect, empty_profile, ratio):
    """Vacia la barra desde `ratio` hasta el final con el perfil de filas del recipiente vacio."""
    cut = bar_rect.x + int(round(ratio * bar_rect.w))
    for i, y in enumerate(range(bar_rect.y, bar_rect.y + len(empty_profile))):
        img[y, cut:bar_rect.right] = empty_profile[i]


def empty_bar_profile(combat_img, bar_rect, rows=7):
    """Color (por fila) del recipiente vacio, medido en el extremo derecho de la barra de combate."""
    sub = combat_img[bar_rect.y:bar_rect.y + rows, bar_rect.right - 20:bar_rect.right - 3]
    return np.median(sub, axis=1).astype(np.uint8)


def shifted(img, top=0, left=0, bottom=0, right=0):
    """Recorta/rellena bordes para simular samples de otro tamano (positivo = recorta, negativo = agrega)."""
    h, w = img.shape[:2]
    out = img[max(top, 0):h - max(bottom, 0), max(left, 0):w - max(right, 0)]
    pad = (max(-top, 0), max(-bottom, 0), max(-left, 0), max(-right, 0))
    if any(pad):
        out = cv2.copyMakeBorder(out, *pad, cv2.BORDER_REPLICATE)
    return out.copy()
