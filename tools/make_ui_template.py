"""Recorta la barra de titulo de una ventana de interfaz como plantilla (templates/ui/<nombre>.png).

Uso: .venv/Scripts/python.exe tools/make_ui_template.py --image samples/real_x.png --name mapa_grande --rect X Y W H
(X, Y = esquina superior izquierda del recorte; W x H = tamano; para la barra de titulo, X Y es la esquina del marco).
Segundo ancla (borde/esquina inferior del marco, estatico): agregar --origin OX OY (esquina sup. izq. de la ventana);
imprime el "offset" [X-OX, Y-OY] para vision.ui_windows.windows[].anchor2.
"""
import argparse
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent

ap = argparse.ArgumentParser()
ap.add_argument("--image", required=True)
ap.add_argument("--name", required=True)
ap.add_argument("--rect", nargs=4, type=int, required=True, metavar=("X", "Y", "W", "H"))
ap.add_argument("--origin", nargs=2, type=int, metavar=("OX", "OY"), help="esquina sup. izq. de la ventana")
a = ap.parse_args()
img = cv2.imread(a.image, cv2.IMREAD_COLOR)
if img is None:
    sys.exit(f"No se pudo leer {a.image}")
x, y, w, h = a.rect
out = ROOT / "templates" / "ui" / f"{a.name}.png"
out.parent.mkdir(parents=True, exist_ok=True)
cv2.imwrite(str(out), img[y:y + h, x:x + w])
print(f"Guardada {out} ({w}x{h})")
if a.origin:
    print(f'offset respecto de la ventana: [{x - a.origin[0]}, {y - a.origin[1]}]')
