# Capturas para validar el detector de piedras por estructura

El detector nuevo (`vision.far_metin.mode`, bot/vision/far_structure.py) busca una piedra con aura saturada y humo palido
arriba, sin mirar el color. Se probo con las capturas reales (rojas) y con variantes de tono generadas por computadora
(verde/azul/violeta), mas un Metin de Avaricia rosa real. Falta confirmarlo con metin REALES de otro color/mapa.

Todas las capturas se sacan con el juego a pantalla normal (misma resolucion de siempre). El comando espera 8 segundos para
que te de tiempo de poner el mouse y la camara; despues captura sola y NO manda ningun input.

## Para un metin de otro color/mapa (hacer por cada tipo distinto; con el primero alcanza para validar)

1. De LEJOS, sin el mouse encima (la piedra se ve chica, con su humo; sin etiqueta):
   `Start-Sleep -Seconds 8; .venv\Scripts\python.exe capture_preview.py --delay 0 --out samples\metin_verde_lejos.png`
2. De CERCA, sin el mouse encima (la piedra grande, sin etiqueta):
   `Start-Sleep -Seconds 8; .venv\Scripts\python.exe capture_preview.py --delay 0 --out samples\metin_verde_cerca.png`
3. Con el MOUSE ENCIMA de la piedra (que se vea la etiqueta con el nombre del metin):
   `Start-Sleep -Seconds 8; .venv\Scripts\python.exe capture_preview.py --delay 0 --out samples\metin_verde_hover.png`
4. (Opcional) Con el metin seleccionado/atacado (que se vea el panel de arriba):
   `Start-Sleep -Seconds 8; .venv\Scripts\python.exe capture_preview.py --delay 0 --out samples\metin_verde_seleccionado.png`

Cambia `verde` por el color real (rosa, azul, violeta, etc.). Con el mapa que tenga monstruos alrededor mejor: sirve para ver
si algun monstruo se confunde con piedra.

## Mapa sin ningun metin a la vista (para falsos positivos)

   `Start-Sleep -Seconds 8; .venv\Scripts\python.exe capture_preview.py --delay 0 --out samples\otro_mapa_sin_metin.png`

(Idealmente con monstruos, arboles, un edificio o la nieve/pasto del mapa nuevo en cuadro.) Si el suelo del mapa nuevo es
muy distinto (claro, verde, arena), una captura mas de esas ayuda a ver si el fondo molesta.

## Comparar color vs estructura (lo corre cualquiera, no toca el juego)

   `.venv\Scripts\python.exe tools\far_compare.py samples\metin_verde_lejos.png samples\metin_verde_cerca.png samples\otro_mapa_sin_metin.png`

Imprime las piedras de cada modo (con su matiz H) y guarda `debug\far_<nombre>.png` (rojo = color, verde = estructura).
Pasame esas capturas (o el resultado) y ajusto los umbrales de `vision.far_metin.structure` si hace falta.

## Dar de alta el metin nuevo (sin sacar mas fotos)

- Plantilla de la etiqueta + tono de la piedra (pista opcional), con el mouse sobre la etiqueta del metin:
  `.venv\Scripts\python.exe calibrate.py metin "Metin de Hielo"`
  (agrega la plantilla, el nombre a `target_metin_names` y mide el tono de la piedra de abajo: `vision.far_metin.hue_hints`.)
- Para afinar o repetir solo el tono (1 a 3 capturas, mouse sobre la piedra, F9 por captura, F10 al terminar):
  `.venv\Scripts\python.exe calibrate.py tono "Metin de Hielo"`

El tono es solo una pista (suma puntaje al candidato); el metin se detecta igual sin ella.
El nombre en el hover sigue siendo la confirmacion final del tipo (ACERCARSE no hace clic sin nombre en el hover).

## Para probar en el juego (cuando haya un metin de otro color a mano)

- `vision.far_metin.mode` esta en `"ambos"` (union de color + estructura). Para probar solo el nuevo: `"estructura"`.
- Ver en `debug\` las capturas de ACERCARSE: el aro magenta marca las piedras lejanas detectadas.
