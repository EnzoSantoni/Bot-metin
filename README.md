# metin-bot

Bot de farmeo de metin para un servidor privado de Metin2 que permite este tipo de programas. Funciona **solo con la pantalla y el teclado/mouse**: captura la ventana del juego, analiza la imagen (OpenCV) y simula clics y teclas (pydirectinput). No se inyecta en el juego ni lee su memoria.

Qué hace: busca los metin de los tipos configurados dentro de una zona de farmeo, los ataca, recoge los objetos (sin yang ni objetos de otros jugadores), explora la zona cuando no hay ninguno a la vista y se destraba si queda bloqueado. Si aparece el captcha "Bot Verify" **no lo resuelve**: se pausa y hace sonar una alarma para que lo resuelvas vos. Si el personaje muere, se detiene con alarma.

> Estado: en desarrollo. El avance, las decisiones y las pruebas pendientes están en [PROGRESS.md](PROGRESS.md). Los requisitos, en [ESPECIFICACION.md](ESPECIFICACION.md).

## Requisitos

- Windows 10/11.
- Python 3.14 (con el lanzador `py`).
- El juego en **modo ventana**.
- **Si el juego corre como administrador, la terminal también tiene que abrirse como administrador.** Si no, Windows bloquea los clics y teclas del bot (el bot lo detecta y avisa).

No hace falta instalar nada fuera de `pip`: la lectura de texto usa plantillas de caracteres, sin OCR.

## Instalación

Desde una terminal en la carpeta del proyecto:

```bat
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Configuración: crear `config.json`

`config.json` es personal y **no está en el repo**. Hay tres formas de crearlo:

1. **Mínimo (recomendado).** Creá `config.json` solo con lo que cambia; todo lo demás toma los valores por defecto:

   ```json
   {
     "window_title": "Guabina",
     "expected_client_size": [1858, 1057],
     "target_metin_names": ["Metin de Oscuridad", "Metin Negro"],
     "farm_zone": {
       "polygon": [[439, 141], [753, 91], [702, 203], [436, 196]],
       "outside_tolerance": 30
     },
     "debug": { "enabled": false, "dir": "debug" }
   }
   ```

2. **Copiando el ejemplo completo.** `config.example.jsonc` tiene todos los campos con su explicación. Copialo como `config.json` y **borrá los comentarios** (las líneas con `//`): `config.json` tiene que ser JSON puro.

   ```bat
   copy config.example.jsonc config.json
   ```

3. **Automático.** Si `config.json` no existe, cualquier script lo crea con los valores por defecto. Después hay que editar al menos `window_title`, `target_metin_names` y `farm_zone`.

Campos principales:

| Campo | Qué es |
|---|---|
| `window_title` | Título (o parte única del título) de la ventana del juego. Para verlo: `list_windows.py`. |
| `expected_client_size` | Tamaño del área de juego sin la barra de título (lo imprime `capture_preview.py`). |
| `target_metin_names` | Tipos de metin a atacar. Cada uno necesita su plantilla en `templates/labels/` y `templates/panel_names/`. |
| `farm_zone.polygon` | Vértices de la zona de farmeo en coordenadas del juego (las de debajo del minimapa), en orden y sin cruces. |
| `farm_zone.outside_tolerance` | Cuánto puede alejarse el personaje del borde hacia afuera antes de volver. |
| `controls` | Teclas del juego (atacar, recoger, montar, cámara, etc.). |
| `safety` | Pánico (F12), pausa (F11), esquina de pánico y cuenta regresiva. |
| `debug.enabled` | Guarda capturas anotadas de cada decisión en `debug/run_<fecha>/`. |

## Puesta en marcha (en este orden)

Todo con `.venv\Scripts\python.exe` desde la carpeta del proyecto y el juego abierto:

1. `list_windows.py` → buscá el título del juego y ponelo en `window_title`.
2. `capture_preview.py` → trae el juego al frente y guarda `debug\capture_preview.png`. Con `--mouse` muestra la posición del cursor en coordenadas del juego.
3. `detect_preview.py` → captura y guarda `debug\detect_preview.png` con todo lo que detecta (metin, panel, coordenadas, objetos, captcha, muerte). **No manda input.** Con `--image archivo.png` analiza una captura guardada.
4. `test_input.py --simular` y después `test_input.py` → prueba que las teclas y clics lleguen al juego (cámara, zoom, Z, Ctrl+G, clic en el piso, Escape).
5. `run_bot.py --observar --max-minutos 3` → el bot percibe y decide, pero **no manda nada**; muestra lo que haría.
6. `run_bot.py --max-minutos 3` → el bot de verdad, supervisado.

## Uso

```bat
.venv\Scripts\python.exe run_bot.py
```

Opciones: `--max-minutos N` (termina solo a los N minutos), `--observar` (sin input), `--config ruta`.

- Cuenta regresiva de 5 s antes de arrancar.
- **F12** o llevar el mouse a la **esquina superior izquierda** de la pantalla: detiene el bot al instante y suelta todas las teclas.
- **F11**: pausa / reanuda.
- No gires la cámara con el mouse mientras corre (el bot lo detecta y corrige, pero conviene no hacerlo).
- Al terminar imprime un resumen (metin rotos, objetos, tiempo por estado, motivo de fin). El detalle queda en `logs\bot.log`.

## Modo debug

Con `"debug": {"enabled": true}` el bot guarda en `debug\run_<fecha_hora>\` una captura anotada en cada cambio de estado, descarte de metin e intento de destrabe, más un `events.log`. Sirve para entender por qué tomó una decisión rara. Tiene topes por tipo de evento para no llenar el disco.

## Tests

```bat
.venv\Scripts\python.exe -m pytest tests -q
```

Los tests de visión usan las capturas reales de `samples/` (no incluidas en el repo por ahora). Ningún test manda teclas ni clics reales: el input, la ventana y el juego están simulados.

## Si algo falla

| Síntoma | Qué revisar |
|---|---|
| "No se encontró ninguna ventana…" | `window_title` (correr `list_windows.py`). |
| Los clics o teclas no llegan | Ejecutar la terminal como administrador si el juego corre como administrador. |
| La captura sale con otra ventana encima | El bot espera a que el juego esté al frente; si no puede traerlo solo, avisa y espera a que hagas clic en el juego. |
| No reconoce un tipo de metin | Que esté en `target_metin_names` y tenga plantilla: `tools/make_label_template.py` y `tools/make_panel_name_template.py`. |
| Se sale de la zona o se acerca mucho al borde | `farm_zone.polygon`, `farm_zone.outside_tolerance` y `explore.border_margin`. |
| Se traba seguido | La sección `stuck` (tiempos de destrabe) y `explore`. Activá el modo debug y mirá las capturas. |
| Lee mal la vida o las coordenadas | Completar plantillas de dígitos con `tools/make_glyph_templates.py`. |

## Estructura

```
bot/            lógica del bot
  capture.py      ventana del juego y captura
  vision/         detección (metin, panel, coordenadas, objetos, captcha, muerte, monstruos, piedras lejanas)
  input/          envío de teclas y clics (pydirectinput) y acciones del juego
  control.py      pánico, pausa y cuenta regresiva
  bot.py          máquina de estados
  navigation.py   navegación en coordenadas del juego
templates/      plantillas recortadas de capturas reales
tools/          herramientas para generar plantillas
tests/          tests (pytest) y simulador del juego
run_bot.py      ejecutable principal
config.example.jsonc  configuración de ejemplo, comentada
```
