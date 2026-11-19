"""Captura de prueba de la ventana del juego, o monitor de posicion del mouse.

Uso: .venv\\Scripts\\python.exe capture_preview.py [--delay S] [--mouse]
"""
import argparse
import sys
import time

import cv2
import win32api

from bot.capture import GameWindow, WindowNotFoundError, enable_dpi_awareness, get_dpi_scale
from bot.config import load_config, resolve_path
from bot.log import setup_logging


def main() -> None:
    ap = argparse.ArgumentParser(description="Captura la ventana del juego y la guarda como PNG.")
    ap.add_argument("--delay", type=float, default=2.0,
                    help="segundos de espera tras traer el juego al frente (default 2)")
    ap.add_argument("--mouse", action="store_true",
                    help="en vez de capturar, muestra la posición del cursor (pantalla y cliente)")
    ap.add_argument("--out", default=None,
                    help="ruta del PNG, relativa al proyecto (default: <debug.dir>/capture_preview.png). "
                         "Ej: samples/real_metin_seleccionado.png")
    ap.add_argument("--config", default="config.json", help="ruta de config.json")
    args = ap.parse_args()

    enable_dpi_awareness()
    cfg = load_config(args.config)
    setup_logging(cfg)
    gw = GameWindow(cfg["window_title"], tuple(cfg["expected_client_size"]),
                    cfg["window"]["settle_ms"], cfg["window"]["unobstructed_poll_ms"])
    info = gw.find()
    r = gw.client_rect_on_screen()
    scale = get_dpi_scale(info.hwnd)
    print(f'Ventana: "{info.title}"')
    print(f"Cliente en pantalla: x={r.x} y={r.y}  tamaño={r.w}x{r.h}")
    print(f"Escala DPI de Windows: {scale:.2f}")
    if scale != 1.0:
        print("  (no es un problema, el bot usa DPI awareness; solo conviene saberlo)")

    if args.mouse:
        try:
            while True:
                sx, sy = win32api.GetCursorPos()
                cx, cy = gw.screen_to_client(sx, sy)
                fuera = "" if gw.client_rect_on_screen().contains(sx, sy) else "  FUERA"
                print(f"\rPantalla=({sx},{sy})  Cliente=({cx},{cy}){fuera}      ", end="", flush=True)
                time.sleep(0.2)
        except KeyboardInterrupt:
            print()
        return

    if not gw.ensure_ready(timeout=30):
        print("No se pudo poner el juego al frente en 30 s: no se captura (saldría con otras ventanas encima).")
        sys.exit(1)
    print("Ventana al frente.")
    time.sleep(args.delay)
    img = gw.capture()
    out = resolve_path(args.out) if args.out else resolve_path(cfg["debug"]["dir"]) / "capture_preview.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), img)
    print(f"Captura guardada en {out.resolve()} ({img.shape[1]}x{img.shape[0]})")


if __name__ == "__main__":
    try:
        main()
    except WindowNotFoundError as e:
        print(f"El juego se cerró o no se encuentra la ventana: {e}")
        sys.exit(1)
