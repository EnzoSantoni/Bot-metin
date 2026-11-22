"""Captcha de la frase dorada (arriba al centro): se detecta como captcha (el bot no lo resuelve) y no da falsos."""
from pathlib import Path

import cv2
import pytest

from bot.config import load_config
from bot.vision.captcha import find_captcha

ROOT = Path(__file__).resolve().parent.parent
FRASE = ROOT / "tests" / "data" / "ataque_frase_captcha_2132.png"


@pytest.fixture(scope="module")
def cfg():
    return load_config()


def test_la_frase_dorada_se_detecta_como_captcha(cfg):
    info = find_captcha(cv2.imread(str(FRASE)), cfg)
    assert info is not None and info.score >= 0.8
    assert abs(info.rect.x - 656) <= 4 and abs(info.rect.y - 141) <= 4


def test_el_cartel_vacio_y_los_anuncios_del_servidor_no_son_captcha(cfg):
    """Mismo marco dorado, otro texto: el cartel sin frase (2129) y el anuncio "Quiero felicitarlos..." de samples/tests."""
    for name in ("tests/data/ataque_punto_viejo_2129.png", "samples/metin_muy_lejos.png",
                 "tests/data/estructura_negro_sin_humo.png"):
        assert find_captcha(cv2.imread(str(ROOT / name)), cfg) is None, name


def test_ninguna_otra_captura_real_da_falso(cfg):
    files = [f for f in list((ROOT / "tests" / "data").glob("*.png")) + list((ROOT / "samples").glob("*.png"))
             if f.name not in (FRASE.name, "captcha.png")]
    assert len(files) > 20
    for f in files:
        im = cv2.imread(str(f))
        if im is None or im.shape[0] < 300:
            continue
        assert find_captcha(im, cfg) is None, f.name


def test_el_bot_pasa_a_captcha_con_la_frase_y_sale_al_desaparecer():
    """Con la percepcion real: la frase en pantalla -> CAPTCHA con alarma (sin teclas); sin ella -> sale."""
    from bot.bot import State
    from bot.perception import perceive
    from test_bot_states import Harness
    on, off = cv2.imread(str(FRASE)), cv2.imread(str(ROOT / "tests" / "data" / "ataque_punto_viejo_2129.png"))
    box = {"img": on}
    h = Harness(lambda h, n: perceive(box["img"], h.cfg, parts={"metins"}))
    h.bot.step()
    assert h.bot.state is State.CAPTCHA and h.alarm.starts == ["captcha"] and h.clicks() == 0
    box["img"] = off
    h.run_until(lambda: h.bot.state is not State.CAPTCHA, limit=50)
    assert h.bot.state is not State.CAPTCHA
