"""Protocolo de `run_bot.py --ui` con dependencias inyectadas (sin juego, sin hotkeys, sin input real)."""
import json
import queue
import threading
import time

import pytest

import bot.input.base as base
import run_bot
from bot.control import BotControl
from test_bot_states import FakeAlarm, FakeWindow, NoKeyboard, make_cfg, perc


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


class PipeStdin:
    """stdin falso: iterable bloqueante, se cierra con close()."""

    def __init__(self):
        self.q: queue.Queue = queue.Queue()

    def send(self, cmd):
        self.q.put(cmd + "\n")

    def close(self):
        self.q.put(None)

    def __iter__(self):
        while True:
            item = self.q.get()
            if item is None:
                return
            yield item


class Run:
    """Corre run_bot.main en un hilo y junta lo que imprime."""

    def __init__(self, capsys, argv, cfg=None, stdin=None, ui=True):
        self.capsys, self.out, self.code = capsys, "", None
        self.cfg = cfg or make_cfg(loop={"cycle_ms": 5})
        self.stdin = stdin or PipeStdin()
        ctl = BotControl(self.cfg, keyboard=NoKeyboard(), cursor_pos=lambda: (9999, 9999), exit_fn=lambda c: None)
        self.thread = threading.Thread(target=self._run, args=(["--ui"] + argv if ui else argv, ctl), daemon=True)
        self.thread.start()

    def _run(self, argv, ctl):
        self.code = run_bot.main(argv, cfg=self.cfg, window=FakeWindow(), control=ctl, alarm=FakeAlarm(),
                                 bot_kwargs={"perceive_fn": lambda img, c, *, full_captcha=False, parts=None: perc()},
                                 stdin=self.stdin)

    def lines(self):
        self.out += self.capsys.readouterr().out
        return self.out.splitlines()

    def wait_for(self, pred, timeout=10.0):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if pred(self.lines()):
                return
            time.sleep(0.02)
        pytest.fail("timeout; salida:\n" + self.out[-800:])

    def statuses(self):
        return [json.loads(ln[len("@@STATUS "):]) for ln in self.lines() if ln.startswith("@@STATUS ")]

    def ends(self):
        return [json.loads(ln[len("@@END "):]) for ln in self.lines() if ln.startswith("@@END ")]

    def join(self):
        self.thread.join(10)
        assert not self.thread.is_alive()


def test_emite_status_pausa_y_stop(capsys):
    r = Run(capsys, ["--observar", "--sin-cuenta-regresiva"])
    r.wait_for(lambda ls: any(ln.startswith("@@STATUS ") for ln in ls))
    s = r.statuses()[-1]
    assert {"state", "metins", "items", "captchas", "unstucks", "elapsed_s", "paused", "profiles", "phase"} <= set(s)
    r.stdin.send("pause")
    r.wait_for(lambda ls: any(st["paused"] for st in r.statuses()))
    r.stdin.send("pause")
    r.wait_for(lambda ls: not r.statuses()[-1]["paused"])
    r.stdin.send("stop")
    r.join()
    ends = r.ends()
    assert r.code == 0 and len(ends) == 1 and ends[0]["reason"] == "panico" and "summary" in ends[0]


def test_stdin_cerrado_detiene(capsys):
    r = Run(capsys, ["--observar", "--sin-cuenta-regresiva"])
    r.wait_for(lambda ls: any(ln.startswith("@@STATUS ") for ln in ls))
    r.stdin.close()
    r.join()
    assert r.code == 0 and r.ends()[0]["reason"] == "panico"


def test_fin_normal_emite_end_con_motivo(capsys):
    r = Run(capsys, ["--observar", "--sin-cuenta-regresiva", "--max-minutos", "0.01"])
    r.join()
    assert r.ends()[0]["reason"] == "tiempo" and r.ends()[0]["elapsed_s"] > 0


def test_cuenta_regresiva_en_status(capsys):
    cfg = make_cfg(loop={"cycle_ms": 5})
    cfg["safety"]["countdown_s"] = 2
    r = Run(capsys, ["--observar", "--max-minutos", "0.001"], cfg=cfg)
    r.wait_for(lambda ls: any(s["phase"] == "countdown" and s["countdown"] == 2 for s in r.statuses()), 5)
    r.join()
    assert r.ends()[0]["reason"] == "tiempo"


def test_sin_ui_no_emite_protocolo(capsys):
    r = Run(capsys, ["--observar", "--max-minutos", "0.01"], ui=False)
    r.join()
    r.lines()
    r.lines()
    assert "@@" not in r.out and "Metin rotos" in r.out


@pytest.mark.parametrize("flag,start,expected", [("--debug", False, True), ("--sin-debug", True, False), (None, True, True),
                                                 (None, False, False)])
def test_debug_pisa_solo_la_corrida(capsys, tmp_path, flag, start, expected):
    cfg = make_cfg(loop={"cycle_ms": 5})
    cfg["debug"]["enabled"] = start
    cfg["debug"]["dir"] = str(tmp_path / "dbg")
    argv = ["--observar", "--max-minutos", "0.01"] + ([flag] if flag else [])
    seen = {}
    real = run_bot.Bot

    class Spy(real):
        def __init__(self, cfg_, *a, **k):
            seen["debug"] = cfg_["debug"]["enabled"]
            super().__init__(cfg_, *a, **k)
    run_bot.Bot = Spy
    try:
        r = Run(capsys, argv, cfg=cfg)
        r.join()
    finally:
        run_bot.Bot = real
    assert seen["debug"] is expected and r.code == 0
