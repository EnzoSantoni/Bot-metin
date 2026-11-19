"""Bot falso que imita el protocolo de `run_bot.py --ui` (para probar la interfaz sin el juego).

Uso: python fake_ui_bot.py [crash|hang]
  (sin argumento) emite @@STATUS, responde a `pause` y `stop` por stdin y termina con @@END.
  crash: imprime un error de arranque y sale con codigo 1.
  hang: ignora `stop` (para probar el corte forzado).
"""
import json
import sys
import threading
import time

mode = sys.argv[1] if len(sys.argv) > 1 else ""
if mode == "crash":
    print("Falta una plantilla: templates/x.png", flush=True)
    sys.exit(1)

state = {"paused": False, "stop": False, "metins": 0}


def emit(tag, payload):
    print(tag + json.dumps(payload), flush=True)


def reader():
    for line in sys.stdin:
        cmd = line.strip()
        if cmd == "pause":
            state["paused"] = not state["paused"]
        elif cmd == "stop" and mode != "hang":
            state["stop"] = True
    state["stop"] = state["stop"] or mode != "hang"   # stdin cerrado: se detiene


threading.Thread(target=reader, daemon=True).start()
print("[INFO] arrancando", flush=True)
print("2024-01-01 [WARNING] bot: algo raro", flush=True)
print("@@STATUS {mal formada", flush=True)
emit("@@STATUS ", {"state": "BUSCAR", "phase": "countdown", "countdown": 3, "metins": 0, "items": 0, "captchas": 0,
                   "unstucks": 0, "elapsed_s": 0, "paused": False})
t0 = time.monotonic()
while not state["stop"]:
    if not state["paused"]:
        state["metins"] += 1
    emit("@@STATUS ", {"state": "ATACAR", "phase": "running", "countdown": 0, "metins": state["metins"], "items": 1,
                       "captchas": 0, "unstucks": 2, "elapsed_s": time.monotonic() - t0, "paused": state["paused"]})
    time.sleep(0.05)
emit("@@END ", {"reason": "panico", "summary": "fin", "metins": state["metins"]})
