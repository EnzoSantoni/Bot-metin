"""Tests de calibrate.py / bot/calibration.py. Todo simulado: ventana falsa, teclado falso, input() parcheado."""
import json
import shutil
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

import calibrate
from bot import calibration as cal
from bot.config import PROJECT_ROOT, load_config
from bot.profiles import build_config
from bot.geometry import Rect
from bot.input.fake import FakeWindow
from bot.vision.metin import find_label_candidates, find_metins
from bot.vision.screen_mask import build_screen_mask

PROFILES_DIR = PROJECT_ROOT / "profiles"
CLIENT = Rect(8, 31, 1858, 1057)
ORIGIN = (8, 31)   # esquina del cliente en pantalla


class FakeKeyboard:
    """Registra hotkeys; al registrarse la ultima (F12) dispara el guion de teclas, en orden."""

    def __init__(self, script):
        self.script = list(script)
        self.handlers = {}
        self.removed = []

    def add_hotkey(self, key, cb):
        self.handlers[key] = cb
        if key == "f12":
            for k in self.script:
                self.handlers[k]()
        return key

    def remove_hotkey(self, h):
        self.removed.append(h)


class ScriptedWindow(FakeWindow):
    """Cada capture() devuelve la siguiente imagen (la ultima se repite)."""

    def __init__(self, images):
        super().__init__(CLIENT)
        self.images = list(images)
        self.n = 0

    def capture(self, region=None):
        img = self.images[min(self.n, len(self.images) - 1)]
        self.n += 1
        return img


@pytest.fixture
def tmp_cfg(tmp_path):
    """config.json temporal con una clave extra y plantillas/debug en carpetas temporales.

    Define los valores que calibrate.py guarda (ventana, zona, velocidad, metin): config.json los pisa, asi que se guardan
    ahi como antes de los perfiles. El destino en los perfiles se prueba en test_profiles.py.
    """
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "window_title": "Guabina",
        "expected_client_size": [1858, 1057],
        "target_metin_names": ["Metin de Oscuridad", "Metin Negro"],
        "farm_zone": {"polygon": [[439, 141], [753, 91], [702, 203], [436, 196]], "outside_tolerance": 30},
        "coords_filter": {"max_speed": 30.0},
        "mi_clave_extra": {"a": 1},
        "debug": {"dir": str(tmp_path / "debug")},
        "vision": {"metin": {"label_templates_dir": str(tmp_path / "labels")},
                   "panel": {"name_templates_dir": str(tmp_path / "panel_names")}},
    }, indent=2), encoding="utf-8")
    return path


def make_session(tmp_cfg, images=(), keys=(), answers=(), yes=False, **kw):
    out = []
    answers = list(answers)

    def ask(prompt):
        out.append(prompt)
        return answers.pop(0) if answers else "n"

    root = tmp_cfg.parent / "profiles"   # copia de los perfiles reales: ningun test escribe en profiles/ del proyecto
    if not root.exists():
        shutil.copytree(PROFILES_DIR, root)
    s = cal.Session(cfg=build_config(tmp_cfg, profiles_root_dir=root, warn=False), config_path=str(tmp_cfg), yes=yes,
                    profiles_root=root,
                    window=ScriptedWindow(list(images) or [np.zeros((10, 10, 3), np.uint8)]),
                    keyboard=FakeKeyboard(keys), say=out.append, ask=ask, **kw)
    s.out = out
    return s


def raw(path):
    return json.loads(path.read_text(encoding="utf-8"))


QUAD = [[453, 161], [704, 193], [688, 194], [453, 169]]
SAMPLES = ["real_metin_negro_inventario_mapa.png", "real_metin_lejos_1.png", "real_rodeado.png",
           "real_inventario_abierto.png"]   # coordenadas (453,161), (704,193), (688,194), (453,169)


@pytest.fixture
def imgs(load_sample):
    return [load_sample(n) for n in SAMPLES]


def test_zona_arma_poligono_y_guarda_con_bak(tmp_cfg, imgs):
    original = tmp_cfg.read_text(encoding="utf-8")
    s = make_session(tmp_cfg, imgs, keys=["f9"] * 4 + ["f10"], answers=["s"])
    assert cal.cmd_zona(s) == 0
    saved = raw(tmp_cfg)
    assert saved["farm_zone"]["polygon"] == QUAD
    assert saved["mi_clave_extra"] == {"a": 1} and saved["window_title"] == "Guabina"
    assert saved["farm_zone"]["outside_tolerance"] == 30   # sin --tolerancia no se toca (queda el de antes)
    assert tmp_cfg.with_name("config.json.bak").read_text(encoding="utf-8") == original
    assert any("Vértice 3: (688, 194)" in line for line in s.out)
    assert (tmp_cfg.parent / "debug" / "calibrate_zona.png").exists()


def test_zona_f8_borra_el_ultimo_y_tolerancia(tmp_cfg, imgs):
    s = make_session(tmp_cfg, imgs, keys=["f9", "f9", "f9", "f8", "f9", "f9", "f10"], answers=["s"])
    # f9 x3 (sam 0,1,2), f8 borra el 3ro, f9 (sam 3), f9 (sam 3 repetida: igual al anterior, no se agrega)
    assert cal.cmd_zona(s, tolerancia=40) == 0
    saved = raw(tmp_cfg)
    assert saved["farm_zone"]["polygon"] == [QUAD[0], QUAD[1], QUAD[3]]
    assert saved["farm_zone"]["outside_tolerance"] == 40


def test_zona_lectura_fallida_no_agrega(tmp_cfg, imgs):
    black = np.zeros_like(imgs[0])
    s = make_session(tmp_cfg, [black] + imgs[:3], keys=["f9"] * 4 + ["f10"], answers=["s"])
    assert cal.cmd_zona(s) == 0
    assert raw(tmp_cfg)["farm_zone"]["polygon"] == QUAD[:3]
    assert any("No se pudieron leer" in line for line in s.out)


def test_zona_con_menos_de_3_vertices_no_termina(tmp_cfg, imgs):
    s = make_session(tmp_cfg, imgs, keys=["f9", "f9", "f10", "f9", "f10"], answers=["s"])
    assert cal.cmd_zona(s) == 0
    assert any("al menos 3" in line for line in s.out)
    assert len(raw(tmp_cfg)["farm_zone"]["polygon"]) == 3


def test_zona_cruzada_ofrece_reordenar(tmp_cfg, imgs):
    order = [imgs[0], imgs[2], imgs[1], imgs[3]]   # P1, P3, P2, P4: aristas cruzadas
    s = make_session(tmp_cfg, order, keys=["f9"] * 4 + ["f10"], answers=["s", "s"])
    assert cal.cmd_zona(s) == 0
    assert any("Orden sugerido" in line for line in s.out)
    poly = raw(tmp_cfg)["farm_zone"]["polygon"]
    assert sorted(poly) == sorted(QUAD)
    from bot.config import validate_farm_zone
    validate_farm_zone({"polygon": poly, "outside_tolerance": 30})   # sin cruces


def test_zona_cruzada_rechazando_el_reorden_no_guarda(tmp_cfg, imgs):
    original = tmp_cfg.read_text(encoding="utf-8")
    order = [imgs[0], imgs[2], imgs[1], imgs[3]]
    s = make_session(tmp_cfg, order, keys=["f9"] * 4 + ["f10"], answers=["n"])
    assert cal.cmd_zona(s) == 1
    assert tmp_cfg.read_text(encoding="utf-8") == original
    assert not tmp_cfg.with_name("config.json.bak").exists()


def test_zona_cancela_con_f12_sin_guardar(tmp_cfg, imgs):
    original = tmp_cfg.read_text(encoding="utf-8")
    s = make_session(tmp_cfg, imgs, keys=["f9", "f9", "f12", "f9", "f10"])
    with pytest.raises(cal.Cancelled):
        cal.cmd_zona(s)
    assert tmp_cfg.read_text(encoding="utf-8") == original
    assert not tmp_cfg.with_name("config.json.bak").exists()
    assert s.keyboard.removed   # soltó los hotkeys


def test_zona_responder_n_no_guarda(tmp_cfg, imgs):
    original = tmp_cfg.read_text(encoding="utf-8")
    s = make_session(tmp_cfg, imgs, keys=["f9"] * 4 + ["f10"], answers=["n"])
    assert cal.cmd_zona(s) == 1
    assert tmp_cfg.read_text(encoding="utf-8") == original
    assert not tmp_cfg.with_name("config.json.bak").exists()


def test_zona_si_no_pregunta(tmp_cfg, imgs):
    s = make_session(tmp_cfg, imgs, keys=["f9"] * 4 + ["f10"], yes=True)
    assert cal.cmd_zona(s) == 0
    assert not any("(s/n)" in p for p in s.out)
    assert raw(tmp_cfg)["farm_zone"]["polygon"] == QUAD


def test_config_invalida_nunca_se_guarda(tmp_cfg, imgs):
    original = tmp_cfg.read_text(encoding="utf-8")
    s = make_session(tmp_cfg, imgs, keys=["f9"] * 4 + ["f10"], yes=True)
    assert cal.cmd_zona(s, tolerancia=-5) == 1   # tolerancia negativa: validate_config la rechaza
    assert tmp_cfg.read_text(encoding="utf-8") == original
    assert not tmp_cfg.with_name("config.json.bak").exists()
    assert any("no es valido" in line for line in s.out)


def test_apply_changes_rechaza_config_invalida(tmp_cfg):
    original = tmp_cfg.read_text(encoding="utf-8")
    s = make_session(tmp_cfg, yes=True)
    assert cal.apply_changes(s, [cal.Change(("coords_filter", "max_speed"), -3)]) is False
    assert tmp_cfg.read_text(encoding="utf-8") == original
    assert not tmp_cfg.with_name("config.json.bak").exists()
    assert any("invalida" in line for line in s.out)


def test_angular_order_y_dibujo():
    img = cal.draw_zone_image(QUAD, 30)
    assert img.ndim == 3 and img.shape[0] > 50 and img.shape[1] > 50
    assert sorted(cal.angular_order([QUAD[0], QUAD[2], QUAD[1], QUAD[3]])) == sorted(QUAD)


# ------------------------------------------------------------------ velocidad

def run_speed(tmp_cfg, readings, answers=("s",), seconds=10, **kw):
    """readings(i) -> (x, y) | None para la i-esima lectura; el reloj falso avanza con sleep()."""
    t = [0.0]
    n = [0]

    def fake_read(img, cfg):
        r = readings(n[0])
        n[0] += 1
        return None if r is None else SimpleNamespace(x=r[0], y=r[1])

    s = make_session(tmp_cfg, keys=["f9"], answers=list(answers), read_coords_fn=fake_read,
                     clock=lambda: t[0], sleep=lambda d: t.__setitem__(0, t[0] + d), **kw)
    return s, cal.cmd_velocidad(s, seconds)


def test_velocidad_calcula_max_y_tipica_ignorando_fallidas(tmp_cfg):
    # 10 u/s: +1 unidad cada 0.1 s; cada 3ra lectura falla
    s, rc = run_speed(tmp_cfg, lambda i: None if i % 3 == 2 else (400 + i, 200))
    assert rc == 0
    assert raw(tmp_cfg)["coords_filter"]["max_speed"] == 15.0   # 10 x 1.5
    assert any("Velocidad tipica: 10.0" in line for line in s.out)
    assert any("fallidas (descartadas): 33" in line for line in s.out)


def test_velocidad_sin_lecturas_no_guarda(tmp_cfg):
    original = tmp_cfg.read_text(encoding="utf-8")
    s, rc = run_speed(tmp_cfg, lambda i: None)
    assert rc == 1 and tmp_cfg.read_text(encoding="utf-8") == original


def test_velocidad_quieto_no_guarda(tmp_cfg):
    original = tmp_cfg.read_text(encoding="utf-8")
    s, rc = run_speed(tmp_cfg, lambda i: (400, 200))
    assert rc == 1 and tmp_cfg.read_text(encoding="utf-8") == original


def test_velocidad_responder_n(tmp_cfg):
    original = tmp_cfg.read_text(encoding="utf-8")
    s, rc = run_speed(tmp_cfg, lambda i: (400 + i, 200), answers=("n",))
    assert rc == 1 and tmp_cfg.read_text(encoding="utf-8") == original


def test_window_speeds_diagonal_y_tramo_corto():
    pts = [(i * 0.5, 3 * i, 4 * i) for i in range(8)]   # 5 u por 0.5 s = 10 u/s
    assert all(abs(v - 10) < 1e-9 for v in cal.window_speeds(pts))
    assert cal.window_speeds([(0.0, 0, 0), (0.4, 4, 0)]) == [10.0]   # menos de 1 s: usa todo el tramo
    assert cal.window_speeds([(0.0, 0, 0)]) == []


# ------------------------------------------------------------------ metin

def label_center(img, cfg=None):
    """Centro de la etiqueta de Metin Negro, detectada con las plantillas reales del proyecto (DEFAULTS)."""
    from bot.config import DEFAULTS
    d = [d for d in find_metins(img, DEFAULTS) if d.name == "metin_negro"][0]
    return d.label_rect.center


def test_metin_guarda_plantilla_de_etiqueta_y_panel_y_agrega_el_nombre(tmp_cfg, load_sample):
    img = load_sample("real_metin_negro_seleccionado.png")
    cfg = load_config(tmp_cfg)
    cx, cy = label_center(img, cfg)
    s = make_session(tmp_cfg, [img], keys=["f9", "f9"], answers=["s", "s"],
                     cursor_fn=lambda: (cx + 20 + ORIGIN[0], cy + 5 + ORIGIN[1]))
    assert cal.cmd_metin(s, "Metin de Prueba") == 0
    # etiqueta: igual a la mascara de la candidata mas cercana al mouse
    tpl = cv2.imread(str(tmp_cfg.parent / "labels" / "metin_de_prueba.png"), cv2.IMREAD_GRAYSCALE)
    cands = find_label_candidates(img, cfg, build_screen_mask(img.shape, cfg))
    cand = min(cands, key=lambda c: abs(c.rect.center[0] - cx) + abs(c.rect.center[1] - cy))
    assert tpl.shape == cand.text_mask.shape and np.array_equal(tpl, cand.text_mask)
    assert (tmp_cfg.parent / "panel_names" / "metin_de_prueba.png").exists()
    assert (tmp_cfg.parent / "debug" / "calibrate_metin.png").exists()
    saved = raw(tmp_cfg)
    assert saved["target_metin_names"] == ["Metin de Oscuridad", "Metin Negro", "Metin de Prueba"]
    assert saved["mi_clave_extra"] == {"a": 1}
    assert tmp_cfg.with_name("config.json.bak").exists()


def test_metin_rechazar_etiqueta_no_guarda(tmp_cfg, load_sample):
    img = load_sample("real_metin_negro_seleccionado.png")
    cx, cy = label_center(img, load_config(tmp_cfg))
    s = make_session(tmp_cfg, [img], keys=["f9", "f10"], answers=["n"],
                     cursor_fn=lambda: (cx + ORIGIN[0], cy + ORIGIN[1]))
    assert cal.cmd_metin(s, "Metin de Prueba") == 1
    assert not (tmp_cfg.parent / "labels").exists()
    assert raw(tmp_cfg)["target_metin_names"] == ["Metin de Oscuridad", "Metin Negro"]


def test_metin_sin_etiqueta_visible_sigue_esperando(tmp_cfg, load_sample):
    img = load_sample("real_metin_negro_seleccionado.png")
    cx, cy = label_center(img, load_config(tmp_cfg))
    s = make_session(tmp_cfg, [np.zeros_like(img), img], keys=["f9", "f9", "f10"], answers=["s", "n"],
                     cursor_fn=lambda: (cx + ORIGIN[0], cy + ORIGIN[1]))
    assert cal.cmd_metin(s, "Metin de Prueba") == 0   # etiqueta guardada; F10 saltea el panel; "n" no agrega el nombre
    assert any("No se vio ninguna etiqueta" in line for line in s.out)
    assert (tmp_cfg.parent / "labels" / "metin_de_prueba.png").exists()
    assert not (tmp_cfg.parent / "panel_names").exists()
    assert raw(tmp_cfg)["target_metin_names"] == ["Metin de Oscuridad", "Metin Negro"]


def test_metin_cancelado_con_f12(tmp_cfg, load_sample):
    img = load_sample("real_metin_negro_seleccionado.png")
    s = make_session(tmp_cfg, [img], keys=["f12"])
    with pytest.raises(cal.Cancelled):
        cal.cmd_metin(s, "X")
    assert not (tmp_cfg.parent / "labels").exists()


def test_metin_nombre_ya_en_la_lista_no_la_toca(tmp_cfg, load_sample):
    img = load_sample("real_metin_negro_seleccionado.png")
    cx, cy = label_center(img, load_config(tmp_cfg))
    s = make_session(tmp_cfg, [img], keys=["f9", "f10"], answers=["s"], yes=True,
                     cursor_fn=lambda: (cx + ORIGIN[0], cy + ORIGIN[1]))
    assert cal.cmd_metin(s, "Metin Negro") == 0
    assert raw(tmp_cfg)["target_metin_names"] == ["Metin de Oscuridad", "Metin Negro"]
    assert (tmp_cfg.parent / "labels" / "metin_negro.png").exists()


# ------------------------------------------------------------------ ventana / zonas / resumen

def win(title, w, h, minimized=False):
    return SimpleNamespace(title=title, client_rect_on_screen=Rect(0, 0, w, h), minimized=minimized)


def test_ventana_elige_y_guarda_titulo_y_tamano(tmp_cfg):
    wins = [win("Otra cosa", 800, 600), win("Guabina2", 1858, 1057)]
    seen = []
    s = make_session(tmp_cfg, answers=["2", "s"], list_windows_fn=lambda f: seen.append(f) or wins)
    assert cal.cmd_ventana(s, "gua") == 0
    assert seen == ["gua"]
    saved = raw(tmp_cfg)
    assert saved["window_title"] == "Guabina2" and saved["expected_client_size"] == [1858, 1057]
    assert saved["mi_clave_extra"] == {"a": 1}
    assert tmp_cfg.with_name("config.json.bak").exists()


def test_ventana_numero_invalido_o_minimizada_no_guarda(tmp_cfg):
    original = tmp_cfg.read_text(encoding="utf-8")
    s = make_session(tmp_cfg, answers=["9"], list_windows_fn=lambda f: [win("A", 10, 10)])
    assert cal.cmd_ventana(s) == 1
    s = make_session(tmp_cfg, answers=["1"], list_windows_fn=lambda f: [win("A", 0, 0, True)])
    assert cal.cmd_ventana(s) == 1
    s = make_session(tmp_cfg, list_windows_fn=lambda f: [])
    assert cal.cmd_ventana(s) == 1
    assert tmp_cfg.read_text(encoding="utf-8") == original


def test_zonas_genera_imagen_sin_tocar_la_config(tmp_cfg, load_sample):
    img = load_sample("real_metin_negro_inventario_mapa.png")
    original = tmp_cfg.read_text(encoding="utf-8")
    s = make_session(tmp_cfg, [img])
    assert cal.cmd_zonas(s) == 0
    out = cv2.imread(str(tmp_cfg.parent / "debug" / "calibrate_zonas.png"))
    assert out is not None and out.shape == img.shape
    text = "\n".join(s.out)
    assert "Ventanas detectadas: 2" in text and "(453, 161)" in text
    assert "chat: anchor=bottom-center dx=-340 dy=-190 w=640 h=190" in text
    assert tmp_cfg.read_text(encoding="utf-8") == original


def test_resumen_imprime_los_valores(tmp_cfg):
    s = make_session(tmp_cfg)
    original = tmp_cfg.read_text(encoding="utf-8")
    assert cal.cmd_resumen(s) == 0
    text = "\n".join(s.out)
    for key, how in [(k, h) for _, items in cal.SUMMARY for k, h in items]:
        assert key in text
    assert "coords_filter.max_speed = 30.0" in text and "explore.camera_steps = 12" in text
    assert "test_input.py" in text
    assert tmp_cfg.read_text(encoding="utf-8") == original


def test_summary_usa_claves_que_existen():
    from bot.config import DEFAULTS
    for _, items in cal.SUMMARY:
        for key, _how in items:
            cal.get_path(DEFAULTS, tuple(key.split(".")))   # KeyError si no existe


# ------------------------------------------------------------------ CLI

def test_parser_subcomandos():
    p = calibrate.build_parser()
    a = p.parse_args(["zona", "--tolerancia", "25", "--si", "--config", "x.json"])
    assert (a.cmd, a.tolerancia, a.si, a.config) == ("zona", 25.0, True, "x.json")
    assert p.parse_args(["velocidad"]).segundos == 10.0
    assert p.parse_args(["metin", "Metin X"]).nombre == "Metin X"
    assert p.parse_args(["ventana", "--filtro", "g"]).filtro == "g"
    with pytest.raises(SystemExit):
        p.parse_args([])


def test_main_resumen_no_manda_input(tmp_cfg, capsys):
    assert calibrate.main(["resumen", "--config", str(tmp_cfg)]) == 0
    assert "stuck.space_s" in capsys.readouterr().out


def test_main_cancelado_devuelve_1(tmp_cfg, monkeypatch, capsys):
    def boom(args, s):
        raise KeyboardInterrupt
    monkeypatch.setattr(calibrate, "run", boom)
    assert calibrate.main(["resumen", "--config", str(tmp_cfg)]) == 1
    assert "No se guardo nada" in capsys.readouterr().out
