"""RECOGER a pie: mira, se baja (Ctrl+G) solo si hay objetos validos, clic en la etiqueta mas cercana, Z al llegar y
se sube al terminar. Todo simulado (backend falso, nada de input real)."""
import logging
import math

import pytest

from bot.bot import State
from bot.control import PanicStop
from bot.geometry import Rect
from bot.vision.items import ItemLabel
from bot.vision.monsters import MonsterLabel
from test_bot_states import CAPTCHA, INSIDE_XY, PLAYER, Harness, make_cfg, perc

OFFSET = (8, 31)   # FakeWindow: cliente -> pantalla
NEAR_A = (PLAYER[0] + 300, PLAYER[1])
NEAR_B = (PLAYER[0], PLAYER[1] + 200)
SLOW = {"still_s": 500.0, "walk_timeout_s": 500.0, "max_seconds": 500.0}   # solo llega por cercania de la etiqueta


def label(p, kind="item", reserved_other=False, allowed=True):
    return ItemLabel(Rect(p[0] - 40, p[1] - 7, 80, 15), kind, False, (int(p[0]), int(p[1])), allowed, 0.0,
                     reserved_other=reserved_other)


class Floor:
    """Objetos en el piso: un clic sobre la etiqueta acerca el objeto al jugador (tras `walk` ciclos); Z lo junta
    si esta cerca. `log` registra lo que paso en orden."""

    def __init__(self, h, pts, pickable=True, walk=5, extra=None):
        self.h, self.walk, self.pickable, self.extra = h, walk, pickable, extra
        self.items = [{"p": tuple(p), "left": 0} for p in pts]
        self.seen, self.last_move, self.log = 0, (0, 0), []

    def _events(self):
        ev = self.h.be.events
        for e in ev[self.seen:]:
            if e[0] == "move_to":
                self.last_move = (e[1] - OFFSET[0], e[2] - OFFSET[1])
            elif e == ("mouse_down", "left"):
                it = min(self.items, key=lambda i: math.dist(i["p"], self.last_move), default=None)
                if it is not None and math.dist(it["p"], self.last_move) < 20:
                    it["left"] = self.walk
                self.log.append(("click", self.last_move))
            elif e == ("key_down", "z"):
                near = [i for i in self.items if math.dist(i["p"], PLAYER) <= 70]
                self.log.append(("z", bool(near)))
                if near and self.pickable:
                    self.items.remove(near[0])
            elif e == ("key_down", "g"):
                self.log.append(("g",))
        self.seen = len(ev)

    def __call__(self, h, n):
        self._events()
        for it in self.items:   # el objeto se acerca al jugador de a poco (la camara sigue al personaje)
            if it["left"] > 0:
                tx, ty = PLAYER[0] + 20, PLAYER[1]
                f = 1.0 / it["left"]
                it["p"] = (it["p"][0] + (tx - it["p"][0]) * f, it["p"][1] + (ty - it["p"][1]) * f)
                it["left"] -= 1
        p = perc(items=[label(i["p"]) for i in self.items])
        if self.extra:
            self.extra(p)
        return p


def start(h):
    h.bot._enter(State.RECOGER, "test")


def floor_harness(pts, bot_kwargs=None, floor_kw=None, **over):
    box = {}
    h = Harness(lambda h, n: box["f"](h, n), bot_kwargs=bot_kwargs, **over)
    box["f"] = h.floor = Floor(h, pts, **(floor_kw or {}))
    return h


def kinds(h):
    h.floor._events()
    return [e[0] for e in h.floor.log]


def test_con_objetos_baja_recoge_el_mas_cercano_primero_y_sube():
    h = floor_harness([NEAR_A, NEAR_B], pickup=SLOW)
    start(h)
    h.run_until(lambda: h.bot.state is State.BUSCAR)
    log = h.floor.log
    assert kinds(h) == ["g", "click", "z", "click", "z", "g"]
    assert log[1] == ("click", NEAR_B) and log[3] == ("click", NEAR_A)   # el mas cercano primero
    assert log[2] == ("z", True) and log[4] == ("z", True)               # Z solo con el objeto ya al lado
    assert h.keys("g") == 2 and not h.bot._dismounted and h.bot.stats.items == 2


def test_sin_objetos_validos_no_se_baja():
    cases = [[], [label(NEAR_A, kind="yang")], [label(NEAR_A, reserved_other=True)], [label(NEAR_A, allowed=False)]]
    for items in cases:
        h = Harness(lambda h, n, items=items: perc(items=items))
        start(h)
        h.run_until(lambda: h.bot.state is State.BUSCAR)
        assert h.keys("g") == 0 and h.keys("z") == 0 and h.clicks() == 0, items


def test_sin_dismount_mismo_flujo_sin_ctrl_g():
    h = floor_harness([NEAR_A, NEAR_B], pickup={**SLOW, "dismount": False})
    start(h)
    h.run_until(lambda: h.bot.state is State.BUSCAR)
    assert kinds(h) == ["click", "z", "click", "z"] and h.keys("g") == 0


def test_etiqueta_con_monstruo_pegado_no_se_clickea(caplog):
    caplog.set_level(logging.INFO)

    def extra(p):
        p.monsters = [MonsterLabel(Rect(NEAR_A[0] - 40, NEAR_A[1] - 20, 80, 12), (NEAR_A[0], NEAR_A[1] - 14))]
    h = floor_harness([NEAR_A], floor_kw={"extra": extra}, pickup={"max_presses": 4, "press_interval_ms": 0})
    start(h)
    h.run_until(lambda: h.bot.state is State.BUSCAR)
    assert h.clicks() == 0 and h.keys("z") == 4 and h.keys("g") == 2
    assert "Etiqueta con monstruo pegado: no la clickeo" in caplog.text


def test_monstruo_lejos_no_impide_el_clic():
    def extra(p):
        p.monsters = [MonsterLabel(Rect(NEAR_A[0] - 40, NEAR_A[1] - 60, 80, 12), (NEAR_A[0], NEAR_A[1] - 54))]
    h = floor_harness([NEAR_A], floor_kw={"extra": extra}, pickup=SLOW)
    start(h)
    h.run_until(lambda: h.bot.state is State.BUSCAR)
    assert h.clicks() == 1


def test_objeto_que_no_se_puede_juntar_se_ignora_sin_loop_infinito():
    h = floor_harness([NEAR_A], floor_kw={"pickable": False}, pickup={**SLOW, "press_interval_ms": 0})
    start(h)
    h.run_until(lambda: h.bot.state is State.BUSCAR, limit=300)
    assert h.keys("z") == 2          # tries_per_item
    assert h.keys("g") == 2 and not h.bot._dismounted


def test_captcha_durante_la_bajada():
    flag = {"gone": False}
    box = {}
    h = Harness(lambda h, n: perc(items=[label(NEAR_B)], captcha=box["cap"]()),
                bot_kwargs={"captcha_fn": lambda img, cfg: box["cap"]()})
    box["cap"] = lambda: CAPTCHA if (h.keys("g") >= 1 and not flag["gone"]) else None
    start(h)
    h.bot.step()    # ve el objeto, Ctrl+G, y en la espera aparece el panel
    assert h.bot.state is State.CAPTCHA and h.bot._dismounted and h.keys("g") == 1
    n = len(h.be.events)
    for _ in range(3):
        h.bot.step()
    assert len(h.be.events) == n
    flag["gone"] = True
    h.run_until(lambda: h.bot.state is State.BUSCAR)
    assert h.keys("g") == 2 and not h.bot._dismounted


def test_captcha_durante_la_recogida():
    flag = {"cap": False}
    h = floor_harness([NEAR_A], pickup=SLOW)
    base = h.floor
    h.script = lambda h, n: _with_captcha(base(h, n), flag)
    start(h)
    h.run_until(lambda: h.bot.state is State.RECOGER and base.log and base.log[-1][0] == "click")
    flag["cap"] = True
    h.bot.step()
    assert h.bot.state is State.CAPTCHA
    n = len(h.be.events)
    for _ in range(3):
        h.bot.step()
    assert len(h.be.events) == n
    flag["cap"] = False
    h.run_until(lambda: h.bot.state is State.BUSCAR)
    assert h.keys("g") == 2 and not h.bot._dismounted


def _with_captcha(p, flag):
    p.captcha = CAPTCHA if flag["cap"] else None
    return p


def test_marca_a_pie_se_escribe_al_bajar_y_se_borra_al_subir(tmp_path):
    marker = tmp_path / "a_pie.flag"
    h = floor_harness([NEAR_B], bot_kwargs={"mount_marker": marker}, pickup=SLOW)
    start(h)
    assert not marker.exists()
    h.run_until(lambda: h.bot._dismounted)
    assert marker.exists() and marker.read_text(encoding="utf-8").strip()
    h.run_until(lambda: h.bot.state is State.BUSCAR)
    assert not marker.exists() and h.keys("g") == 2


def test_sin_marker_no_se_escribe_nada(tmp_path):
    h = floor_harness([NEAR_B], pickup=SLOW)
    start(h)
    h.run_until(lambda: h.bot.state is State.BUSCAR)
    assert list(tmp_path.iterdir()) == []


def test_panico_a_pie_deja_la_marca_y_la_corrida_nueva_se_sube_primero(tmp_path):
    marker = tmp_path / "a_pie.flag"
    h = floor_harness([NEAR_B], bot_kwargs={"mount_marker": marker}, pickup=SLOW)
    start(h)
    h.run_until(lambda: h.bot._dismounted)

    def boom(h, n):
        raise PanicStop("F12")
    h.script = boom
    with pytest.raises(PanicStop):
        h.bot.step()
    assert marker.exists() and h.keys("g") == 1

    h2 = Harness(lambda h, n: perc(), bot_kwargs={"mount_marker": marker})
    h2.bot.run(max_minutes=0.01)
    assert h2.be.events[:2] == [("key_down", "ctrl"), ("key_down", "g")]
    assert h2.keys("g") == 1 and not marker.exists() and not h2.bot._dismounted


def test_tope_de_tiempo_de_run_a_pie_se_sube():
    h = floor_harness([NEAR_B], pickup=SLOW)
    start(h)
    h.run_until(lambda: h.bot._dismounted)
    h.bot.run(max_minutes=0)
    assert h.keys("g") == 2 and not h.bot._dismounted and h.bot.stats.end_reason == "tiempo"


def test_dismounted_solo_se_asigna_en_un_lugar():
    import inspect
    import bot.bot as mod
    src = inspect.getsource(mod)
    assert src.count("self._dismounted = ") == 2   # __init__ y _set_dismounted
