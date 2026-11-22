"""Toda la rueda del mouse pasa por camera_pitch.zoom_out_sign: ningun archivo gira la rueda con un sentido fijo."""
import ast
import json
from pathlib import Path

import pytest

from bot.camera_pitch import CameraPitch
from bot.config import DEFAULTS, load_config

ROOT = Path(__file__).resolve().parent.parent
# Implementaciones de la rueda (reciben el sentido ya resuelto; no deciden hacia donde girar).
WHEEL_IMPL = {"bot/input/game_input.py": {"zoom"}, "bot/input/base.py": {"scroll", "_scroll"},
              "bot/input/pydirect.py": {"_scroll", "send_wheel"}, "bot/input/fake.py": {"_scroll"}}
WHEEL_CALLS = {"zoom", "scroll", "_scroll", "send_wheel"}


def _project_files():
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith((".venv/", ".claude/", "tests/")) or "/__pycache__/" in rel:
            continue
        yield rel, p


def _wheel_calls(tree):
    """(funcion que la contiene, llamada) por cada llamada a la rueda."""
    out = []

    def visit(node, func):
        for child in ast.iter_child_nodes(node):
            f = child if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func
            if isinstance(child, ast.Call):
                fn = child.func
                name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else None
                if name in WHEEL_CALLS:
                    out.append((f, child))
            visit(child, f)

    visit(tree, None)
    return out


def test_ningun_archivo_gira_la_rueda_sin_zoom_out_sign():
    malos = []
    for rel, path in _project_files():
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for func, call in _wheel_calls(tree):
            fname = func.name if func is not None else "<modulo>"
            if fname in WHEEL_IMPL.get(rel, set()) or fname in ("scroll", "_scroll"):
                continue   # implementacion de la rueda (o un backend que reenvia el valor recibido)
            body = ast.get_source_segment(src, func) if func is not None else src
            if "zoom_out_sign" not in body:
                malos.append(f"{rel}:{call.lineno} ({fname})")
    assert not malos, "giran la rueda sin camera_pitch.zoom_out_sign: " + ", ".join(malos)


def test_el_test_de_guardia_detecta_un_sentido_fijo():
    """Control del propio test: una funcion que gira la rueda con un numero fijo tiene que salir marcada."""
    src = "def f(gi, m):\n    gi.zoom(-3, m)\n"
    tree = ast.parse(src)
    calls = _wheel_calls(tree)
    assert len(calls) == 1 and "zoom_out_sign" not in ast.get_source_segment(src, calls[0][0])


class _GI:
    def __init__(self):
        self.wheel, self.held = [], []

    def zoom(self, clicks, mask):
        self.wheel.append(clicks)
        return True

    def hold(self, action, seconds):
        self.held.append((action, seconds))
        return True

    def tap(self, action, seconds):
        self.held.append((action, seconds))
        return True


class _Mask:
    def allows(self, x, y):
        return True

    def allows_rect(self, r):
        return True


@pytest.mark.parametrize("sign", [1, -1])
def test_calibrate_y_bot_usan_el_zoom_out_sign_de_config_json(tmp_path, sign):
    """Mismo camino que calibrate.py camara-tope y el bot: load_config(config.json) -> CameraPitch -> gi.zoom."""
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"camera_pitch": {"zoom_out_sign": sign}}), encoding="utf-8")
    cfg = load_config(p)
    gi = _GI()
    cp = CameraPitch(cfg)
    perc = (None, type("P", (), {"mask": _Mask()})())
    assert cp.reset(lambda: perc, gi, lambda s: None, 0.0, "test") is True   # rueda, G, toques de T, rueda
    n = sign * int(DEFAULTS["camera_pitch"]["zoom_out_clicks"])
    assert gi.wheel == [n, n] and gi.held[0] == ("camera_top", cfg["camera_pitch"]["g_hold_s"])
