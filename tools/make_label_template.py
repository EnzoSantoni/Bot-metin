"""Genera una plantilla de etiqueta de metin a partir de una captura.

Uso: .venv/Scripts/python.exe tools/make_label_template.py --image samples/x.png --name "Metin de Oscuridad" [--near x,y]
"""
import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402

from bot.config import DEFAULTS, load_config, resolve_path  # noqa: E402
from bot.vision.metin import find_label_candidates, slugify  # noqa: E402
from bot.vision.screen_mask import build_screen_mask  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Guarda la mascara de una etiqueta naranja como plantilla.")
    ap.add_argument("--image", required=True)
    ap.add_argument("--name", required=True, help='nombre del metin, ej. "Metin de Oscuridad"')
    ap.add_argument("--near", default=None, help="x,y: elige la etiqueta mas cercana a ese punto")
    ap.add_argument("--out-dir", default=None, help="default: vision.metin.label_templates_dir")
    ap.add_argument("--config", default="config.json")
    args = ap.parse_args()

    cfg = load_config(args.config)
    img = cv2.imread(str(resolve_path(args.image)), cv2.IMREAD_COLOR)
    if img is None:
        sys.exit(f"No se pudo leer {args.image}")
    cands = find_label_candidates(img, cfg, build_screen_mask(img.shape, cfg))
    if not cands:
        sys.exit("No se encontro ninguna etiqueta naranja en la imagen.")
    if args.near:
        nx, ny = (int(v) for v in args.near.split(","))
        cand = min(cands, key=lambda c: math.dist(c.rect.center, (nx, ny)))
    elif len(cands) == 1:
        cand = cands[0]
    else:
        lst = ", ".join(f"{c.rect.center}" for c in cands)
        sys.exit(f"Hay {len(cands)} etiquetas ({lst}); indica cual con --near x,y")
    out_dir = resolve_path(args.out_dir or cfg["vision"]["metin"]["label_templates_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{slugify(args.name)}.png"
    cv2.imwrite(str(out), cand.text_mask)
    print(f"Plantilla guardada en {out} ({cand.rect.w}x{cand.rect.h}, etiqueta en {cand.rect})")


if __name__ == "__main__":
    main()
