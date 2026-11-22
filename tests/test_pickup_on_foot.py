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
FAST_WALK = {"step_s": 0.02, "min_step_s": 0.01}   # tramos cortos: el hold de teclas duerme de verdad
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

    def __init__(self, h, pts, pickable=True, blocked=False, z_needed=1, misplaced_until=0):
        self.h, self.pickable, self.blocked = h, pickable, blocked
        self.z_needed, self.zs = z_needed, 0          # retraso de la Z: cuantas Z cerca hacen falta para juntar un objeto
        self.misplaced_until = misplaced_until        # las primeras N Z no juntan nada (mal ubicado) aunque se vea cerca
        self.z_log = []                               # (n_objetos_restantes_antes, tiempo) de cada Z
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
                self.z_log.append((len(self.items), self.h.clock.t))
                if near and self.pickable and len(self.z_log) > self.misplaced_until:
                    self.zs += 1
                    if self.zs >= self.z_needed:
                        self.zs = 0
                        self.items.remove(min(near, key=lambda i: math.dist(item_point(i), FEET)))   # Guabina: el mas cercano
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


def test_objeto_inalcanzable_se_abandona_a_item_max_s_sin_loop_infinito(caplog):
    caplog.set_level(logging.INFO)
    h = floor_harness([UP_RIGHT], floor_kw={"blocked": True}, pickup=FAST_WALK)   # las teclas no lo acercan (pared)
    start(h)
    h.run_until(lambda: h.bot.state is State.BUSCAR, limit=600)
    assert h.keys("z") == 0 and h.clicks() == 0
    assert h.keys("g") == 2 and not h.bot._dismounted
    assert "me rindo con el objeto" in caplog.text and h.bot.stats.items == 0


def test_z_se_repite_hasta_que_desaparece_la_etiqueta_y_recien_ahi_el_siguiente(caplog):
    caplog.set_level(logging.INFO)
    h = floor_harness([DOWN, LEFT], floor_kw={"z_needed": 4})   # el juego tarda 4 Z en juntar cada uno
    start(h)
    h.run_until(lambda: h.bot.state is State.BUSCAR, limit=600)
    assert not h.floor.items and h.bot.stats.items == 2
    assert h.keys("z") == 8 and h.clicks() == 0 and h.keys("g") == 2
    # ninguna Z se dio con el primer objeto ya juntado antes de la cuarta, ni con el segundo antes de que el primero se fuera
    counts = [n for n, _ in h.floor.z_log]
    assert counts == [2, 2, 2, 2, 1, 1, 1, 1]
    ts = [t for _, t in h.floor.z_log]
    assert all(b - a >= 0.4 - 1e-9 for a, b in zip(ts, ts[1:]))          # z_interval_s
    assert "Z (4) esperando que desaparezca" in caplog.text and "recogido(s)" in caplog.text


def test_se_reacomoda_a_los_reposition_s_y_sigue_con_z(caplog):
    caplog.set_level(logging.INFO)
    h = floor_harness([DOWN], floor_kw={"misplaced_until": 9})     # las primeras 9 Z no juntan: mal ubicado
    start(h)
    h.run_until(lambda: h.bot.state is State.BUSCAR, limit=600)
    assert "reacomodo sobre el objeto" in caplog.text and "me rindo" not in caplog.text
    assert not h.floor.items and h.bot.stats.items == 1 and h.keys("z") == 10
    t0 = h.floor.z_log[0][1]
    first_rep = next(r for r in caplog.records if "reacomodo" in r.message)
    assert first_rep is not None and h.floor.z_log[-1][1] - t0 >= 3.0


def test_abandona_a_item_max_s_y_pasa_al_siguiente(caplog):
    caplog.set_level(logging.INFO)
    h = floor_harness([DOWN, LEFT], floor_kw={"pickable": False}, pickup=dict(FAST_WALK, item_max_s=6.0))
    start(h)
    h.run_until(lambda: h.bot.state is State.BUSCAR, limit=800)
    assert caplog.text.count("me rindo con el objeto") == 2 and h.bot.stats.items == 0
    assert h.keys("g") == 2 and not h.bot._dismounted and h.clicks() == 0
    assert len(h.floor.z_log) >= 6


def test_no_se_sube_mientras_quede_un_objeto_valido():
    h = floor_harness([DOWN, LEFT], floor_kw={"z_needed": 5})
    start(h)
    h.run_until(lambda: h.keys("z") >= 6)                 # ya junto el primero y va por el segundo
    assert h.keys("g") == 1 and h.bot.state is State.RECOGER and h.bot._dismounted
    h.run_until(lambda: h.bot.state is State.BUSCAR, limit=600)
    assert not h.floor.items and h.keys("g") == 2 and kinds(h)[-1] == "g"


def test_el_objeto_actual_es_el_mas_cercano_a_los_pies_en_cada_ciclo():
    """Guabina junta con cada Z el MAS CERCANO: si aparece uno encima de los pies pasa a ser el actual."""
    h = floor_harness([DOWN], floor_kw={"z_needed": 4})
    start(h)
    h.run_until(lambda: h.keys("z") >= 1)
    h.floor.items.append([FEET[0] + 1.0, FEET[1] - 18.0])     # aparece uno justo debajo de los pies
    new = (int(FEET[0] + 1), int(FEET[1] - 18))
    h.run_until(lambda: h.bot._pk.cur is not None and h.bot._pk.cur.point == new, limit=100)
    assert h.bot._pk.cur.point == new


def test_un_vecino_cerca_no_se_toma_por_el_mismo_objeto_y_la_recogida_se_cuenta(caplog):
    """El objeto actual desaparece (lo junto el Z) y queda otro a ~50 px: no es el mismo (same_px), cuenta como recogido."""
    caplog.set_level(logging.INFO)
    near_a = [FEET[0] + 10.0, FEET[1] - 18.0]
    near_b = [FEET[0] + 60.0, FEET[1] - 18.0]
    h = floor_harness([near_a, near_b], floor_kw={"z_needed": 2})
    start(h)
    h.run_until(lambda: h.bot.state is State.BUSCAR, limit=600)
    assert not h.floor.items and h.bot.stats.items == 2
    assert "me rindo" not in caplog.text


def test_un_objeto_que_no_era_el_actual_y_desaparece_con_z_cuenta_como_progreso_y_reinicia_el_actual():
    h = floor_harness([[FEET[0] + 10.0, FEET[1] - 18.0], DOWN], floor_kw={"z_needed": 99})
    start(h)
    h.run_until(lambda: h.keys("z") >= 2)
    cur = h.bot._pk.cur
    assert cur is not None and cur.z >= 1
    h.floor.items.pop(0)                  # el juego junto otro (el de los pies): desaparece mientras se aprieta Z
    h.run_until(lambda: h.bot.stats.items >= 1, limit=200)
    assert h.bot.stats.items == 1 and h.bot._pk.cur is None or h.bot._pk.cur.z <= 1


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
    for name in ("_do_recoger", "_pickup_step", "_pickup_walk", "_pickup_follow", "_pickup_look"):
        assert "click_at" not in inspect.getsource(getattr(mod.Bot, name)), name
