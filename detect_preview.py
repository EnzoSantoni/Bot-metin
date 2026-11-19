"""Detecta metin sobre una captura (de la ventana del juego o de un archivo) y guarda una imagen anotada.

Uso: .venv/Scripts/python.exe detect_preview.py [--image archivo.png] [--out salida.png]
Sin --image captura la ventana del juego (la trae al frente). No manda ningun input.
"""
import argparse
import sys
import time

import cv2

from bot.config import resolve_path
from bot.debug_draw import annotate, draw_alerts, draw_items, draw_panel_coords, items_line, panel_line  # noqa: F401
from bot.log import setup_logging
from bot.profiles import build_config
from bot.vision.captcha import find_captcha
from bot.vision.coords import read_coords
from bot.vision.death import find_death
from bot.vision.items import choose_item, find_items
from bot.vision.metin import choose_target, find_metins, player_point
from bot.vision.panel import read_panel
from bot.vision.screen_mask import build_screen_mask, find_ui_windows

def main() -> None:
    ap = argparse.ArgumentParser(description="Deteccion de metin con imagen anotada (sin input).")
    ap.add_argument("--image", default=None, help="PNG a analizar; sin esto captura la ventana del juego")
    ap.add_argument("--out", default=None, help="PNG de salida (default: <debug.dir>/detect_preview.png)")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--servidor", default=None, help="perfil de servidor")
    ap.add_argument("--personaje", default=None, help="perfil de personaje")
    ap.add_argument("--zona", default=None, help="perfil de zona")
    args = ap.parse_args()

    cfg = build_config(args.config, args.servidor, args.personaje, args.zona, warn=False)
    setup_logging(cfg)
    prof = cfg["_profiles"]
    if prof["servidor"]:
        print(f"Perfiles: servidor={prof['servidor']}, personaje={prof['personaje']}, zona={prof['zona']}")
    for o in prof["overrides"]:
        print(f"Aviso: {o['message']}")
    if args.image:
        img = cv2.imread(str(resolve_path(args.image)), cv2.IMREAD_COLOR)
        if img is None:
            print(f"No se pudo leer {args.image}")
            sys.exit(1)
    else:
        from bot.capture import GameWindow, enable_dpi_awareness
        enable_dpi_awareness()
        gw = GameWindow(cfg["window_title"], tuple(cfg["expected_client_size"]),
                        cfg["window"]["settle_ms"], cfg["window"]["unobstructed_poll_ms"])
        gw.find()
        if not gw.ensure_ready(timeout=30):
            print("No se pudo poner el juego al frente en 30 s.")
            sys.exit(1)
        img = gw.capture()

    t0 = time.perf_counter()
    mask = build_screen_mask(img.shape, cfg, extra=find_ui_windows(img, cfg))
    dets = find_metins(img, cfg, mask)
    player = player_point(img.shape, cfg)
    chosen = choose_target(dets, player)
    panel = read_panel(img, cfg)
    coords = read_coords(img, cfg)
    items = find_items(img, cfg, mask)
    chosen_item = choose_item(items, player)
    cap = find_captcha(img, cfg)
    death = find_death(img, cfg)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    out = annotate(img, mask, dets, player, chosen)
    draw_panel_coords(out, panel, coords)
    draw_items(out, items, chosen_item)
    draw_alerts(out, cap, death)

    path = resolve_path(args.out) if args.out else resolve_path(cfg["debug"]["dir"]) / "detect_preview.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), out)
    n_t = sum(d.is_target for d in dets)
    print(f"Imagen {img.shape[1]}x{img.shape[0]}: {len(dets)} etiquetas, {n_t} objetivo. Guardada en {path}")
    for d in dets:
        print(f"  {d.name} score={d.score:.2f} objetivo={d.is_target} permitido={d.allowed} "
              f"etiqueta={d.label_rect.center} clic={d.click_point}")
    print(f"Elegido: {chosen.click_point if chosen else None} (personaje en {player})")
    print(panel_line(panel))
    print(items_line(items, chosen_item))
    print(f"Captcha: {'si' if cap else 'no'}")
    print(f"Muerte: {'si' if death else 'no'}")
    print(f"Tiempo del ciclo de deteccion: {elapsed_ms:.0f} ms")
    print(f"Coordenadas: ({coords.x}, {coords.y}) score {coords.score:.2f}" if coords else "Coordenadas: no se leyeron")


if __name__ == "__main__":
    from bot.capture import WindowNotFoundError
    try:
        main()
    except WindowNotFoundError as e:
        print(f"El juego se cerró o no se encuentra la ventana: {e}")
        sys.exit(1)
