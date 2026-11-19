import pytest

from bot.config import DEFAULTS, ConfigError, load_config, save_config


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
    assert load_config(p) == cfg


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
