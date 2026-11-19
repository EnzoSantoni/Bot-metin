"""Genera la plantilla del NOMBRE de un panel superior (templates/panel_names/<slug>.png) desde una captura.

Uso: .venv/Scripts/python.exe tools/make_panel_name_template.py --image samples/x.png --name "Metin de Oscuridad" [--skip-words 3]

La linea del panel es "Lv.30 (Nivel 5.) Metin de Oscuridad": `--skip-words` es la cantidad de palabras del prefijo
"Lv.30 (Nivel 5.)" que se descartan (3 por defecto; usar 0 para paneles sin prefijo, como el del propio personaje).
Las palabras se separan por huecos de al menos `--word-gap` columnas vacias.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from bot.config import load_config, resolve_path  # noqa: E402
from bot.vision.digits import text_mask  # noqa: E402
from bot.vision.metin import slugify  # noqa: E402
from bot.vision.panel import _runs, find_frame, name_region  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Guarda la mascara del nombre del panel superior como plantilla.")
    ap.add_argument("--image", required=True)
    ap.add_argument("--name", required=True, help='nombre exacto, ej. "Metin de Oscuridad"')
    ap.add_argument("--skip-words", type=int, default=3, help='palabras del prefijo "Lv.. (Nivel ..)" a descartar')
    ap.add_argument("--word-gap", type=int, default=4, help="columnas vacias minimas entre palabras")
    ap.add_argument("--force", action="store_true", help="pisar la plantilla si ya existe")
    ap.add_argument("--config", default="config.json")
    args = ap.parse_args()

    cfg = load_config(args.config)
    pcfg = cfg["vision"]["panel"]
    img = cv2.imread(str(resolve_path(args.image)), cv2.IMREAD_COLOR)
    if img is None:
        sys.exit(f"No se pudo leer {args.image}")
    frame = find_frame(img, pcfg)
    if frame is None:
        sys.exit("No se encontro el panel superior en la imagen.")
    nrect = name_region(frame, pcfg, img.shape)
    mask = text_mask(img[nrect.y:nrect.bottom, nrect.x:nrect.right],
                     {"color": pcfg["name_color"], "color_tol": pcfg["name_color_tol"]})
    cols = np.nonzero(mask.any(axis=0))[0]
    if len(cols) == 0:
        sys.exit("No se encontro texto del color del nombre en la linea del panel.")
    words: list[list[int]] = []
    for s, e in _runs(mask.any(axis=0)):
        if words and s - words[-1][1] - 1 < args.word_gap:
            words[-1][1] = e
        else:
            words.append([s, e])
    if args.skip_words >= len(words):
        sys.exit(f"Hay {len(words)} palabras y --skip-words={args.skip_words}.")
    x0, x1 = words[args.skip_words][0], words[-1][1]
    piece = mask[:, x0:x1 + 1]
    ys = np.nonzero(piece.any(axis=1))[0]
    tpl = piece[ys.min():ys.max() + 1]
    out_dir = resolve_path(pcfg["name_templates_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{slugify(args.name)}.png"
    if out.exists() and not args.force:
        sys.exit(f"{out} ya existe (usa --force para pisarla).")
    cv2.imwrite(str(out), tpl)
    print(f"Plantilla guardada en {out} ({tpl.shape[1]}x{tpl.shape[0]}); palabras del renglon: {len(words)}, "
          f"descartadas: {args.skip_words}")


if __name__ == "__main__":
    main()
