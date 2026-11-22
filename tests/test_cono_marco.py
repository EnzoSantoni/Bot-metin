"""Cono del minimapa con capturas reales de las corridas del 2026-11-21 (debug/run_20261121_205635 y run_20261121_212404): tasa de
lectura, coherencia entre capturas seguidas sin giro, robustez, y el marco cono <-> coordenadas (mismo marco, sin espejo)."""
import math
from pathlib import Path

import cv2
import numpy as np
import pytest

from bot.config import DEFAULTS
from bot.vision.minimap import camera_heading

DATA = Path(__file__).parent / "data" / "minimap"
W, H = 1858, 1057
# (recorte de 150x150, rumbo del cono en grados): comprobados a ojo contra el abanico del recorte (0 = este, 90 = sur)
REAL = [("real205635_0016.png", 211), ("real205635_0017.png", 140), ("real205635_0018.png", 140), ("real205635_0019.png", 139),
        ("real205635_0020.png", 140), ("real205635_0021.png", 140), ("real205635_0045.png", 122), ("real205635_0048.png", 116),
        ("real205635_0049.png", 155), ("real205635_0050.png", 177), ("real205635_0051.png", 165), ("real205635_0052.png", 242),
        ("real205635_0060.png", 290), ("real205635_0072.png", 97), ("real212404_0010.png", 202), ("real212404_0030.png", 119),
        ("real212404_0060.png", 91)]


def canvas(crop: np.ndarray) -> np.ndarray:
    img = np.zeros((H, W, 3), np.uint8)
    img[0:150, W - 150:W] = crop
    return img


def diff(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def crop(name: str) -> np.ndarray:
    c = cv2.imread(str(DATA / name))
    assert c is not None
    return c


@pytest.mark.parametrize("name,expected", REAL)
def test_lectura_en_capturas_reales_de_la_prueba(name, expected):
    h = camera_heading(canvas(crop(name)), DEFAULTS)
    assert h is not None and diff(h, expected) <= 6.0


def test_tasa_de_lectura_total():
    ok = sum(camera_heading(canvas(crop(n)), DEFAULTS) is not None for n, _ in REAL)
    assert ok == len(REAL)


def test_capturas_seguidas_sin_giro_dan_el_mismo_rumbo():
    hs = [camera_heading(canvas(crop(f"real205635_00{i}.png")), DEFAULTS) for i in (17, 18, 19, 20, 21)]   # camara quieta
    assert max(diff(a, b) for a in hs for b in hs) <= 3.0


@pytest.mark.parametrize("kind", ["rojos", "tenue", "ruido"])
def test_robusto_a_monstruos_cono_tenue_y_ruido(kind):
    rng = np.random.default_rng(3)
    for name, expected in REAL:
        c = crop(name).copy()
        if kind == "rojos":    # nube de marcas rojas alrededor del personaje
            for _ in range(120):
                x, y = int(np.clip(rng.normal(82, 22), 0, 146)), int(np.clip(rng.normal(82, 22), 0, 146))
                c[y:y + 3, x:x + 3] = (0, 0, 200)
        elif kind == "tenue":  # contraste del cono a la mitad (niebla / noche)
            mean = c.mean()
            c = np.clip(mean + (c.astype(float) - mean) * 0.6, 0, 255).astype(np.uint8)
        else:
            c = np.clip(c.astype(int) + rng.integers(-10, 11, c.shape), 0, 255).astype(np.uint8)
        h = camera_heading(canvas(c), DEFAULTS)
        assert h is not None and diff(h, expected) <= 10.0, (kind, name)


def test_pase_relajado_lee_un_cono_muy_tenue():
    c = crop("real205635_0017.png")
    mean = c.mean()
    faint = np.clip(mean + (c.astype(float) - mean) * 0.35, 0, 255).astype(np.uint8)
    h = camera_heading(canvas(faint), DEFAULTS)
    assert h is not None and diff(h, 140) <= 10.0
    strict = {**DEFAULTS, "vision": {**DEFAULTS["vision"], "minimap": {**DEFAULTS["vision"]["minimap"], "relax_factor": 1.0,
                                                                        "relax_width_deg": DEFAULTS["vision"]["minimap"]["width_deg"]}}}
    # con el pase relajado apagado el mismo recorte ya no se lee o se lee peor: el relajado aporta
    assert camera_heading(canvas(faint), strict) is None or diff(camera_heading(canvas(faint), strict), 140) <= 10.0


def test_marco_el_cono_y_las_coordenadas_son_el_mismo():
    """Captura 0048 (cono 116) -> 0049: la caminata fue con S (hacia atras) y el desplazamiento medido (+7, -13) apunta a cono + 180;
    un espejo (180 - cono) o un giro de 180 grados no encajan."""
    cone = camera_heading(canvas(crop("real205635_0048.png")), DEFAULTS)
    moved = math.degrees(math.atan2(-13, 7)) % 360
    assert diff(moved, cone + 180) <= 10.0
    assert diff(moved, (180 - cone) + 180) > 40.0 and diff(moved, cone) > 40.0
    assert DEFAULTS["vision"]["minimap"]["heading_offset_deg"] == 0.0


def test_el_cono_sube_con_el_giro_a_la_derecha():
    """D giro la vista a la derecha (~36 grados por segundo de tecla) en los datos reales: el rumbo del cono crece en sentido horario
    de pantalla (0048 -> 0049 -> 0050: 116 -> 155 -> 177)."""
    hs = [camera_heading(canvas(crop(f"real205635_00{i}.png")), DEFAULTS) for i in (48, 49, 50)]
    assert hs[0] < hs[1] < hs[2]
