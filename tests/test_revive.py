"""Fase 14: revivir al morir (apagado por defecto). Todo con fakes: nada de input real."""
import pathlib

import cv2
import numpy as np
import pytest

from bot.bot import BotStats, Bot, State
from bot.config import ConfigError, validate_config
from bot.geometry import Rect
from bot.history import build_record
from bot.profiles import kind_of
from bot.revive import RectMask, ReviveCfg, find_icon, here_button
from bot.vision.death import DeathInfo, find_death
from bot.vision.screen_mask import ScreenMask
from test_bot_states import CAPTCHA, DEATH, H, W, Harness, make_cfg, perc

import dataclasses

ROOT = pathlib.Path(__file__).resolve().parent.parent
HERE = (68, 51)   # centro del boton (60, 20) del cliente + origen (8, 31) de FakeWindow
ICON = Rect(500, 300, 32, 32)
ICON_SCREEN = (516 + 8, 316 + 31)


def rcfg(**rv):
    cfg = make_cfg()
    cfg["revive"].update(auto_revive=True, button_wait_s=0.0, confirm_wait_s=0.0, inventory_wait_s=0.0,
                         after_revive_wait_s=0.0, horse_wait_s=0.0)
    cfg["revive"].update(rv)
    return cfg


class Scene:
    """Pantalla de muerte hasta que `clicks_needed` clics en el boton la hagan desaparecer (despues, ya no hay)."""

    def __init__(self, clicks_needed=1, die_at=3, captcha=None):
        self.clicks_needed, self.die_at = clicks_needed, die_at
        self.captcha = captcha or {}    # llamadas a captcha_fn (numero) -> hasta que ciclo hay panel
        self.h = None
        self.alive_after = False
        self.cap_cycles = 0
        self.captcha_calls = 0

    def clicks(self):
        return sum(1 for e in self.h.be.events if e == ("mouse_down", "left"))

    def script(self, h, n):
        self.h = h
        dead = n >= self.die_at and not self.alive_after
        cap = CAPTCHA if self.cap_cycles > 0 else None
        if cap is not None:
            self.cap_cycles -= 1
        return perc(death=DEATH if dead else None, captcha=cap)

    def death_fn(self, img, cfg):
        if self.clicks() >= self.clicks_needed:
            self.alive_after = True
            return None
        return DEATH

    def captcha_fn(self, img, cfg):
        self.captcha_calls += 1
        if self.captcha_calls in self.captcha:
            self.cap_cycles = self.captcha[self.captcha_calls]
            return CAPTCHA
        return None


def make(cfg, scene=None, marker=None, icon_fn=None, **kw):
    scene = scene or Scene()
    kwargs = dict(death_fn=scene.death_fn, captcha_fn=scene.captcha_fn, mount_marker=marker)
    if icon_fn is not None:
        kwargs["icon_fn"] = icon_fn
    kwargs.update(kw)
    h = Harness(scene.script, cfg=cfg, bot_kwargs=kwargs)
    scene.h = h
    return h, scene


def evs(h):
    """Eventos de input resumidos: ('key', k), ('click', boton, (x, y))."""
    out, pos = [], None
    for e in h.be.events:
        if e[0] == "move_to":
            pos = (e[1], e[2])
        elif e[0] == "mouse_down":
            out.append(("click", e[1], pos))
        elif e[0] == "key_down":
            out.append(("key", e[1]))
    first = next((i for i, e in enumerate(out) if e[0] == "click"), 0)   # antes del clic hay giros de busqueda
    return out[first:]


def run_revive(h, limit=300):
    h.run_until(lambda: h.bot.stats.revives >= 1 and h.bot.state is h.bot._home, limit)


# ---------------------------------------------------------------- apagado y comportamiento por defecto
def test_apagado_es_fin_como_antes():
    cfg = make_cfg()
    assert cfg["revive"]["auto_revive"] is False
    h, sc = make(cfg)
    stats = h.bot.run()
    assert stats.end_reason == "muerte" and h.alarm.starts == ["muerte"]
    assert h.states()[-2:] == [State.MUERTO, State.FIN] and State.REVIVIR not in h.states()
    assert stats.deaths == 1 and stats.revives == 0 and h.clicks() == 0


# ---------------------------------------------------------------- secuencia
def test_sin_caballo_clic_y_vuelve_a_buscar(caplog):
    h, sc = make(rcfg())
    with caplog.at_level("WARNING"):
        run_revive(h)
    assert evs(h) == [("click", "left", HERE)]
    assert h.bot.state is State.BUSCAR and h.alarm.starts == [] and h.bot.stats.deaths == 1 and h.bot.stats.revives == 1
    assert State.REVIVIR in h.states() and State.MUERTO not in h.states()
    assert any("no se llama al caballo" in r.message for r in caplog.records)
    assert h.bot._dismounted is True    # a pie y sin caballo: sin Ctrl+G
    assert h.keys("g") == 0


def test_el_boton_se_hace_clic_aunque_la_mascara_lo_bloquee():
    mask = np.zeros((H, W), np.uint8)
    mask[0:200, 0:300] = 255
    blocked = ScreenMask(zones=[], mask=mask)
    assert not blocked.allows(60, 20)

    def script(h, n):
        return dataclasses.replace(sc.script(h, n), mask=blocked)

    sc = Scene()
    h, _ = make(rcfg(), sc)
    h.script = script
    run_revive(h)
    assert evs(h) == [("click", "left", HERE)]


def test_rect_mask_solo_permite_su_rectangulo():
    m = RectMask(Rect(10, 10, 20, 5))
    assert m.allows(10, 10) and m.allows(29, 14) and not m.allows(30, 10) and not m.allows(9, 10) and not m.allows(10, 15)


def test_con_horse_key_monta_y_la_cuenta_queda_par(tmp_path):
    marker = tmp_path / "a_pie.txt"
    h, sc = make(rcfg(horse_key="5"), marker=marker)
    run_revive(h)
    assert evs(h) == [("click", "left", HERE), ("key", "5"), ("key", "ctrl"), ("key", "g")]
    assert h.bot._dismounted is False and not marker.exists()


def test_horse_key_tiene_prioridad_sobre_el_inventario():
    h, sc = make(rcfg(horse_key="5", horse_item_template="templates/death/aqui.png"),
                 icon_fn=lambda *a: pytest.fail("no debe buscar en el inventario"))
    run_revive(h)
    assert ("key", "i") not in evs(h) and ("key", "5") in evs(h)


def test_inventario_con_plantilla_encontrada():
    calls = []

    def icon_fn(img, tpl, thr, cfg):
        calls.append((tpl, thr))
        return ICON

    h, sc = make(rcfg(horse_item_template="templates/death/aqui.png"), icon_fn=icon_fn)
    run_revive(h)
    assert evs(h) == [("click", "left", HERE), ("key", "i"), ("click", "right", ICON_SCREEN), ("key", "i"),
                      ("key", "ctrl"), ("key", "g")]
    assert calls == [("templates/death/aqui.png", 0.8)]
    assert h.bot._dismounted is False


def test_inventario_clic_izquierdo_configurable():
    h, sc = make(rcfg(horse_item_template="templates/death/aqui.png", horse_item_click="left", mount_after=False),
                 icon_fn=lambda *a: ICON)
    run_revive(h)
    assert evs(h)[2] == ("click", "left", ICON_SCREEN)
    assert h.keys("g") == 0 and h.bot._dismounted is True


def test_plantilla_no_encontrada_cierra_inventario_y_sigue_sin_caballo(caplog):
    h, sc = make(rcfg(horse_item_template="templates/death/aqui.png"), icon_fn=lambda *a: None)
    with caplog.at_level("WARNING"):
        run_revive(h)
    assert evs(h) == [("click", "left", HERE), ("key", "i"), ("key", "i")]
    assert any("no se encontro el objeto del caballo" in r.message for r in caplog.records)
    assert h.bot._dismounted is True and h.bot.state is State.BUSCAR


def test_modo_bichos_vuelve_a_bichos():
    cfg = rcfg()
    cfg["zone"]["farm_mode"] = "bichos"
    h, sc = make(cfg)
    run_revive(h)
    assert h.bot.state is State.BICHOS


def test_habilidades_vencidas_tras_revivir():
    cfg = rcfg(horse_key="5")
    cfg["character"]["skills"] = [{"name": "Aura", "key": "f6", "duration_s": 600, "requires_dismount": False,
                                   "animation_s": 0.0, "enabled": True}]
    h, sc = make(cfg, Scene(die_at=6))
    h.run_until(lambda: h.keys("f6") == 1)          # primera tirada al empezar
    assert h.bot.skills.due(h.clock()) == []        # dura 600 s: no vence sola
    run_revive(h)
    assert len(h.bot.skills.due(h.clock())) == 1    # revivir la dejo vencida
    h.run_until(lambda: h.keys("f6") == 2)


def test_sin_recast_las_habilidades_no_se_tocan():
    cfg = rcfg(recast_skills=False)
    cfg["character"]["skills"] = [{"name": "Aura", "key": "f6", "duration_s": 600, "requires_dismount": False,
                                   "animation_s": 0.0, "enabled": True}]
    h, sc = make(cfg, Scene(die_at=6))
    h.run_until(lambda: h.keys("f6") == 1)
    run_revive(h)
    assert h.bot.skills.due(h.clock()) == []


# ---------------------------------------------------------------- limites y fallos
def test_reintenta_el_clic_y_funciona_a_la_tercera():
    h, sc = make(rcfg(), Scene(clicks_needed=3))
    run_revive(h)
    assert sum(1 for e in evs(h) if e[0] == "click") == 3 and h.bot.stats.revives == 1


def test_si_la_pantalla_sigue_fin_con_alarma():
    h, sc = make(rcfg(), Scene(clicks_needed=99))
    stats = h.bot.run()
    assert sum(1 for e in evs(h) if e[0] == "click") == 3
    assert stats.end_reason == "muerte" and h.alarm.starts == ["muerte"] and stats.revives == 0
    assert h.states()[-3:] == [State.REVIVIR, State.MUERTO, State.FIN]


def test_max_revives_y_luego_fin():
    class Many(Scene):
        def script(self, h, n):
            self.h = h
            # muere cada 4 ciclos despues de revivir
            dead = n >= self.die_at and (n % 4 == 0 or self.pending)
            if dead and not self.pending:
                self.pending = True
                self.base = self.clicks()
            return perc(death=DEATH if dead else None)

        pending = False
        base = 0

        def death_fn(self, img, cfg):
            if self.clicks() > self.base:
                self.pending = False
                return None
            return DEATH

    sc = Many()
    h, _ = make(rcfg(max_revives=2), sc)
    stats = h.bot.run()
    assert stats.revives == 2 and stats.deaths == 3 and stats.end_reason == "muerte"
    assert h.alarm.starts == ["muerte"] and h.states()[-2:] == [State.MUERTO, State.FIN]


def test_max_revives_cero_es_fin_inmediato():
    h, sc = make(rcfg(max_revives=0))
    stats = h.bot.run()
    assert stats.revives == 0 and stats.end_reason == "muerte" and h.clicks() == 0


# ---------------------------------------------------------------- captcha
def test_captcha_en_medio_se_retoma_y_no_manda_input_mientras_esta(tmp_path):
    marker = tmp_path / "a_pie.txt"
    # la 2a lectura rapida de captcha (durante la espera tras el clic) encuentra el panel por 3 ciclos
    sc = Scene(captcha={2: 3})
    h, _ = make(rcfg(horse_key="5"), sc, marker=marker)
    seen_len = {}

    orig = h.bot._enter

    def watch(new, reason):
        if new is State.CAPTCHA:
            seen_len["at"] = len(h.be.events)
        orig(new, reason)
    h.bot._enter = watch
    run_revive(h)
    assert h.alarm.starts == ["captcha"] and h.bot.stats.captchas == 1
    ev = evs(h)
    assert ev.count(("key", "5")) == 1 and ev.count(("key", "g")) == 1   # nada se repite ni se monta de mas
    assert ev[0][0] == "click" and h.bot._dismounted is False and not marker.exists()
    assert h.bot.stats.revives == 1 and h.bot.state is State.BUSCAR


def test_captcha_antes_del_clic_va_primero():
    cfg = rcfg()
    sc = Scene(die_at=2)

    orig = sc.script

    def script(h, n):
        p = orig(h, n)
        if n in (2, 3):
            return dataclasses.replace(p, captcha=CAPTCHA)
        return p
    h, _ = make(cfg, sc)
    h.script = script
    h.run_until(lambda: h.bot.state is State.CAPTCHA)
    assert State.REVIVIR not in h.states() and h.clicks() == 0
    run_revive(h)
    assert h.alarm.starts == ["captcha"] and h.bot.stats.deaths == 1 and h.bot.stats.revives == 1


# ---------------------------------------------------------------- estadisticas
def test_resumen_e_historial_cuentan_muertes():
    s = BotStats(deaths=2, revives=1)
    assert "muertes: 2 (revividas: 1)" in s.summary()
    assert "muertes" not in BotStats().summary()
    r = build_record(s, __import__("datetime").datetime(2026, 1, 1), __import__("datetime").datetime(2026, 1, 2), {}, "muerte")
    assert r["muertes"] == 2 and r["revives"] == 1


# ---------------------------------------------------------------- muerte.png
SAMPLE = ROOT / "samples" / "muerte.png"


@pytest.mark.skipif(not SAMPLE.exists(), reason="falta samples/muerte.png")
def test_posicion_del_boton_en_muerte_png():
    img = cv2.imread(str(SAMPLE))
    cfg = make_cfg()
    info = find_death(img, cfg)
    rc = ReviveCfg.from_config(cfg)
    r = here_button(info, rc)
    assert (r.x, r.y, r.w, r.h) == (59, 83, 177, 22) and r.center == (147, 94)
    only_city = DeathInfo(here_rect=None, city_rect=info.city_rect, score=info.score)
    assert here_button(only_city, rc) == r    # derivado de "Ciudad", 30 px mas arriba


# ---------------------------------------------------------------- plantilla de icono
def test_find_icon_con_plantilla_real(tmp_path):
    rng = np.random.default_rng(1)
    icon = rng.integers(0, 255, (24, 24, 3), dtype=np.uint8)
    img = np.zeros((300, 400, 3), np.uint8)
    img[100:124, 200:224] = icon
    tpl = tmp_path / "caballo.png"
    cv2.imwrite(str(tpl), icon)
    cfg = make_cfg()
    assert find_icon(img, str(tpl), 0.8, cfg) == Rect(200, 100, 24, 24)
    assert find_icon(np.zeros((300, 400, 3), np.uint8) + 7, str(tpl), 0.8, cfg) is None
    assert find_icon(img, str(tmp_path / "no_existe.png"), 0.8, cfg) is None


# ---------------------------------------------------------------- config y perfiles
def test_validacion():
    validate_config(rcfg())
    validate_config(rcfg(horse_key="5", horse_item_click="left", inventory_key="ctrl+i"))
    for bad in ({"inventory_key": "f12"}, {"inventory_key": "nada"}, {"horse_key": "f11"}, {"horse_key": 5},
                {"button_wait_s": -1}, {"after_revive_wait_s": "x"}, {"horse_wait_s": -0.1}, {"max_revives": -1},
                {"button_tries": 0}, {"horse_item_match": 0}, {"horse_item_match": 1.5}, {"horse_item_click": "middle"},
                {"horse_item_template": "templates/no_existe_xyz.png"}, {"auto_revive": "si"}, {"mount_after": 1}):
        with pytest.raises(ConfigError):
            validate_config(rcfg(**bad))
    validate_config(rcfg(horse_item_template="templates/death/aqui.png"))


def test_revive_es_perfil_de_personaje():
    assert kind_of(("revive",)) == "personaje" and kind_of(("revive", "auto_revive")) == "personaje"


# ---------------------------------------------------------------- integracion con la vuelta de busqueda (Fase 6g)

def _fake_bot(pos, death_pos, unknown=False, state=State.BUSCAR, n_cands=2):
    from types import SimpleNamespace
    from bot.candidates import CandidateMap
    cands = CandidateMap(100.0, 10.0, 4.0, 0.5)
    for i in range(n_cands):
        cands.add((100.0 * i, 0.0), "metin_negro", True, 0.0)
    return SimpleNamespace(state=state, _rv=None, _death_pos=death_pos, _rv_moved_unknown=unknown,
                           tracker=SimpleNamespace(position=pos), ex={"candidate_merge_u": 12.0}, cands=cands)


def test_revivir_aqui_no_descarta_los_candidatos():
    b = _fake_bot(pos=(500.0, 150.0), death_pos=(503.0, 150.0))
    Bot._check_revive_move(b)
    assert len(b.cands) == 2 and b._death_pos is None


def test_revivir_en_otro_lugar_descarta_los_candidatos():
    b = _fake_bot(pos=(300.0, 90.0), death_pos=(503.0, 150.0))
    Bot._check_revive_move(b)
    assert len(b.cands) == 0 and b._death_pos is None


def test_revivir_sin_saber_donde_murio_descarta_pero_espera_la_posicion():
    b = _fake_bot(pos=None, death_pos=None, unknown=True)
    Bot._check_revive_move(b)
    assert len(b.cands) == 2 and b._rv_moved_unknown       # todavia no se lee la posicion
    b.tracker.position = (500.0, 150.0)
    Bot._check_revive_move(b)
    assert len(b.cands) == 0 and not b._rv_moved_unknown


def test_no_se_evalua_mientras_se_revive():
    b = _fake_bot(pos=(300.0, 90.0), death_pos=(503.0, 150.0), state=State.REVIVIR)
    Bot._check_revive_move(b)
    assert len(b.cands) == 2 and b._death_pos is not None


def test_habilidades_no_salen_durante_la_vuelta_ni_el_giro_hacia_un_candidato():
    from types import SimpleNamespace
    def safe(phase, walk=None):
        b = SimpleNamespace(_scan=None, state=State.EXPLORAR, _walk=walk, _ex=SimpleNamespace(phase=phase), _apr=None,
                            _last_click_t=-1e9, atk={"min_click_gap_s": 0.6})
        return Bot._skill_safe(b, 0.0)
    assert not safe("sweep") and not safe("aim")
    assert safe("walk") and safe("cwalk")
    assert not safe("walk", walk=object())
