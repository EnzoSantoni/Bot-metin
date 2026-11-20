"""Compara los detectores de piedras sin etiqueta (color vs estructura) sobre capturas y guarda imagenes anotadas.

Uso: .venv\\Scripts\\python.exe tools\\far_compare.py samples\\metin_verde_lejos.png [otra.png ...] [--out-dir debug]
Por imagen imprime las piedras de cada modo (centro, humo, puntaje, matiz H 0-179) y guarda <out-dir>/far_<nombre>.png con
las de color en ROJO y las de estructura en VERDE (donde coinciden se ven los dos aros). No manda ningun input ni captura el juego.
"""
import argparse
import copy
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.config import load_config, resolve_path  # noqa: E402
from bot.perception import label_exclusions  # noqa: E402
from bot.vision.far_metin import find_far_metins  # noqa: E402
from bot.vision.metin import find_metins  # noqa: E402
from bot.vision.monsters import find_monsters  # noqa: E402
from bot.vision.screen_mask import build_screen_mask, find_ui_windows  # noqa: E402

RED, GREEN = (0, 0, 255), (0, 255, 0)


def analyze(path: Path, cfg: dict, out_dir: Path) -> None:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        print(f"{path}: no se pudo leer")
        return
    mask = build_screen_mask(img.shape, cfg, find_ui_windows(img, cfg))
    exclude = label_exclusions(find_metins(img, cfg, mask), cfg)
    monsters = find_monsters(img, cfg, mask)
    out = img.copy()
    print(f"== {path.name}")
    for mode, color, grow in (("color", RED, 0), ("estructura", GREEN, 5)):
        c = copy.deepcopy(cfg)
        c["vision"]["far_metin"]["mode"] = mode
        found = find_far_metins(img, c, mask, exclude, monsters)
        print(f"  {mode}: {len(found)}")
        for f in found:
            print(f"    base {f.center} rect {f.rect.w}x{f.rect.h} humo={f.smoke} puntaje={f.score:.2f} H={f.hue}")
            cv2.circle(out, f.center, max(14, max(f.rect.w, f.rect.h) // 2) + grow, color, 2)
    dst = out_dir / f"far_{path.stem}.png"
    cv2.imwrite(str(dst), out)
    print(f"  imagen anotada: {dst}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Color vs estructura sobre capturas (sin input).")
    ap.add_argument("images", nargs="+", help="PNG/JPG a analizar")
    ap.add_argument("--out-dir", default=None, help="carpeta de salida (default: debug.dir de la config)")
    ap.add_argument("--config", default="config.json")
    args = ap.parse_args()
    cfg = load_config(args.config)
    out_dir = resolve_path(args.out_dir or cfg["debug"]["dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in args.images:
        analyze(resolve_path(p), cfg, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
