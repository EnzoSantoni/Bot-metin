"""RECOGER a pie y SIN clics: mira, se baja (Ctrl+G) solo si hay objetos validos, camina con W/A/S/D (combinaciones de 8
direcciones) hacia el objeto mas cercano, Z al llegar y se sube al terminar. Todo simulado (backend falso, nada de input
real)."""
import logging
import math

import pytest

from bot.bot import State
from bot.control import PanicStop
from bot.geometry import Rect
from bot.ground import feet_of
from bot.vision.items import ItemLabel
from test_bot_states import CAPTCHA, OUT_XY, PLAYER, Harness, make_cfg, perc

FEET = feet_of(PLAYER, make_cfg())
STEP_PX = 120.0          # cuanto se desplaza (en pantalla) un tramo de teclas en el mundo falso
DIRS = {"w": (0.0, -1.0), "s": (0.0, 1.0), "a": (-1.0, 0.0), "d": (1.0, 0.0)}
ARRIVE = 70.0            # pickup.arrive_px por defecto
# etiquetas (centro): el punto del objeto es el centro inferior + 10 px = (x, y + 18)
UP_RIGHT = (PLAYER[0] + 500, 450)          # arriba y a la derecha de los pies -> W+D
DOWN = (PLAYER[0], 800)                    # abajo -> S
LEFT = (PLAYER[0] - 400, FEET[1] - 18)     # a la izquierda, a la misma altura de los pies -> A


def label(p, kind="item", reserved_other=False, allowed=True):
    return ItemLabel(Rect(int(p[0]) - 40, int(p[1]) - 7, 80, 15), kind, False, (int(p[0]), int(p[1])), allowed, 0.0,
                     reserved_other=reserved_other)


def item_point(p):
    return p[0], p[1] + 18


class Floor:
    """Objetos en el piso. Cada tramo de teclas (key_down ... key_up de todas) desplaza los objetos en pantalla al revés de
    la direccion caminada (suma normalizada de las teclas); Z junta el objeto si su punto esta a <= `ARRIVE` de los pies.
    `log` anota en orden ("combo", "W+D") por cada tramo, ("z", hay_objeto_cerca), ("g",) y ("click",)."""

    def __init__(self, h, pts, pickable=True, blocked=False):
        self.h, self.pickable, self.blocked = h, pickable, blocked
        self.items = [list(map(float, p)) for p in pts]
        self.seen, self.down, self.leg, self.log = 0, set(), set(), []

    def _leg_done(self):
        keys = self.leg
        self.leg = set()
        if not keys:
            return
        self.log.append(("combo", "+".join(k.upper() for k in ("w", "s", "a", "d") if k in keys)))
        if self.blocked:
            return
        dx = sum(DIRS[k][0] for k in keys)
        dy = sum(DIRS[k][1] for k in keys)
        n = math.hypot(dx, dy)
        for it in self.items:
            it[0] -= dx / n * STEP_PX
            it[1] -= dy / n * STEP_PX

    def _events(self):
        ev = self.h.be.events
        for e in ev[self.seen:]:
            if e[0] == "key_down" and e[1] in DIRS:
                self.down.add(e[1])
                self.leg.add(e[1])
            elif e[0] == "key_up" and e[1] in DIRS:
                self.down.discard(e[1])
                if not self.down:
                    self._leg_done()
            elif e == ("mouse_down", "left"):
                self.log.append(("click",))
            elif e == ("key_down", "z"):
                near = [i for i in self.items if math.dist(item_point(i), FEET) <= ARRIVE]
                self.log.append(("z", bool(near)))
                if near and self.pickable:
                    self.items.remove(near[0])
            elif e == ("key_down", "g"):
                self.log.append(("g",))
        self.seen = len(ev)

    def __call__(self, h, n):
        self._events()
        return perc(items=[label(i) for i in self.items])


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


def combos(h):
    h.floor._events()
    return [e[1] for e in h.floor.log if e[0] == "combo"]


@pytest.mark.parametrize("pt,first", [(UP_RIGHT, "W+D"), (DOWN, "S"), (LEFT, "A")])
def test_camina_con_la_combinacion_de_su_sector_y_nunca_clickea(pt, first):
    h = floor_harness([pt])
    start(h)
    h.run_until(lambda: h.bot.state is State.BUSCAR)
    cs = combos(h)
    assert cs[0] == first
    assert h.clicks() == 0 and not any(e[0] == "click" for e in h.floor.log)
    assert h.floor.log[-2] == ("z", True)                      # Z solo al llegar (con el objeto ya junto a los pies)
    assert not h.floor.items and h.bot.stats.items == 1
    assert h.keys("g") == 2 and not h.bot._dismounted          # Ctrl+G par: se baja y se sube


def test_con_objetos_alrededor_va_al_mas_cercano_primero_y_los_junta_todos(caplog):
    caplog.set_level(logging.INFO)
    h = floor_harness([UP_RIGHT, DOWN, LEFT])
    start(h)
    h.run_until(lambda: h.bot.state is State.BUSCAR)
    assert not h.floor.items and h.bot.stats.items == 3
    assert h.clicks() == 0 and h.keys("z") == 3 and h.keys("g") == 2
    assert kinds(h)[0] == "g" and kinds(h)[-1] == "g"
    assert combos(h)[0] == "A"                                  # a distancia de piso, el de la izquierda es el mas cercano
    assert {"W+D", "A"} <= set(combos(h)) and any("S" in c for c in combos(h))
    assert any("Recoger: camino A 0.25 s hacia objeto" in r.message for r in caplog.records)


def test_los_tramos_son_cortos_cerca_del_objeto(caplog):
    caplog.set_level(logging.INFO)
    h = floor_harness([(FEET[0] + 80, FEET[1] - 18)])           # a 80 px de los pies: menos que full_step_px
    start(h)
    h.run_until(lambda: h.bot.state is State.BUSCAR)
    assert any("camino D 0.10 s" in r.message for r in caplog.records)      # min_step_s


def test_sin_objetos_validos_no_se_baja():
    cases = [[], [label(DOWN, kind="yang")], [label(DOWN, reserved_other=True)], [label(DOWN, allowed=False)]]
    for items in cases:
        h = Harness(lambda h, n, items=items: perc(items=items))
        start(h)
        h.run_until(lambda: h.bot.state is State.BUSCAR)
        assert h.keys("g") == 0 and h.keys("z") == 0 and h.clicks() == 0, items
        assert not any(e[0] == "key_down" and e[1] in DIRS for e in h.be.events)


def test_sin_dismount_mismo_flujo_sin_ctrl_g():
    h = floor_harness([UP_RIGHT, DOWN], pickup={"dismount": False})
    start(h)
    h.run_until(lambda: h.bot.state is State.BUSCAR)
    assert h.keys("g") == 0 and h.keys("z") == 2 and h.clicks() == 0 and not h.floor.items


def test_un_monstruo_pegado_ya_no_importa_y_no_hay_clics():
    h = floor_harness([DOWN])
    start(h)
    h.run_until(lambda: h.bot.state is State.BUSCAR)
    assert h.clicks() == 0 and h.keys("z") == 1
    assert "monsters" not in {x for c in h.calls if c[0] is State.RECOGER for x in c[1]}   # RECOGER ya no mira monstruos


def test_objeto_inalcanzable_se_ignora_sin_loop_infinito(caplog):
    caplog.set_level(logging.INFO)
    h = floor_harness([UP_RIGHT], floor_kw={"blocked": True})   # las teclas no lo acercan (pared)
    start(h)
    h.run_until(lambda: h.bot.state is State.BUSCAR, limit=300)
    assert h.keys("z") == 0 and h.clicks() == 0
    assert h.keys("g") == 2 and not h.bot._dismounted
    assert "no se puede juntar" in caplog.text and "sin acercarme" in caplog.text


def test_objeto_que_no_se_puede_juntar_estando_encima_se_ignora():
    h = floor_harness([DOWN], floor_kw={"pickable": False}, pickup={"press_interval_ms": 0})
    start(h)
    h.run_until(lambda: h.bot.state is State.BUSCAR, limit=300)
    assert h.keys("z") == 2          # tries_per_item
    assert h.keys("g") == 2 and not h.bot._dismounted and h.clicks() == 0


def test_tras_un_z_fallido_da_un_tramo_corto_antes_del_siguiente():
    h = floor_harness([(FEET[0] + 30, FEET[1] - 18)], floor_kw={"pickable": False}, pickup={"press_interval_ms": 0})
    start(h)
    h.run_until(lambda: h.bot.state is State.BUSCAR, limit=300)
    h.floor._events()
    seq = [e[0] for e in h.floor.log if e[0] in ("z", "combo")]
    assert seq[:2] == ["z", "combo"] and seq[-1] == "z" and seq.count("z") == 2   # Z, tramo corto, Z (y se ignora)


def test_fuera_de_la_zona_corta_la_recogida_y_vuelve():
    h = floor_harness([DOWN])
    h.script = lambda h, n: perc(items=[label(DOWN)], coords=OUT_XY if h.keys("g") >= 1 else (550, 150))
    start(h)
    h.run_until(lambda: h.bot.state is State.VOLVER)
    assert h.keys("z") == 0 and h.keys("g") == 2


def test_captcha_durante_la_bajada():
    flag = {"gone": False}
    box = {}
    h = Harness(lambda h, n: perc(items=[label(DOWN)], captcha=box["cap"]()),
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


def test_captcha_durante_la_caminata():
    flag = {"cap": False}
    h = floor_harness([UP_RIGHT])
    base = h.floor
    h.script = lambda h, n: _with_captcha(base(h, n), flag)
    h.bot._captcha_fn = lambda img, cfg: CAPTCHA if flag["cap"] else None
    start(h)
    h.run_until(lambda: h.bot.state is State.RECOGER and base.log and base.log[-1][0] == "combo")
    flag["cap"] = True
    h.bot.step()
    assert h.bot.state is State.CAPTCHA
    assert h.be.pressed == ([], [])                          # el captcha suelta todo
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


def test_las_teclas_siempre_se_sueltan_todas():
    h = floor_harness([UP_RIGHT, LEFT])
    start(h)
    h.run_until(lambda: h.bot.state is State.BUSCAR)
    downs = [e for e in h.be.events if e[0] == "key_down" and e[1] in DIRS]
    ups = [e for e in h.be.events if e[0] == "key_up" and e[1] in DIRS]
    assert len(downs) == len(ups) and h.be.pressed == ([], [])


def test_marca_a_pie_se_escribe_al_bajar_y_se_borra_al_subir(tmp_path):
    marker = tmp_path / "a_pie.flag"
    h = floor_harness([DOWN], bot_kwargs={"mount_marker": marker})
    start(h)
    assert not marker.exists()
    h.run_until(lambda: h.bot._dismounted)
    assert marker.exists() and marker.read_text(encoding="utf-8").strip()
    h.run_until(lambda: h.bot.state is State.BUSCAR)
    assert not marker.exists() and h.keys("g") == 2


def test_sin_marker_no_se_escribe_nada(tmp_path):
    h = floor_harness([DOWN])
    start(h)
    h.run_until(lambda: h.bot.state is State.BUSCAR)
    assert list(tmp_path.iterdir()) == []


def test_panico_a_pie_deja_la_marca_y_la_corrida_nueva_se_sube_primero(tmp_path):
    marker = tmp_path / "a_pie.flag"
    h = floor_harness([DOWN], bot_kwargs={"mount_marker": marker})
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
    h = floor_harness([DOWN])
    start(h)
    h.run_until(lambda: h.bot._dismounted)
    h.bot.run(max_minutes=0)
    assert h.keys("g") == 2 and not h.bot._dismounted and h.bot.stats.end_reason == "tiempo"


def test_dismounted_solo_se_asigna_en_un_lugar():
    import inspect
    import bot.bot as mod
    src = inspect.getsource(mod)
    assert src.count("self._dismounted = ") == 2   # __init__ y _set_dismounted


def test_recoger_no_tiene_ningun_clic_en_el_codigo():
    import inspect
    import bot.bot as mod
    for name in ("_do_recoger", "_pickup_step", "_pickup_walk", "_pickup_press", "_pickup_stall", "_pickup_look"):
        assert "click_at" not in inspect.getsource(getattr(mod.Bot, name)), name
