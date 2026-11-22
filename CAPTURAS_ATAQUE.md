# Capturas que ayudan al ataque (piedra tapada y menu de opciones)

Todas se sacan con el juego a pantalla normal y el bot PARADO. Los comandos esperan 8 s para que te dé tiempo de ir al juego.
Se corren desde la carpeta del proyecto (`D:\proyectos\metin-bot`). Las capturas quedan en `samples\`; pasale a Claude los nombres.

## 1. Menu de opciones del juego (confirmar el detector)

El detector ya existe y está **prendido** (`vision.game_menu.enabled`): se calibró con las 5 capturas del menu abierto que
dejó el Escape del bot en `debug\run_20261121_205635` (frames 0014, 0015, 0075, 0076, 0077; score 1.0, contra < 0.45 en las
73 capturas sin menu). Si querés confirmarlo con una captura tuya:

1. Con el personaje parado y **nada seleccionado** (sin panel de monstruo ni de metin arriba), apretá **Escape** una vez: abre el menu.
2. Sin tocar nada más (el menu abierto), corré:

```powershell
Start-Sleep -Seconds 8; .venv\Scripts\python.exe capture_preview.py --delay 0 --out samples\menu_opciones.png
```

(Primero lanzá el comando y, durante los 8 s, abrí el menu con Escape.) Después cerrá el menu con "Cancelar".

## 2. Metin tapado (por bichos, arboles o estructuras)

Para ajustar el punto de clic sobre la parte visible. Lo ideal son 3 o 4 capturas con la piedra visible a medias:

- una con **bichos encima de la piedra** (los nombres rojos justo arriba de ella),
- una con la piedra **detrás de un tronco** o rama de arbol,
- una con la piedra **detrás de una casa/estructura**,
- (si pasa) una justo cuando el bot clickea y el panel se pierde enseguida: apenas ves la piedra, el personaje tiene que estar quieto o caminando hacia ella.

Para cada una, con el metin a la vista (sin seleccionar):

```powershell
Start-Sleep -Seconds 8; .venv\Scripts\python.exe capture_preview.py --delay 0 --out samples\metin_tapado_1.png
Start-Sleep -Seconds 8; .venv\Scripts\python.exe capture_preview.py --delay 0 --out samples\metin_tapado_2.png
Start-Sleep -Seconds 8; .venv\Scripts\python.exe capture_preview.py --delay 0 --out samples\metin_tapado_3.png
```

Anotá en cada una qué tapa la piedra (bicho / arbol / casa) y de qué lado.

Con el modo debug del bot prendido (`debug.enabled`) también sirven las `_raw.png` de `debug\run_*`: indicá el número de frame.
