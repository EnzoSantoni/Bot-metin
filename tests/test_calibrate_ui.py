"""calibrate.py en modo interfaz (--ui / --en-perfil) y el @@STATUS con posicion de run_bot.py."""
import io
from types import SimpleNamespace

import pytest

import calibrate
import run_bot
from bot import calibration as cal
from bot.profiles import load_profile
from bot.zone import FarmZone
from test_calibration import QUAD, imgs, make_session, raw, tmp_cfg  # noqa: F401 - fixtures


def test_zona_en_perfil_guarda_en_el_perfil_aunque_config_la_defina(tmp_cfg, imgs):  # noqa: F811
    s = make_session(tmp_cfg, imgs, keys=["f9"] * 4 + ["f10"], yes=True, prefer_profile=True)
    original = tmp_cfg.read_text(encoding="utf-8")
    assert cal.cmd_zona(s) == 0
    assert tmp_cfg.read_text(encoding="utf-8") == original   # config.json intacto
    zona = s.cfg["_profiles"]["zona"]
    assert load_profile("zona", zona, s.profiles_root)["farm_zone"]["polygon"] == QUAD
    assert (s.profiles_root / "zonas" / f"{zona}.json.bak").exists()


def test_zona_sin_en_perfil_sigue_guardando_en_config(tmp_cfg, imgs):  # noqa: F811
    s = make_session(tmp_cfg, imgs, keys=["f9"] * 4 + ["f10"], yes=True)
    assert cal.cmd_zona(s) == 0
    assert raw(tmp_cfg)["farm_zone"]["polygon"] == QUAD


def test_parser_acepta_ui_y_en_perfil():
    args = calibrate.build_parser().parse_args(["zona", "--ui", "--en-perfil", "--zona", "X"])
    assert args.ui and args.en_perfil and args.zona == "X"
    args = calibrate.build_parser().parse_args(["metin", "Metin X", "--ui"])
    assert args.ui and not args.en_perfil and args.nombre == "Metin X"
    args = calibrate.build_parser().parse_args(["zona"])
    assert not args.ui and not args.en_perfil


def test_ui_ask_imprime_la_pregunta_y_lee_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("s\n"))
    assert calibrate.ui_ask("Guardar? (s/n): ") == "s\n"
    assert capsys.readouterr().out == "@@ASK Guardar? (s/n):\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    with pytest.raises(EOFError):
        calibrate.ui_ask("x")


def test_confirm_con_ui_ask_y_eof_es_no(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    s = cal.Session(cfg={}, ask=calibrate.ui_ask)
    assert s.confirm("Guardar?") is False


def test_confirm_con_ui_ask_responde_si(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("s\n"))
    s = cal.Session(cfg={}, ask=calibrate.ui_ask)
    assert s.confirm("Guardar?") is True


def test_make_session_ui(tmp_cfg):  # noqa: F811
    args = calibrate.build_parser().parse_args(["resumen", "--config", str(tmp_cfg), "--ui", "--en-perfil"])
    s = calibrate.make_session(args, {})
    assert s.ask is calibrate.ui_ask and s.prefer_profile


def test_metin_en_perfil_agrega_a_los_objetivos_del_perfil(tmp_cfg, load_sample):  # noqa: F811
    """Con --en-perfil la lista base es la del perfil de zona, no la que pisa config.json."""
    s = make_session(tmp_cfg, [], prefer_profile=True)
    assert cal._profile_targets(s) == ["Metin de Oscuridad", "Metin Negro"]
    s.prefer_profile = False
    assert cal._profile_targets(s) == ["Metin de Oscuridad", "Metin Negro"]   # el helper no depende del flag


# ---------------------------------------------------------------- @@STATUS de run_bot.py
def _bridge(bot):
    b = run_bot.UiBridge.__new__(run_bot.UiBridge)
    b.bot = bot
    return b


def test_ui_bridge_position_con_tracker():
    zone = FarmZone(((0, 0), (10, 0), (10, 10), (0, 10)), 5.0)
    assert _bridge(SimpleNamespace(tracker=SimpleNamespace(position=(3.14159, 4.0)), zone=zone))._position() == ([3.1, 4.0], "inside")
    assert _bridge(SimpleNamespace(tracker=SimpleNamespace(position=(12, 5)), zone=zone))._position() == ([12.0, 5.0], "tolerance")
    assert _bridge(SimpleNamespace(tracker=SimpleNamespace(position=(40, 5)), zone=zone))._position() == ([40.0, 5.0], "outside")
    assert _bridge(SimpleNamespace(tracker=SimpleNamespace(position=None), zone=zone))._position() == (None, None)
    assert _bridge(SimpleNamespace())._position() == (None, None)   # bot sin tracker: no rompe
