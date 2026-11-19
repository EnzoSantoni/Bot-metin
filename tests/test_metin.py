"""Deteccion de metin sobre ./samples (verdad de referencia medida sobre recortes ampliados)."""
import copy
import math

import cv2
import numpy as np
import pytest

from bot.config import DEFAULTS
from bot.geometry import Rect
from bot.vision.metin import MetinDetection, choose_target, find_metins, is_target_name, player_point
from bot.vision.screen_mask import build_screen_mask, find_ui_windows

# sample -> (etiqueta aprox. (x, y, w, h), rectangulo aprox. de la piedra (x, y, w, h)).
# Etiquetas: texto naranja "Metin de Oscuridad" (96x8). Piedra medida a ojo en recortes x2.5 (+-6 px).
OSCURIDAD = {
    "metin_cerca_sin_seleccionar.png": ((792, 334, 96, 8), (810, 387, 68, 80)),
    "metin_oscuridad_a_distancia.png": ((109, 331, 96, 8), (137, 370, 44, 72)),
    "metin_oscuridad_seleccionado_vida_llena.png": ((897, 210, 96, 8), (923, 253, 52, 62)),
    # En combate la piedra queda tapada por el personaje y los efectos: se usa la zona del resplandor.
    "metin_oscuridad_combate_vida_parcial.png": ((899, 371, 96, 8), (890, 400, 120, 130)),
}
# Samples donde no hay ninguna etiqueta naranja de metin (verificado en recortes / pixeles).
SIN_ETIQUETA = ["items_en_piso", "yang_en_piso", "muerte", "captcha", "metin_muy_lejos", "explorando_zona"]
REAL = "real_metin_negro_inventario_mapa.png"


def _cfg(name=None):
    cfg = copy.deepcopy(DEFAULTS)
    if name:
        cfg["target_metin_names"] = [name] if isinstance(name, str) else name
    return cfg


def _run(img, cfg=None):
    cfg = cfg or _cfg()
    mask = build_screen_mask(img.shape, cfg, extra=find_ui_windows(img, cfg))
    return find_metins(img, cfg, mask), mask


def _inside(pt, rect, tol=4):
    x, y, w, h = rect
    return x - tol <= pt[0] <= x + w + tol and y - tol <= pt[1] <= y + h + tol


@pytest.mark.parametrize("name", list(OSCURIDAD))
def test_oscuridad_objetivo_con_clic_dentro_de_la_piedra(load_sample, name):
    img = load_sample(name)
    dets, _ = _run(img)
    assert len(dets) == 1, dets
    d = dets[0]
    label, stone = OSCURIDAD[name]
    assert d.name == "metin_de_oscuridad" and d.is_target and d.allowed and d.score >= 0.9
    assert abs(d.label_rect.x - label[0]) <= 3 and abs(d.label_rect.y - label[1]) <= 3
    assert (d.label_rect.w, d.label_rect.h) == (label[2], label[3])
    assert _inside(d.click_point, stone), (d.click_point, stone)
    assert choose_target(dets, player_point(img.shape, DEFAULTS)) is d


def test_avaricia_detectada_pero_no_objetivo(load_sample):
    dets, _ = _run(load_sample("metin_avaricia_seleccionado.png"))
    assert len(dets) == 1
    d = dets[0]
    assert d.name == "metin_de_avaricia" and not d.is_target and d.score >= 0.9
    assert (d.label_rect.w, d.label_rect.h) == (86, 10)
    assert choose_target(dets, (900, 500)) is None


def test_metin_negro_es_objetivo_con_lista_por_defecto(load_sample):
    dets, _ = _run(load_sample(REAL))
    assert len(dets) == 1  # solo el Metin Negro; sin falsos positivos con inventario y mapa abiertos
    d = dets[0]
    assert d.name == "metin_negro" and d.is_target
    assert abs(d.label_rect.x - 258) <= 3 and abs(d.label_rect.y - 373) <= 3
    assert (d.label_rect.w, d.label_rect.h) == (59, 9)


def test_filtro_es_por_nombre_no_por_forma(load_sample):
    cfg = _cfg("Metin de Avaricia")
    d_av = _run(load_sample("metin_avaricia_seleccionado.png"), cfg)[0]
    assert len(d_av) == 1 and d_av[0].is_target
    for name in OSCURIDAD:
        dets = _run(load_sample(name), cfg)[0]
        assert len(dets) == 1 and dets[0].name == "metin_de_oscuridad" and not dets[0].is_target, name
    assert not any(d.is_target for d in _run(load_sample(REAL), cfg)[0])


def test_avaricia_ignorada_con_lista_por_defecto(load_sample):
    dets = _run(load_sample("metin_avaricia_seleccionado.png"))[0]
    assert not dets[0].is_target


def test_is_target_name():
    cfg = _cfg()
    assert is_target_name("METIN NEGRO", cfg) and is_target_name("Métin de Oscuridad", cfg)
    assert not is_target_name("Metin de Avaricia", cfg)


def test_dos_objetivos_elige_el_mas_cercano_sin_importar_el_tipo(load_sample):
    patch = load_sample("metin_cerca_sin_seleccionar.png")[329:347, 787:893]
    img = load_sample(REAL).copy()
    img[300:300 + patch.shape[0], 1000:1000 + patch.shape[1]] = patch  # Oscuridad lejos del Negro (258,373)
    dets, _ = _run(img)
    assert {d.name for d in dets if d.is_target} == {"metin_negro", "metin_de_oscuridad"}
    assert choose_target(dets, (250, 440)).name == "metin_negro"
    assert choose_target(dets, (1050, 370)).name == "metin_de_oscuridad"


@pytest.mark.parametrize("names", [[], ["Metin de Oscuridad", ""], "Metin Negro"])
def test_lista_invalida(names):
    from bot.config import ConfigError, validate_config
    cfg = _cfg()
    cfg["target_metin_names"] = names
    with pytest.raises(ConfigError):
        validate_config(cfg)


@pytest.mark.parametrize("name", SIN_ETIQUETA)
def test_sin_etiquetas_naranjas(load_sample, name):
    """Monstruos (rojo + Lv verde), personaje amarillo, danos, etiquetas de objetos y UI no son metin."""
    dets, _ = _run(load_sample(name + ".png"))
    assert dets == []


def test_a_distancia_el_juego_no_dibuja_la_etiqueta(load_sample):
    """En metin_muy_lejos se ve la piedra (resplandor ~ (220,230)) pero SIN etiqueta: 0 px de naranja exacto fuera de la interfaz."""
    for name in ("metin_muy_lejos.png", "explorando_zona.png"):
        img = load_sample(name)
        dets, mask = _run(img)
        assert dets == []
        exacto = np.all(img == (0, 128, 255), axis=2) & (mask.mask == 0)  # fuera de zonas de interfaz
        assert not exacto.any()


def test_etiqueta_tapada_30_por_ciento_no_es_objetivo(load_sample):
    img = load_sample("metin_cerca_sin_seleccionar.png")
    x, y, w, h = OSCURIDAD["metin_cerca_sin_seleccionar.png"][0]
    for x0, x1 in [(x - 2, x + int(w * 0.3)), (x + int(w * 0.7), x + w + 2), (x + int(w * 0.35), x + int(w * 0.65))]:
        occ = img.copy()
        cv2.rectangle(occ, (x0, y - 3), (x1, y + h + 3), (25, 20, 20), -1)
        dets, _ = _run(occ)
        assert not any(d.is_target for d in dets), (x0, x1, dets)


def test_plantilla_del_objetivo_faltante_da_error_claro(load_sample):
    with pytest.raises(FileNotFoundError, match="Falta la plantilla del objetivo 'Metin Inexistente'"):
        _run(load_sample("metin_cerca_sin_seleccionar.png"), _cfg(["Metin Negro", "Metin Inexistente"]))


def test_clic_sobre_inventario_no_esta_permitido(load_sample):
    """Etiqueta pegada justo arriba del inventario: el clic (65 px debajo) cae en la ventana -> allowed False."""
    base = load_sample(REAL)
    patch = load_sample("metin_cerca_sin_seleccionar.png")[329:347, 787:893]  # etiqueta con su fondo
    img = base.copy()
    img[432:432 + patch.shape[0], 1650:1650 + patch.shape[1]] = patch   # el clic queda DENTRO de la ventana
    cfg = _cfg()
    inv = max(find_ui_windows(img, cfg), key=lambda r: r.h)
    dets, _ = _run(img)
    # la etiqueta cae en el margen de seguridad de la ventana: ni siquiera se considera candidata
    assert [d.name for d in dets] == ["metin_negro"]
    assert find_metins(img, cfg, build_screen_mask(img.shape, cfg))   # sin ventanas si se ve la etiqueta
    img = base.copy()
    img[372:372 + patch.shape[0], 1650:1650 + patch.shape[1]] = patch   # clic a ~12 px del borde, FUERA de la ventana
    dets, _ = _run(img)
    assert len(dets) == 2  # Negro + la pegada
    pegada = next(d for d in dets if d.name == "metin_de_oscuridad")
    assert not inv.contains(*pegada.click_point) and inv.y - pegada.click_point[1] < cfg["vision"]["ui_windows"]["safety_margin"]
    assert pegada.is_target and not pegada.allowed
    chosen = choose_target(dets, (1700, 500))  # la pegada es la mas cercana pero no permitida
    assert chosen is not None and chosen.name == "metin_negro"
    # sin la mascara de ventanas el mismo punto estaria permitido: la exclusion la causan las ventanas
    plain = find_metins(img, cfg, build_screen_mask(img.shape, cfg))
    assert next(d for d in plain if d.name == "metin_de_oscuridad").allowed


def _det(x, y, target=True, allowed=True):
    return MetinDetection(Rect(x - 40, y - 70, 80, 8), "m", 1.0, target, (x, y), allowed)


def test_choose_target_elige_la_mas_cercana_permitida():
    player = (900, 540)
    far, near = _det(300, 300), _det(1000, 600)
    forbidden, other = _det(910, 545, allowed=False), _det(905, 550, target=False)
    assert choose_target([far, forbidden, other, near], player) is near
    assert choose_target([forbidden, other], player) is None
    assert choose_target([], player) is None
    pts = [(300, 300), (1500, 100), (950, 620)]
    best = min(pts, key=lambda p: math.dist(p, player))
    assert choose_target([_det(*p) for p in pts], player).click_point == best


def test_inventario_compacto_pegado_al_metin_no_se_clickea(load_sample):
    """real_inventario_abierto.png (bug A3): el Metin Negro esta justo arriba de la barra de titulo del inventario
    (titulo en y=458..486, clic en (1739, 449)); con el inventario detectado y su margen no es permitido."""
    img = load_sample("real_inventario_abierto.png")
    cfg = _cfg()
    dets, _ = _run(img)
    assert len(dets) == 1 and dets[0].name == "metin_negro" and dets[0].is_target
    assert _inside(dets[0].click_point, (1739, 449, 0, 0), 6)
    assert not dets[0].allowed
    assert choose_target(dets, (929, 548)) is None
    # sin las ventanas el mismo clic estaria permitido: lo que lo evita es la deteccion del inventario
    plain = find_metins(img, cfg, build_screen_mask(img.shape, cfg))
    assert plain[0].allowed
