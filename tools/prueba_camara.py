r"""Prueba en el juego del reinicio de camara con teclas (manda input real: correr como administrador si el juego lo es).

Uso: .venv\Scripts\python.exe tools\prueba_camara.py
Hace rueda lejos, G mantenida (camera_pitch.g_hold_s), los toques de T (t_taps) de a uno y rueda lejos, sin clics ni movimiento,
y guarda una captura despues de cada paso en debug/prueba_camara_gt/ (dice si se ve la etiqueta del personaje). F12 corta.
Sirve para comprobar que el tope de G no acerca la camara y que al final se ve al personaje entero."""
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import os
os.chdir(ROOT)

import cv2

from bot.camera_pitch import CameraPitch
from bot.capture import GameWindow
from bot.config import load_config
from bot.control import BotControl
from bot.input.game_input import GameInput
from bot.input.pydirect import PyDirectInputBackend
from bot.perception import perceive
from bot.vision.monsters import find_own_label

cfg = load_config("config.json")
cfg = copy.deepcopy(cfg)
cfg["camera_pitch"]["enabled"] = True
out = ROOT / "debug" / "prueba_camara_gt"
out.mkdir(parents=True, exist_ok=True)
for f in out.glob("*.png"):
    f.unlink()

win = GameWindow(cfg["window_title"], tuple(cfg["expected_client_size"]), cfg["window"]["settle_ms"],
                 cfg["window"]["unobstructed_poll_ms"])
win.find()
control, backend = BotControl(cfg), PyDirectInputBackend(cfg)
gi = GameInput(win, backend, control, cfg)
gi.startup_check()
control.add_stop_callback(backend.release_all)
control.start()
cp = CameraPitch(cfg)
sleep = control.sleep


def look():
    img = win.capture()
    return img, perceive(img, cfg, parts=("metins",))


def shot(name):
    sleep(0.6)
    img, p = look()
    lab = find_own_label(img, cfg, p.player_xy)
    cv2.imwrite(str(out / f"{name}.png"), img)
    print(f"{name}: etiqueta propia {'SI ' + str((lab.x, lab.y, lab.w, lab.h)) if lab else 'NO'}", flush=True)


try:
    if not win.ensure_ready(timeout=15, should_stop=lambda: control.is_stopped):
        print("el juego no quedo al frente")
        sys.exit(1)
    shot("0_antes")
    cp.wheel_out(look, gi)
    shot("1_rueda_lejos")
    gi.hold("camera_top", float(cfg["camera_pitch"]["g_hold_s"]))
    shot("2_G_tope_arriba")
    for i in range(1, int(cfg["camera_pitch"]["t_taps"]) + 1):
        cp.tap_down(gi, sleep, 1)
        shot(f"3_T_toque_{i}")
    cp.wheel_out(look, gi)
    shot("4_rueda_final")
finally:
    control.mark_finished()
    backend.release_all()
    control.close()
