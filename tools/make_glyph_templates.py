"""Genera plantillas de caracteres a partir de una linea de texto conocida de una captura.

Uso: .venv/Scripts/python.exe tools/make_glyph_templates.py --image samples/x.png --anchor top-right \
        --region -90,170,50,14 --text "(678, 114)" --set coords [--force]

La region (ancla + dx,dy,w,h, como las zonas de config) tiene que contener SOLO la linea. Se segmenta, y cada
caracter se guarda con el nombre del texto conocido (los espacios del texto se ignoran). No pisa plantillas
existentes salvo con --force; si una existente difiere del recorte, avisa. Sirve para completar digitos faltantes.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from bot.config import ANCHORS, load_config, resolve_path  # noqa: E402
from bot.vision.digits import GlyphSet, char_filename, read_text_line  # noqa: E402
from bot.vision.screen_mask import anchor_rect  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Guarda los caracteres de una linea de texto conocida como plantillas.")
    ap.add_argument("--image", required=True)
    ap.add_argument("--anchor", required=True, choices=ANCHORS)
    ap.add_argument("--region", required=True, help="dx,dy,w,h respecto del ancla")
    ap.add_argument("--text", required=True, help='texto exacto de la linea, ej. "(678, 114)" o "18.131/80.250"')
    ap.add_argument("--set", required=True, dest="set_name", help="coords o panel (vision.glyphs.<set>)")
    ap.add_argument("--force", action="store_true", help="pisar plantillas existentes")
    ap.add_argument("--config", default="config.json")
    args = ap.parse_args()

    cfg = load_config(args.config)
    section = cfg["vision"]["glyphs"].get(args.set_name)
    if section is None:
        sys.exit(f"Set desconocido {args.set_name!r}; usa uno de {', '.join(cfg['vision']['glyphs'])}")
    img = cv2.imread(str(resolve_path(args.image)), cv2.IMREAD_COLOR)
    if img is None:
        sys.exit(f"No se pudo leer {args.image}")
    dx, dy, w, h = (int(v) for v in args.region.split(","))
    ih, iw = img.shape[:2]
    rect = anchor_rect(iw, ih, {"anchor": args.anchor, "dx": dx, "dy": dy, "w": w, "h": h})
    clipped = rect.clip(iw, ih)
    if clipped is None:
        sys.exit(f"La region {rect} cae fuera de la imagen {iw}x{ih}")
    crop = img[clipped.y:clipped.bottom, clipped.x:clipped.right]

    glyphs = GlyphSet.load(section["dir"])
    read = read_text_line(crop, glyphs, {**section, "min_score": 0.0})  # solo interesan las cajas
    text = args.text.replace(" ", "")
    if len(read.crops) != len(text):
        sys.exit(f"Se segmentaron {len(read.crops)} caracteres pero el texto tiene {len(text)} ({text!r}). "
                 f"Cajas: {[(b.x, b.y, b.w, b.h) for b in read.boxes]}. Ajusta la region o proba con otra captura "
                 f"(si hay caracteres pegados hacen falta plantillas previas de esos caracteres).")
    out_dir = resolve_path(section["dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    saved, kept, differ = [], [], []
    for ch, bitmap in zip(text, read.crops):
        path = out_dir / char_filename(ch)
        if path.exists() and not args.force:
            old = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE) > 127
            (kept if old.shape == bitmap.shape and np.array_equal(old, bitmap) else differ).append(ch)
            continue
        cv2.imwrite(str(path), bitmap.astype(np.uint8) * 255)
        saved.append(ch)
    print(f"Guardadas: {''.join(saved) or '-'} | ya existian iguales: {''.join(kept) or '-'} "
          f"| existen y DIFIEREN (no se pisaron): {''.join(differ) or '-'}  -> {out_dir}")
    have = sorted(p.stem for p in out_dir.glob("*.png"))
    print(f"Plantillas del set {args.set_name}: {', '.join(have)}")


if __name__ == "__main__":
    main()
