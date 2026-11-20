"""Filtro de coordenadas: confianza, saltos con velocidad medida, conteo, avisos, recortes de debug y posicion vieja."""
import copy
import logging

import cv2
import numpy as np

from bot.config import DEFAULTS
from bot.debug_draw import DebugRecorder
from bot.tracking import CoordStats, CoordTracker
from bot.vision.coords import Coords

CFG = copy.deepcopy(DEFAULTS)
CF = CFG["coords_filter"]


def c(x, y, score=1.0):
    return Coords(x, y, score)


def walking(tr, t0=0.0, x0=100, speed=10.0, n=10, dt=0.25):
    """Camina en linea recta con `speed` u/s leyendo cada `dt` s; devuelve el tiempo final."""
    t = t0
    tr.update(c(x0, 100), t)
    for i in range(1, n + 1):
        t = t0 + i * dt
        tr.update(c(round(x0 + speed * i * dt), 100), t)
    return t


def test_score_bajo_se_descarta_y_se_cuenta():
    tr = CoordTracker(CFG)
    tr.update(c(100, 100), 0)
    assert tr.update(c(101, 100, CF["min_score"] - 0.01), 0.3) == (100, 100)
    assert tr.last_outcome == CoordTracker.LOW_CONF and tr.stats.low_conf == 1
    assert tr.update(c(101, 100, CF["min_score"]), 0.6) == (101, 100)
    assert tr.confident(c(1, 1, 0.5)) is None and tr.confident(None) is None and tr.confident(c(1, 1)) is not None


def test_sin_medicion_usa_max_speed():
    tr = CoordTracker(CFG)
    tr.update(c(100, 100), 0)
    ok = int(CF["max_speed"] * 0.5 + CF["slack"]) - 1
    assert tr.speed_limit() == CF["max_speed"] and tr.measured_speed is None
    assert tr.update(c(100 + ok, 100), 0.5) == (100 + ok, 100)


def test_velocidad_medida_baja_el_limite_de_salto():
    tr = CoordTracker(CFG)
    t = walking(tr, speed=10.0)
    v = tr.measured_speed
    assert v is not None and 8 <= v <= 12
    lim = tr.speed_limit()
    assert lim < CF["max_speed"] and lim == max(v * CF["speed_factor"], CF["min_speed_limit"])
    pos = tr.position
    # un salto de 16 u en 0.5 s: valido con max_speed (30*0.5+2) pero imposible con la velocidad medida
    far = (pos[0] + 16, pos[1])
    assert far[0] - pos[0] <= CF["max_speed"] * 0.5 + CF["slack"]
    assert tr.update(c(*far), t + 0.5) == pos
    assert tr.last_outcome == CoordTracker.JUMP and tr.stats.jump == 1


def test_limite_nunca_pasa_max_speed_ni_baja_del_minimo():
    tr = CoordTracker(CFG)
    walking(tr, speed=1.0, n=12)           # por debajo de speed_move_min: no se mide
    assert tr.measured_speed is None
    tr2 = CoordTracker(CFG)
    tr2._speeds.extend([100.0] * 10)
    assert tr2.speed_limit() == CF["max_speed"]
    tr3 = CoordTracker(CFG)
    tr3._speeds.extend([1.0] * 10)
    assert tr3.speed_limit() == CF["min_speed_limit"]


def test_recuperacion_por_consistencia_y_remedicion():
    tr = CoordTracker(CFG)
    t = walking(tr, speed=10.0)
    assert tr.measured_speed is not None
    k = CF["accept_after"]
    for i in range(k - 1):
        assert tr.update(c(600, 300), t + 0.2 * (i + 1)) == (round(100 + 10 * 10 * 0.25), 100)
    assert tr.update(c(601, 300), t + 0.2 * k) == (601, 300)   # teletransporte confirmado (p.ej. al revivir)
    assert tr.measured_speed is None                           # y la velocidad se vuelve a medir
    assert tr.stats.jump == k   # las lecturas del salto cuentan como descartadas


def test_conteo_y_linea_de_resumen():
    tr = CoordTracker(CFG)
    tr.update(c(100, 100), 0)
    tr.update(None, 0.3)
    tr.update(c(100, 100, 0.5), 0.6)
    tr.update(c(900, 900), 0.9)
    tr.update(c(101, 100), 1.2)
    s = tr.stats
    assert (s.total, s.failed, s.low_conf, s.jump) == (5, 1, 1, 1)
    assert s.line() == "Coordenadas: 5 lecturas, 1 fallidas (20.0%), 1 descartadas por confianza, 1 por salto"
    assert CoordStats().line().startswith("Coordenadas: 0 lecturas, 0 fallidas (0.0%)")


def test_aviso_sin_lectura_buena_una_vez_y_vuelta(caplog):
    caplog.set_level(logging.INFO, logger="bot.tracking")
    tr = CoordTracker(CFG)
    tr.update(c(100, 100), 0)
    warn = CF["no_read_warn_s"]
    t = 0.0
    while t < warn - 0.5:
        t += 0.5
        tr.update(None, t)
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]
    for _ in range(6):
        t += 0.5
        tr.update(c(500, 500, 0.1), t)   # tambien cuentan como "sin lectura buena"
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1                      # una vez por episodio
    tr.update(c(102, 100), t + 0.2)
    assert any("vuelve" in r.getMessage() for r in caplog.records)
    tr.update(None, t + 0.4)
    tr.update(None, t + 0.4 + warn + 0.1)       # otro episodio: otro aviso
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2


def test_posicion_vieja_es_zona_desconocida():
    now = [0.0]
    tr = CoordTracker(CFG, clock=lambda: now[0])
    tr.update(c(100, 100), 0.0)
    now[0] = CF["stale_s"] - 0.1
    assert tr.position == (100, 100)
    now[0] = CF["stale_s"] + 0.1
    assert tr.position is None and tr.last_known == (100, 100)
    tr.update(c(101, 100), now[0])
    assert tr.position == (101, 100)


def test_posicion_vieja_sin_reloj_usa_el_ultimo_update():
    tr = CoordTracker(CFG)
    tr.update(c(100, 100), 0.0)
    assert tr.update(None, CF["stale_s"] + 1) is None


def _img():
    img = np.zeros((1057, 1858, 3), np.uint8)
    cv2.putText(img, "(710, 119)", (1700, 190), cv2.FONT_HERSHEY_PLAIN, 1, (255, 255, 255), 1)
    return img


def test_recortes_de_debug_con_tope_y_separacion(tmp_path):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["debug"].update({"enabled": True, "dir": str(tmp_path), "max_coord_crops": 3, "coord_crop_every_s": 1.0})
    rec = DebugRecorder(cfg)
    img = _img()
    assert rec.save_coord_crop(img, "sin_lectura", 0.0) is not None
    assert rec.save_coord_crop(img, "salto", 0.5) is None          # muy pegado al anterior
    assert rec.save_coord_crop(img, "salto", 1.5) is not None
    assert rec.save_coord_crop(img, "confianza", 3.0) is not None
    assert rec.save_coord_crop(img, "confianza", 5.0) is None      # tope
    files = sorted((rec.run_dir / "coordenadas").glob("*.png"))
    assert [f.name for f in files] == ["0001_sin_lectura.png", "0002_salto.png", "0003_confianza.png"]
    h, w = cv2.imread(str(files[0])).shape[:2]
    assert (w, h) == (200 * 4, 110 * 4)


def test_recortes_desactivado_sin_debug(tmp_path):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["debug"].update({"enabled": False, "dir": str(tmp_path)})
    assert DebugRecorder(cfg).save_coord_crop(_img(), "salto", 0.0) is None
    assert not list(tmp_path.iterdir())


def test_config_invalida():
    import pytest

    from bot.config import ConfigError, validate_config
    for sec, k, v in [("coords_filter", "min_score", 1.5), ("coords_filter", "stale_s", 0),
                      ("coords_filter", "speed_percentile", 0), ("debug", "max_coord_crops", -1)]:
        cfg = copy.deepcopy(DEFAULTS)
        cfg[sec][k] = v
        with pytest.raises(ConfigError):
            validate_config(cfg)


def test_capturas_reales_leen_con_confianza_por_encima_del_filtro():
    """Todas las capturas reales de samples/ (texto nitido, sin compresion) leen con score >= coords_filter.min_score."""
    from pathlib import Path

    from bot.vision.coords import read_coords
    files = sorted((Path(__file__).resolve().parent.parent / "samples").glob("*.png"))
    if not files:
        import pytest
        pytest.skip("no hay samples/")
    for f in files:
        got = read_coords(cv2.imread(str(f)), CFG)
        assert got is not None and got.score >= CF["min_score"], f.name


# ---------------------------------------------------------------- integracion con el bot
def _harness(script, **over):
    from test_bot_states import Harness, make_cfg
    return Harness(script, cfg=make_cfg(**over))


def _p(reading):
    """Perception con la lectura (x, y, score) o sin coordenadas si es None."""
    from test_bot_states import perc
    p = perc((450, 150))
    p.coords = None if reading is None else Coords(*reading)
    return p


def test_el_bot_cuenta_y_el_resumen_trae_la_linea():
    seq = [(450, 150, 1.0), None, (450, 150, 0.2), (700, 190, 1.0), (451, 150, 1.0)]
    h = _harness(lambda h, n: _p(seq[min(n, 4)]))
    for _ in range(5):
        h.bot.step()
    h.bot._account(h.clock())
    s = h.bot.stats
    assert (s.coords.total, s.coords.failed, s.coords.low_conf, s.coords.jump) == (5, 1, 1, 1)
    assert s.summary().splitlines()[-1] == ("Coordenadas: 5 lecturas, 1 fallidas (20.0%), "
                                            "1 descartadas por confianza, 1 por salto")


def test_el_bot_guarda_recortes_de_las_lecturas_fallidas(tmp_path):
    h = _harness(lambda h, n: _p(None), debug={"enabled": True, "dir": str(tmp_path), "coord_crop_every_s": 0.0,
                                               "max_coord_crops": 2})
    for _ in range(4):
        h.bot.step()
    files = sorted((h.bot.debug.run_dir / "coordenadas").glob("*.png"))
    assert [f.name for f in files] == ["0001_sin_lectura.png", "0002_sin_lectura.png"]


def test_el_bot_trata_la_posicion_vieja_como_zona_desconocida():
    h = _harness(lambda h, n: _p((450, 150, 1.0) if n == 0 else None))
    h.bot.step()
    assert h.bot.tracker.position == (450, 150)
    h.clock.sleep(CF["stale_s"] + 1)
    assert h.bot.tracker.position is None and h.bot.tracker.last_known == (450, 150)
