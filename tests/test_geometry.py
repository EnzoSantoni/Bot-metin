import numpy as np
import pytest

from bot.geometry import Rect, client_to_screen, screen_to_client


def test_rect_basic():
    r = Rect(10, 20, 100, 50)
    assert (r.right, r.bottom, r.center) == (110, 70, (60, 45))
    assert r.contains(10, 20) and r.contains(109, 69)
    assert not r.contains(110, 20) and not r.contains(9, 20)


def test_intersect_clip():
    r = Rect(10, 10, 100, 100)
    assert r.intersect(Rect(50, 50, 100, 100)) == Rect(50, 50, 60, 60)
    assert r.intersect(Rect(200, 200, 5, 5)) is None
    assert Rect(-5, -5, 20, 20).clip(100, 100) == Rect(0, 0, 15, 15)
    assert Rect(500, 0, 10, 10).clip(100, 100) is None


def test_crop():
    img = np.arange(100 * 200 * 3, dtype=np.uint8).reshape(100, 200, 3)
    c = Rect(10, 5, 30, 20).crop(img)
    assert c.shape == (20, 30, 3)
    assert np.array_equal(c, img[5:25, 10:40])
    assert Rect(190, 90, 50, 50).crop(img).shape == (10, 10, 3)
    assert Rect(500, 500, 5, 5).crop(img).size == 0


@pytest.mark.parametrize("origin", [(8, 31), (-8, 23)])
def test_client_screen_roundtrip(origin):
    cr = Rect(*origin, 1920, 1080)
    sx, sy = client_to_screen(100, 200, cr)
    assert (sx, sy) == (100 + origin[0], 200 + origin[1])
    assert screen_to_client(sx, sy, cr) == (100, 200)


def test_strict():
    cr = Rect(8, 31, 1920, 1080)
    for x, y in [(-1, 0), (0, -1), (1920, 0), (0, 1080)]:
        with pytest.raises(ValueError):
            client_to_screen(x, y, cr)
    assert client_to_screen(1920, 0, cr, strict=False) == (1928, 31)
    assert screen_to_client(0, 0, cr) == (-8, -31)
