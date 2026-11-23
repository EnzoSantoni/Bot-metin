"""Tests de bot/bot.py y run_bot.py: ventana, backend, alarma, reloj y percepcion simulados (nada de input real)."""
import copy
import logging
import math
import random

import numpy as np
import pytest

import bot.input.base as base
import run_bot
from bot.bot import Bot, State
from bot.capture import WindowNotFoundError
from bot.config import DEFAULTS, ConfigError, validate_config
from bot.control import BotControl, PanicStop
from bot.geometry import Rect
from bot.input.fake import FakeBackend, FakeWindow
from bot.input.game_input import GameInput
from bot.perception import Perception
from bot.vision.captcha import CaptchaInfo
from bot.vision.coords import Coords
from bot.vision.death import DeathInfo
from bot.vision.items import ItemLabel
from bot.vision.metin import MetinDetection
from bot.vision.panel import PanelInfo
from bot.vision.screen_mask import ScreenMask

W, H = 1858, 1057
OPEN = ScreenMask(zones=[], mask=np.zeros((H, W), dtype=np.uint8))
PLAYER = (W // 2, H // 2 + 20)
INSIDE_XY = (550, 150)
TOL_XY = (415, 170)       # afuera del poligono pero dentro de la tolerancia (30)
OUT_XY = (300, 150)       # mas lejos que la tolerancia


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


class NoKeyboard:
    def add_hotkey(self, key, cb):
        return object()

    def remove_hotkey(self, handle):
        pass


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, s)


class FakeAlarm:
    def __init__(self):
        self.starts, self.stops = [], 0
        self.is_active = False

    def start(self, reason):
        self.starts.append(reason)

    def stop(self):
        self.stops += 1


def make_cfg(**over):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["navigation"]["mode"] = "clic"   # los tests de fondo son del modo clic
    cfg["spawns"]["enabled"] = False
    cfg["camera_pitch"].update(enabled=False, drag_s=0.0)   # el reinicio de la camara lo prueba tests/test_camera_pitch.py
    # los tests de fondo no prueban la regla de zona de los metin (Fase 6i): sin limite de piso/margen y con la tolerancia entera;
    # los de esa regla (tests/test_metin_zona.py) usan `strict_zone=True`
    if not over.pop("strict_zone", False):
        cfg["approach"].update(blind_near_floor=1000.0, blind_inside_margin=-1000.0,
                               metin_outside_u=float(cfg["farm_zone"]["outside_tolerance"]))
    cfg["input"].update(delay_ms=[0, 0], key_hold_ms=[0, 0], move_settle_ms=[0, 0], click_jitter_px=0)
    cfg["safety"].update(hard_kill_after_s=5.0, countdown_s=0)
    cfg["explore"].update(camera_steps=2, camera_step_s=0.01)
    cfg["stuck"].update(back_key_s=0.01, space_s=0.01, forward_s=0.01, look_step_s=0.01)
    for sec, vals in over.items():
        cfg[sec].update(vals)
    return cfg


def perc(coords=INSIDE_XY, metins=(), panel=None, items=(), item_choice=None, captcha=None, death=None):
    return Perception(
        t=0.0, shape=(H, W, 3), mask=OPEN, windows=[], metins=list(metins), target=None, panel=panel,
        coords=Coords(coords[0], coords[1], 1.0) if coords else None, items=list(items), item_choice=item_choice,
        captcha=captcha, death=death, player_xy=PLAYER)


def metin(click=(1000, 500)):
    return MetinDetection(Rect(click[0] - 40, click[1] - 70, 80, 10), "metin_de_oscuridad", 1.0, True, click, True)


def target_panel(hp=0.8):
    return PanelInfo(present=True, kind="target_metin", name="metin_de_oscuridad", hp_ratio=hp)


def item(kind="item", reserved_other=False, allowed=True):
    return ItemLabel(Rect(900, 600, 80, 15), kind, False, (940, 607), allowed, 0.0, reserved_other=reserved_other)


CAPTCHA = CaptchaInfo(Rect(1600, 400, 152, 194), 1.0)
DEATH = DeathInfo(Rect(10, 10, 100, 20), None, 1.0)


class Harness:
    """Bot completo con todo simulado; `script(h, n)` devuelve la Perception del ciclo n."""

    def __init__(self, script, cfg=None, bot_kwargs=None, **over):
        self.cfg = cfg or make_cfg(**over)
        self.clock = FakeClock()
        self.be, self.win, self.alarm = FakeBackend(), FakeWindow(), FakeAlarm()
        self.ctl = BotControl(self.cfg, keyboard=NoKeyboard(), cursor_pos=lambda: (9999, 9999), exit_fn=lambda c: None)
        self.gi = GameInput(self.win, self.be, self.ctl, self.cfg, rng=random.Random(2))
        self.ctl.add_stop_callback(self.gi.release_all)
        self.script, self.n, self.calls, self.seen = script, 0, [], []
        self.releases = 0
        orig_release = self.gi.release_all

        def spy_release():
            self.releases += 1
            orig_release()
        self.gi.release_all = spy_release
        self.bot = Bot(self.cfg, self.win, self.gi, self.ctl, self.alarm, perceive_fn=self._perceive,
                       clock=self.clock, sleep_fn=self.clock.sleep, rng=random.Random(3), coords_fn=self._peek_coords,
                       **(bot_kwargs or {}))
        orig_enter = self.bot._enter

        def rec(new, reason):
            self.seen.append((new, reason))
            orig_enter(new, reason)
        self.bot._enter = rec

    def _perceive(self, img, cfg, *, full_captcha=False, parts=None):
        self.calls.append((self.bot.state, set(parts), full_captcha))
        n, self.n = self.n, self.n + 1
        p = self.script(self, n)
        self.last_coords = p.coords
        return p

    def _peek_coords(self, img, cfg):
        """Lectura rapida entre tramos de una tecla sostenida: las ultimas coordenadas del guion."""
        return getattr(self, "last_coords", None)

    def train_camera(self):
        """Camara ya calibrada (escala 0.2, sin giro): los clics a +-100 px dan +-20 unidades."""
        self.bot.camera.update((100, 0), (20, 0))
        self.bot.camera.update((0, 100), (0, 20))
        assert self.bot.camera.ready

    def keys(self, k):
        return sum(1 for e in self.be.events if e == ("key_down", k))

    def clicks(self):
        return sum(1 for e in self.be.events if e == ("mouse_down", "left"))

    def states(self):
        return [s for s, _ in self.seen]

    def run_until(self, cond, limit=400):
        for _ in range(limit):
            if cond():
                return
            self.bot.step()
        raise AssertionError(f"no se cumplio la condicion; estados vistos: {self.seen}")


# ---------------------------------------------------------------- ciclo completo

def test_ciclo_completo_buscar_atacar_roto_recoger_explorar():
    w = {"atk": 0, "done": False}
    hps = [0.9, 0.5, 0.05]   # roto real: la ultima vida leida es casi 0

    def script(h, n):
        st = h.bot.state
        if st is State.BUSCAR and not w["done"]:
            return perc(metins=[metin()])
        if st is State.ATACAR:
            w["atk"] += 1
            if w["atk"] == len(hps) + 1:
                h.clock.t += 20.0   # un metin real tarda mas de attack.min_break_s en romperse
            return perc(panel=target_panel(hps[w["atk"] - 1]) if w["atk"] <= len(hps) else None)
        if st is State.RECOGER:
            w["done"] = True
            return perc(items=[item()] if h.keys("z") < 2 else [])
        return perc()

    h = Harness(script)
    h.run_until(lambda: State.EXPLORAR in h.states())
    assert h.states() == [State.ATACAR, State.RECOGER, State.BUSCAR, State.EXPLORAR]
    assert h.bot.stats.metins == 1 and h.bot.stats.items == 1
    assert h.clicks() == 1 and h.keys("z") == 2 and h.keys("g") == 2   # 1 clic del ataque; el objeto esta encima: solo Z
    # las partes de percepcion que pide cada estado
    assert ({"items", "coords"}) in [c[1] for c in h.calls if c[0] is State.RECOGER]
    assert all("panel" in c[1] for c in h.calls if c[0] is State.ATACAR)


def test_seleccion_equivocada_sin_escape():
    def script(h, n):
        if h.bot.state is State.ATACAR:
            return perc(panel=PanelInfo(present=True, kind="other", name="se_rang"))
        return perc(metins=[metin()])

    h = Harness(script)
    h.run_until(lambda: h.states()[-1:] == [State.BUSCAR] and State.ATACAR in h.states())
    assert h.keys("esc") == 0
    assert h.states()[:2] == [State.ATACAR, State.BUSCAR]


def test_un_solo_ciclo_sin_panel_no_cuenta_como_roto():
    seq = [target_panel(0.09), target_panel(0.08), None, target_panel(0.07), target_panel(0.06), None]
    w = {"i": 0}

    def script(h, n):
        if h.bot.state is State.ATACAR:
            w["i"] += 1
            if w["i"] == 6:
                h.clock.t += 20.0   # pasaron mas de attack.min_break_s
            return perc(panel=seq[w["i"] - 1] if w["i"] <= len(seq) else None)
        return perc(metins=[metin()])

    h = Harness(script)
    h.run_until(lambda: w["i"] == 5)
    assert h.bot.state is State.ATACAR and h.bot.stats.metins == 0
    h.run_until(lambda: State.RECOGER in h.states())   # ahora si: 2 seguidos
    assert h.bot.stats.metins == 1


def test_vida_en_cero_dos_ciclos_es_roto():
    w = {"i": 0}

    def script(h, n):
        if h.bot.state is State.ATACAR:
            w["i"] += 1
            if w["i"] == 3:
                h.clock.t += 20.0   # pasaron mas de attack.min_break_s
            return perc(panel=target_panel(0.5 if w["i"] == 1 else 0.0))
        return perc(metins=[metin()])

    h = Harness(script)
    h.run_until(lambda: w["i"] == 2)
    assert h.bot.state is State.ATACAR
    h.run_until(lambda: State.RECOGER in h.states())
    assert w["i"] == 4 and h.bot.stats.metins == 1   # vida 0 con panel 2 ciclos + 2 ciclos sin piedra a la vista


# ---------------------------------------------------------------- captcha y muerte

def test_captcha_suelta_todo_alarma_sin_input_y_sigue_al_desaparecer():
    w = {"cap": 0}

    def script(h, n):
        if n < 2:
            return perc(metins=[])
        w["cap"] += 1
        c = w["cap"]
        # 4 lecturas con panel, 1 sin (no alcanza), 1 con, y despues sin panel
        return perc(captcha=CAPTCHA if c <= 4 or c == 6 else None)

    h = Harness(script)
    h.run_until(lambda: h.bot.state is State.CAPTCHA)
    assert h.releases == 1 and h.alarm.starts == ["captcha"] and h.bot.stats.captchas == 1
    events_at_entry = len(h.be.events)
    h.run_until(lambda: w["cap"] == 6)
    assert h.bot.state is State.CAPTCHA and h.alarm.stops == 0
    assert len(h.be.events) == events_at_entry     # ningun input mientras esta el panel
    h.run_until(lambda: h.bot.state is State.BUSCAR)
    assert h.alarm.stops == 1 and w["cap"] == 8    # 2 lecturas seguidas sin panel
    assert [c[2] for c in h.calls if c[0] is State.CAPTCHA] == [True] * 7   # lee con busqueda completa


def test_captcha_no_roba_el_foco():
    def script(h, n):
        return perc(captcha=CAPTCHA if n < 3 else None)

    h = Harness(script)
    h.run_until(lambda: h.bot.state is State.CAPTCHA)
    ready_calls, perceived = h.win.ensure_ready_calls, h.n
    h.win.foreground = False
    for _ in range(5):
        h.bot.step()
    assert h.n == perceived and h.win.ensure_ready_calls == ready_calls and h.bot.state is State.CAPTCHA
    h.win.foreground = True
    h.run_until(lambda: h.bot.state is State.BUSCAR)
    assert h.win.ensure_ready_calls == ready_calls or h.bot.state is State.BUSCAR


def test_captcha_busqueda_completa_cada_n_ciclos():
    h = Harness(lambda h, n: perc(), loop={"captcha_full_search_every": 3})
    for _ in range(7):
        h.bot.step()
    assert [c[2] for c in h.calls] == [True, False, False, True, False, False, True]


def test_muerte_alarma_y_fin():
    def script(h, n):
        return perc(death=DEATH if n >= 2 else None)

    h = Harness(script)
    stats = h.bot.run()
    assert stats.end_reason == "muerte" and h.bot.finished
    assert h.alarm.starts == ["muerte"] and h.releases == 1 and h.alarm.stops == 0
    assert h.states()[-2:] == [State.MUERTO, State.FIN]


# ---------------------------------------------------------------- panico, cierre y tiempo

def test_panico_a_mitad_de_camino_no_manda_nada_mas():
    w = {"events": None}

    def script(h, n):
        if n == 4:
            w["events"] = len(h.be.events)
            h.ctl.panic("F12")
        return perc(metins=[metin()])

    h = Harness(script)
    with pytest.raises(PanicStop):
        h.bot.run()
    h.ctl.mark_finished()
    assert w["events"] is not None and len(h.be.events) == w["events"]
    assert h.bot.stats.end_reason == "panico"
    assert h.be.pressed == ([], [])


def test_max_minutes_termina_con_motivo_tiempo():
    h = Harness(lambda h, n: perc())
    stats = h.bot.run(max_minutes=0.05)
    assert stats.end_reason == "tiempo" and 3.0 <= stats.elapsed_s < 6.0 and h.bot.finished


def test_juego_cerrado_termina():
    h = Harness(lambda h, n: perc())
    calls = {"n": 0}
    orig = h.win.capture

    def capture(region=None):
        calls["n"] += 1
        if calls["n"] > 3:
            raise WindowNotFoundError("cerrada")
        return orig(region)
    h.win.capture = capture
    stats = h.bot.run()
    assert stats.end_reason == "juego cerrado" and h.bot.finished


# ---------------------------------------------------------------- zona, trabas

def test_outside_vuelve_y_regresa_al_estar_adentro():
    def script(h, n):
        if h.bot.state is State.VOLVER and h.bot.stats.time_by_state.get("VOLVER", 0) > 2:
            return perc(coords=INSIDE_XY)
        return perc(coords=OUT_XY)

    h = Harness(script)
    assert h.bot.zone.status(OUT_XY).name == "OUTSIDE"
    h.run_until(lambda: State.VOLVER in h.states())
    assert h.states() == [State.VOLVER]
    h.run_until(lambda: len(h.seen) == 2)
    assert h.states() == [State.VOLVER, State.BUSCAR]
    assert h.clicks() >= 1        # camino con clics en el piso


def test_tolerancia_al_terminar_deja_vuelta_pendiente():
    w = {"i": 0}

    def script(h, n):
        if h.bot.state is State.ATACAR:
            w["i"] += 1
            if w["i"] == 3:
                h.clock.t += 20.0   # pasaron mas de attack.min_break_s
            return perc(coords=TOL_XY, panel=target_panel(0.05) if w["i"] <= 2 else None)
        return perc(coords=TOL_XY, metins=[metin()] if State.RECOGER not in h.states() else [])

    h = Harness(script)
    assert h.bot.zone.status(TOL_XY).name == "TOLERANCE"
    h.train_camera()
    h.run_until(lambda: h.states()[-1:] == [State.VOLVER])
    assert h.states()[:3] == [State.ATACAR, State.RECOGER, State.VOLVER]
    assert h.bot.stats.metins == 1


def test_atacando_afuera_abandona_y_vuelve():
    def script(h, n):
        if h.bot.state is State.ATACAR:
            return perc(coords=OUT_XY, panel=target_panel(0.5))
        return perc(metins=[metin()])

    h = Harness(script)
    h.train_camera()
    h.run_until(lambda: State.VOLVER in h.states())
    assert h.states()[:2] == [State.ATACAR, State.VOLVER] and h.keys("esc") == 0


def test_atacando_afuera_con_la_vida_bajando_no_lo_deja_lo_rompe_recoge_y_vuelve(caplog):
    """Panel confirmado y la vida bajando: salir de la zona no suelta el metin; se rompe, se recoge y recien despues VOLVER
    (corrida de las 02:36: a las 02:46:12 lo abandono y el metin se rompio igual mientras volvia)."""
    caplog.set_level(logging.INFO)
    w = {"i": 0}

    def script(h, n):
        if h.bot.state is State.ATACAR:
            w["i"] += 1
            if w["i"] == 10:
                h.clock.t += 20.0   # pasaron mas de attack.min_break_s (la vida baja en el ciclo siguiente)
            if w["i"] < 30:
                return perc(coords=OUT_XY, panel=target_panel(max(0.05, 0.9 - 0.03 * w["i"])))
            return perc(coords=OUT_XY)   # roto: sin panel ni piedra
        if h.bot.state is State.RECOGER:
            return perc(coords=OUT_XY, items=[item()] if h.keys("z") < 1 else [])
        return perc(metins=[metin()]) if h.bot.stats.metins == 0 else perc(coords=OUT_XY)

    h = Harness(script)
    h.train_camera()
    h.run_until(lambda: h.states()[-1:] == [State.VOLVER], limit=600)
    assert h.states() == [State.ATACAR, State.RECOGER, State.VOLVER]
    assert h.bot.stats.metins == 1 and h.keys("z") >= 1
    assert "salio de la zona atacando" not in caplog.text and "le estoy pegando al metin" in caplog.text


def test_trabado_destraba_y_vuelve_al_estado_anterior():
    pos = {"xy": INSIDE_XY}
    h = Harness(lambda h, n: perc(coords=pos["xy"]))
    h.run_until(lambda: State.DESTRABAR in h.states())
    assert h.states() == [State.EXPLORAR, State.DESTRABAR] and h.bot.stats.unstucks == 1
    pos["xy"] = (INSIDE_XY[0] + 12, INSIDE_XY[1] + 8)      # el intento de destrabe lo movio
    h.run_until(lambda: len(h.seen) == 3)
    assert h.seen[2][0] is State.EXPLORAR and "destrabado" in h.seen[2][1]


def test_destrabe_agota_intentos_y_va_al_centro():
    h = Harness(lambda h, n: perc(), stuck={"max_attempts": 4})
    h.run_until(lambda: State.CENTRO in h.states(), limit=1500)
    why = dict((s_, w) for s_, w in h.seen)[State.CENTRO]
    assert "4 intentos" in why and h.bot.stats.centros == 1
    ep = h.bot._ep
    assert ep is not None and ep.attempt == 0 and len(ep.strategies) == 4   # contador persistente entre reentradas
    assert all(a != b for a, b in zip(ep.strategies, ep.strategies[1:]))     # nunca la misma dos veces seguidas
    assert h.keys("s") >= 1                                                  # uno de los intentos fue ATRAS


def test_atacar_sin_ningun_panel_en_2_s_ignora_la_posicion_un_rato():
    """Decision simple: sin NINGUN panel attack.no_panel_s despues del clic, recien ahi se ignora esa posicion
    approach.no_label_ignore_s (no 10 minutos); un solo clic."""
    def script(h, n):
        return perc(metins=[metin()])

    h = Harness(script)
    h.train_camera()
    h.run_until(lambda: h.bot.ignored, limit=200)
    assert h.states()[:2] == [State.ATACAR, State.BUSCAR] and h.clicks() == 1 and "ningun panel" in h.seen[1][1]
    assert h.bot.ignored[0].expiry - h.clock.t <= float(h.cfg["approach"]["no_label_ignore_s"]) + 1


NEAR_EDGE_XY = (442, 170)   # adentro del poligono a +4.6 u del borde (menos que explore_margin)


def _far_screen_metin(px=150):
    return metin(click=(PLAYER[0] + px, PLAYER[1]))


def test_sin_camara_ataca_metin_a_la_vista_cerca_del_borde():
    """Corrida real 22:43: adentro a +5 u del borde, metin etiquetado a ~150 px y la camara no lista: se ataca."""
    h = Harness(lambda h, n: perc(coords=NEAR_EDGE_XY, metins=[_far_screen_metin(150)]))
    assert not h.bot.camera.ready
    h.bot.step()
    assert State.ATACAR in h.states() and h.clicks() == 1


def test_sin_camara_ataca_en_la_franja_de_tolerancia():
    h = Harness(lambda h, n: perc(coords=TOL_XY, metins=[metin()]))
    h.bot.step()
    assert State.ATACAR in h.states() and h.clicks() == 1


@pytest.mark.parametrize("px", [150, 300, 700])
def test_sin_camara_metin_con_etiqueta_se_ataca_a_cualquier_distancia_en_pantalla(px):
    """Sin limite de pixeles (regla vieja de 160 px): adentro o en la tolerancia, a 174 px o mas, se ataca."""
    for xy in (NEAR_EDGE_XY, TOL_XY):
        p = perc(coords=xy, metins=[_far_screen_metin(px)])
        h = Harness(lambda h, n: p)
        h.bot.tracker.update(p.coords, h.clock())
        cands, why = h.bot._label_view(p, 0.0)
        assert len(cands) == 1 and not why
        h.bot.step()
        assert State.ATACAR in h.states() and h.clicks() == 1


def test_camara_lista_filtra_metin_fuera_de_la_zona():
    h = Harness(lambda h, n: perc(coords=INSIDE_XY, metins=[metin(click=(1800, 1000))]))   # ~ +170, +90 unidades: afuera de la tolerancia
    h.train_camera()
    h.bot.step()
    assert State.ATACAR not in h.states() and h.clicks() == 0


# ---------------------------------------------------------------- recoger

def _to_recoger(h):
    h.bot._enter(State.RECOGER, "test")


def test_dismount_emparejado_aunque_haya_timeout():
    w = {"atk": 0}

    def script(h, n):
        if h.bot.state is State.ATACAR:
            w["atk"] += 1
            if w["atk"] == 2:
                h.clock.t += 20.0   # pasaron mas de attack.min_break_s
            return perc(panel=target_panel(0.05) if w["atk"] <= 1 else None)
        if h.bot.state is State.RECOGER:
            return perc(items=[item()])      # nunca se van: sale por timeout
        return perc(metins=[metin()] if State.RECOGER not in h.states() else [])

    h = Harness(script, pickup={"dismount": True, "max_seconds": 1.0, "z_interval_s": 0.1})
    h.run_until(lambda: State.RECOGER in h.states() and h.bot.state is State.BUSCAR)
    assert h.keys("g") == 2 and h.keys("ctrl") == 2
    assert h.be.events.index(("key_down", "g")) < h.be.events.index(("key_down", "z"))
    assert not h.bot._dismounted


def test_dismount_se_vuelve_a_montar_si_hay_error():
    h = Harness(lambda h, n: perc(items=[item()]), pickup={"dismount": True})
    _to_recoger(h)
    h.bot.step()   # mira, ve el objeto y se baja
    assert h.keys("g") == 1
    h.script = lambda h, n: (_ for _ in ()).throw(RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        h.bot.run()
    assert h.keys("g") == 2


def test_dismount_con_captcha_no_manda_input_hasta_que_desaparece():
    cap = {"on": False}

    def script(h, n):
        return perc(items=[item()], captcha=CAPTCHA if cap["on"] else None)

    h = Harness(script, pickup={"dismount": True})
    _to_recoger(h)
    h.bot.step()   # se baja
    assert h.keys("g") == 1
    cap["on"] = True
    h.bot.step()
    assert h.bot.state is State.CAPTCHA
    n_events = len(h.be.events)
    for _ in range(3):
        h.bot.step()
    assert len(h.be.events) == n_events
    cap["on"] = False
    h.run_until(lambda: h.bot.state is State.BUSCAR)
    assert h.keys("g") == 2


def test_objetos_de_otro_y_yang_no_disparan_z():
    h = Harness(lambda h, n: perc(items=[item(reserved_other=True), item(kind="yang")], item_choice=None))
    _to_recoger(h)
    h.run_until(lambda: h.bot.state is State.BUSCAR)
    assert h.keys("z") == 0 and h.bot.stats.items == 0


# ---------------------------------------------------------------- explorar

def test_explorar_barre_la_camara_y_ataca_si_aparece_un_metin():
    def script(h, n):
        sweeps = h.keys("q") + h.keys("e")
        return perc(metins=[metin()] if sweeps >= 2 else [])

    h = Harness(script, explore={"sweep_step_deg": 180.0})   # vuelta de 2 pasos, cada uno de una sola pulsacion
    h.bot.keynav.rate = 18000.0
    h.train_camera()
    h.run_until(lambda: State.ATACAR in h.states())
    assert h.states()[:2] == [State.EXPLORAR, State.ATACAR]
    assert h.keys("q") + h.keys("e") == 2 and not h.bot.camera.ready   # on_camera_rotated


def test_explorar_sin_nada_camina_hacia_un_punto_del_poligono():
    h = Harness(lambda h, n: perc(), explore={"sweep_step_deg": 180.0})   # vuelta de 2 pasos
    h.bot.keynav.rate = 18000.0
    h.run_until(lambda: h.clicks() >= 1)
    assert h.keys("q") + h.keys("e") == 2          # primero el barrido
    assert h.bot._ex.phase == "walk" and h.bot.zone.accepts(h.bot._ex.target)


# ---------------------------------------------------------------- config y run_bot

def test_config_nueva_validada():
    validate_config(copy.deepcopy(DEFAULTS))
    for sec, key, val in [("attack", "max_seconds", 0), ("pickup", "dismount", "si"), ("loop", "captcha_full_search_every", 0),
                          ("captcha_wait", "clear_confirm", 0), ("navigation", "settle_s", 0), ("stuck", "max_attempts", 0)]:
        cfg = copy.deepcopy(DEFAULTS)
        cfg[sec][key] = val
        with pytest.raises(ConfigError):
            validate_config(cfg)


def run_main(script, argv, cfg=None, window=None):
    cfg = cfg or make_cfg(loop={"cycle_ms": 5})
    ctl = BotControl(cfg, keyboard=NoKeyboard(), cursor_pos=lambda: (9999, 9999), exit_fn=lambda c: None)
    alarm = FakeAlarm()
    hold = {}

    def perceive_fn(img, cfg_, *, full_captcha=False, parts=None):
        return script(hold, 0)
    code = run_bot.main(argv, cfg=cfg, window=window or FakeWindow(), control=ctl, alarm=alarm,
                        bot_kwargs={"perceive_fn": perceive_fn})
    return code, ctl, alarm


def test_run_bot_observar_loguea_y_no_manda_input(capsys, caplog):
    caplog.set_level(logging.INFO)
    code, ctl, alarm = run_main(lambda h, n: perc(metins=[metin()]), ["--observar", "--max-minutos", "0.01"])
    out = capsys.readouterr().out
    assert code == 0 and "MODO OBSERVAR" in out and "Metin rotos" in out and "motivo de fin: tiempo" in out
    assert any("[observar] haria" in r.message for r in caplog.records)
    assert alarm.stops >= 1


def test_run_bot_panico_devuelve_cero(capsys):
    calls = {"n": 0}
    cfg = make_cfg(loop={"cycle_ms": 5})
    ctl_holder = {}

    def script(h, n):
        calls["n"] += 1
        if calls["n"] == 3:
            ctl_holder["ctl"].panic("F12")
        return perc()

    ctl = BotControl(cfg, keyboard=NoKeyboard(), cursor_pos=lambda: (9999, 9999), exit_fn=lambda c: None)
    ctl_holder["ctl"] = ctl
    code = run_bot.main(["--observar"], cfg=cfg, window=FakeWindow(), control=ctl, alarm=FakeAlarm(),
                        bot_kwargs={"perceive_fn": lambda img, c, *, full_captcha=False, parts=None: script(None, 0)})
    assert code == 0 and "Cortado por pánico" in capsys.readouterr().out


def test_run_bot_muerte_deja_la_alarma(capsys):
    code, ctl, alarm = run_main(lambda h, n: perc(death=DEATH), ["--observar"])
    assert code == 0 and alarm.starts == ["muerte"] and alarm.stops == 0


def test_run_bot_juego_cerrado_devuelve_uno():
    win = FakeWindow()

    def capture(region=None):
        raise WindowNotFoundError("cerrada")
    win.capture = capture
    code, _, _ = run_main(lambda h, n: perc(), ["--observar"], window=win)
    assert code == 1


def test_run_bot_plantilla_faltante_falla_claro(capsys, monkeypatch):
    cfg = make_cfg()
    cfg["target_metin_names"] = ["Metin Inexistente"]
    code = run_bot.main(["--observar"], cfg=cfg, window=FakeWindow())
    assert code == 1 and "plantilla" in capsys.readouterr().out.lower()


# ---------------------------------------------------------------- tope de tiempo de ataque con la vida baja
def _long_attack_script(hps):
    """BUSCAR ve un metin; en ATACAR el panel muestra `hps` (la ultima se repite) y cada ciclo pasa 1 s."""
    w = {"atk": 0}

    def script(h, n):
        if h.bot.state is State.ATACAR:
            w["atk"] += 1
            h.clock.t += 1.0
            return perc(metins=[metin()], panel=target_panel(hps[min(w["atk"], len(hps)) - 1]))
        return perc(metins=[metin()])
    return script


_LONG = {"max_seconds": 10.0, "reclick_wait_s": 3.0, "hp_progress_s": 2.0, "reclick_stall_s": 1e9, "blocked_s": 1e9,
         "scan_enabled": False}


def test_vida_del_metin_baja_no_se_baja_del_caballo_atacando():
    """Ctrl+G hace que el personaje deje de atacar: solo se baja al recoger, nunca con el metin vivo."""
    h = Harness(_long_attack_script([0.5, 0.2, 0.09, 0.05, 0.04, 0.03]), attack=_LONG, stuck={"fast_s": 1e9})
    h.run_until(lambda: h.bot._atk is not None and h.clock.t - h.bot._atk.start_t > 8, limit=200)
    assert h.bot.state is State.ATACAR and h.keys("g") == 0 and not h.bot._dismounted


# ---------------------------------------------------------------- panel perdido con vida 100%: truco del clic derecho
def _lost_panel_script(on_me):
    import dataclasses

    from bot.vision.monsters import MonsterLabel
    w = {"atk": 0}

    def script(h, n):
        h.clock.t += 0.5
        if h.bot.state is State.BUSCAR:
            return perc(metins=[metin()])
        if h.bot.state is not State.ATACAR:
            return perc()
        w["atk"] += 1
        rights = [e for e in h.be.events if e == ("mouse_down", "right")]
        panel = target_panel(1.0) if w["atk"] == 1 or rights else None   # se pierde con vida 100%; vuelve tras el derecho
        r = Rect(PLAYER[0] - 150, PLAYER[1] - 60, 100, 12)                 # un monstruo cerca, del lado opuesto a la piedra
        return dataclasses.replace(perc(metins=[metin()], panel=panel), monsters=[MonsterLabel(r, r.center)],
                                   monsters_near=1, monsters_on_me=on_me)
    return script


@pytest.mark.parametrize("on_me", [0, 1])
def test_panel_perdido_con_vida_100_clic_derecho_en_un_monstruo_y_despues_clic_izquierdo(caplog, on_me):
    caplog.set_level(logging.INFO)
    h = Harness(_lost_panel_script(on_me))
    h.run_until(lambda: any("ahora clic izquierdo al metin" in r.getMessage() for r in caplog.records)
                and h.be.events.count(("mouse_down", "left")) >= 2, limit=200)
    ev = h.be.events
    first_right = ev.index(("mouse_down", "right"))
    assert ("mouse_down", "left") in ev[first_right:], "despues del clic derecho, clic izquierdo al metin"
    assert ev[:first_right].count(("mouse_down", "left")) == 1, "ningun reclic antes del truco"
    assert not any(e == ("key_down", "space") for e in ev[:first_right]), "sin ESPACIO antes del truco"
    assert "Panel perdido con vida 100%" in caplog.text


# ---------------------------------------------------------------- seleccion equivocada repetida: ESPACIO y parte alta de la piedra
def test_dos_selecciones_de_monstruo_seguidas_espacio_y_clic_en_la_parte_alta_de_la_piedra(caplog):
    """22:25:48: 4 clics seguidos seleccionaron un monstruo (los puntos se corrian hacia abajo) y lo aparto. Ahora, al
    segundo: ESPACIO 1.5 s y clic mas arriba, debajo del nombre y lejos de las etiquetas de monstruos."""
    import dataclasses

    from bot.vision.monsters import MonsterLabel
    caplog.set_level(logging.INFO)
    mon = Rect(985, 475, 70, 12)   # un monstruo con su nombre encima de la parte de abajo de la piedra

    def script(h, n):
        h.clock.t += 0.5
        if h.bot.state is State.BUSCAR:
            return perc(metins=[metin()])
        if h.bot.state is not State.ATACAR:
            return perc()
        other = PanelInfo(present=True, kind="other", name="Lobo", hp_ratio=1.0)
        return dataclasses.replace(perc(metins=[metin()], panel=other), monsters=[MonsterLabel(mon, mon.center)],
                                   monsters_near=2, monsters_on_me=1)
    h = Harness(script)
    spaces, clicks = [], []
    real_space, real_click = h.bot._hold_space, h.gi.click_at
    h.bot._hold_space = lambda seconds=None: spaces.append(seconds) or real_space(seconds)
    h.gi.click_at = lambda x, y, *a, **k: clicks.append((x, y)) or real_click(x, y, *a, **k)
    h.run_until(lambda: "Parte alta de la piedra" in caplog.text and len(clicks) >= 3, limit=200)
    assert spaces[:2] == [None, 1.5], spaces   # 1ra: ESPACIO de siempre; 2da seguida: 1.5 s
    label_bottom = metin().label_rect.bottom
    x, y = clicks[2]
    assert clicks[2] != clicks[1] and y < clicks[0][1] and label_bottom < y <= label_bottom + 40, clicks   # parte alta
    gap = math.hypot(max(mon.x - x, 0, x - mon.right), max(mon.y - y, 0, y - mon.bottom))
    assert gap > DEFAULTS["attack"]["monster_clear_px"], (clicks, gap)   # lejos de la etiqueta del monstruo
    assert "2 clics seguidos seleccionaron un monstruo" in caplog.text


# ---------------------------------------------------------------- decision simple: el panel decide
def _with_stone(p, x=1300, y=460):
    from bot.vision.far_metin import FarMetin
    p.far_metins = [FarMetin(Rect(x - 25, y - 60, 50, 70), (x, y), 0.9, True)]
    return p


def test_piedra_sin_nombre_clic_directo_sin_hover():
    h = Harness(lambda h, n: _with_stone(perc()) if h.bot.state is State.BUSCAR else perc())
    h.bot.step()
    assert h.seen[0][0] is State.ATACAR and h.clicks() == 1
    assert State.ACERCARSE not in h.states()


def test_avaricia_confirmada_por_el_panel_se_va_e_ignora_la_posicion():
    avaricia = PanelInfo(present=True, kind="other_metin", name="metin_de_avaricia", hp_ratio=1.0)

    def script(h, n):
        h.clock.t += 0.3
        return perc(metins=[metin()], panel=avaricia if h.bot.state is State.ATACAR else None)
    h = Harness(script)
    h.run_until(lambda: State.ATACAR in h.states() and h.bot.state is not State.ATACAR, limit=40)
    assert "confirmado por el panel" in h.seen[1][1] and h.bot.ignored and h.clicks() == 1


def test_ningun_panel_en_2_s_ignora_la_posicion():
    def script(h, n):
        h.clock.t += 0.5
        return perc(metins=[metin()])
    h = Harness(script)
    h.run_until(lambda: h.bot.ignored, limit=40)
    att = next(i for i, (s, _) in enumerate(h.seen) if s is State.ATACAR)
    assert "ningun panel" in h.seen[att + 1][1] and h.clicks() == 1


def test_volver_con_un_metin_objetivo_cerca_a_la_vista_lo_ataca_antes_de_irse():
    h = Harness(lambda h, n: perc(coords=OUT_XY, metins=[metin()]))
    h.bot._enter(State.VOLVER, "test")
    h.bot.step()
    assert h.bot.state is State.ATACAR and h.clicks() == 1


def test_explorar_frena_en_el_primer_metin_que_aparece_al_girar():
    seen = {"n": 0}

    def script(h, n):
        if h.bot.state is State.EXPLORAR:
            seen["n"] += 1
        return perc(metins=[metin()]) if seen["n"] >= 3 else perc()
    h = Harness(script)
    h.run_until(lambda: State.ATACAR in h.states(), limit=60)
    assert h.bot._ex is None or h.bot._ex.phase == "sweep"
    assert State.ACERCARSE not in h.states()


# ---------------------------------------------------------------- trabado sin monstruos: reclic en la piedra antes del paso
def test_trabado_sin_monstruos_con_la_piedra_a_la_vista_reclic_2_veces_y_recien_despues_paso_al_costado(caplog):
    """23:49:01: trabado a los 1.9 s con 0 monstruos y paso al costado enseguida. Ahora, con la piedra en pantalla: hasta 2
    reclics en ella esperando 3 s cada uno; recien si sigue sin moverse, el paso al costado."""
    caplog.set_level(logging.INFO)
    far = metin(click=(1300, 500))   # lejos del personaje: no "llego"

    def script(h, n):
        h.clock.t += 0.25
        if h.bot.state is State.ATACAR:
            return perc(metins=[far], panel=target_panel(0.9))   # confirmado, vida quieta, personaje quieto
        return perc(metins=[far])
    h = Harness(script)
    at, real = [], h.bot._reclick
    h.bot._reclick = lambda a, why, *r, **k: (at.append(h.clock.t) if why.startswith("trabado, reclic") else None)         or real(a, why, *r, **k)
    side = lambda: sum(1 for e in h.be.events if e[0] == "key_down" and e[1] in ("a", "d"))   # noqa: E731
    h.run_until(lambda: side() >= 1, limit=400)
    assert len(at) == 2 and at[1] - at[0] >= 3.0 - 1e-6, at   # espera 3 s entre reclics
    msgs = [r.getMessage() for r in caplog.records]
    reclics = [i for i, m in enumerate(msgs) if "la piedra sigue en pantalla: reclic" in m]
    paso = next(i for i, m in enumerate(msgs) if "paso al costado (" in m)
    assert len(reclics) == 2 and reclics[-1] < paso, msgs
    assert h.clicks() >= 3   # clic inicial + 2 reclics antes del paso


def test_trabado_sin_monstruos_y_sin_la_piedra_a_la_vista_paso_al_costado_directo(caplog):
    caplog.set_level(logging.INFO)
    far = metin(click=(1300, 500))
    w = {"n": 0}

    def script(h, n):
        h.clock.t += 0.25
        if h.bot.state is State.ATACAR:
            w["n"] += 1
            return perc(metins=[] if w["n"] > 2 else [far], panel=target_panel(0.9))   # la piedra deja de verse
        return perc(metins=[far])
    h = Harness(script)
    h.run_until(lambda: any("paso al costado (" in r.getMessage() for r in caplog.records), limit=400)
    assert not any("la piedra sigue en pantalla: reclic" in r.getMessage() for r in caplog.records)
