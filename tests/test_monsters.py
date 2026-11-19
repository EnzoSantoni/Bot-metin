"""Etiquetas de monstruos ("Lv XX" verde + nombre rojo). Verdad de referencia (recortes de ./samples):

- Cada etiqueta detectada en las samples poco pobladas se verifico a ojo (todas son etiquetas reales).
- real_rodeado: 8 etiquetas con mascara = 2 sueltas (izquierda (531,357) y derecha (1420,278)) + una pila
  de ~6 junto al personaje (se ven 5 legibles y al menos 1-2 tapadas debajo; las apiladas se fusionan).
  El personaje esta en (929, 548); su nombre amarillo "Lv 25 Farfarfa" (915, 423) no es monstruo.
- real_metin_lejos_1: 8 etiquetas (3 al medio + 5 abajo a la izquierda); hay un monstruo con la etiqueta
  cortada por el minimapa (borde derecho) que no se cuenta.
- metin_oscuridad_combate_vida_parcial: ~19 (no contado a mano: rango amplio).
"""
import time

import cv2
import numpy as np
import pytest

from bot.config import load_config
from bot.geometry import Rect
from bot.vision.metin import player_point
from bot.vision.monsters import count_near, find_monsters, monsters_near
from bot.vision.screen_mask import build_screen_mask

# sample -> (minimo, maximo) de etiquetas con la mascara de zonas
COUNTS = {
    "yang_en_piso.png": (2, 2),
    "metin_oscuridad_a_distancia.png": (3, 3),
    "real_inventario_abierto.png": (2, 2),
    "items_en_piso.png": (5, 5),
    "metin_avaricia_seleccionado.png": (7, 7),
    "real_metin_lejos_1.png": (8, 8),
    "real_rodeado.png": (7, 9),
    "metin_oscuridad_combate_vida_parcial.png": (14, 24),
}


@pytest.fixture(scope="module")
def cfg():
    return load_config()


def _find(img, cfg):
    return find_monsters(img, cfg, build_screen_mask(img.shape, cfg))


@pytest.mark.parametrize("name", sorted(COUNTS))
def test_cantidad_de_etiquetas(load_sample, cfg, name):
    lo, hi = COUNTS[name]
    n = len(_find(load_sample(name), cfg))
    assert lo <= n <= hi, f"{name}: {n}"


def test_las_etiquetas_tienen_forma_de_etiqueta(load_sample, cfg):
    for name in COUNTS:
        for mo in _find(load_sample(name), cfg):
            assert 3 <= mo.rect.h <= 16 and 30 <= mo.rect.w <= 280, (name, mo.rect)
            assert mo.rect.contains(*mo.center)


def test_rodeado_monstruos_cerca(load_sample, cfg):
    img = load_sample("real_rodeado.png")
    mons = _find(img, cfg)
    player = player_point(img.shape, cfg)
    near = monsters_near(mons, player, cfg=cfg)
    assert 5 <= len(near) <= len(mons)           # la pila (6); las 2 sueltas quedan lejos
    assert count_near(mons, player, cfg=cfg) == len(near)
    assert count_near(mons, player, cfg=cfg) >= 2                         # "rodeado"
    far = [m for m in mons if m not in near]
    assert len(far) >= 1 and all(abs(m.center[0] - 929) > 120 or m.center[1] < 430 for m in far)
    # radio explicito y valores extremos
    assert monsters_near(mons, player, radius_px=0) == []
    assert len(monsters_near(mons, player, radius_px=5000)) == len(mons)
    with pytest.raises(ValueError):
        monsters_near(mons, player)


def test_lejos_1_ninguno_cerca(load_sample, cfg):
    """Los monstruos de real_metin_lejos_1 estan lejos del personaje (929, 548): no hay 'rodeado'."""
    img = load_sample("real_metin_lejos_1.png")
    assert count_near(_find(img, cfg), player_point(img.shape, cfg), cfg=cfg) == 0


def test_nombre_amarillo_del_personaje_no_cuenta(load_sample, cfg):
    img = load_sample("real_rodeado.png")
    name_rect = Rect(880, 413, 100, 20)             # "Lv 25 Farfarfa" en amarillo
    assert not any(m.rect.intersect(name_rect) for m in _find(img, cfg))
    # y recortado y pegado en suelo limpio
    g = img[700:900, 100:430].copy()
    g[40:60, 20:120] = img[413:433, 880:980]
    assert find_monsters(g, cfg) == []


def test_etiqueta_naranja_de_metin_no_cuenta(load_sample, cfg):
    img = load_sample("real_metin_lejos_1.png")
    rod = load_sample("real_rodeado.png")
    g = rod[700:900, 100:430].copy()
    g[40:58, 20:150] = img[309:327, 340:470]         # "Metin de Oscuridad"
    g[100:118, 20:150] = rod[221:239, 915:1045]
    assert find_monsters(g, cfg) == []


def test_numeros_de_dano_no_cuentan(load_sample, cfg):
    g = load_sample("real_rodeado.png")[700:900, 100:430].copy()
    for i, col in enumerate(((0, 0, 230), (9, 22, 235), (0, 230, 255), (51, 255, 152))):
        cv2.putText(g, str(1234 * (i + 1)), (10 + 70 * i, 50), cv2.FONT_HERSHEY_DUPLEX, 1.2, col, 3)
        cv2.putText(g, "812", (30 + 60 * (i % 3), 120 + 15 * i), cv2.FONT_HERSHEY_SIMPLEX, 1.0, col, 4)
    assert find_monsters(g, cfg) == []
    # el "448" amarillo de la sample de combate no genera etiqueta propia
    img = load_sample("metin_oscuridad_combate_vida_parcial.png")
    assert not any(m.rect.intersect(Rect(1130, 190, 90, 40)) for m in _find(img, cfg))


def test_todas_las_etiquetas_tapadas_da_cero(load_sample, cfg):
    img = load_sample("real_rodeado.png").copy()
    ground = img[700:900, 100:430]
    for _ in range(4):                               # las tapadas aparecen al quitar las de arriba
        for m in find_monsters(img, cfg):
            r = m.rect
            x0, y0 = max(0, r.x - 8), max(0, r.y - 6)
            patch = np.tile(ground[:1, :1], (r.bottom + 6 - y0, r.right + 8 - x0, 1))
            img[y0:r.bottom + 6, x0:r.right + 8] = patch
    img[440:510, 730:1040] = ground[:1, :1]          # pila de etiquetas
    assert find_monsters(img, cfg) == []
    assert find_monsters(img, cfg, build_screen_mask(img.shape, cfg)) == []


def test_zonas_excluidas_filtran(load_sample, cfg):
    """Sin mascara aparecen las etiquetas del chat/log (yang_en_piso: 4 sin mascara, 2 con mascara)."""
    img = load_sample("yang_en_piso.png")
    assert len(find_monsters(img, cfg)) > len(_find(img, cfg))


def test_imagen_vacia_o_negra(cfg):
    assert find_monsters(np.zeros((0, 0, 3), np.uint8), cfg) == []
    assert find_monsters(np.zeros((100, 100, 3), np.uint8), cfg) == []


@pytest.mark.parametrize("dx,dy", [(0, 0), (4, 0), (0, 9), (12, 12)])
def test_desplazamientos_por_borde(load_sample, cfg, dx, dy):
    img = load_sample("real_rodeado.png")
    base = find_monsters(img, cfg)
    shifted = find_monsters(img[dy:, dx:], cfg)
    assert len(shifted) == len(base)
    for a, b in zip(base, shifted):
        assert abs(a.center[0] - dx - b.center[0]) <= 1 and abs(a.center[1] - dy - b.center[1]) <= 1


def test_tiempo(load_sample, cfg):
    img = load_sample("metin_oscuridad_combate_vida_parcial.png")
    mask = build_screen_mask(img.shape, cfg)
    find_monsters(img, cfg, mask)
    ts = []
    for _ in range(5):
        t = time.perf_counter()
        find_monsters(img, cfg, mask)
        ts.append(time.perf_counter() - t)
    print("monsters ms:", min(ts) * 1000)
    assert min(ts) < 0.05     # objetivo < 10 ms; limite laxo
