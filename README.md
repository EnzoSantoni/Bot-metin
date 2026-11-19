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

### Calibración

`calibrate.py` ayuda a medir y guardar valores (en los perfiles o en `config.json`, ver "Perfiles"). **Es pasivo: nunca manda teclas ni clics al juego.** Solo lee la pantalla y escucha las hotkeys **F8 / F9 / F10** (para que marques momentos mientras jugás); **F12** o Ctrl+C cancelan sin guardar. Los subcomandos que escriben muestran el cambio (antes → después), piden `s/n` (o usá `--si`), copian `config.json.bak` y validan antes de guardar. Todos aceptan `--config ruta`.

| Cuándo | Comando |
|---|---|
| Elegir la ventana del juego y medir su cliente (`window_title`, `expected_client_size`) | `.venv\Scripts\python.exe calibrate.py ventana [--filtro texto]` |
| Definir la zona de farmeo: caminá a cada esquina y apretá F9 (F8 borra el último, F10 termina); guarda `debug\calibrate_zona.png` | `.venv\Scripts\python.exe calibrate.py zona [--tolerancia N]` |
| Medir la velocidad a caballo y proponer `coords_filter.max_speed` (F9 y cabalgá en línea recta) | `.venv\Scripts\python.exe calibrate.py velocidad [--segundos 10]` |
| Agregar un tipo de metin: mouse cerca de la etiqueta + F9 (un segundo F9 con el metin seleccionado guarda el nombre del panel) | `.venv\Scripts\python.exe calibrate.py metin "Metin de Oscuridad"` |
| Revisar a ojo las zonas excluidas, ventanas y coordenadas: `debug\calibrate_zonas.png` (no modifica nada) | `.venv\Scripts\python.exe calibrate.py zonas` |
| Ver qué valores siguen siendo estimaciones y cómo medirlos | `.venv\Scripts\python.exe calibrate.py resumen` |

## Perfiles

La configuración se arma en capas: **valores por defecto ← perfil de servidor ← perfil de personaje ← perfil de zona ← `config.json`**. Gana siempre la última: `config.json` pisa a todo. El bot recibe el mismo diccionario de siempre; lo único que cambia es de dónde salen los valores.

Los perfiles son archivos JSON (sin comentarios) en `profiles/`, **dentro del repo**, y guardan solo lo que difiere de los valores por defecto:

| Perfil | Archivo | Qué va |
|---|---|---|
| Servidor | `profiles/servidores/Guabina.json` | `window_title`, `expected_client_size`, `controls`, `vision.*` (plantillas y colores de la interfaz) y la sección `server` (`name`, `pickup_mode`, `captcha_type`). |
| Personaje | `profiles/personajes/Farfarfa.json` | La sección `character` (`name`, `class`, `potions`, `skills`), `vision.items.owner_template` (el nombre amarillo "Farfarfa's"), `pickup.dismount` y `coords_filter.max_speed`. |
| Zona | `profiles/zonas/Guabina - Oscuridad y Negro.json` | `farm_zone`, `target_metin_names` y la sección `zone` (`name`, `farm_mode`, `metin_priority`, `bichos`). |

Todo lo demás (tiempos de ataque, destrabe, navegación, debug, alarma, seguridad) es configuración **general** y va en `config.json`. Un perfil con una clave de otra categoría da un error claro (por ejemplo, "la clave 'controls' no va en un perfil de zona; va en el de servidor"). Las secciones `server`, `character` y `zone` son el lugar previsto para las fases 11 a 13: **el bot todavía no las usa** (están documentadas en `config.example.jsonc`).

**Qué perfiles se usan.** Sin nada más: `Guabina`, `Farfarfa` y `Guabina - Oscuridad y Negro`. Para cambiarlo, el bloque `"profiles": {"servidor": "...", "personaje": "...", "zona": "..."}` de `config.json`, o las opciones `--servidor`, `--personaje` y `--zona` de `run_bot.py`, `detect_preview.py` y `calibrate.py` (pisan al bloque). Un perfil inexistente da un error con la lista de los disponibles. Si la carpeta `profiles/` no existe, todo funciona como antes (valores por defecto + `config.json`). `run_bot.py` loguea al arrancar `Perfiles: servidor=…, personaje=…, zona=…`.

**Avisos de pisado.** Si `config.json` define un valor que el perfil elegido también define, el log avisa una sola vez por sección: `config.json pisa farm_zone del perfil de zona 'Guabina - Oscuridad y Negro'` (y, si el valor es igual, que podés borrarlo o migrar). El bot sigue usando el de `config.json`.

**Comandos**

```bat
.venv\Scripts\python.exe calibrate.py perfiles listar
.venv\Scripts\python.exe calibrate.py perfiles migrar
.venv\Scripts\python.exe calibrate.py personaje "Nombre" [--clase guerrero]
```

- `perfiles listar`: perfiles disponibles, cuáles están activos y qué pisa `config.json`.
- `perfiles migrar`: pasa de `config.json` a los perfiles activos todos los valores de servidor, personaje y zona, **los saca de `config.json`**, agrega el bloque `profiles` y deja el resto general. Muestra el plan, pide `s/n` y hace `.bak` de todo lo que toca. Después de migrar la configuración resultante es idéntica y no hay avisos.
- `personaje "Nombre"`: crea `profiles/personajes/Nombre.json`. Con un objeto reservado para ese personaje en el piso (se ve su nombre amarillo "Nombre's"), poné el mouse cerca y apretá **F9**: muestra el recorte en `debug\calibrate_personaje.png`, pide confirmación y guarda `templates/items/owner_<nombre>.png`. Opcional: con el propio personaje seleccionado, un segundo **F9** guarda la plantilla del nombre del panel. Al final pregunta si lo dejás como personaje activo. Es pasivo (nunca manda input); F12 o Ctrl+C cancelan.

**Dónde guarda cada subcomando de calibración:** `ventana` → perfil de servidor, `zona` → perfil de zona, `velocidad` → perfil de personaje, `metin` → plantillas + `target_metin_names` del perfil de zona. Si ese valor está definido en `config.json` (lo pisa), se guarda ahí y se avisa que conviene migrar.

**Crear un perfil nuevo a mano:** copiá uno existente de la misma carpeta, cambiale el nombre del archivo y el campo `name`, dejá solo lo que difiere, y usalo con `--zona "Otro"` (o en el bloque `profiles`). Los nombres pueden tener espacios y guiones, pero no `/`, `\`, `..` ni `< > : " | ? *`.

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

Opciones: `--debug` / `--sin-debug` (pisan `debug.enabled` solo en esa corrida), `--ui` (protocolo para la interfaz), `--max-minutos N` (termina solo a los N minutos), `--observar` (sin input), `--config ruta`, `--servidor`/`--personaje`/`--zona` (perfiles, ver más abajo).

- Cuenta regresiva de 5 s antes de arrancar.
- **F12** o llevar el mouse a la **esquina superior izquierda** de la pantalla: detiene el bot al instante y suelta todas las teclas.
- **F11**: pausa / reanuda.
- No gires la cámara con el mouse mientras corre (el bot lo detecta y corrige, pero conviene no hacerlo).
- Al terminar imprime un resumen (metin rotos, objetos, tiempo por estado, motivo de fin). El detalle queda en `logs\bot.log`.

## Interfaz

Doble clic en `abrir_interfaz.bat` (o en `abrir_interfaz_admin.bat` si el juego corre como administrador: pide permisos). Abre `gui.py` con el `pythonw.exe` del `.venv` (sin consola); si no hay `.venv` local busca `..\metin-bot\.venv`.

- **Perfiles:** listas de servidor, personaje y zona. Debajo, en naranja, los avisos de "config.json pisa X del perfil Y".
- **Opciones:** tiempo máximo en minutos (0 = sin límite), **Debug** (solo para esa corrida, no toca `config.json`), **Observar (sin input)** y **Siempre encima**.
- **Iniciar / Pausar-Reanudar / Detener:** se habilitan según el estado. Detener manda la orden de parada (igual que F12); si el bot no termina en ~5 s se lo cierra a la fuerza.
- **Estadísticas en vivo:** estado, metin rotos, objetos, captchas, destrabes, tiempo, cuenta regresiva y motivo de fin. Con el bot en pausa aparece "PAUSADO" en rojo.
- **Log:** últimas 200 líneas (WARNING en naranja, ERROR en rojo). Si el bot no arranca (juego no encontrado, falta de admin, plantillas), muestra el error y un aviso.
- La ventana no se minimiza (para dejarla en el segundo monitor) y recuerda posición y tamaño.
- El bot corre como subproceso (`run_bot.py --ui`): la ventana no se congela y **F12, la esquina del mouse y F11 siguen funcionando igual**. Si cerrás la ventana con el bot corriendo, pide confirmación y lo detiene.
- Las últimas opciones se guardan en `ui_settings.json` (no se sube al repo).

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
  profiles.py     perfiles (servidor, personaje, zona) y armado del cfg
profiles/       perfiles en JSON (servidores, personajes, zonas)
templates/      plantillas recortadas de capturas reales
tools/          herramientas para generar plantillas
tests/          tests (pytest) y simulador del juego
run_bot.py      ejecutable principal
config.example.jsonc  configuración de ejemplo, comentada
```
