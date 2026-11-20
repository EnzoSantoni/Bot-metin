import pytest

from bot.config import DEFAULTS, ConfigError, load_config, save_config, validate_config


def test_missing_file_creates_defaults(tmp_path):
    p = tmp_path / "c.json"
    cfg = load_config(p)
    assert cfg == DEFAULTS and p.exists()


def test_deep_merge_and_unknown_keys(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"log": {"level": "DEBUG"}, "extra": {"a": 1}}', encoding="utf-8")
    cfg = load_config(p)
    assert cfg["log"]["level"] == "DEBUG"
    assert cfg["log"]["dir"] == DEFAULTS["log"]["dir"]
    assert cfg["extra"] == {"a": 1}
    assert cfg["debug"] == DEFAULTS["debug"]


def test_invalid_json(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"a": }', encoding="utf-8")
    with pytest.raises(ConfigError, match="linea 1"):
        load_config(p)


def test_empty_title(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"window_title": ""}', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


@pytest.mark.parametrize("bad", ['{"expected_client_size": [1920]}',
                                 '{"expected_client_size": [0, 10]}',
                                 '{"log": {"level": "NOPE"}}'])
def test_other_validation(tmp_path, bad):
    p = tmp_path / "c.json"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


def test_roundtrip_non_ascii(tmp_path):
    p = tmp_path / "c.json"
    cfg = load_config(p)
    cfg["map_name"] = "Guabina, Canal General"
    cfg["window_title"] = "Ñ"
    save_config(cfg, p)
    assert "Ñ" in p.read_text(encoding="utf-8")
    assert {k: v for k, v in load_config(p).items() if k != "_profiles"} == cfg   # _profiles lo agrega el armado con perfiles


@pytest.mark.parametrize("override", [
    {"debug": {"enabled": "no"}},
    {"debug": None},
    {"log": {"file": None}},
    {"log": {"dir": ""}},
])
def test_invalid_log_debug_fields(tmp_path, override):
    import json
    from bot.config import ConfigError, load_config
    p = tmp_path / "config.json"
    p.write_text(json.dumps(override), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


def test_migra_clave_vieja_target_metin_name(tmp_path, caplog):
    p = tmp_path / "c.json"
    p.write_text('{"target_metin_name": "Metin de Avaricia"}', encoding="utf-8")
    cfg = load_config(p)
    assert cfg["target_metin_names"] == ["Metin de Avaricia"]
    assert "target_metin_name" not in cfg
    assert "obsoleto" in caplog.text


def test_clave_nueva_gana_sobre_la_vieja(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"target_metin_name": "X", "target_metin_names": ["Y"]}', encoding="utf-8")
    assert load_config(p)["target_metin_names"] == ["Y"]


def test_relative_paths_resolve_to_project_root():
    from bot.config import PROJECT_ROOT, resolve_path
    assert resolve_path("logs") == PROJECT_ROOT / "logs"
    assert (PROJECT_ROOT / "bot" / "config.py").exists()


def test_blind_screen_px_vieja_se_ignora_y_no_rompe(tmp_path, caplog):
    p = tmp_path / "c.json"
    p.write_text('{"approach": {"blind_screen_px": 160, "hover_tries": 3}}', encoding="utf-8")
    cfg = load_config(p)
    assert "blind_screen_px" not in cfg["approach"] and cfg["approach"]["hover_tries"] == 3
    assert "obsoleta" in caplog.text
    assert "blind_screen_px" not in DEFAULTS["approach"]


def test_claves_nuevas_con_sus_defaults():
    assert DEFAULTS["vision"]["ground"] == {"horizon_frac": 0.14, "focal_frac": 0.7, "feet_dy_px": 40, "min_dv_px": 20}
    assert (DEFAULTS["approach"]["hover_wait_s"], DEFAULTS["approach"]["hover_label_px"],
            DEFAULTS["approach"]["hover_tries"]) == (0.3, 120, 2)
    assert (DEFAULTS["attack"]["walk_max_s"], DEFAULTS["attack"]["unstick_space_s"]) == (25.0, 1.5)
    assert (DEFAULTS["explore"]["settle_s"], DEFAULTS["explore"]["settle_frames"]) == (0.6, 2)


@pytest.mark.parametrize("sec,key,val", [("vision.ground", "horizon_frac", 0.6), ("vision.ground", "focal_frac", 0),
                                         ("vision.ground", "min_dv_px", 0), ("approach", "hover_tries", 0),
                                         ("attack", "walk_max_s", -1), ("explore", "settle_frames", 0),
                                         ("explore", "settle_s", "x")])
def test_validacion_de_claves_nuevas(sec, key, val):
    import copy
    cfg = copy.deepcopy(DEFAULTS)
    d = cfg
    for part in sec.split("."):
        d = d[part]
    d[key] = val
    with pytest.raises(ConfigError):
        validate_config(cfg)
