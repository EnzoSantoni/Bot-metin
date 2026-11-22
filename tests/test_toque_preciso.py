"""Toques de T de la camara con duracion precisa (bot/input/precise.py, GameInput.tap): la espera normal en Windows salta de a
~15 ms y `hold` la partia en tramos de 50 ms (un toque de 120 ms duraba 120-165 ms y el angulo del reinicio no se repetia)."""
import pytest

import bot.input.precise as precise
from test_game_input import make


class FakeTime:
    """Reloj falso: `sleep` avanza el reloj lo pedido + `oversleep` (como el temporizador del sistema)."""

    def __init__(self, oversleep=0.0):
        self.t, self.oversleep, self.sleeps, self.reads = 0.0, oversleep, [], 0

    def clock(self):
        self.reads += 1
        self.t += 0.0001   # cada lectura del reloj "tarda" 0.1 ms (la espera activa avanza)
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s + self.oversleep


@pytest.mark.parametrize("oversleep", [0.0, 0.0015])
def test_wait_until_nunca_vuelve_antes_y_duerme_hasta_el_final_activo(monkeypatch, oversleep):
    ft = FakeTime(oversleep)
    monkeypatch.setattr(precise, "_clock", ft.clock)
    monkeypatch.setattr(precise, "_sleep", ft.sleep)
    precise.wait_until(0.120)
    assert ft.t >= 0.120
    assert ft.t - 0.120 <= 0.0002   # el sleep se pasa menos que SPIN_S: el final lo cubre la espera activa
    assert ft.sleeps and all(s > 0 for s in ft.sleeps)
    assert ft.sleeps[0] == pytest.approx(0.120 - 0.0001 - precise.SPIN_S)   # duerme hasta SPIN_S antes; despues gira


def test_tap_aprieta_y_suelta_una_vez_con_la_duracion_pedida():
    g, be, _, _ = make()
    assert g.tap("camera_flat", 0.08) is True
    keys = [e for e in be.events if e[0] in ("key_down", "key_up")]
    assert keys == [("key_down", "t"), ("key_up", "t")]
    assert 0.080 <= g.last_tap_s <= 0.086   # reloj de alta precision: sin el salto de ~15 ms de la espera normal
    assert be.pressed == ([], [])


def test_tap_no_admite_combinaciones():
    g, _, _, _ = make()
    with pytest.raises(ValueError):
        g.tap("mount", 0.05)   # ctrl+g


def test_tap_repetido_queda_parejo():
    g, _, _, _ = make()
    durs = []
    for _ in range(5):
        g.tap("camera_flat", 0.12)
        durs.append(g.last_tap_s)
    assert max(durs) - min(durs) <= 0.006, durs
