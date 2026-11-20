"""Detector de piedras por estructura (piedra + aura + humo), independiente del tono: bot/vision/far_structure.py.

Capturas reales en tests/data (recortadas en negro desde y=620/720 para pesar menos; las piedras quedan arriba):
  estructura_tres_metin      3 piedras rojas (una con etiqueta de Metin de Oscuridad)
  estructura_oscuridad_lejos 3 piedras rojas, una pegada al horizonte
  estructura_avaricia        Metin de Avaricia (ROSA, otro tono, sin humo claro) seleccionado
  estructura_negro_sin_humo  Metin Negro (anaranjado, sin humo)
  estructura_melee           pelea con muchos monstruos (el detector por color da 3 falsos aca; no hay piedras)
"""
import copy
from pathlib import Path

import cv2
import numpy as np
import pytest

from bot.config import ConfigError, load_config, validate_config
from bot.geometry import Rect
from bot.vision.far_metin import FarMetin, find_far_metins, merge_far
from bot.vision.far_structure import find_structural_metins, hue_matches, hue_range, measure_stone_hue
from bot.vision.screen_mask import build_screen_mask, find_ui_windows
from test_far_metin_falsos import BOTTOM, LEFT, MONSTERS, clean_debug_drawings

DATA = Path(__file__).resolve().parent / "data"
TOL = 20
TRES = [(1237, 235), (546, 280), (1407, 379)]          # base de cada piedra de estructura_tres_metin


def _cfg(mode: str, **structure):
    c = copy.deepcopy(load_config())
    c["vision"]["far_metin"]["mode"] = mode
    c["vision"]["far_metin"]["hue_hints"] = {}
    c["vision"]["far_metin"]["structure"].update(structure)
    return c


@pytest.fixture(scope="module")
def img_tres():
    return _load("estructura_tres_metin.png")


def _load(name: str) -> np.ndarray:
    img = cv2.imread(str(DATA / name), cv2.IMREAD_COLOR)
    assert img is not None, name
    return img


def _find(img, cfg, **kw):
    return find_far_metins(img, cfg, build_screen_mask(img.shape, cfg, find_ui_windows(img, cfg)), **kw)


def _near(found, x, y, tol=TOL):
    return [f for f in found if abs(f.center[0] - x) <= tol and abs(f.center[1] - y) <= tol]


def shift_hue(img: np.ndarray, rects: list[Rect], delta: int, s_scale: float = 1.0, v_scale: float = 1.0) -> np.ndarray:
    """Variante de color de una captura: rota el matiz (y opcionalmente baja saturacion/brillo) de los pixeles calidos (rojos,
    naranjas, el tinte del humo) alrededor de cada piedra. El suelo azul no se toca. Sirve para simular otro tipo de metin."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.int32)
    h, w = hsv.shape[:2]
    for r in rects:
        y0, y1, x0, x1 = max(0, r.y - int(1.6 * r.h)), min(h, r.bottom + 10), max(0, r.x - 14), min(w, r.right + 14)
        sub = hsv[y0:y1, x0:x1]
        warm = ((sub[:, :, 0] <= 40) | (sub[:, :, 0] >= 160)) & (sub[:, :, 1] >= 15)
        sub[:, :, 0] = np.where(warm, (sub[:, :, 0] + delta) % 180, sub[:, :, 0])
        sub[:, :, 1] = np.where(warm, np.clip(sub[:, :, 1] * s_scale, 0, 255), sub[:, :, 1])
        sub[:, :, 2] = np.where(warm, np.clip(sub[:, :, 2] * v_scale, 0, 255), sub[:, :, 2])
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


# ------------------------------------------------------------------ piedras reales
def test_encuentra_las_tres_piedras_reales(img_tres):
    found = _find(img_tres, _cfg("estructura"))
    assert len(found) == 3, [f.center for f in found]
    for x, y in TRES:
        hit = _near(found, x, y)
        assert len(hit) == 1 and hit[0].allowed and hit[0].smoke and 0.5 <= hit[0].score <= 1
        assert hit[0].center[1] - hit[0].rect.y >= 0.7 * hit[0].rect.h      # el centro es la base
        assert 0 <= hit[0].hue <= 20 or hit[0].hue >= 170                  # rojo


def test_oscuridad_lejos_incluye_la_piedra_pegada_al_horizonte():
    found = _find(_load("estructura_oscuridad_lejos.png"), _cfg("estructura"))
    assert len(found) == 3, [f.center for f in found]
    for x, y in ((1472, 280), (432, 265), (164, 418)):
        assert _near(found, x, y), (x, y, [f.center for f in found])


def test_metin_de_avaricia_rosa_se_detecta_sin_ser_rojo():
    """Un metin de otro tono (rosa, H~163) que el detector por color no ve: la estructura si."""
    img = _load("estructura_avaricia.png")
    assert _find(img, _cfg("color")) == []
    found = _find(img, _cfg("estructura"))
    assert len(found) == 1 and _near(found, 919, 413)
    assert 140 <= found[0].hue <= 175


def test_metin_sin_humo_se_acepta_marcado_como_dudoso():
    found = _find(_load("estructura_negro_sin_humo.png"), _cfg("estructura"))
    assert len(found) == 1 and _near(found, 226, 245) and found[0].smoke is False
    assert _find(_load("estructura_negro_sin_humo.png"), _cfg("estructura", nosmoke_enabled=False)) == []


# ------------------------------------------------------------------ independencia del color
@pytest.mark.parametrize("nombre,delta", [("verde", 55), ("azul", 105), ("violeta", 135)])
def test_variantes_de_tono_se_siguen_encontrando(img_tres, nombre, delta):
    rects = [f.rect for f in _find(img_tres, _cfg("estructura"))]
    img = shift_hue(img_tres, rects, delta)
    assert not np.array_equal(img, img_tres)
    found = _find(img, _cfg("estructura"))
    assert len(found) == 3, f"{nombre}: {[f.center for f in found]}"
    for x, y in TRES:
        assert _near(found, x, y), (nombre, x, y)
    assert _find(img, _cfg("color")) == [], "el detector por color depende del tono: no ve las variantes"
    assert len(_find(img, _cfg("ambos"))) == 3


def test_variante_verde_ligeramente_mas_tenue_se_encuentra(img_tres):
    rects = [f.rect for f in _find(img_tres, _cfg("estructura"))]
    img = shift_hue(img_tres, rects, 55, s_scale=0.92, v_scale=0.95)
    assert len(_find(img, _cfg("estructura"))) == 3


def test_el_tono_medido_sigue_a_la_rotacion(img_tres):
    rects = [f.rect for f in _find(img_tres, _cfg("estructura"))]
    base = _find(img_tres, _cfg("estructura"))[0].hue
    verde = _find(shift_hue(img_tres, rects, 55), _cfg("estructura"))[0].hue
    assert abs((verde - base - 55 + 90) % 180 - 90) <= 12, (base, verde)    # desplazamiento 55 en el circulo de 180


# ------------------------------------------------------------------ falsos positivos
def test_melee_sin_piedras_no_da_falsos():
    """Pelea con decenas de monstruos y efectos: el detector por color da 3 falsos, el de estructura ninguno."""
    img = _load("estructura_melee.png")
    assert len(_find(img, _cfg("color"))) == 3          # control: el falso existe con el detector viejo
    assert _find(img, _cfg("estructura")) == []


def test_cuerpos_de_monstruos_de_las_capturas_debug_no_son_metin():
    for name in (LEFT, BOTTOM):
        img = clean_debug_drawings(cv2.imread(str(DATA / name), cv2.IMREAD_COLOR))
        for mons in (MONSTERS[name], None):
            found = find_far_metins(img, _cfg("estructura"), monsters=mons)
            for x, y in ((162, 642), (37, 567), (32, 600), (1368, 860), (1467, 813), (766, 866)):
                assert not _near(found, x, y, 40), (name, x, y, [f.center for f in found])
    left = find_far_metins(clean_debug_drawings(cv2.imread(str(DATA / LEFT), cv2.IMREAD_COLOR)), _cfg("estructura"),
                           monsters=MONSTERS[LEFT])
    assert len(left) == 1 and _near(left, 1538, 413, 30)      # el metin de la derecha (con etiqueta)


def test_suelo_y_texto_no_dan_nada():
    img = np.full((1057, 1858, 3), (60, 45, 40), np.uint8)
    for i, col in enumerate(((0, 0, 230), (9, 22, 235), (0, 230, 255), (255, 200, 0))):
        cv2.putText(img, str(1234 * (i + 1)), (300 + 120 * i, 500), cv2.FONT_HERSHEY_DUPLEX, 1.3, col, 3)
    assert find_structural_metins(img, _cfg("estructura")) == []
    assert find_structural_metins(np.zeros((0, 0, 3), np.uint8), _cfg("estructura")) == []
    assert find_structural_metins(np.zeros((300, 300, 3), np.uint8), _cfg("estructura")) == []


def test_etiqueta_de_monstruo_arriba_y_exclude_descartan(img_tres):
    cfg = _cfg("estructura")
    from bot.vision.monsters import MonsterLabel
    lb = MonsterLabel(Rect(500, 210, 90, 10), Rect(500, 210, 90, 10).center)   # justo arriba de la piedra (546, 280)
    assert not _near(_find(img_tres, cfg, monsters=[lb]), 546, 280)
    assert not _near(_find(img_tres, cfg, exclude=[Rect(1180, 170, 120, 100)]), 1237, 235)
    assert len(_find(img_tres, cfg, exclude=[])) == 3


# ------------------------------------------------------------------ modo "ambos"
def test_ambos_deduplica_y_une(img_tres):
    both = _find(img_tres, _cfg("ambos"))
    assert len(both) == 3          # color y estructura ven las mismas 3: no se duplican
    assert all(f.hue >= 0 for f in both)         # y se conserva el tono medido
    # en la de avaricia solo ve la estructura; en el melee solo "ven" (mal) los de color: la union conserva ambas listas
    assert len(_find(_load("estructura_avaricia.png"), _cfg("ambos"))) == 1
    assert len(_find(_load("estructura_melee.png"), _cfg("ambos"))) == 3


def test_merge_far_sintetico():
    def fm(x, y, w, h, score=0.5, smoke=True, hue=-1):
        r = Rect(x, y, w, h)
        return FarMetin(r, (r.x + r.w // 2, r.y + int(r.h * 0.85)), score, True, smoke, hue)
    a = [fm(100, 100, 30, 30, 0.6, False), fm(400, 100, 30, 30)]
    b = [fm(95, 95, 40, 40, 0.8, True, 7), fm(700, 300, 30, 30, 0.7, True, 9)]
    out = merge_far(a, b)
    assert len(out) == 3
    dup = [f for f in out if f.rect.x == 100][0]            # conserva el rect del detector por color, no el de b
    assert (dup.score, dup.smoke, dup.hue) == (0.8, True, 7)
    assert [f.rect.x for f in out] == [100, 400, 700]
    assert merge_far([], []) == [] and merge_far(a, []) == a


def test_modo_color_sigue_igual_y_modo_invalido_no_valida(img_tres):
    cfg = _cfg("color")
    assert [f.center for f in find_far_metins(img_tres, cfg)] == [f.center for f in
                                                                   find_far_metins(img_tres, _cfg("color"))]
    bad = copy.deepcopy(cfg)
    bad["vision"]["far_metin"]["mode"] = "otro"
    with pytest.raises(ConfigError, match="mode"):
        validate_config(bad)
    validate_config(cfg)


# ------------------------------------------------------------------ pistas de tono (hue_hints)
def test_hue_hints_es_opcional_y_solo_suma_puntaje(img_tres):
    base = _find(img_tres, _cfg("estructura"))
    weakest = min(base, key=lambda f: f.score)
    thr = round(weakest.score + 0.05, 3)           # corte justo por encima del puntaje de la piedra mas floja
    sin = _find(img_tres, _cfg("estructura", min_score=thr))
    assert not _near(sin, *weakest.center)
    c = _cfg("estructura", min_score=thr)
    c["vision"]["far_metin"]["hue_hints"] = {"metin_de_oscuridad": [0, 12]}      # el rojo de la piedra
    con = _find(img_tres, c)
    assert _near(con, *weakest.center) and len(con) >= len(sin)
    c["vision"]["far_metin"]["hue_hints"] = {"metin_verde": [50, 70]}            # otro tono: no suma ni resta
    assert not _near(_find(img_tres, c), *weakest.center)
    # sin pistas el detector funciona igual (no son requisito)
    assert len(base) == 3


def test_hue_matches_y_hue_range_con_envolvente():
    assert hue_matches(5, {"a": [0, 12]}) and not hue_matches(60, {"a": [0, 12]})
    assert hue_matches(175, {"a": [170, 8]}) and hue_matches(3, {"a": [170, 8]}) and not hue_matches(90, {"a": [170, 8]})
    assert not hue_matches(5, {})
    rng = hue_range([6, 8, 7], 8)
    assert rng == [-1 % 180, 15] or (rng[0] in (178, 179, 0) and rng[1] in (14, 15, 16))
    wrap = hue_range([178, 2], 4)                    # rojo a ambos lados del 0
    assert hue_matches(0, {"x": wrap}) and hue_matches(179, {"x": wrap}) and not hue_matches(90, {"x": wrap})
    assert hue_matches(60, {"x": hue_range([58, 62], 5)})


def test_measure_stone_hue_cerca_del_punto(img_tres):
    h = measure_stone_hue(img_tres, load_config(), (546, 280))
    assert h is not None and (h <= 20 or h >= 170)
    assert measure_stone_hue(img_tres, load_config(), (900, 800)) is None
    verde = shift_hue(img_tres, [f.rect for f in _find(img_tres, _cfg("estructura"))], 55)
    hv = measure_stone_hue(verde, load_config(), (546, 280))
    assert hv is not None and 45 <= hv <= 90
    pink = measure_stone_hue(_load("estructura_avaricia.png"), load_config(), (919, 413))
    assert pink is not None and 140 <= pink <= 175


def test_config_valida_hue_hints():
    cfg = copy.deepcopy(load_config())
    cfg["vision"]["far_metin"]["hue_hints"] = {"x": [10, 20]}
    validate_config(cfg)
    for bad in ({"x": [10]}, {"x": [10, 200]}, {"x": "rojo"}, [1, 2]):
        cfg["vision"]["far_metin"]["hue_hints"] = bad
        with pytest.raises(ConfigError):
            validate_config(cfg)


# ------------------------------------------------------------------ calibrate.py: tono y metin
def _cal_session(tmp_path, img, keys, cursor, answers=(), extra=None):
    import json

    from test_calibration import ORIGIN, make_session
    path = tmp_path / "config.json"
    raw = {"window_title": "Guabina", "debug": {"dir": str(tmp_path / "debug")},
           "vision": {"metin": {"label_templates_dir": str(tmp_path / "labels")},
                      "panel": {"name_templates_dir": str(tmp_path / "panel_names")}}}
    path.write_text(json.dumps(raw), encoding="utf-8")
    pts = iter(cursor)
    s = make_session(path, [img], keys=keys, answers=list(answers),
                     cursor_fn=lambda: (lambda p: (p[0] + ORIGIN[0], p[1] + ORIGIN[1]))(next(pts)))
    return s, path


def _saved(path):
    """Config efectiva tras calibrar: con perfiles, `hue_hints` va al perfil de servidor y los tipos de metin al de zona."""
    from bot.profiles import build_config
    return build_config(path, profiles_root_dir=path.parent / "profiles", warn=False)


def test_calibrate_tono_guarda_la_pista_con_varias_capturas(tmp_path, img_tres):
    import bot.calibration as cal
    # la tercera captura cae lejos de toda piedra: se avisa y se pide otra (no cuenta)
    s, path = _cal_session(tmp_path, img_tres, ["f9"] * 4 + ["f10"],
                           [(546, 280), (1407, 379), (900, 800), (1237, 235)], answers=["s"])
    assert cal.cmd_tono(s, "Metin de Prueba") == 0
    assert any("No se vio una piedra" in line for line in s.out)
    hint = _saved(path)["vision"]["far_metin"]["hue_hints"]["metin_de_prueba"]
    assert len(hint) == 2 and hue_matches(4, {"x": hint}) and not hue_matches(60, {"x": hint})


def test_calibrate_tono_sin_piedra_no_guarda_nada(tmp_path, img_tres):
    import bot.calibration as cal
    s, path = _cal_session(tmp_path, img_tres, ["f10"], [(900, 800)])
    assert cal.cmd_tono(s, "Metin de Prueba") == 1
    assert not _saved(path)["vision"]["far_metin"].get("hue_hints")


def test_calibrate_metin_mide_el_tono_de_la_piedra_bajo_la_etiqueta(tmp_path, img_tres):
    import bot.calibration as cal
    s, path = _cal_session(tmp_path, img_tres, ["f9", "f10"], [(1236, 200)], answers=["s", "s"])   # etiqueta "Metin de Oscuridad"
    assert cal.cmd_metin(s, "Metin Nuevo") == 0
    saved = _saved(path)
    assert "Metin Nuevo" in saved["target_metin_names"]
    assert hue_matches(4, {"x": saved["vision"]["far_metin"]["hue_hints"]["metin_nuevo"]})
    assert (tmp_path / "labels" / "metin_nuevo.png").exists()
