"""Piedras de metin lejanas (sin etiqueta): verdad de referencia anotada a mano sobre los recortes de ./samples.

Posicion = base de la piedra (`center`), con tolerancia. Todas las piedras de abajo se ven en la captura.
Conocido: `muerte.png` tiene una piedra (717, ~225) medio tapada por un personaje y con poco humo que NO se
detecta (captura faltante / limite del detector); por eso no se exige 0 detecciones ahi sino ninguna otra.
"""
import time

import cv2
import numpy as np
import pytest

from bot.config import load_config
from bot.geometry import Rect
from bot.vision.far_metin import find_far_metins
from bot.vision.screen_mask import build_screen_mask, find_ui_windows

# sample -> (x, y de la base, tiene_etiqueta)
STONES = {
    "captcha.png": [(900, 252, True)],
    "explorando_zona.png": [(967, 422, True)],
    "metin_cerca_sin_seleccionar.png": [(852, 429, True)],
    "metin_muy_lejos.png": [(224, 241, False)],                      # Metin Negro sin humo (2o criterio)
    "metin_oscuridad_a_distancia.png": [(1472, 277, True), (166, 413, True)],
    "metin_oscuridad_seleccionado_vida_llena.png": [(949, 302, True)],
    "real_inventario_abierto.png": [(1737, 454, True)],
    "real_metin_lejos_1.png": [(339, 230, False), (1386, 231, False), (398, 393, True)],
    "real_metin_negro_inventario_mapa.png": [(766, 181, False), (299, 466, False)],
    "real_metin_negro_seleccionado.png": [(1062, 255, True)],
    "real_rodeado.png": [(698, 123, False), (994, 299, True)],
}
# sin piedra detectable
NO_STONES = ["items_en_piso.png", "yang_en_piso.png", "metin_avaricia_seleccionado.png",
             "metin_oscuridad_combate_vida_parcial.png"]
TOL = 20


@pytest.fixture(scope="module")
def cfg():
    return load_config()


def _find(img, cfg, exclude=None, windows=False):
    extra = find_ui_windows(img, cfg) if windows else None
    return find_far_metins(img, cfg, build_screen_mask(img.shape, cfg, extra), exclude)


def _near(found, x, y, tol=TOL):
    return [f for f in found if abs(f.center[0] - x) <= tol and abs(f.center[1] - y) <= tol]


@pytest.mark.parametrize("name", sorted(STONES))
def test_detecta_las_piedras_de_la_verdad(load_sample, cfg, name):
    found = _find(load_sample(name), cfg)
    for x, y, _ in STONES[name]:
        hits = _near(found, x, y)
        assert len(hits) == 1, f"{name}: piedra ({x},{y}) -> {[f.center for f in found]}"
        f = hits[0]
        assert f.allowed and 0 < f.score <= 1
        assert f.center[1] - f.rect.y >= 0.7 * f.rect.h      # center en la base (parte baja del cuerpo)
        assert f.center[1] <= f.rect.bottom
    # y ninguna otra (cada deteccion explicada por la verdad)
    assert len(found) == len(STONES[name]), f"{name}: {[f.center for f in found]}"


@pytest.mark.parametrize("name", NO_STONES)
def test_sin_falsos_positivos(load_sample, cfg, name):
    assert _find(load_sample(name), cfg, windows=True) == []


def test_piedra_bajo_la_ventana_de_inventario_no_se_ofrece(load_sample, cfg):
    """real_inventario_abierto: la piedra (1737,454) queda tapada por el inventario; con la mascara de ventanas
    no se ofrece (un clic caeria en la ventana)."""
    found = _find(load_sample("real_inventario_abierto.png"), cfg, windows=True)
    assert all(not f.allowed or not _near([f], 1737, 454) for f in found)
    assert not _near(found, 1737, 454)


def test_muerte_no_hay_otra_deteccion_que_la_piedra_tapada(load_sample, cfg):
    found = _find(load_sample("muerte.png"), cfg)
    assert all(abs(f.center[0] - 717) < 30 and abs(f.center[1] - 225) < 30 for f in found)


def test_sin_etiqueta_con_exclude_no_se_repite(load_sample, cfg):
    """Las piedras con etiqueta se tapan con el rect de la etiqueta extendido hacia abajo: solo quedan las sin etiqueta."""
    for name, labeled_rect in (("real_rodeado.png", Rect(940, 180, 110, 160)),
                               ("real_metin_lejos_1.png", Rect(340, 290, 120, 140)),
                               ("real_metin_negro_seleccionado.png", Rect(1000, 150, 130, 160))):
        img = load_sample(name)
        rest = _find(img, cfg, exclude=[labeled_rect])
        expected = [(x, y) for x, y, lab in STONES[name] if not lab]
        assert len(rest) == len(expected), f"{name}: {[f.center for f in rest]}"
        for x, y in expected:
            assert _near(rest, x, y)


def test_exclude_vacio_o_none_no_cambia(load_sample, cfg):
    img = load_sample("real_rodeado.png")
    assert _find(img, cfg) == _find(img, cfg, exclude=[]) == _find(img, cfg, exclude=None)


def test_imagen_vacia_o_negra(cfg):
    assert find_far_metins(np.zeros((0, 0, 3), np.uint8), cfg) == []
    assert find_far_metins(np.zeros((300, 300, 3), np.uint8), cfg) == []


@pytest.mark.parametrize("dx,dy", [(0, 0), (3, 0), (0, 7), (12, 12), (5, 11)])
def test_desplazamientos_por_borde(load_sample, cfg, dx, dy):
    """Las samples reales difieren unos px de borde: recortar dx,dy desde arriba/izquierda mueve todo igual."""
    img = load_sample("real_metin_lejos_1.png")[dy:, dx:]
    found = _find(img, cfg)
    for x, y, _ in STONES["real_metin_lejos_1.png"]:
        assert _near(found, x - dx, y - dy, 12), f"({dx},{dy}) {[f.center for f in found]}"
    assert len(found) == 3
    # recorte por abajo/derecha no cambia las coordenadas
    found = _find(load_sample("real_rodeado.png")[:1057 - dy, :1858 - dx], cfg)
    assert len(found) == 2


# ---------------------------------------------------------------- sinteticos
def _ground(load_sample):
    """Suelo limpio (sin piedras ni monstruos) de real_rodeado, 330x200."""
    return load_sample("real_rodeado.png")[700:900, 100:430].copy()


def _paste(dst, src_img, rect, at):
    x, y, w, h = rect
    dst[at[1]:at[1] + h, at[0]:at[0] + w] = src_img[y:y + h, x:x + w]


def test_el_suelo_solo_no_detecta(load_sample, cfg):
    assert find_far_metins(_ground(load_sample), cfg) == []


def test_numeros_de_dano_rojos_y_amarillos(load_sample, cfg):
    g = _ground(load_sample)
    for i, col in enumerate(((0, 0, 230), (9, 22, 235), (30, 30, 200), (0, 230, 255))):
        cv2.putText(g, str(1234 * (i + 1)), (20 + 70 * (i % 4), 60 + 40 * i), cv2.FONT_HERSHEY_DUPLEX, 1.3, col, 3)
        cv2.putText(g, "5678", (30 + 60 * (i % 3), 150 + 10 * i), cv2.FONT_HERSHEY_SIMPLEX, 1.0, col, 4)
    assert find_far_metins(g, cfg) == []


def test_nombre_rojo_de_monstruo(load_sample, cfg):
    g = _ground(load_sample)
    src = load_sample("real_rodeado.png")
    _paste(g, src, (744, 470, 260, 24), (20, 40))       # pila de etiquetas "Lv .. Soldado ... (rojo + verde)"
    _paste(g, src, (531, 350, 139, 20), (20, 120))
    assert find_far_metins(g, cfg) == []


def test_piel_de_monstruo_y_resplandor_de_efectos(load_sample, cfg):
    g = _ground(load_sample)
    # piel de monstruo (oso, mujer de piel rojiza) y sangre/efectos del combate
    _paste(g, load_sample("explorando_zona.png"), (50, 330, 90, 90), (10, 10))
    _paste(g, load_sample("metin_muy_lejos.png"), (1530, 610, 60, 60), (120, 10))
    _paste(g, load_sample("real_metin_negro_seleccionado.png"), (560, 150, 90, 120), (200, 20))
    _paste(g, load_sample("items_en_piso.png"), (980, 480, 100, 110), (10, 85))
    assert find_far_metins(g, cfg) == []
    # bola de fuego de una habilidad (combate): grande, naranja, sin suelo alrededor
    g2 = _ground(load_sample)
    _paste(g2, load_sample("metin_oscuridad_combate_vida_parcial.png"), (940, 470, 100, 110), (100, 40))
    assert find_far_metins(g2, cfg) == []


def test_una_piedra_pegada_en_otro_suelo_se_detecta(load_sample, cfg):
    """Positivo sintetico: recortes de piedras (con humo / sin humo, anaranjada) pegados en suelo limpio."""
    for name, rect in (("real_metin_lejos_1.png", (320, 195, 90, 70)),
                       ("real_metin_negro_inventario_mapa.png", (735, 140, 70, 70))):
        g = _ground(load_sample)
        _paste(g, load_sample(name), rect, (120, 70))
        assert len(find_far_metins(g, cfg)) == 1, name


def test_tiempo(load_sample, cfg):
    img = load_sample("real_rodeado.png")
    mask = build_screen_mask(img.shape, cfg)
    find_far_metins(img, cfg, mask)
    ts = []
    for _ in range(5):
        t = time.perf_counter()
        find_far_metins(img, cfg, mask)
        ts.append(time.perf_counter() - t)
    print("far_metin ms:", min(ts) * 1000)
    assert min(ts) < 0.06     # objetivo < 15 ms; limite laxo para PCs lentas
