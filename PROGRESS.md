# PROGRESS — metin-bot

Requisitos completos: [ESPECIFICACION.md](ESPECIFICACION.md). Este archivo registra estado, decisiones y pruebas pendientes. **Para retomar una sesión: leer este archivo primero.**

## Estado actual (2026-11-20)
**Rama `desarrollo`** (worktree `D:\proyectos\metin-bot-desarrollo`): **Fases 9 (perfiles), 10 (interfaz), 11 (habilidades), 12 (prioridad de metin, historial y conteo de objetos por tipo) 13 (modo bichos) y 14 (revivir al morir, apagado por defecto) hechas, y la tanda 1 de la interfaz completa (pestañas Metin y Zona, base común), con 1586 tests pasando.** Falta la prueba de Enzo en la PC (ver "A probar por Enzo → Rama `desarrollo`" y la sección de la Fase 14). `main` no se tocó.

- **main 0c66ec3 (Fase 6d) integrado en desarrollo:** elección del metin más cercano por perspectiva (`bot/ground.py`), ACERCARSE con hover + clic, ATACAR con caminata, barrido de cámara con pausa, RECOGER con W/A/S/D sin clics, `bot/keynav.py` reescrito y mapa de metin `bot/spawns.py`, todo junto con perfiles, interfaz, habilidades y prioridad. Prioridad + distancia de piso: con `zone.metin_priority` vacía el orden es el de main; con lista, las etiquetas van por rango de su tipo, las piedras sin etiqueta con el rango "no listado" y, a igual rango, la menor distancia de piso (`Bot._goals`, `bot/priority.py::order_by_priority`). Mapa de metin por zona: `spawns` es categoría de zona; sin `spawns.file` en el perfil de zona se deriva `profiles/zonas/<zona>.mapa.json` (ignorado por git, no se lista como perfil). Líneas de base regeneradas desde main (única diferencia con main: `spawns.file`, probada en `tests/test_profiles.py`).
- **main b353ff2 (Fase 6c) integrado en desarrollo:** regla del metin a la vista sin cámara, "etiqueta dudosa" y movimiento solo con teclado (`navigation.mode`, `bot/keynav.py`) conviven con perfiles, habilidades y prioridad. Líneas de base de "sin cambios de comportamiento" regeneradas desde main.
- **main 3c1b74c (recoger a pie) integrado en desarrollo:** las habilidades se bajan y se suben con `_set_dismounted` (una sola cuenta de montado con recoger, y la marca `logs/a_pie.flag` también las cubre). El perfil `Farfarfa` quedó con `pickup.dismount: true`. Líneas de base regeneradas desde main.

**Rama `main`** (carpeta `D:\proyectos\metin-bot`):
**Fase 6b completa en código: 903 tests pasan, revisada y corregida, y `debug.enabled: true` en `config.json`. PRÓXIMO PASO: la segunda prueba de Enzo en el juego** (ver "A probar por Enzo → Fase 6b"). Con el resultado, el log, la carpeta `debug/run_*` y la calibración de los valores estimados se cierra la Fase 6. **No tocar la lógica de la Fase 6 hasta esa prueba.**

**Fase 6c (correcciones tras la prueba de la 6b de las 22:43) en `main`: 980 tests pasan. PRÓXIMO PASO: Enzo repite la prueba de la 6b** (ver `PRUEBAS_ESTA_NOCHE.md`, punto 1).

**Rama `main`: Fase 6c + recoger a pie, 992 tests pasan.**

## Decisiones tomadas sin Enzo (noche del 2026-11-20)
- **Interfaz completa (tanda 1):** decisiones en la sección "Interfaz completa (tanda 1)" (selector rápido de modo guarda al elegir; guías de calibración con `calibrate.py --ui --en-perfil`; con `config.json` pisando valores se guarda en el perfil y se muestra el aviso).
- **Prioridad + distancia de piso:** con `zone.metin_priority` vacía se elige exactamente como en main (el más cercano por piso entre etiquetas objetivo y piedras). Con lista: rango de prioridad del tipo (piedra sin etiqueta = "no listado" = `len(lista)`) y, a igual rango, la menor distancia de piso. Se mantiene el log "Elegido X por prioridad (mas cerca habia Y)".
- **Modo bichos (Fase 13):** las decisiones (señuelo primero, recogida periódica, bloqueo si no junta nada, caminata corta = tramo de EXPLORAR, valores estimados de `zone.bichos`) están en la sección "Fase 13 — modo bichos".
- **Mapa de metin por zona:** el archivo va en el perfil de zona (`spawns.file`); si no lo define, se deriva `profiles/zonas/<zona>.mapa.json`. Cambiar de zona en la interfaz usa el mapa de esa zona. El mapa viejo `data/mapa_metin.json` de main no se migra solo (copiarlo a mano al archivo de la zona si se quiere conservar).

### Fase 6d: qué cambió (pedido de Enzo tras la prueba de las 01:15)
- **1. Elegir el metin más cercano** (`bot/ground.py`, `Bot._pick_goal`):
  - La distancia se estima sobre el plano del piso, con perspectiva: horizonte en `vision.ground.horizon_frac` (0.14 del alto) y focal `focal_frac` (0.7 del ancho). Más abajo en pantalla = más cerca.
  - Se elige el más cercano entre las etiquetas objetivo y las piedras lejanas. Log "Elegido … el mas cercano (piso D.D); tambien: …".
  - Sin límite de píxeles: sin cámara, un metin se ataca si el jugador está adentro o en la tolerancia. `approach.blind_screen_px` quedó obsoleto: si está en la config, se ignora con un aviso.
  - Test con `samples/real_tres_metin_hover.png`: elige la piedra de adelante a la derecha (1407, 374).
- **2. Ir con clic y hover** (`Bot._do_acercarse`, `gi.hover_at`):
  - Pone el mouse sobre la piedra, espera `approach.hover_wait_s` (0.3 s) y lee la etiqueta.
  - Si es un objetivo, hace clic en la piedra y el personaje va solo. Si es otro tipo, ignora esa posición.
  - Si después de `hover_tries` (2) intentos no aparece la etiqueta, hace clic igual y el panel confirma el tipo.
  - ATACAR tolera la caminata (`attack.walk_max_s` 25 s). Si se traba en el camino con monstruos cerca: espacio `attack.unstick_space_s` (1.5 s) y vuelve a clickear la piedra.
- **3. Recoger sin clics:** camina con combinaciones de W/A/S/D (8 direcciones) hacia el objeto más cercano y aprieta Z cuando lo tiene a ≤ `pickup.arrive_px` (70 px) de los pies. Lo demás sigue igual (bajarse solo si hay objetos, subirse al terminar, marca de a pie).
- **4. Cámara en pasos:** entre un giro de búsqueda y el siguiente espera `explore.settle_s` (0.6 s) y al menos `settle_frames` (2) capturas. Una piedra perdida de vista ya no queda ignorada 10 min.
- **5. Volver por coordenadas** (`bot/keynav.py` reescrito):
  - Para caminar ya no gira la cámara. Mide hacia dónde camina W con cada tramo (cualquier combinación de teclas) y elige la combinación de W/A/S/D más cercana al rumbo del destino. Q/E quedan solo para mirar.
  - Si A/D caminan al revés de lo supuesto, lo detecta con dos tramos y lo corrige.
  - EXPLORAR vuelve al centro si queda afuera, si está a menos de `explore.edge_return_u` (5 u) del borde y se aleja, o si termina un barrido sin ver nada cerca del borde.
  - En el simulador, con giros de cámara ruidosos y A/D espejadas, siempre vuelve en menos de 25 s.
- **6. Mapa de dónde aparecen los metin** (`bot/spawns.py`, config `spawns`):
  - Al romper un metin guarda la posición del jugador en `data/mapa_metin.json`, junto con cuántas veces lo rompió ahí y cuándo. Une los puntos a ≤ `merge_u` (6 u) y el archivo dura entre sesiones.
  - En EXPLORAR, sin metin a la vista, va al punto conocido más cercano que ya reapareció y que no visitó hace poco: `respawn_s` 240 s desde que lo rompió y `revisit_s` 120 s desde la última visita.
  - Al llegar (≤ `arrive_u` 5 u, o pasados `max_walk_s` 40 s) lo marca como visitado y hace un barrido de cámara. Elegir siempre el más cercano listo arma el recorrido entre puntos. Sin puntos listos, explora al azar como antes.
  - Logs: "Mapa de metin: guardo punto …", "Mapa de metin: voy al punto …" y "ningun punto listo (proximo en S s): exploro".
  - Con `--observar` lee el mapa pero no lo escribe.

### Decisiones tomadas sin Enzo (noche del 2026-11-20)
1. **Perspectiva:** modelo de plano del piso con valores estimados (`horizon_frac` 0.14, `focal_frac` 0.7). Solo se verificó con `real_tres_metin_hover.png`: allí elige bien con `horizon_frac` entre 0.10 y 0.19.
2. **Piedra sin etiqueta tras 2 hovers:** hace clic igual. Si no era objetivo, el panel lo detecta en ATACAR y la descarta.
3. **Para caminar no se gira la cámara** (punto 5): con combinaciones de W/A/S/D se llega a 8 direcciones sin depender de cuánto gira Q/E, que en la prueba de las 01:17 no coincidía con lo pedido. Q/E solo miran alrededor.
4. **Inclinar la cámara con clic derecho:** no lo hice. Era una sugerencia y puede dejar la cámara mirando solo el piso. Lo vemos después de la prueba.
5. **RECOGER:** si el jugador queda afuera de la zona mientras recoge, corta la recogida y vuelve.
6. **Commits:** los puntos 3 y 5 van en el mismo commit porque comparten las teclas combinadas.
7. **Tests del modo "clic":** el tope de VOLVER en los mundos al azar quedó en 50 s, porque ese modo no cambió. En modo teclado el tope sigue en 25 s.
8. **Mapa de metin:**
   - En `main`, que no tiene perfiles, el archivo es `data/mapa_metin.json` (ignorado por git). En `desarrollo` va junto al perfil de zona.
   - `respawn_s` 240 s es estimado ("a los pocos minutos").
   - Un punto al que no pudo llegar porque se trabó queda marcado como visitado, para no insistir con él en bucle.

### Recoger a pie (pedido de Enzo, 2026-11-20; RECOGER con clics reemplazado por W/A/S/D en la Fase 6d)
- **RECOGER en dos fases** (`Bot._do_recoger`):
  - **"Mirar":** hasta `pickup.look_s` (1 s) y `empty_confirm_cycles` ciclos. Si no ve objetos válidos (kind "item", ni yang ni de otro jugador, `allowed`), NO se baja y vuelve a BUSCAR.
  - **Con objetos válidos:** Ctrl+G y espera `mount_wait_s` (solo si `pickup.dismount`).
  - **Recoger, objeto por objeto:** elige el más cercano, clic en su etiqueta y Z al llegar. "Llegar" es que la etiqueta esté a ≤ `arrive_px`, que desaparezca, que el jugador quede quieto `still_s`, o que pasen `walk_timeout_s`. Si la etiqueta ya está a ≤ `arrive_px`, aprieta Z sin clic.
  - **Etiquetas con un nombre de monstruo pegado** (≤ `monster_gap_px`): no se clickean; solo Z.
  - **Objetos que no se pueden juntar:** se ignoran después de `tries_per_item` intentos.
  - **Al terminar:** Ctrl+G y espera, y vuelve a BUSCAR (o a VOLVER).
- **Una sola cuenta de montado:**
  - `Bot._set_dismounted` es el único lugar que cambia `_dismounted`. `_dismount` y `_remount` nunca repiten el Ctrl+G.
  - Las esperas de la animación miran si aparece el captcha. Si el captcha sale estando a pie, al irse el panel se sube antes de seguir.
- **F12 a pie:**
  - Mientras está a pie existe `logs/a_pie.flag`. `run_bot.py` pasa `mount_marker` (salvo con `--observar`).
  - La corrida siguiente ve la marca, se sube primero y la borra. Si Enzo se sube a mano después de un F12, tiene que borrar el archivo.
- **`pickup.dismount` (true por defecto) = recoger a pie**; false = recoge montado con el mismo flujo de clic + Z. Claves nuevas: `look_s`, `monster_gap_px`, `arrive_px`, `track_px`, `still_s`, `walk_timeout_s`, `tries_per_item` y `same_spot_u`. `presses_before_click` se eliminó: si una config vieja lo trae, se ignora.

**Antes (2026-11-19, noche): Fase 6c** (correcciones tras la prueba de la 6b de las 22:43) en `main`, con 980 tests.

### Fase 6c: qué cambió (pedido de Enzo tras la prueba de las 22:43)
- **Diagnóstico de la prueba:** el jugador estaba en (689, 107), ADENTRO del polígono a 5,7 u del borde norte. La zona estaba bien. Lo que falló fue la regla: sin cámara calibrada exigía que el jugador estuviera a ≥ 15 u del borde (`explore_margin`).
- **Regla nueva del metin a la vista** (`Bot._zone_reason`):
  - Con la posición del metin estimada, vale la zona del METIN.
  - Sin cámara, un metin con etiqueta a ≤ `approach.blind_screen_px` (160 px) se ataca si el jugador está adentro o en la franja de tolerancia.
  - Una piedra lejana sin etiqueta se persigue si el jugador está adentro (sin margen).
- **"tipo ignorado (metin_negro)" era falso:** era el cartel de un monstruo que se parecía un poco a la plantilla sin llegar al umbral. Ahora el log dice "etiqueta dudosa (se parece a X, score; no alcanza el umbral)". `config.json` y las plantillas estaban bien: Metin Negro es objetivo (lo prueba `tests/test_metin.py` con una captura real).
- **Movimiento solo con teclado** (`navigation.mode: "teclado"`, por defecto; `"clic"` vuelve al modo anterior):
  - `bot/keynav.py` (`KeyboardNav`) mide el rumbo con tramos de W y gira la vista con Q/E. Aprende la velocidad de giro, la de caminata y si Q/E están al revés (WARNING en el log).
  - EXPLORAR, VOLVER, CENTRO, ACERCARSE y DESTRABAR (MIRAR = girar + W; ATRAS = S) ya no clickean el piso.
  - El clic izquierdo queda solo para el metin (clic y reclic) y para los objetos de RECOGER.
  - Claves nuevas: `navigation.keyboard.*`, `stuck.back_s` y `stuck.look_walk_s`. Valores iniciales estimados: 15 u/s y 60°/s; se aprenden solos.

**Antes (Fase 6b):** completa en código: 903 tests pasan, revisada y corregida, y `debug.enabled: true` en `config.json`. PRÓXIMO PASO: la segunda prueba de Enzo en el juego** (ver "A probar por Enzo → Fase 6b"). Con el resultado, el log, la carpeta `debug/run_*` y la calibración de los valores estimados se cierra la Fase 6. **No tocar la lógica de la Fase 6 hasta esa prueba.**
- **GitHub (2026-11-19):** el proyecto está en https://github.com/EnzoSantoni/Bot-metin, rama `main`, con el primer commit `21724e4` (pusheado y verificado).
  - Autor: el que tenía configurado git en la PC de Enzo.
  - Excluidos por `.gitignore`: `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `logs/`, `debug/`, `config.json`, `.claude/settings.local.json` y **`samples/`**, este último pendiente de la decisión de Enzo (ver "A probar por Enzo → GitHub").
  - README con la instalación y cómo crear `config.json`.
- **Fase 7 completa en código** (sin tocar la Fase 6; 933 tests pasan):
  - `calibrate.py` (lógica en `bot/calibration.py`), pasivo: nunca manda input.
  - El modo debug quedó hecho en la Fase 6b.
  - `README.md`: instalación, `config.json`, puesta en marcha, uso, debug, tests, problemas comunes y la sección "Calibración".
  - **Sin commitear todavía**: `calibrate.py`, `bot/calibration.py`, `tests/test_calibration.py`, `README.md` y `PROGRESS.md` están modificados localmente, después del commit `21724e4`.
  - Falta la prueba de Enzo de `calibrate.py` (ver "A probar por Enzo → Fase 7").
- Fases 0–5 completas y probadas por Enzo. Quedan para más adelante: B con el juego minimizado, muerte, alarma y lo pendiente de la Fase 3.
- Fase 6: primera prueba de 3 min. Atacó y recogió bien el primer metin, pero buscar, explorar, destrabar y respetar la zona funcionaron mal. Eso dio origen a la Fase 6b, con las reglas A–E de Enzo (ver "Resultado de Enzo" en "A probar por Enzo → Fase 6").
- La sesión del 2026-11-19 se cortó dos veces por el límite de uso, pero no quedó trabajo a medio hacer.

### Historia de la Fase 6b
Enzo aprobó el plan y mandó `samples/real_metin_lejos_1.png` y `samples/real_rodeado.png` (coordenadas verificadas a ojo: (704, 193) y (688, 194), las dos pegadas al borde de abajo del polígono, sin panel).
- Paso 1 hecho (787 tests pasan):
  - `bot/vision/far_metin.py`: núcleo rojo + humo. Detecta las 3 piedras de `real_metin_lejos_1` y las 2 de `real_rodeado`, sin falsos positivos. No detecta la de `metin_muy_lejos` (anaranjada y sin humo).
  - `bot/vision/monsters.py`: rojo (9,22,235) + "Lv" verde (51,255,152). Cuenta 6 monstruos cerca en `real_rodeado`, con un radio de 120 px.
- Paso 2 hecho: estados ACERCARSE y CENTRO, `StuckEpisode` persistente con las estrategias ESPACIO / MIRAR / ATRÁS → CENTRO, regla B en ATACAR, EXPLORAR en tramos con barridos, log del motivo de cada metin no elegido y `bot/debug_draw.py` con `DebugRecorder`. En el simulador: alejamiento máximo 17,3 u (tolerancia 30) y 32 de 32 episodios de traba resueltos.
- Paso 3 hecho: un segundo criterio de piedra sin humo recupera las 3 piedras Metin Negro que faltaban, sin falsos positivos. Sigue sin detectarse la piedra semitapada de `muerte.png`.
- 872 tests pasan. La revisión encontró 4 bloqueantes:
  - B1: loop con un metin fuera de la zona y la cámara sin aprender.
  - B2: sondeos sin guardia de zona.
  - B3: ESPACIO + W sin guardia de zona.
  - B4: captcha durante los holds (alarma hasta 2.8 s tarde, con teclas saliendo).
- Y 7 importantes: descarte sin cámara que bloquea todo, loop BUSCAR↔ACERCARSE con una piedra que desaparece, ESPACIO primero solo en el primer intento, "progreso real" mal medido, umbral de guardia que se corre, presupuesto del debug y visión nueva en combate.
- **Correcciones hechas (903 tests pasan)**:
  - B1: sin loop con un metin afuera (0 ataques, 8,7 u como máximo fuera).
  - B2: sondeos con guardia de zona. Explorando sin metin sale como mucho 5,7 u (antes 24); desde el borde, DESTRABAR llega a 26,5 u (antes 47).
  - B3: W en tramos de 0,3 s con chequeo de zona.
  - B4: holds en tramos de ≤ 0,5 s con chequeo de captcha. La alarma suena ≤ 0,3 s después, sin teclas de más.
  - I1–I7 y los menores, incluido el motivo de fin propio "no puede llegar al centro".
- **`debug.enabled: true` en `config.json`** para la próxima prueba.
- **Falta la segunda prueba de Enzo** (ver "A probar por Enzo → Fase 6b").

### Fase 6b — valores nuevos a calibrar (estimaciones)
- `stuck`: `space_s` 2, `forward_s` 1, `monsters_min` 2, `resolve_dist` 10, `look_steps` 4 × `look_step_s` 0.8, `space_fail_max` 2.
- `explore`: `camera_steps` 12 × `camera_step_s` 0.5 (¿da ~360°?), `border_margin` 25, `leg_s` 10, `max_without_metin_s` 90.
- `approach`: `blind_screen_px` 160, `blind_player_radius` 25, `max_seconds` 40.
- `attack`: `blocked_s` 4. Tramos: `forward_chunk_s` 0.3, `hold_chunk_s` 0.5. ESPACIO se suelta y vuelve a apretar cada 0.5 s: hay que verificar en el juego que el empujón no se corte.
- Las piedras lejanas sin humo solo se calibraron con Metin Negro (3 ejemplos).

## Forma de trabajo
- Sesión principal: planifica, reparte tareas, revisa. El código grande se delega:
  - `implementer` (sonnet): un módulo puntual por vez, con interfaz y criterios definidos.
  - `vision-tester` (sonnet): tests de pytest contra `./samples` y ajuste de umbrales.
  - `reviewer` (haiku): revisión de solo lectura al cerrar cada fase.
- Definiciones en `.claude/agents/`. **Solo se cargan al abrir una sesión nueva**; si no aparecen, usar un agente `general-purpose` con `model` sonnet/haiku y decirle que lea su `.md` primero (así se hizo la Fase 1).
- Al cerrar cada fase: actualizar este archivo (hecho / pendiente / "a probar por Enzo").

### Ramas (desde el 2026-11-19)
- **`main`** = la versión que Enzo prueba en el juego. **No se toca** mientras se desarrolla. La carpeta `D:\proyectos\metin-bot` queda **siempre en `main`**.
- **`desarrollo`** = rama de trabajo (fases 9 en adelante), en un worktree aparte: **`D:\proyectos\metin-bot-desarrollo`**.
  - Usa el venv de la carpeta principal: `D:\proyectos\metin-bot\.venv\Scripts\python.exe`.
  - `samples/` y `config.json` están copiados ahí (no están en git).
- Commit y push a `desarrollo` al terminar cada fase, y en puntos intermedios si la fase es larga.
- Errores que encuentre Enzo en `main`: se corrigen en `main` y después se pasan a `desarrollo` (merge de `main` en `desarrollo`).
- `config.json` es personal y no está en git. Cada carpeta tiene su copia: **nunca cambiar el formato del `config.json` de la carpeta principal** desde `desarrollo`.

## Fases
- [x] 0. Subagentes en `.claude/agents/`, especificación guardada, este archivo.
- [x] 1. Estructura, venv, captura de la ventana (+ script para listar ventanas). Probada por Enzo.
- [x] 2. Detección de metin por etiqueta naranja y filtro por nombre (+ zonas excluidas, inventario/Mapa grande, foco de ventana). *Falta la prueba de Enzo en el juego.*
- [x] 3. Panel superior (nombre, barra y número de vida) y coordenadas. *Falta la prueba de Enzo en el juego.*
- [x] 4. Objetos vs yang, captcha (solo detección) y muerte, más la alarma. *Falta la prueba de Enzo en el juego.*
- [x] 5. Input (interfaz + pydirectinput), `test_input.py`, pánico (F12 / esquina) y pausa (F11). Probada por Enzo.
- [x] 6. Máquina de estados, zona de farmeo y destrabe. *Falta la prueba de Enzo en el juego.*
- [x] 7. `calibrate.py`, modo debug y README. *Falta la prueba de Enzo de `calibrate.py` en el juego.*
- [ ] 8. Opcional: buff F1 y auto-revivir. *(Quedan absorbidas por la 11 (habilidades con temporizador) y la 14 (revivir automáticamente).)*
- [x] 9. *(Hecha en `desarrollo`; falta la prueba de Enzo en la PC.)* **Perfiles:** separar la configuración en perfil de servidor, de personaje y de mapa/zona, compatible con el `config.json` actual. Por defecto: servidor "Guabina", personaje "Farfarfa" y la zona actual, para que todo siga igual. Reorganiza la configuración, no la lógica del bot.
- [x] 10. *(Hecha en `desarrollo`; falta la prueba de Enzo en la PC.)* **Interfaz base** (tkinter, pensada para el segundo monitor y sin minimizarse):
  - Elegir perfiles; iniciar, pausar y detener; tiempo máximo; interruptor de debug.
  - Estadísticas en vivo: estado, metin rotos, objetos, captchas, destrabes y tiempo.
  - Las últimas líneas del log.
  - F11 y F12 siguen funcionando igual.
  - Guarda las últimas opciones y tiene un `.bat` para abrirla con doble clic.
- [x] 11. *(Hecha en `desarrollo`; falta la prueba de Enzo en la PC.)* Habilidades con temporizador (lista editable en la interfaz).
- [x] 12. *(Hecha en `desarrollo`; falta la prueba de Enzo en la PC.)* Historial de sesiones, conteo de objetos recogidos por tipo y prioridad entre tipos de metin.
- [x] 13. *(Hecha en `desarrollo`; falta la prueba de Enzo en la PC.)* Modo bichos: F4 cada X segundos + barra espaciadora + recoger (estado BICHOS, `zone.farm_mode`, sección "Modo de farmeo" en la interfaz).
- [x] 14. *(Hecha en `desarrollo`; falta la prueba de Enzo en la PC.)* Revivir automáticamente al morir (`revive.auto_revive`, apagado por defecto; faltan las capturas del inventario y del objeto del caballo).

## Ideas para después (de Enzo, 2026-11-19)
Los perfiles de la Fase 9 dejan el lugar previsto y los valores por defecto. La funcionalidad va en sus fases.
- **Servidor:**
  - Modo de recoger: `"uno_por_uno"` (Guabina: cada Z junta un objeto) o `"todo_junto"` (una Z junta todo).
  - Tipo de captcha.
- **Personaje:**
  - Teclas de pociones: en Guabina, F2 vida y F3 maná, y se toman solas.
  - Lista de habilidades con temporizador: tecla, duración en segundos, si requiere bajarse del caballo y duración de la animación. Ejemplos: guerrero "Aura de Espada" en F1, sura "Espada Espejo" en F1, defensiva en 4. → Fase 11.
- **Zona:**
  - Modo de farmeo: `"metin"` o `"bichos"` (→ Fase 13).
  - Prioridad entre tipos de metin (→ Fase 12).
  - Para el modo bichos: la tecla del ítem que atrae monstruos (F4 en la configuración de Enzo) y cada cuántos segundos usarlo.

## Fase 9 — plan (aprobado por Enzo, con ajustes)
**Ajustes de Enzo:**
1. Los perfiles incluyen los lugares de "Ideas para después", con valores por defecto.
2. Si `config.json` pisa un valor definido en el perfil elegido, el log lo avisa al arrancar (por ejemplo, "config.json pisa farm_zone del perfil de zona X"). `calibrate.py perfiles migrar` saca de `config.json` lo que pasa a los perfiles.
3. Nuevo subcomando `calibrate.py personaje "Nombre"`: crea el perfil de un personaje nuevo, con su plantilla del nombre amarillo ("<Nombre>'s").
4. El perfil de zona se llama **"Guabina - Oscuridad y Negro"**, no "Zona actual".

**Plan original:**
**Objetivo:** separar la configuración en tres perfiles sin cambiar nada de lo que hace el bot. El bot sigue recibiendo **el mismo diccionario `cfg` de siempre**. Lo único que cambia es de dónde salen los valores.
- **Archivos** (JSON puro, en git, en `profiles/`):
  - `profiles/servidores/Guabina.json`: lo que depende del servidor o del cliente, es decir `window_title`, `expected_client_size`, `controls` (teclas del cliente) y `vision.*` (plantillas y colores de la interfaz).
  - `profiles/personajes/Farfarfa.json`: lo que depende del personaje, es decir nombre, clase (para la Fase 11), `vision.items.owner_template` (el "Farfarfa's" amarillo), `pickup.dismount` y `coords_filter.max_speed` (velocidad a caballo).
  - `profiles/zonas/Zona actual.json`: dónde se farmea, es decir `farm_zone` (polígono y tolerancia) y `target_metin_names`.
  - Cada perfil guarda **solo lo que difiere de los valores por defecto**. Como los valores por defecto se hicieron para Guabina, los perfiles por defecto quedan cortos.
  - El resto (tiempos de ataque, destrabe, navegación, debug, alarma, seguridad) sigue siendo configuración **general** en `config.json`.
- **Cómo se arma la configuración:** valores por defecto ← perfil de servidor ← perfil de personaje ← perfil de zona ← `config.json` (**el `config.json` gana**). Con el `config.json` actual (que tiene `farm_zone` y `target_metin_names`), el resultado es **idéntico** al de hoy.
- **Qué perfiles se usan:**
  - Una clave nueva opcional en `config.json`: `"profiles": {"servidor": "Guabina", "personaje": "Farfarfa", "zona": "Zona actual"}`. Sin esa clave se usan esos tres por defecto.
  - `run_bot.py`, `detect_preview.py` y `calibrate.py` aceptan `--servidor`, `--personaje` y `--zona`, que la interfaz de la Fase 10 va a usar.
  - Un perfil inexistente da un error claro con la lista de los que hay.
  - Al arrancar, el log dice qué perfiles se usaron.
- **Validación por tipo:** cada perfil solo acepta las claves de su categoría (por ejemplo, un perfil de zona no puede traer `controls`). Error claro si no.
- **`bot/profiles.py`:** `list_profiles(tipo)`, `load_profile` / `save_profile` (valida y hace `.bak`), `build_config(ruta_config, selección)` y `migrate_config(ruta_config)`.
  - `load_config()` sigue existiendo con la misma firma, así que todo el código actual funciona sin cambios.
- **Migración (opcional, la corre Enzo cuando quiera):** `calibrate.py perfiles migrar` pasa los valores de `config.json` a los perfiles y deja en `config.json` solo `profiles` y lo general, con `.bak`. **No se aplica al `config.json` de la carpeta principal desde `desarrollo`**, para no romper la prueba en `main`.
- **`calibrate.py`:** cada subcomando guarda en el perfil que corresponde (`ventana` → servidor, `zona` → zona, `velocidad` → personaje, `metin` → plantillas del servidor + lista de la zona), salvo que ese valor esté pisado en `config.json`. En ese caso guarda en `config.json` como hoy y sugiere migrar.
- **Plantillas:** se quedan donde están (`templates/`); el perfil de servidor solo apunta a ellas. Más adelante se puede mover cada servidor a su carpeta.
- **Tests:**
  - El principal: con el `config.json` actual y los perfiles por defecto, `build_config` da **exactamente** el mismo `cfg` que la versión de `main`, comparando clave por clave.
  - Precedencia, validación por tipo, perfil faltante, selección por CLI, migración ida y vuelta (migrar y reconstruir da lo mismo) y que `calibrate` guarde en el perfil correcto.
  - Toda la suite actual tiene que seguir pasando sin cambios.
- **Documentación:** sección "Perfiles" en el README, y el bloque `profiles` documentado en `config.example.jsonc`.

## Interfaz completa (tanda 1) — pestañas Metin y Zona, base común (rama `desarrollo`)
Pedido de Enzo: interfaz completa y prolija para el segundo monitor, todo en español, con ayuda al pasar el mouse, validación de lo que se escribe y diseño consistente. La tanda 2 (pestaña Personaje, Modo bichos completa, pulido de Habilidades, repaso de tooltips) queda para después.
- **Base común** (`bot/ui/widgets.py`, `bot/ui/validators.py`): paleta y estilo ttk único (tema `clam`), espaciados constantes, `section()` (marco con título), `Tooltip`/`add_tooltip` (retardo 500 ms), `ValidatedEntry` (marca roja y mensaje corto) y validadores puros (`game_key` no reservada, `positive_number`, `non_negative_number`, `non_negative_int`, `non_empty`). Aplicados a Control, Metin, Zona, Modo bichos y las ventanas guiadas; Habilidades, Historial y Objetos conservan sus widgets (el estilo global los alcanza, el pulido es de la tanda 2).
- **Pestañas** (en este orden): Control, Metin, Zona, Modo bichos, Personaje (placeholder "Próximamente"), Habilidades, Historial, Objetos. Las que editan perfiles (Metin, Modo bichos, Habilidades) muestran "*" en el título con cambios sin guardar. Ventana por defecto 1000x800 (si no hay geometría guardada).
- **Control** en dos columnas: izquierda perfiles, selector rápido de modo (Metin/Bichos, guarda en el perfil de zona al elegir), opciones (tiempo máximo validado), Iniciar/Pausar/Detener; derecha estadísticas (suma "Muertes (revividas)"; señuelos ya estaba), objetos de la sesión y log.
- **Metin** (`bot/ui/metin_editor.py`, reemplaza a `priority_editor.py`): tipos = `target_metin_names` de la zona + los que tienen plantilla en `vision.metin.label_templates_dir` (nombre bonito por slug: `metin_de_avaricia` → "Metin de Avaricia"). Tabla con prioridad, casilla "Atacar" (clic), nombre y "Plantilla". ↑/↓, "Elegir por prioridad", Guardar en el perfil de zona con `.bak`. Validación: al menos un tipo marcado. Aviso (no bloquea) si un tipo marcado no tiene plantilla. `metin_priority` guarda solo los marcados en el orden de la lista. Botón "Agregar tipo nuevo".
- **Zona** (`bot/ui/zone_view.py` + Canvas en `app.py`): polígono de `farm_zone` escalado al canvas con margen, franja de tolerancia, puntos del mapa de metin (`spawns.file` de la config armada con `build_config`; tooltip con cuántas veces y hace cuánto), posición actual y rastro de las últimas 25 posiciones. Botón "Rehacer la zona". Se redibuja al cambiar de perfil, al terminar la guía, con "Actualizar" y cada vez que cambia el contador de metin rotos.
- **@@STATUS**: `run_bot.py` agrega `pos` ([x, y] redondeado a 0.1) y `zone_status` ("inside" | "tolerance" | "outside"), `null` si no hay lectura. `controller.py` los parsea tolerante (`Status.pos`, `Status.zone_status`; cualquier cosa rara → `None` / `""`).
- **Guías de calibración** (`bot/ui/calibrate_runner.py` pura + `bot/ui/calibrate_dialog.py` tk): ventana con los pasos en pantalla, botón Empezar (en "Agregar tipo nuevo", con el nombre validado), salida en vivo y preguntas respondibles (Sí/No o texto por stdin). Corre `calibrate.py metin|zona --ui --en-perfil` con `ctl.gui_python()`. Los botones se bloquean con el bot corriendo y de a una guía por vez; Cancelar/cerrar mata el subproceso (calibrate.py solo escribe tras confirmar).
- **`calibrate.py`**: dos opciones nuevas, sin cambiar el uso por consola: `--ui` (las preguntas salen como línea `@@ASK <pregunta>` y la respuesta se lee de stdin) y `--en-perfil` (guarda en el perfil activo —zona → perfil de zona con `.bak`— aunque `config.json` defina ese valor; `metin` toma como base los `target_metin_names` del perfil). Sin esas opciones, todo igual que antes (si `config.json` define el valor, se guarda ahí). El nombre del metin ya era posicional, así que no hizo falta `--nombre`; no se usa `--si` para que las preguntas las responda la persona.
- **Decisiones tomadas sin Enzo:** (a) el selector rápido de Control guarda de inmediato (si hay segundos/tecla de bichos inválidos avisa y no guarda); (b) Modo bichos conserva la sección actual tal cual, más tooltips; (c) si `config.json` pisa `target_metin_names` o `farm_zone`, la interfaz guarda en el perfil igual y muestra el aviso de pisado (también en la pestaña Metin): el bot sigue usando lo de `config.json` hasta migrar; (d) tipos con plantilla pero no en la zona aparecen desmarcados; (e) "Rehacer la zona" no pide guardar cambios de Metin porque no los toca; "Agregar tipo nuevo" sí (recarga la lista); (f) `priority_editor.py` y `tests/test_ui_priority.py` se reemplazaron por `metin_editor.py` y `tests/test_ui_metin.py`.
- **Tests:** `tests/test_ui_metin.py`, `tests/test_ui_tanda1.py` (validadores, escala del polígono, franja, tooltips, rastro, @@STATUS, comandos de calibrate, humo de ventanas y de la guía con un subproceso falso), `tests/test_calibrate_ui.py` (`--ui`, `--en-perfil`, `UiBridge._position`). Los humos de tkinter se saltean sin pantalla.
- **A probar por Enzo:** (1) abrir la interfaz y recorrer las pestañas (tooltips al pasar el mouse, "*" al editar, campo de minutos en rojo con "abc"); (2) Metin: marcar/desmarcar, orden, Guardar, ver el `.bak`; (3) "Agregar tipo nuevo" con un metin nuevo a la vista (F9 sobre la etiqueta, responder Sí/No desde la ventana; tiene que verse la plantilla en `templates/labels/` y el tipo en la tabla); (4) "Rehacer la zona" con F9 en cada esquina, F8, F10, responder Sí a guardar: el dibujo cambia y el perfil de zona queda con `.bak`; (5) con el bot corriendo, ver el punto rojo y el rastro moverse en la pestaña Zona y que los puntos azules aparezcan al romper metin; (6) las guías no andan si el juego corre como administrador y la interfaz no (F9 no llega): abrir con `abrir_interfaz_admin.bat`.

## Fase 14 — revivir al morir (rama `desarrollo`, APAGADO por defecto)
Con `revive.auto_revive: false` (el valor por defecto) todo es como antes: muerte -> MUERTO -> FIN con alarma "muerte" (las líneas de base pasan sin regenerar). Con `true`, la pantalla de muerte lleva al estado nuevo **REVIVIR** (`Bot._do_revivir`, percepción vacía); `bot/revive.py` tiene la config (`ReviveCfg`), `RectMask`, `here_button` y `find_icon`.
- **Config `revive` (perfil de PERSONAJE, en `RULES` de `bot/profiles.py`):** `auto_revive` false, `button_wait_s` 3.0, `confirm_wait_s` 1.5, `inventory_wait_s` 1.0, `button_tries` 3, `max_revives` 5, `after_revive_wait_s` 3.0, `recast_skills` true, `inventory_key` "i", `horse_item_template` "", `horse_item_match` 0.8, `horse_item_click` "right", `horse_key` "", `horse_wait_s` 3.0, `mount_after` true, `here_above_city_px` 30. Documentadas en `config.example.jsonc`; validadas en `bot/config.py::_validate_revive` (teclas válidas y no reservadas, segundos >= 0, plantilla existente si no es vacía).
- **Pasos (todos con espera en tramos y chequeo de captcha):** 1) espera `button_wait_s`, clic izquierdo en el centro de "Volver a Empezar Aquí" (rectángulo de `find_death`; si solo calzó "Ciudad", 30 px más arriba) y confirma con una captura que la pantalla de muerte se fue (hasta `button_tries` clics; si no, FIN con alarma); 2) espera `after_revive_wait_s`, `_set_dismounted(True, ...)` y `tracker.forget_reference()`; 3) caballo: `horse_key` si está; si no, con plantilla: abre el inventario, `find_icon` (`cv2.matchTemplate`, primero dentro de las ventanas de UI detectadas y si no en toda la imagen), clic `horse_item_click`, cierra el inventario; sin plantilla ni tecla: WARNING y se saltea; espera `horse_wait_s`; 4) `mount_after`: Ctrl+G vía `_remount` (solo si se llamó al caballo); 5) `recast_skills`: todas las habilidades quedan vencidas; 6) vuelve a `Bot._home` (BUSCAR o BICHOS) con WARNING "Revivido (n de max)"; no suena la alarma.
- **Máscara:** el clic del botón y el del objeto del caballo usan `RectMask` (solo permite el rectángulo de ese botón / ícono), porque la máscara normal bloquea las ventanas de UI (el inventario). Probado.
- **Captcha:** manda siempre. Si aparece con la muerte presente, primero el captcha; si aparece en medio del revivir, el revivir se retoma en el mismo paso (el estado vive en `Bot._rv`); si el inventario quedó abierto se cierra antes de reintentar. Al salir del captcha no se aprieta Ctrl+G mientras hay un revivir en curso.
- **Stats:** `deaths` (cada vez que se ve la pantalla de muerte, también con auto_revive apagado) y `revives`; van a @@STATUS (`deaths`, `revives`, `Status` del controlador), al resumen final ("muertes: N (revividas: M)", solo si hubo) y al historial (`muertes`, `revives`). La interfaz todavía no los muestra en pantalla.
- **max_revives:** por corrida; la muerte número max+1 termina la corrida como hoy (alarma "muerte", motivo "muerte"). Un revivir que falla (la pantalla no se va tras 3 clics) también.
- **Tests:** `tests/test_revive.py` (23): secuencias exactas de clics/teclas, sin caballo, `horse_key`, plantilla encontrada / no encontrada, clic izquierdo, max_revives, apagado = FIN, captcha en el medio y antes del clic, habilidades vencidas, Ctrl+G par y marca de a pie, máscara bloqueada, `muerte.png` (botón en (59, 83, 177, 22), centro (147, 94)), `find_icon` con una plantilla sintética, validación. `tests/test_profiles.py`: `revive` es sección nueva (`NEW_SECTIONS`).
- **Decisiones tomadas sin Enzo (noche del 2026-11-20):** (a) al entrar a REVIVIR el personaje se marca a pie (`_set_dismounted(True, "muerte")`): está muerto/sin caballo; si el revivir falla y la corrida termina, la marca queda y la próxima corrida intenta Ctrl+G al empezar (inofensivo sin caballo); (b) si no se logró llamar al caballo no se aprieta Ctrl+G y se sigue a pie (queda a pie en la cuenta; habilidades que piden bajarse no tocan Ctrl+G); (c) claves extra que no pediste: `confirm_wait_s`, `inventory_wait_s`, `button_tries`, `here_above_city_px`; (d) "Aquí" revive en el mismo lugar, así que no se maneja el regreso a la zona: el flujo normal (VOLVER) se encarga si quedó afuera; (e) `deaths` cuenta también con el auto-revivir apagado.
- **A probar por Enzo (falta la prueba):** poner `"revive": {"auto_revive": true}` en el perfil de personaje (o en `config.json`), dejarse matar y mirar el log: tiene que verse "REVIVIR", el clic en "Volver a Empezar Aquí" y "Revivido (1 de 5 permitidos)". Primero sin caballo (`horse_key` y plantilla vacíos: sigue a pie con un WARNING); después con `horse_key` o plantilla. Ajustar `button_wait_s` si el botón tarda más en habilitarse.
- **Capturas que Enzo tiene que pasar:** (a) captura completa del juego con el inventario abierto mostrando el objeto que llama al caballo, en la misma página donde lo tiene (`capture_preview.py --out samples\real_inventario_caballo.png`); (b) recorte del ícono del objeto del caballo (solo el ícono, PNG, p. ej. `templates/caballo.png`) o decir en qué casilla/página está; (c) si el objeto está en la barra rápida, qué tecla es (entonces va en `revive.horse_key` y no hace falta nada más); (d) una captura de la pantalla de muerte justo cuando el botón "Volver a Empezar Aquí" se habilita, si `samples/muerte.png` es de otro momento (por ejemplo, si el botón se ve gris/deshabilitado en esa captura).

## Fase 13 — modo bichos (rama `desarrollo`, 1482 tests)
Con `zone.farm_mode: "bichos"` el bot no busca metins: entra al estado nuevo **BICHOS** (percepción: coords, monsters, items). Con `"metin"` todo es idéntico a antes (las líneas de base `tests/data/baseline_events_*.json` y `test_baseline_identico...` pasan sin regenerar).
- **Qué hace BICHOS** (una acción por ciclo, decisión pura en `bot/bichos.py::decide`, ejecuta `Bot._do_bichos`): 1) señuelo (`lure_key`, al empezar y cada `lure_every_s`; log "Bichos: uso el señuelo (F4); proximo en 60 s"); 2) recoger "periódico" (pasaron `pickup_every_s`, hay objetos válidos y a lo sumo `pickup_max_monsters` monstruos cerca); 3) pelear: ESPACIO en tramos de `space_s` con chequeo de captcha (`_hold_space`) si `monsters_near >= min_monsters`; 4) recoger cuando no hay monstruos `clear_s` y hay objetos válidos; 5) caminar un tramo corto a un punto al azar adentro (`navigator.leg_target`, mismo largo/margen que EXPLORAR, W/A/S/D en modo teclado, sin girar la cámara) si no hay monstruos `idle_s`; 6) esperar.
- **Zona:** afuera o a menos de `explore.edge_return_u` del borde (tope: la mitad de la profundidad del centro, para no rebotar en zonas chicas) -> VOLVER por coordenadas (termina a `max(edge_return_u, navigation.explore_margin)`) y de vuelta a BICHOS. CENTRO también vuelve a BICHOS (no a EXPLORAR). DESTRABAR, CAPTCHA y RECOGER vuelven a BICHOS (`Bot._home`).
- **Habilidades:** se lanzan en BICHOS sin caminata en curso (`_skill_safe`); nunca en RECOGER. Captcha, muerte, pausa, F12, tope de tiempo y marca de a pie funcionan igual.
- **Estadísticas:** `BotStats.lures`, `fight_s`, `farm_mode`. @@STATUS suma `farm_mode`, `lures`, `fight_s`; el historial (`history.jsonl`) suma `farm_mode` y `lures`; el resumen final suma "señuelos: N | peleando: X min" solo en modo bichos. La interfaz muestra "Señuelos usados" y el detalle de sesión del historial "Modo de farmeo: Bichos | señuelos usados".
- **Interfaz** (pestaña Control, bajo "Prioridad de metin"): sección "Modo de farmeo" (Metin / Bichos, tecla del señuelo, cada cuántos segundos, Guardar). Lógica pura en `bot/ui/farm_mode_editor.py` (`validate_bichos`, `FarmModeEditor`): tecla válida y no reservada (F11/F12), segundos > 0; guarda en el perfil de zona conservando el resto (con .bak). Pregunta si hay cambios sin guardar al cambiar de zona y al Iniciar; se bloquea con el bot corriendo.
- **Claves nuevas en `zone.bichos`** (perfil de zona; validadas en `bot/config.py`, DEFAULTS y `config.example.jsonc`): `min_monsters` 1, `space_s` 2.0, `clear_s` 3.0, `pickup_every_s` 60, `pickup_max_monsters` 1, `idle_s` 20. `lure_every_s` ahora debe ser > 0. Todos son valores estimados, a calibrar en el juego. Los perfiles viejos (solo `lure_key` y `lure_every_s`) toman el resto de DEFAULTS.
- **Simulador:** `SimWorld.lure()` (el señuelo hace aparecer `lure_count` bichos pegados al personaje; con ESPACIO muere uno cada `bug_kill_s` y deja un objeto), `SimBackend` mapea `zone.bichos.lure_key`, `SimEnv.initial_state`. Tests: `tests/test_bichos_sim.py` (corridas de punta a punta, modo teclado y clic), `tests/test_bichos.py` (decisión, config, resumen, historial, @@STATUS), `tests/test_ui_farm_mode.py`.
- **Decisiones tomadas sin Enzo:** (a) el señuelo manda sobre todo lo demás cuando vence (incluso peleando); (b) la recogida periódica va antes que pelear solo si hay a lo sumo `pickup_max_monsters` monstruos; (c) si una recogida no junta ningún objeto (ítems que no se pueden levantar) se bloquea recoger por `pickup_every_s` para no bajarse y subirse en bucle; (d) después de usar el señuelo, el reloj de "sin monstruos" y el de `idle_s` arrancan de cero (se espera a que lleguen); (e) "caminata corta" = el mismo tramo de `explore.leg_units`/`explore.leg_s` que EXPLORAR, sin clave nueva; (f) el tiempo "sin monstruos" para recoger cuenta desde la última vez que se los vio (un tramo de ESPACIO de 2 s es una sola observación), así que recoge ~`clear_s` después del último ciclo con monstruos; (g) la tecla del señuelo se aprieta con `press_key` y `skill_timing.press_hold_s`, sin esperar animación; no se baja del caballo para usarlo.
- **Qué probar (Enzo):** ver "A probar por Enzo → Rama `desarrollo`: Fase 13".

## Fase 12 (parte 2) — historial y conteo de objetos
**Hecha en `desarrollo`; falta la prueba de Enzo.**
- **Catálogo de objetos** (`bot/item_catalog.py`, config `items_catalog`: `enabled` true, `dir` "data/objetos", `match` 0.88):
  - `label_signature(img, rect)`: máscara del texto blanco (`vision.items.white_min`) de las últimas `text_rows` filas del interior de la etiqueta, recortada a la caja del texto y escalada a 10 px de alto.
  - `ItemCatalog.match_or_add(firma, recorte)`: mismo ancho ±2 px y correlación >= `match` -> ese grupo; si no, grupo nuevo "Objeto N". `rename(id, nombre)`. Guarda `catalogo.json` + `<id>.png` (miniatura) + `<id>.npy` (firma), con guardado atómico; un archivo roto va a `.bak` y se empieza vacío. `dir=None` = desactivado.
  - Sobre `samples/items_en_piso.png` las 5 etiquetas reales dan 5 grupos distintos y la misma etiqueta da correlación 1.0.
- **Conteo** (`Bot._do_recoger`): en cada ciclo de la fase de recoger guarda la firma y el recorte de cada objeto válido (`_Pickup.seen`). Cuando la cantidad baja, los objetos de la captura anterior sin pareja en la actual (pareja = misma firma, desempate por cercanía en pantalla) son los que se recogieron; el más cercano a los pies primero. Cada uno suma a `stats.items_by_type[id]`; los que no se pueden asociar (sin firma o sin imagen) cuentan como `sin_identificar` ("Objeto sin identificar"). `stats.items` sigue igual. Log INFO "Objeto recogido: <nombre> (total en la sesion: N)". `stats.metins_by_type[slug]` suma al romper un metin, con el tipo confirmado en el panel.
- **Historial** (`bot/history.py`, config `history`: `enabled` true, `file` "data/historial.jsonl"): `run_bot.py` agrega una línea JSON por corrida en el `finally` (tope de tiempo, F12/pánico, Detener desde la interfaz -> motivo "interfaz", juego cerrado, muerte, excepción). Campos: `inicio`, `fin`, `duracion_s`, `perfiles`, `motivo`, `metins`, `metins_por_tipo`, `objetos`, `objetos_por_tipo` (ids del catálogo), `captchas`, `destrabes`, `acercamientos`, `habilidades`. `load_history` saltea líneas rotas con WARNING; `summarize(sesiones, catalogo, desde)` da los totales con el nombre actual de cada objeto.
- **Interfaz:** `@@STATUS` suma `items_by_type` ([{id, name, count}]) y `metins_by_type` (parseo tolerante en `controller.py`). Pestaña Control: tabla "Objetos de esta sesión" en vivo. Pestaña "Historial": totales Hoy / Total, tabla de sesiones (más nueva arriba), detalle de la elegida y botón Actualizar (recarga sola al terminar la corrida). Pestaña "Objetos": catálogo con miniatura, nombre y cantidad total; doble clic o "Renombrar" (valida no vacío, máx. 60 caracteres). Lógica pura en `bot/ui/history_view.py`; tkinter mínimo en `bot/ui/app.py`.
- **Decisiones tomadas sin Enzo (noche del 2026-11-20):**
  1. **No hay OCR:** el "tipo" de un objeto se identifica por la firma visual de su etiqueta. Etiquetas iguales se agrupan solas, cada grupo nuevo es "Objeto N" con miniatura y Enzo lo renombra en la interfaz; el nombre queda en el catálogo y también vale para el historial pasado (que guarda ids).
  2. Con `--observar` NO se registra en el historial (no es farmeo real) y el catálogo queda desactivado.
  3. El texto de la firma es solo el blanco puro (como `find_items`); un nombre de objeto con otro color de texto no se firma y cuenta como "sin identificar". Un objeto con la etiqueta pegada a terreno muy claro puede dar una firma distinta (grupo repetido): se arregla renombrando ambos con el mismo nombre (el historial los suma por nombre).
  4. Renombrar está bloqueado mientras el bot corre (el bot escribe el catálogo y pisaría el cambio).
  5. El conteo por tipo se hace con la misma heurística que `stats.items` (baja la cantidad de objetos válidos a la vista); si un objeto sale de pantalla al caminar, también cuenta.
- Tests: `tests/test_item_catalog.py`, `tests/test_history.py`, `tests/test_ui_history.py`. Los `make_cfg` de tests dejan `items_catalog` e `history` desactivados (como `spawns`).

## Fase 12 (parte 1) — estado: prioridad entre tipos de metin HECHA (1140 tests)
- `bot/priority.py` (`priority_rank`, `order_candidates`). Se usa en `_label_view` de `bot/bot.py`: con la lista vacía (o con un solo candidato) queda el orden de antes, por distancia. Los 4 baselines salen idénticos.
- Log "Elegido X por prioridad (más cerca había Y)", sin repetirse gracias a `debug.reason_memory_s`.
- Validación: `metin_priority` sin repetidos (por slug) ni vacíos. WARNING si un nombre no es objetivo (se ignora).
- **Interfaz:** sección "Prioridad de metin" en la pestaña Control, entre Perfiles y Opciones.
  - Tiene un interruptor, la lista con ↑ y ↓, Guardar (perfil de zona, con `.bak`; apagado guarda la lista vacía) y el indicador "sin guardar".
  - Se recarga al cambiar de zona, pregunta si hay cambios sin guardar y se bloquea durante la corrida.
  - Lógica en `bot/ui/priority_editor.py` (en la tanda 1 de la interfaz completa pasó a la pestaña Metin, `bot/ui/metin_editor.py`).
- Ojo: si `config.json` define `zone.metin_priority`, pisa al perfil (sale el aviso de pisado al arrancar) y el editor de la ventana no lo refleja.
- **Pendiente de la Fase 12:** historial de sesiones y conteo de objetos recogidos por tipo.

### Plan original
- **Config:** `zone.metin_priority` del perfil de zona (ya existe desde la Fase 9, vacía por defecto). Es una lista de nombres en orden: el primero tiene más prioridad. Los objetivos que no están en la lista van después, todos con la misma prioridad. Se compara por slug, como `is_target_name`.
- **Elección en BUSCAR:** entre los objetivos válidos, gana el de mayor prioridad y, a igual prioridad, el más cercano. **Con la lista vacía, el comportamiento es idéntico al actual**: se verifica con los baselines de `tests/data/baseline_events_*.json`. Si la prioridad cambió la elección, se loguea ("elegido X por prioridad; más cerca había Y").
- **Interfaz:** editor de la prioridad de la zona elegida, con los `target_metin_names` de esa zona ordenables con ↑ y ↓, un interruptor "Elegir por prioridad" (apagado = lista vacía) y Guardar en el perfil de zona, con `.bak`.
- **Validación:** lista de strings no vacíos, sin repetidos. Aviso si un nombre no está en `target_metin_names` (se ignora).
- **Queda para después:** el historial de sesiones y el conteo de objetos por tipo.

## Fase 11 — estado
- **Parte 1, núcleo: hecha** (commit `9265068`, 1093 tests).
  - `bot/skills.py` (`Skill`, `SkillScheduler`).
  - Lanzamiento en `bot/bot.py`, solo en momentos seguros (BUSCAR, EXPLORAR entre tramos, ATACAR confirmado y con `attack_settle_s` desde el último clic, con re-clic después).
  - Config `skill_timing` (`mount_wait_s` 1.5 estimado, `press_hold_s` 0.1, `recast_margin_s` 0, `attack_settle_s` 3, `chunk_s` 0.5).
  - `GameInput.press_key`, y `skills_cast` + `skills` en `@@STATUS`.
  - Baselines `tests/data/baseline_events_*.json` (se regeneran con `tests/baseline_scenarios.py`): con la lista vacía o deshabilitada, la secuencia es idéntica.
- **Parte 2, tabla editable en la interfaz: hecha** (1113 tests).
  - La ventana ahora tiene pestañas, "Control" y "Habilidades". La de Habilidades tiene una tabla con Activa, Nombre, Tecla, Duración, Bajarse, Animación y el tiempo Restante en vivo.
  - Botones Agregar, Editar (o doble clic), Quitar, ↑, ↓, Guardar y "Agregar ejemplo": "Aura de Espada" F1, "Espada Espejo" F1 y "Defensiva" 4, con 100 s, 2 s y bajarse sí, valores para ajustar.
  - El diálogo valida en vivo, reutilizando la validación de `bot/config.py`. Avisa, sin bloquear, si la tecla coincide con una de `controls`.
  - Indicador "sin guardar" y pregunta al cambiar de personaje o al iniciar con cambios pendientes.
  - Durante la corrida la tabla se bloquea y muestra el tiempo restante. "Habilidades lanzadas" aparece en las estadísticas.
  - Lógica en `bot/ui/skills_editor.py` (`SkillsEditor`, `validate_skill`, `key_warnings` y `EXAMPLES`). Guarda en el perfil de personaje, con `.bak`.
- **Fase 11 completa en `desarrollo`. Falta la prueba de Enzo en la PC.**

## Fase 11 — plan (pedido por Enzo, 2026-11-19): habilidades con temporizador
- **Lista:** `character.skills` del perfil de personaje (Fase 9). Cada habilidad tiene `name`, `key`, `duration_s`, `requires_dismount`, `animation_s` y `enabled`. **Con la lista vacía el bot se comporta exactamente igual que antes**: un test compara la secuencia de input contra una grabada antes del cambio.
- **`bot/skills.py`:** `SkillScheduler`. Al iniciar la sesión, todas las habilidades activas quedan vencidas, así se lanzan enseguida. Al lanzar una, su próximo vencimiento queda en `duration_s` (con un margen configurable).
- **Momento seguro:** solo al principio de un ciclo, nunca en medio de un clic, de recoger, de un destrabe, de un acercamiento, de una vuelta a la zona, del captcha ni de la muerte. Se lanza en BUSCAR, entre pasos de EXPLORAR y en ATACAR con el metin ya confirmado y unos segundos después del último clic. Después del lanzamiento, otro clic en el metin para seguir atacando.
- **Lanzamiento:**
  1. Soltar todo.
  2. Si `requires_dismount` y está montado: Ctrl+G y esperar.
  3. Apretar la tecla y esperar `animation_s`, en tramos con chequeo de captcha y pánico.
  4. Ctrl+G para subirse, solo si se bajó para esta habilidad.
  
  Si aparece el captcha en el medio: aborta, pasa a CAPTCHA, se sube al terminar el captcha y la habilidad queda vencida para reintentar. Con pánico: no manda nada más. Se lleva la cuenta de si está montado o no, para no desparejar Ctrl+G con el bajarse para recoger.
- **Interfaz:** tabla editable de las habilidades del personaje elegido (agregar, editar, quitar y guardar en el perfil), con ejemplos para cargar rápido ("Aura de Espada" F1, "Espada Espejo" F1, defensiva 4). Contador de habilidades lanzadas en las estadísticas.
- **Tests:** simulador con montar/desmontar, secuencia del lanzamiento, nunca durante RECOGER ni a mitad de un clic, relanzamiento al vencer, captcha y pánico durante el lanzamiento, habilidad sin bajarse, y la lista vacía sin cambios.

## Fase 10 — qué quedó (rama `desarrollo`)
- **Arquitectura:** la interfaz (`gui.py` → `bot/ui/app.py`, tkinter) lanza el bot como **subproceso** (`run_bot.py --ui`). La ventana nunca se congela, y el corte forzado del bot (`os._exit`) no la cierra. F11, F12 y la esquina del mouse siguen funcionando igual, porque viven en el proceso del bot.
- **Protocolo:**
  - El bot emite `@@STATUS {json}` cada 0,5 s (estado, fase, metin, objetos, captchas, destrabes, tiempo, pausado y perfiles) y `@@END {json}` al terminar.
  - Por stdin acepta `pause` (= F11) y `stop` (= F12, `panic("interfaz")`). Si stdin se cierra, se detiene.
  - Lo implementa `UiBridge` en `run_bot.py`.
- **Flags nuevos de `run_bot.py`:** `--ui`, `--debug` / `--sin-debug` (pisan `debug.enabled` solo en esa corrida) y `--sin-cuenta-regresiva`. Sin esos flags, `run_bot.py` funciona igual que antes.
- **`bot/ui/controller.py`** (lógica pura): línea de comando, parseo del protocolo, estado de los botones, `ui_settings.json` (últimas opciones, posición y tamaño; ignorado por git) y avisos de pisado de perfiles.
- **Ventana:**
  - Perfiles con los avisos de pisado; tiempo máximo (0 = sin límite); Debug, Observar y Siempre encima.
  - Iniciar, Pausar/Reanudar y Detener. Si el bot no termina en ~5 s, Detener lo cierra a la fuerza.
  - Estadísticas en vivo, "PAUSADO" en rojo, cuenta regresiva y motivo de fin.
  - Log de 200 líneas con colores.
  - **No se minimiza:** se quita el botón de minimizar (`WS_MINIMIZEBOX` por ctypes) y, además, vuelve sola si algo la minimiza (Win+D, Win+M).
  - Al cerrarla con el bot corriendo, pide confirmación y lo detiene.
- **Lanzadores:** `abrir_interfaz.bat` (`pythonw` del `.venv` local, o del de `..\metin-bot` en el worktree) y `abrir_interfaz_admin.bat` (pide permisos de administrador, con la ruta completa a `gui.py`).
- Tests: 1065 pasan (52 nuevos), con un bot falso (`tests/fake_ui_bot.py`). Nunca se lanzó el bot real ni la interfaz interactiva.
- Nota: durante el desarrollo quedaron procesos `python.exe` colgados del agente (comandos mal armados que esperaban stdin). La sesión principal los revisó, detuvo el agente y los cerró (2026-11-19 ~17:15). No quedó ninguno.

## Fase 9 — qué quedó (rama `desarrollo`)
- **`bot/profiles.py`:**
  - `KINDS`, `list_profiles`, `load_profile`, `save_profile` (valida categorías y hace `.bak`) y `profile_path` (rechaza nombres inválidos).
  - `build_config(config_path, servidor, personaje, zona, warn=True)` deja `cfg["_profiles"] = {servidor, personaje, zona, overrides}`.
  - `RULES` es la lista blanca de categorías. También `plan_migration` y `apply_migration`.
- **Perfiles por defecto:**
  - `profiles/servidores/Guabina.json`: ventana, tamaño, `controls` y `server` (`pickup_mode` "uno_por_uno", `captcha_type` "bot_verify_numerico").
  - `profiles/personajes/Farfarfa.json`: `character` (nombre, clase vacía, pociones F2/F3 automáticas, `skills` vacío), `owner_template`, `pickup.dismount` y `coords_filter.max_speed`.
  - `profiles/zonas/Guabina - Oscuridad y Negro.json`: `farm_zone`, `target_metin_names` y `zone` (`farm_mode` "metin", `metin_priority` vacía, `bichos` F4 cada 60 s).
- **Secciones nuevas** `server`, `character` y `zone` en DEFAULTS, con validación. Por ahora solo son el lugar previsto: el bot no las usa (Fases 11–13).
- **Armado:** DEFAULTS ← servidor ← personaje ← zona ← `config.json`, y gana `config.json`. Cada valor pisado se avisa en el log al arrancar. Con el `config.json` de Enzo hay 4 avisos, todos "con el mismo valor": `window_title`, `expected_client_size`, `target_metin_names` y `farm_zone`.
- **Selección:** bloque `profiles` en `config.json`, o `--servidor` / `--personaje` / `--zona` en `run_bot.py`, `detect_preview.py` y `calibrate.py`. `run_bot.py` loguea los perfiles y los avisos en `bot.log`.
- **`calibrate.py`:**
  - `perfiles listar`.
  - `perfiles migrar`: pasa los valores a los perfiles y los saca de `config.json`, con `.bak`.
  - `personaje "Nombre" [--clase]`: crea el perfil y la plantilla del nombre amarillo (F9), más la plantilla del panel (opcional, segundo F9).
  - Los subcomandos existentes guardan en el perfil que corresponde, salvo que `config.json` pise ese valor.
- **Test de igualdad:** con el `config.json` de Enzo, el `cfg` es **idéntico** al de `main`, clave por clave, salvo `_profiles` y las tres secciones nuevas. Baselines en `tests/data/`. `tests/data/config_enzo.json` es una copia del `config.json` de Enzo (sin datos sensibles).
- 1013 tests pasan (80 nuevos). `.gitignore` también ignora `*.bak`.

## Fase 1 — qué quedó
- `.venv` con Python 3.14.2. `requirements.txt`: mss 10.2.0, numpy 2.5.5, opencv-python 5.1.0.95, pywin32 312, pydirectinput 1.0.4, keyboard 0.13.5, pytest 9.2.0.
- `bot/config.py`: `load_config` / `save_config` / `validate_config`, merge profundo sobre `DEFAULTS`, `resolve_path` (rutas relativas = relativas a la carpeta del proyecto, no al directorio desde donde se ejecuta).
- `bot/log.py`: `setup_logging(cfg)` → consola + `logs/bot.log` rotativo (5 MB × 3).
- `bot/geometry.py`: `Rect` y `client_to_screen` / `screen_to_client` puros (con `strict` que impide clickear fuera del juego).
- `bot/capture.py`: `GameWindow` (buscar por título con prioridad al título exacto, área de cliente recalculada en cada llamada, `activate()`, `capture(region)` en coords de cliente; la región tiene que entrar entera). Ventana cerrada → `WindowNotFoundError` y vuelve a buscar en la siguiente llamada. DPI awareness habilitado.
- `list_windows.py`, `capture_preview.py` (`--delay`, `--mouse`, `--out`).
- `config.json` (JSON puro) y `config.example.jsonc` (comentado).
- Tests: config, geometry, capture (con win32/mss simulados), samples.

## Decisiones
- Python: se usa **3.14.2** (el instalado), por pedido de Enzo (2026-11-18), en lugar de 3.12.
- **Tamaño real de trabajo: cliente de 1858×1057, DPI 1.00** (medido por Enzo; ya está en `config.json`). Nada se calibra a 1920×1080.
- **Las samples son capturas de la ventana real, pero de tamaño variable** (1846–1858 × 1010–1064): la escala es 1:1 (letras y objetos miden en píxeles lo mismo que en el juego, sirve para plantillas), pero las posiciones absolutas se corren entre samples. Por eso las regiones (minimapa/coordenadas, panel superior, zonas excluidas) se ubican relativas a los bordes de la imagen y/o buscando el elemento por su aspecto. Las futuras `samples/real_*.png` (de `capture_preview.py --out`) son exactas de 1858×1057.
- **Lectura de texto, nombres de metin (Fase 2): plantillas de la etiqueta entera, sin OCR.**
  - Justificación: el naranja del texto es exacto, BGR (0,128,255) sin antialiasing, y la fuente tiene un tamaño fijo en píxeles. Una máscara binaria contra una plantilla (TM_CCOEFF_NORMED) da 1.00 contra su propio nombre y como máximo 0.62 contra otro metin.
  - Umbrales: `min_score` 0.85 y `min_margin` 0.1. Ante la duda, no es objetivo.
  - No agrega dependencias. easyocr arrastraría PyTorch.
- **Lectura de números, vida y coordenadas (Fase 3): plantillas por carácter, sin OCR.**
  - Justificación: los dos textos son de color plano sin antialiasing, gris (217,217,217) en las coordenadas y (199,199,199) en el panel, con contorno negro de 1 px. Se binarizan por color (±12 por canal) aunque el fondo cambie con el terreno.
  - Son dos fuentes de tamaño fijo: coordenadas con dígitos de 5×8 y panel con dígitos de 4×7. En las samples aparecen los 10 dígitos de las dos fuentes, más `( ) ,` en coordenadas y `. /` en el panel.
  - Lectura en ~1 ms (coordenadas) y ~3 ms (panel). Sin dependencias nuevas; un OCR sería más lento y pesado, y más propenso a errores con texto de 7–8 px.
  - Segmentación: componentes conexas. Los caracteres pegados (el "/" del panel) se separan "pelando" con la plantilla más grande que entra. Un hueco anormal entre caracteres inserta '?', así un dígito tapado nunca da otro número válido.
  - Plantillas en `templates/glyphs/{coords,panel}/`. Se completan con `tools/make_glyph_templates.py`.
- **Nombre en el panel:** plantilla del nombre entero en la fuente del panel (`templates/panel_names/`), buscada en la línea del nombre. Hay plantillas de Oscuridad, Negro y Avaricia, y de control: el propio personaje (farfarfa) y un monstruo (se_rang). Un nombre sin plantilla nunca es objetivo.
- **Vida:** la fuente principal es la barra roja. El número solo se acepta si es coherente con la barra; sin barra legible, el número no se usa.
- **Zonas excluidas:** una etiqueta se descarta si más del 50% de su rectángulo cae en una zona. No se borran píxeles, porque eso le comía letras a etiquetas pegadas al borde de una zona.
- **Punto de clic:** centro de la etiqueta + (0, +65) px, fijo. La distancia etiqueta→piedra medida va de 57 a 80 px y la piedra mide 60–80 px de alto, así que el clic cae dentro. Si en el juego falla a otras distancias, se puede refinar buscando el anillo rojo.
- **A distancia el juego no dibuja la etiqueta del metin**: en `metin_muy_lejos` y `explorando_zona` la piedra se ve, pero sin texto. La exploración de la Fase 6 tiene que acercarse caminando; más adelante se podría detectar el brillo rojo a lo lejos.
- Captcha: muestra 4 dígitos (en `captcha.png`, "6 1 1 6") que hay que replicar clickeando los botones del teclado del panel en el mismo orden. Cada captcha trae dígitos distintos, así que el lector tiene que reconocer 0-9 aunque la sample solo muestre 6 y 1: la idea es sacar las plantillas de los propios botones del teclado (ahí están los 10 dígitos) o usar OCR. Se define en la fase 4.

## Fase 2 — qué quedó
- **Foco de ventana** (`bot/capture.py`):
  - `activate(timeout=1.5)` restaura la ventana y, si con eso no quedó al frente, prueba el intento A (ALT + `SetForegroundWindow`) y después el B (`AttachThreadInput` + `BringWindowToTop`). Después de cada intento espera, porque la activación es asíncrona.
  - `wait_until_foreground(timeout, poll, beep_every, should_stop)`: si no logra traer el juego, avisa, hace beep y espera a que Enzo lo ponga al frente. Se puede cancelar.
  - `elevation_mismatch(hwnd)` detecta el caso en que el juego corre como administrador y Python no.
  - `WindowNotFoundError` se propaga y los scripts la muestran con un mensaje claro.
- **Zonas excluidas** (`bot/vision/screen_mask.py`): `build_screen_mask(shape, cfg, extra)` devuelve un `ScreenMask` con `allows(x, y)`, `allows_rect` y `overlap_fraction`.
  - Zonas estáticas en `vision.exclusion_zones`, cada una con ancla + dx/dy + w/h: chat, exp_log, bottom_bar, buffs, quests, top_right (minimapa y coordenadas) y top_center.
  - `find_ui_windows(img, cfg)` detecta el inventario y el Mapa grande en cualquier posición. Busca dos anclas por ventana (barra de título y esquina inferior del marco), primero en gris a escala 0.25 y después refina a resolución completa. Tarda ~16 ms. Plantillas en `templates/ui/`.
- **Metin** (`bot/vision/metin.py`): `find_metins(img, cfg, mask)` devuelve todas las etiquetas naranjas, cada una con `name`, `score`, `is_target`, `click_point` y `allowed`. También `choose_target(dets, player_xy)` y `player_point(shape, cfg)`. Plantillas de etiquetas en `templates/labels/`: Oscuridad, Avaricia y Negro.
- **Herramientas**:
  - `detect_preview.py`: captura el juego (o `--image`) y guarda una imagen anotada, sin mandar input.
  - `tools/make_label_template.py`: genera la plantilla de una etiqueta nueva, por ejemplo si cambia el objetivo.
  - `tools/make_ui_template.py`.
- **Rendimiento**: el ciclo completo (ventanas + máscara + metin) tarda ~25 ms en 1858×1057. Antes de las correcciones tardaba ~170 ms.
- **Tests**: 103 pasan.
  - Positivos con el clic dentro de la piedra en 5 samples.
  - Con `target_metin_names` = ["Metin de Oscuridad", "Metin Negro"] (requisito cambiado el 2026-11-18), los dos son objetivo y Avaricia queda ignorado. `choose_target` elige el más cercano de cualquier tipo. `is_target_name(name, cfg)` compara por slug y la Fase 3 la usa para el panel.
- **Zona de farmeo** (`bot/zone.py`, lógica pura): `FarmZone.from_config(cfg)` con `signed_distance` (`cv2.pointPolygonTest`, positiva adentro), `status` (INSIDE / TOLERANCE / OUTSIDE), `accepts`, `centroid` (de área: ≈ (598.6, 155.8)), `direction_to_center` y `random_point_inside(rng, margin)`. Se valida el polígono: mínimo 3 vértices, sin cruces, área > 0 y tolerancia ≥ 0. Se usa en la Fase 6.
- Tests: 123 pasan.
  - Sin falsos positivos en objetos, yang, muerte y captcha.
  - Una etiqueta tapada un 30% no cuenta como objetivo.
  - Ventanas con el título tapado o fuera de la imagen se detectan igual.

## Correcciones después de las pruebas de Enzo (2026-11-18)
- **A3, inventario:** la causa era que el inventario estaba en modo **compacto**, sin el panel de estadísticas "Defensivo/Ofensivo": mide 165 px de ancho contra 295 del modo completo, y la plantilla de la barra de título no calzaba. No era la página III; el título es igual en todas.
  - `find_ui_windows` ahora acepta variantes `alt` por ventana (se prueban solo si la principal no calza), con `templates/ui/inventario_compacto.png`.
  - `coarse_min_score` bajó de 0.6 a 0.5. Es solo el prefiltro; el umbral fino sigue en 0.8.
  - **`vision.ui_windows.safety_margin` = 30 px**: las ventanas se agrandan 30 px en la máscara, así ningún clic queda pegado a un borde. En el caso real, el clic quedaba a 23 px.
  - Límite: el modo compacto no tiene segundo ancla (la base queda bajo la barra de atajos). Las páginas I y IV y otros modos no están validados.
- **B, foco:** `GameWindow.is_unobstructed()` verifica con `WindowFromPoint` en 9 puntos del cliente que nada tape el juego. `GameWindow.ensure_ready(timeout, should_stop)` es el punto único antes de capturar o mandar input:
  - Si ya estaba listo, vuelve enseguida.
  - Si no: trae el juego al frente, espera a que nada lo tape, espera `window.settle_ms` (500 ms) y confirma de nuevo.
  - Si entre ciclos se perdió el foco, la próxima llamada vuelve a hacer la espera completa.
  - `capture_preview.py` y `detect_preview.py` lo usan. Config: `window.settle_ms` y `window.unobstructed_poll_ms`.
- Tests: 389 pasan. Incluyen `real_inventario_abierto.png`: inventario compacto y mapa detectados, Metin Negro con `allowed=False`, coordenadas (453, 169) verificadas a ojo y sin panel.

## Fase 6b — plan (aprobado por Enzo)
1. **Visión nueva** (implementer):
   - `bot/vision/far_metin.py`, `find_far_metins(img, cfg, mask) -> list[FarMetin(rect, center, score)]`: la piedra de lejos, sin etiqueta, por el brillo rojo de la base y el humo. No sabe el tipo: eso se confirma al acercarse.
   - `bot/vision/monsters.py`, `find_monsters(img, cfg, mask)` (nombres rojos con "Lv" verde) y `monsters_near(monsters, player_xy, radio)`.
   - Se miden sobre `metin_muy_lejos`, `explorando_zona` y la sample de combate (monstruos alrededor).
2. **Comportamiento** (implementer; en paralelo con el paso 3 cuando estén las interfaces del 1):
   - **ACERCARSE** (estado nuevo): sin etiquetas visibles pero con piedras lejanas, caminar hacia la más cercana cuya posición estimada quede dentro de la zona o la tolerancia, hasta que aparezca el nombre. Objetivo → ATACAR; otro tipo → se recuerda su posición para ignorarla. Con timeout.
   - **Destrabe por episodios:** el episodio de traba (con su contador y la estrategia que toca) **persiste entre VOLVER/EXPLORAR/ATACAR** hasta que haya progreso real: alejarse ≥ N u del punto de traba o acercarse al objetivo, no 2 u. Las estrategias rotan sin repetir:
     - (A) Si hay monstruos cerca: barra espaciadora ~2 s + W ~1 s.
     - (C) Sin monstruos: girar la cámara para quedar mirando hacia el interior del polígono (con la cámara estimada) o hacia un metin, y avanzar con W.
     - Clic hacia atrás o hacia un costado.
     - (D) Agotadas, o varias trabas seguidas en poco tiempo: ir al **centro** del polígono.
   - **ATACAR:**
     - (B) Confirmado pero sin bajar la vida y sin moverse (o girando en círculo): barra espaciadora ~2 s y re-clic en el metin.
     - Selección equivocada de un monstruo pegado: barra espaciadora ~2 s, Escape y re-clic en el metin (cuenta para el descarte).
   - **EXPLORAR (E):** ciclo de tramo recto hacia una dirección interior (lejos de los bordes, `explore.border_margin`), parar, barrido de cámara de 360° mirando etiquetas y piedras lejanas, y otra dirección. Sin metin en `explore.max_without_metin_s` → ir al centro.
   - **Zona:** los destinos de exploración y de vuelta quedan bien adentro (margen mayor). VOLVER apunta al centro.
   - **Diagnóstico:**
     - Log del motivo de cada metin detectado y no elegido: tipo ignorado, fuera de zona (con la posición estimada y la distancia), clic no permitido, descartado hasta cuándo, sin cámara. Se loguea solo cuando cambia, para no llenar el log.
     - Log del estado de la cámara estimada en cada tramo.
   - **Modo debug** (`debug.enabled`): una captura anotada en `debug/run_<fecha>/` en cada cambio de estado, cada descarte y cada intento de destrabe. Muestra detecciones, piedras lejanas, monstruos, zona y coordenadas, cámara, destino y clic planeado. Con un tope de imágenes por corrida. El dibujo sale de `detect_preview.py` a un módulo común.
   - **Simulador:** monstruos que rodean y bloquean, piedras lejanas sin etiqueta, bordes y paredes. Tests de cada regla (A–E) y del destrabe persistente.
3. **Calibración de la visión nueva** (vision-tester): positivos y negativos en las samples (y en capturas nuevas de piedras lejanas, si Enzo las manda).
4. **Revisión** (reviewer) → correcciones → `debug.enabled: true` en `config.json` para la próxima prueba → "a probar por Enzo".

## Fase 6 — qué quedó
- **`bot/perception.py`:** `perceive(img, cfg, full_captcha, parts)`, un objeto `Perception` por ciclo. Tarda ~49 ms con todas las partes y ~27 ms solo con coordenadas, captcha y muerte.
- **`bot/tracking.py`:** `CoordTracker`. Descarta saltos imposibles (`max_speed`, con el `dt` acotado por `max_dt_s`) y detecta trabas (`is_stuck`).
- **`bot/navigation.py`:**
  - `CameraModel`: `game = R(φ)·A0·screen`. A0 se aprende con 2 tramos no colineales y se conserva; φ se re-estima con 1 tramo.
  - Detecta un **giro externo** de la cámara (más de `external_rotation_deg`) y lo re-estima.
  - `Navigator`: `plan_click` nunca da un punto prohibido por la máscara ni uno cuyo destino previsto salga de la tolerancia. Tramos de como mucho `max_walk_px` (120 px), `exploration_target` y `return_target`.
- **`bot/bot.py`:** `Bot.run(max_minutes)` / `step()`, con los estados BUSCAR, ATACAR, RECOGER, EXPLORAR, VOLVER, DESTRABAR, CAPTCHA, MUERTO y FIN. Todo el input va por `GameInput`, con la máscara del ciclo.
  - **Durante cada tramo de caminata:** si el rumbo real difiere del esperado más de 30° en 2 ciclos, frena y re-estima. Si se acerca al límite de la tolerancia, corta y vuelve.
  - **Selección equivocada:** se cuenta, y a la 3ª el metin se descarta.
  - **VOLVER:** el tiempo acumula entre reentradas. Con 3 destrabes sin progreso, prueba otro punto de vuelta (hasta 3). Si pasa `volver.max_seconds` (120), termina con "no puede volver a la zona" y alarma.
  - **Captcha:** suelta todo, alarma, espera sin mandar input y sigue cuando el panel desaparece 2 lecturas. **Muerte:** alarma y FIN.
- **`run_bot.py`:**
  - Opciones: `--observar` (`PassiveWindow`: no activa la ventana ni manda input; espera a que Enzo ponga el juego al frente), `--max-minutos` (no corta durante un captcha) y `--config`.
  - Al arrancar: `verify_templates` y `startup_check`. Al terminar, un resumen.
  - Con muerte o "no puede volver", la alarma suena hasta F12 o `alarm.max_seconds`.
- **Simulador** (`tests/sim_game.py`, `tests/sim_bot_env.py`): un juego falso con cámara oculta, obstáculos, metin, objetos y eventos programados.
- **Tests:** 771 pasan. Incluyen 12 escenarios de punta a punta, 20 mundos aleatorios y 28 tests de robustez (giros externos de 60/90/150/−120°, pared, selección equivocada repetida, `--observar` sin input). La suite tarda ~48 s.
- **Resultados en el simulador:**
  - Con giro externo, peor alejamiento: en EXPLORAR, 20 u (antes 83); en VOLVER, arrancando afuera, 32,5 u (antes 91,6), un poco más que la tolerancia de 30, por el primer sondeo sin cámara conocida.
  - Clics de caminata fuera de la tolerancia: 0 de 1430 (antes 179 de 1425).
  - La navegación promedia 6 clics para llegar (máximo 15), por los tramos más cortos.
- **Límites:**
  - El simulador es ideal (sin errores de visión, caminata recta, obstáculos fijos).
  - Frente a una pared, el bot no garantiza rodearla: vuelve por otro lado o termina con alarma.
  - Si la cámara gira por fuera (por ejemplo, Enzo con el mouse), el bot lo detecta y corrige, pero conviene no tocarla mientras corre.

### Valores a calibrar en la primera prueba (son estimaciones sin medir)
- `explore.camera_steps` × `camera_step_s` (6 × 0.5 s): cuánto gira la cámara de verdad.
- `coords_filter.max_speed` (30 u/s): velocidad real a caballo.
- `pickup.empty_confirm_cycles` (5) y `pickup.max_seconds` (20): cuánto tardan en aparecer los objetos. `press_interval_ms` (350).
- `attack.confirm_timeout_s` (6), `hp_stall_s` (8), `max_seconds` (120), `discard_radius` (8).
- `stuck.window_s` (3), `min_move` (2), `unstuck_radius_px` (200).
- `navigation.settle_s` (1.0), `segment_timeout_s` (8), `scale_hint` ([0.15, 0.4]), `avoid_metin_px` (70), `max_walk_px` (120).
- `volver.max_seconds` (120).
- Los saca Claude de `logs/bot.log` después de la prueba.

## Fase 6 — plan (aprobado por Enzo, ejecutado)
**El problema central:** el bot clickea en la PANTALLA, pero la zona de farmeo está en coordenadas del JUEGO, y la cámara gira (Q/E). Para ir hacia un punto del juego, por ejemplo el centro del polígono, hay que saber hacia dónde apunta la cámara. Solución: **navegación con realimentación**. Cada vez que el personaje camina por un clic, se compara la dirección del clic en pantalla con cuánto cambiaron las coordenadas. Así se estima el ángulo de la cámara (y la escala píxeles↔unidades), y se corrige en cada tramo. Si la cámara gira, se vuelve a estimar con un tramo corto.

1. **Percepción y navegación** (implementer; lógica pura, sin input):
   - `bot/perception.py`, `perceive(img, cfg, full_captcha)`: un solo objeto por ciclo con ventanas, máscara, metin, panel, coordenadas, objetos, captcha y muerte. Se prueba contra las samples.
   - `CoordTracker`: descarta saltos imposibles entre ciclos (mala lectura), guarda la última coordenada buena y detecta "no me muevo" para el destrabe.
   - `bot/navigation.py`: estimador del ángulo y la escala de la cámara a partir de los tramos caminados. `step_toward(destino del juego)` → punto de pantalla permitido donde clickear. Puntos aleatorios de exploración dentro del polígono (`FarmZone.random_point_inside`).
   - Un **simulador** simple para los tests: un juego falso donde clickear mueve al personaje según un ángulo y una escala ocultos, con monstruos que traban.
2. **Máquina de estados** (implementer): `bot/bot.py` y `run_bot.py`.
   - **BUSCAR**: elegir el metin objetivo más cercano con clic permitido. Antes, filtrar por zona: solo si el personaje está adentro o en la franja, y si la distancia estimada del metin (píxeles × escala) cabe en lo que le queda hasta el borde más la tolerancia.
   - **ATACAR**:
     - Clic y confirmar en el panel en T segundos. Si se seleccionó otra cosa: Escape.
     - Si la vida no baja en X segundos: otro clic. Si sigue igual: descartarlo por N minutos, recordado por la coordenada del personaje.
     - Si mientras va hacia el metin el personaje sale de la tolerancia: abandonar y volver.
   - **Roto**: el panel desaparece, o la vida queda en 0 dos ciclos seguidos (no uno solo, por si algo tapa la barra). Pasa a RECOGER.
   - **RECOGER**: Z repetido (recoge uno por vez) mientras queden objetos válidos. Si quedan y se pueden clickear: clic en la etiqueta y Z. Opción de bajarse del caballo para recoger (`pickup.dismount`, apagada). Máximo de intentos y de tiempo.
   - **EXPLORAR**: girar la cámara con Q/E en pasos, mirando después de cada uno. Si no aparece nada, caminar hacia un punto aleatorio dentro del polígono.
   - **VOLVER**: fuera de la tolerancia, volver enseguida hacia el centro. En la franja de tolerancia, volver al terminar el metin actual.
   - **DESTRABAR**: si las coordenadas no cambian en N segundos mientras intenta moverse (también yendo a un metin), caminar hacia otro lado, girar y reintentar, con un máximo de intentos. Después, cambiar de destino.
   - **En cada ciclo:**
     - Captcha (zona fija; búsqueda completa cada N ciclos): soltar todo, alarma y esperar a que el panel desaparezca (2 lecturas seguidas), parar la alarma y seguir. El bot no lo resuelve.
     - Muerte: alarma y detener el bot.
     - Juego cerrado: mensaje claro y detenerse.
   - **`run_bot.py`**:
     - Al arrancar: verifica plantillas y config, `startup_check`, cuenta regresiva de 5 s y pánico activo.
     - Durante el loop: log de cada cambio de estado.
     - Al terminar: resumen (metin rotos, objetos recogidos, tiempo).
     - Opciones: `--observar` (percibe y decide, pero no manda input; muestra qué haría, para la primera prueba) y `--max-minutos N`.
   - Config nueva: `loop`, `attack`, `pickup`, `explore`, `stuck`, `navigation` (todos con tiempos máximos e intentos máximos, para que nada sea infinito salvo la espera del captcha y la de foco, que corta F12).
3. **Tests:** transiciones con percepción guionada (metin → atacar → roto → recoger → explorar; selección equivocada; vida trabada; captcha; muerte; pánico) y navegación con el simulador (llega al destino con cualquier ángulo de cámara, no sale de la zona, se destraba). Nunca input real.
4. **Revisión** (reviewer): requisitos del loop, ningún loop infinito, seguridad. Después, correcciones y "a probar por Enzo": primero `--observar`, después unos minutos de verdad y supervisado, con `--max-minutos`.

## Fase 5 — qué quedó
- **Control** (`bot/control.py`):
  - `BotControl(cfg)`: `start()` / `close()`, pánico con F12 o con el mouse en la esquina superior izquierda (un hilo vigía cada 50 ms), pausa con F11.
  - `check()` lanza `PanicStop`, o espera mientras está en pausa. `sleep()` se despierta con el pánico, y el tiempo en pausa no cuenta. `countdown()` es cancelable.
  - `add_stop_callback` y `add_pause_callback`.
  - Corte forzado: el temporizador se arma antes de los callbacks. A los `hard_kill_after_s` (2 s) intenta soltar todo y corta el proceso. `mark_finished()` y `close()` lo cancelan.
- **Backends** (`bot/input/`):
  - `InputBackend`: las teclas y botones apretados se anotan, y anotar y mandar es atómico bajo un RLock. Así `release_all()`, llamado desde el hilo del pánico, no puede dejar nada apretado; `release_all()` nunca lanza.
  - `PyDirectInputBackend`: `PAUSE=0`, `FAILSAFE=False`, normaliza los nombres de teclas y valida todas al crearse. La rueda va por `SendInput`, porque pydirectinput 1.0.4 no la tiene.
  - `FakeBackend` y `FakeWindow` para los tests y para `--simular`. `parse_keys`, `VALID_KEYS`.
- **Acciones** (`bot/input/game_input.py`): `GameInput(window, backend, control, cfg)` es el único camino de input al juego.
  - Antes de cada acción: `check()`, `ensure_ready()` y `check()` otra vez.
  - `click_at(x, y, mask)` y `zoom(clicks, mask)` exigen máscara y rechazan:
    - un punto no permitido;
    - un punto fuera del cliente;
    - una ventana que se movió o cambió de tamaño desde la última captura (`last_capture_rect`).
  - Variación aleatoria de posición y de tiempos.
  - `press(acción)`, `hold(acción, s)` (en tramos de 50 ms: suelta la tecla en pausa o si se pierde el foco, y la vuelve a apretar por el tiempo que falta), `rotate_camera` y `move_key`.
  - `startup_check()`: si el juego corre como administrador y Python no → `InputBlockedError`.
- **`ensure_ready` / `activate`** cortan en ≤ 50 ms con el pánico. No se manda ALT si ya hay parada.
- **Config**: `controls` (todas las teclas), `safety` (F12, F11, la esquina, la cuenta regresiva, `hard_kill_after_s` > 0) e `input` (jitter y tiempos). La validación rechaza:
  - combinaciones mal escritas y teclas desconocidas;
  - teclas de `controls` iguales a F11 o F12;
  - acciones faltantes.
- **`test_input.py`**: 7 pasos anunciados (cámara, zoom, Z, Ctrl+G ×2, W, clic en el piso, Escape), con `--solo teclas|mouse|camara` y `--simular`.
- **Captcha**: `find_captcha(img, cfg, full=False)` busca en la zona fija (~2.5 ms). Con `full=True` busca en toda la imagen (~18.6 ms).
- Tests: 597 pasan, todo simulado; nunca se mandó input real. Cubren:
  - El pánico en cada estado (sleep, hold, pausa, cuenta regresiva, entre `move_to` y `click`, dentro de `ensure_ready`).
  - La pausa durante un hold y la pérdida de foco.
  - Un callback colgado con el corte forzado, y la carrera de `release_all`.
  - La validación de teclas.
- Límite: la latencia real de F12 con el juego (hook de `keyboard`) y el signo de la rueda solo se verifican en el juego.

## Fase 5 — plan (aprobado por Enzo, ejecutado)
0. **Captcha en zona fija** (implementer, chico; en paralelo con el paso 1): `vision.captcha.search_region` (relativa al borde derecho, holgada alrededor de donde aparece siempre) y `find_captcha(img, cfg, full=False)`. Por defecto busca solo en la zona fija (~2 ms en vez de ~13). Con `full=True` busca en toda la imagen: el loop de la Fase 6 lo va a usar cada N ciclos como respaldo.
1. **Control y backends de input** (implementer):
   - `bot/control.py`, `BotControl`:
     - Pánico con F12 y con el mouse en la esquina superior izquierda de la pantalla. Un hilo vigía revisa cada ~50 ms y marca un evento de parada.
     - Pausa con F11 (alterna).
     - `check()` lanza `PanicStop` si se paró. `sleep(s)` se despierta al instante con el pánico y espera mientras está en pausa.
     - `countdown(5)` cancelable.
     - Al parar se sueltan todas las teclas y botones. Si el loop no termina en ~2 s después del pánico, se corta el proceso.
   - `bot/input/base.py`: interfaz `InputBackend` (mover, clic izquierdo/derecho, tecla, mantener/soltar, combinación tipo Ctrl+G, rueda, soltar todo).
   - `bot/input/pydirect.py`: la implementación con pydirectinput (scan codes), sin su pausa automática ni su failsafe propio, porque el pánico es nuestro.
   - `bot/input/fake.py`: backend falso que registra las llamadas, para los tests.
   - Config: `controls` (todas las teclas del juego, configurables), `safety` (F12, F11, píxeles de la esquina, cuenta regresiva, corte forzado) e `input` (backend, variaciones aleatorias).
2. **Acciones del juego** (implementer): `bot/input/game_input.py`, `GameInput(window, backend, control, cfg)`. Es el único lugar que manda input al juego, en coordenadas de cliente. Antes de **cada** acción:
   - `control.check()` y `window.ensure_ready()`.
   - Traducción cliente→pantalla estricta: nunca fuera del juego.
   - Rechazar el clic si `ScreenMask.allows` da False.
   - Variación aleatoria de posición (radio configurable, sin salir de lo permitido) y de tiempos.
   - Acciones: `click_at`, `press(acción)` (atacar, recoger, montar, buff, deseleccionar…), `rotate_camera(dir, pasos)`, `zoom`, `move_key`.
   - Al arrancar, si el juego corre como administrador y Python no, error claro: Windows bloquearía las teclas y los clics.
   - `test_input.py`: con cuenta regresiva y el pánico activo, manda pasos de prueba anunciados por consola, para que Enzo verifique en el juego que llegan: girar la cámara con Q/E, zoom, Z, Ctrl+G dos veces, un clic en el piso cerca del personaje, Escape. Tiene `--solo teclas|mouse|camara` y `--simular` (backend falso, imprime sin mandar nada).
3. **Tests** (los escribe el implementer, sin visión): traducción de coordenadas; clic rechazado fuera de lo permitido, con pánico o en pausa; variación dentro del radio; teclas sueltas al parar; cuenta regresiva cancelada; mapeo de teclas de la config (incluido "ctrl+g"); `test_input.py --simular`. **Nunca se manda input real en los tests.**
4. **Revisión** (reviewer), con foco en seguridad: el pánico detiene al instante desde cualquier estado, no hay input sin `ensure_ready` y nada puede quedar trabado apretado. Después, correcciones y "a probar por Enzo": `test_input.py` en el juego, F12, F11 y la esquina.

## Fase 4 — qué quedó
- **Objetos** (`bot/vision/items.py`):
  - `find_items(img, cfg, mask) -> list[ItemLabel]`, con `rect`, `kind` (`item` / `yang`), `reserved` (de Enzo), `reserved_other`, `owner_score`, `click_point`, `allowed` y `score`.
  - `choose_item(items, player_xy)`: el más cercano entre los `item` permitidos que no son de otro jugador.
  - Recuadro: borde negro exacto de 1 px, alto de 1 o 2 líneas (15 o 31 px), texto blanco exacto.
  - Dueño: amarillo (0,255,255) que calza con `templates/items/owner.png` ("Farfarfa's", `owner_min_score` 0.95; lo propio da 1.0 y otros nombres ≤ 0.26). Cualquier amarillo por encima de `yellow_noise_px` (3) que no calce cuenta como **de otro jugador** y no se recoge.
  - Yang: plantilla `templates/items/yang.png` (1.00 en yang, ≤ 0.73 en objetos; umbral 0.85). "20 Yang" sigue siendo yang.
  - `allowed` exige el recuadro entero fuera de zonas y ventanas (con su margen). Un objeto pegado a la interfaz no se clickea, pero en la Fase 6 se puede recoger con Z.
  - `max_edges` = 1500 como tope de seguridad. Un caso patológico de rayas negras pasó de ~208 s a ~4 ms.
- **Captcha** (`bot/vision/captcha.py`): `find_captcha(img, cfg) -> CaptchaInfo(rect, score) | None`, en cualquier posición, con dos anclas: el pie (OK + "Bot Verify") y el bloque del teclado. **Solo detección**: no lee dígitos ni devuelve posiciones de botones. Tarda ~13 ms.
- **Muerte** (`bot/vision/death.py`): `find_death(img, cfg) -> DeathInfo(here_rect, city_rect, score) | None`, en una región arriba a la izquierda. Basta con uno de los dos botones. Tarda ~2 ms.
- **Alarma** (`bot/alarm.py`): `Alarm(cfg)` con `start(reason)` (no bloquea, en un hilo daemon, idempotente), `stop()`, `is_active` y `beep_once()`. Cada corrida usa su propio evento, así un `stop` y un `start` cruzados no dejan dos alarmas sonando. Config `alarm`: `freq`, `beep_ms`, `pause_ms`, `max_seconds`.
- `screen_mask.coarse_to_fine_match(...)`: helper público de búsqueda gruesa→fina, que comparten las ventanas, el captcha y la muerte.
- `detect_preview.py` dibuja e imprime objetos (verde = se recoge, gris = yang, naranja = de otro), captcha, muerte y el tiempo del ciclo.
- **Tiempo del ciclo completo de detección** (ventanas, máscara, metin, panel, coordenadas, objetos, captcha y muerte): ~65–75 ms con las plantillas cargadas.
- Tests: 499 pasan. Objetos verificados a mano en las 14 samples, más:
  - Casos sintéticos: yang con número, reservado para otro, recuadros apilados, lados tapados, desplazamientos y un objeto debajo del inventario.
  - Captcha en 5 posiciones, con cada ancla tapada y parcialmente fuera de la imagen.
  - Muerte con un botón tapado.
  - La carrera de la alarma.
- Límites: un recuadro con un lado entero tapado se pierde (falla segura). El caso de objetos de otro jugador es solo sintético. Del captcha hay una sola sample real.

## Fase 4 — plan (aprobado por Enzo, ejecutado)
1. **Objetos vs yang** (implementer): `bot/vision/items.py`.
   - `find_items(img, cfg, mask) -> list[ItemLabel]` con `rect`, `kind` (`item` / `yang`), `reserved` (nombre amarillo "Farfarfa's" encima), `click_point` y `allowed`.
   - Detección: recuadro oscuro con texto blanco adentro. "Yang" se distingue con plantilla de la palabra entera. Se mide sobre `items_en_piso` y `yang_en_piso`.
   - `choose_item(items, player_xy)`: el objeto no yang permitido más cercano.
   - Las zonas excluidas y las ventanas también aplican: el texto blanco del chat, del inventario y de los paneles no cuenta.
2. **Captcha, muerte y alarma** (implementer):
   - `bot/vision/captcha.py`: `find_captcha(img, cfg) -> Rect | None` detecta el panel "Bot Verify" en cualquier posición, con dos anclas y búsqueda gruesa→fina, como las ventanas. **Solo detección**: no lee los dígitos ni ubica botones.
   - `bot/vision/death.py`: `find_death(img, cfg) -> DeathInfo | None` con los rectángulos de "Volver a Empezar Aquí" y "…en la Ciudad" (el primero queda para la Fase 8).
   - `bot/alarm.py`: alarma sonora no bloqueante (en un hilo), con `start()` / `stop()` y un patrón configurable.
   - `detect_preview.py` imprime y dibuja objetos, captcha y muerte.
3. **Tests y calibración** (vision-tester):
   - Objetos: cantidad, públicos vs reservados y yang excluido en las samples de objetos, con la verdad de referencia sacada de recortes.
   - Sin falsos objetos en el resto, ni en chat, inventario, paneles o nombres de monstruos.
   - Captcha solo en `captcha.png`, más casos sintéticos movidos de lugar o tapados en parte. Muerte solo en `muerte.png`.
4. **Revisión** (reviewer) → correcciones → actualizar este archivo con "a probar por Enzo".
- El comportamiento va en la Fase 6: con captcha, pausar el loop y sonar la alarma hasta que el panel desaparezca; con muerte, detenerse y sonar la alarma.

## Fase 3 — qué quedó
- `bot/vision/digits.py`:
  - `GlyphSet.load` (descarta plantillas vacías), `classify_ranked`, `accept(crop, min_score, min_margin)` y `get_glyphs`.
  - `read_text_line(crop, glyphs, section) -> TextRead(text, min_score, boxes, scores, crops)`.
  - Un carácter dudoso o un hueco anormal se leen como '?'.
- `bot/vision/panel.py`: `read_panel(img, cfg) -> PanelInfo`, con:
  - `present`, `rect`, `kind` (`target_metin` / `other_metin` / `other` / None) y `name` / `name_score`.
  - `hp_ratio` (barra), `hp_current` / `hp_max` / `hp_text_score`, y `bar_rect`, `number_rect`, `name_rect` para debug.
  - También `reset_warnings()`.
- `bot/vision/coords.py`: `read_coords(img, cfg) -> Coords(x, y, score, rect) | None`, con regex estricta y rango validado.
- Herramientas: `tools/make_glyph_templates.py` y `tools/make_panel_name_template.py`.
- `detect_preview.py` ahora también imprime y dibuja el panel y las coordenadas.
- Umbrales de glifos: `min_score` 0.9 y `min_margin` 0.15. En las samples el correcto da 1.0 y el segundo como máximo 0.80. Las plantillas 8/9 y 8/6 se parecen en 0.80, así que un 8 al que le faltan 1–2 píxeles se rechaza por margen. `max_gap` 3 y `max_gap_after_comma` 7.
- La zona de farmeo también se reforzó: `interior_center` (el punto interior más alejado del borde) para polígonos no convexos, mensajes de validación precisos y `validate_farm_zone` pública.
- Las imágenes vacías o muy chicas no cortan el ciclo en ninguna función de visión.
- Tiempos: `read_coords` ~1 ms y `read_panel` ~3 ms. El ciclo completo (ventanas + máscara + metin + panel + coordenadas) tarda ~27 ms.
- Tests: 372 pasan. Incluyen:
  - Lecturas en las 13 samples, verificadas a ojo con recortes.
  - Nombre desconocido, número ilegible, Metin Negro con vida parcial sintética y número incoherente con la barra.
  - Números y coordenadas que no aparecen en ninguna sample, y los 100 pares de dígitos pegados.
  - Dígitos tapados, desplazamientos de 0–12 px e imágenes degeneradas.
- Límites conocidos:
  - Las plantillas salen de las mismas samples, así que la prueba real es en el juego.
  - Un "8" al que le faltan justo los píxeles que lo distinguen de un "9" es indistinguible de un 9.
  - Un carácter separado por "pelado" recibe score 1.0 por construcción: solo se pelan componentes más anchas que cualquier glifo.

## Captcha (Fase 4)
- El 2026-11-18 Enzo pidió que el bot lo resuelva solo, como dice ESPECIFICACION.md. **Claude no implementa la resolución automática del "Bot Verify"**: ese panel es el control del servidor para comprobar que hay una persona presente, y resolverlo solo es saltear ese control.
- Lo que sí se implementa en la Fase 4: detectar el panel en cualquier posición en cada ciclo, pausar el loop, hacer sonar la alarma hasta que Enzo lo resuelva y retomar solo cuando el panel desaparece. No hay campo `solve_captcha`.

## Pendientes para fases siguientes
- Fase 5: el input no puede clickear donde `ScreenMask.allows(x, y)` dé False (zonas de interfaz, inventario, Mapa grande). Construir la máscara con `extra=find_ui_windows(img, cfg)`.
- Fase 6: todo el input va por `GameInput` (que ya hace `check` + `ensure_ready`). En cada ciclo: `window.capture()` (actualiza `last_capture_rect`), armar la máscara de ESA captura y pasársela a `click_at` / `zoom`. Construir `BotControl`, `PyDirectInputBackend` y `GameInput` una sola vez, con `control.add_stop_callback(backend.release_all)`, `startup_check()`, `countdown()`, y `mark_finished()` + `close()` en `finally`.
- Fases 5 y 6: antes de CADA captura y de CADA clic o tecla, llamar a `GameWindow.ensure_ready(should_stop=<pánico>)`. Si el juego no está al frente o algo lo tapa, espera a que vuelva y se estabilice; si da False, no se manda nada. `GameWindow` se construye con `cfg["window"]`. Si la ventana se cierra, sale `WindowNotFoundError`: hay que manejarla.
- Fase 6: un metin lejano no tiene etiqueta (ver "Decisiones"), así que hay que explorar acercándose.
- Fase 6: verificar la plausibilidad temporal de las coordenadas. Entre ciclos el personaje no puede saltar más de N unidades: un salto grande se descarta como mala lectura y se usa la anterior. Lo mismo para la vida: no puede subir de golpe en un metin.
- Fase 6: `find_captcha(img, cfg)` busca por defecto solo en la zona fija `vision.captcha.search_region` (~2.5 ms). Cada N ciclos hay que llamarlo con `full=True` como respaldo (~18.6 ms). Falta agregar el campo de config para N, por ejemplo `loop.captcha_full_search_every`.
- Fase 6: en cada ciclo, `find_captcha` → pausar el loop y `Alarm.start("captcha")` hasta que el panel desaparezca, y después `stop()` y seguir. `find_death` → detenerse y `Alarm.start("muerte")` (en la Fase 8, opcional, auto-revivir con `here_rect`).
- Fase 6, RECOGER: como `allowed` es conservador, si quedan objetos válidos pero ninguno permitido para clickear, usar Z.
- Fase 6: si algo tapa la barra de vida (un anuncio, por ejemplo), se puede leer como vacía (`hp_ratio` 0.0). Para dar un metin por roto, la señal principal es que el panel desaparezca. Una barra en 0 tiene que confirmarse en 2 o más ciclos seguidos antes de pasar a RECOGER.
- Fase 6: `load_label_templates` lanza `FileNotFoundError` si falta la plantilla de un objetivo. Hay que capturarla al arrancar el bot, con un mensaje claro, no en medio del loop.
- Menores de la revisión, aceptados por ahora:
  - Dos etiquetas a menos de ~8 px se unen y se descartan las dos (`group_kernel`). Es poco probable.
  - El rectángulo del inventario mide 575 de alto y el marco real ~554: es conservador.
  - Las zonas `buffs` (cartel "Caballo Militar Lleno") y `top_center` (anuncios) son estimadas: no hay ninguna sample con el cartel, y la franja de anuncios no está excluida. No genera detecciones porque no es naranja.

## Capturas
- **Fase 3:** `real_metin_negro_seleccionado.png`, con un Metin Negro seleccionado (clic sobre él) y el panel superior visible, para la plantilla de su nombre en el panel. Sin esa captura, el panel no puede confirmar que se seleccionó un Metin Negro.
- Fase 2: ya está `real_metin_negro_inventario_mapa.png`. Serían útiles, pero no bloquean: un Metin de Oscuridad con el inventario abierto, el inventario y el Mapa grande en otra posición, y el cartel "Caballo Militar Lleno" y un anuncio del servidor, para medir sus zonas.
- Fase 8: inventario abierto y el objeto para llamar al caballo.
- Fase 4, solo si las plantillas del teclado no sirven para leer los dígitos grandes: 1 o 2 capturas más del captcha con otros números.

## Resultado de las pruebas de Enzo
### Fase 1 (2026-11-18) ✅
- Título de la ventana: **"Guabina"**. El hwnd cambia cada vez que se abre el juego, así que se sigue buscando por título.
- Cliente en pantalla x=8 y=31, **1858×1057, DPI 1.00**. `debug/capture_preview.png` se ve bien.
- Mouse: Pantalla=(949,873) → Cliente=(941,842). La traducción resta bien (8,31).
- ⚠️ Salió "No se pudo traer al frente". La captura salió bien porque el juego estaba visible, pero el bot necesita el juego al frente para mandar input → se corrige en el paso 0 de la Fase 2.
- Datos nuevos del juego: en el mapa también hay **"Metin Negro"** (a ignorar), y a veces están abiertos el **inventario** y el **"Mapa grande"**: el bot tiene que ignorarlos en la detección y no clickear encima.

## A probar por Enzo
### Rama `desarrollo` (después de probar `main`): Fase 9, perfiles
Se prueba desde la carpeta **`D:\proyectos\metin-bot-desarrollo`**, con el Python de la carpeta principal: abrí la terminal en `D:\proyectos\metin-bot-desarrollo` y usá `D:\proyectos\metin-bot\.venv\Scripts\python.exe` en lugar de `.venv\Scripts\python.exe`. Esta carpeta tiene su propia copia de `config.json`: lo que hagas acá no afecta a `main`.
1. `D:\proyectos\metin-bot\.venv\Scripts\python.exe calibrate.py perfiles listar`: muestra los 3 perfiles activos y los 4 avisos de `config.json` pisando valores (con el mismo valor).
2. `D:\proyectos\metin-bot\.venv\Scripts\python.exe run_bot.py --observar --max-minutos 1`: en `logs\bot.log` tiene que aparecer "Perfiles: servidor=Guabina, personaje=Farfarfa, zona=Guabina - Oscuridad y Negro" y los avisos. El bot tiene que comportarse igual que en `main`.
3. `D:\proyectos\metin-bot\.venv\Scripts\python.exe calibrate.py perfiles migrar`: revisá el plan que muestra, confirmá con `s` y verificá:
   - que `config.json` quede sin `window_title`, `farm_zone`, etc., y con el bloque `profiles`;
   - que haya copias `.bak`;
   - que `perfiles listar` ya no muestre avisos.
4. (Opcional, en el juego) `D:\proyectos\metin-bot\.venv\Scripts\python.exe calibrate.py personaje "Prueba"`: con un objeto tuyo en el piso, poné el mouse sobre el "Farfarfa's" amarillo y apretá F9. Mirá el recorte en `debug\` y cancelá con F12 (o guardalo y después borrá `profiles\personajes\Prueba.json`).

### Rama `desarrollo`: Fase 10, interfaz
En `D:\proyectos\metin-bot-desarrollo`:
1. **Abrir:** doble clic en `abrir_interfaz.bat`. Usa el venv de `..\metin-bot` porque el worktree no tiene uno propio. Si el juego corre como administrador, usá `abrir_interfaz_admin.bat`: tiene que aparecer el aviso de permisos de Windows y abrirse la ventana.
2. Mové la ventana al segundo monitor, cerrala y volvé a abrirla: tiene que aparecer en el mismo lugar y con el mismo tamaño.
3. **No se minimiza:** probá Win+D y Win+M. La ventana no tiene que desaparecer, o tiene que volver sola.
4. **Sin el juego abierto:** Iniciar → tiene que mostrar en el log el error "no se encuentra la ventana" y un aviso, sin colgarse.
5. **Con el juego abierto, en modo Observar:** Iniciar → cuenta regresiva, estados y tiempo en vivo, log. Probá Pausar/Reanudar ("PAUSADO" en rojo) y Detener.
6. **F11 y F12 desde el juego** con la interfaz abierta: F11 pausa (lo muestra la interfaz) y F12 corta. Probá también la esquina del mouse.
7. Cerrá la ventana con el bot corriendo: tiene que pedir confirmación y detenerlo.
8. Interruptor **Debug** encendido y una corrida corta: tiene que crearse la carpeta `debug\run_*`. Apagado, no se crea, aunque `config.json` tenga `debug.enabled: true`.
9. Cambiá la zona en la lista: si `config.json` todavía pisa `farm_zone`, tiene que aparecer el aviso en naranja.

### Rama `desarrollo`: Fase 11, habilidades con temporizador
En `D:\proyectos\metin-bot-desarrollo`, con la interfaz (`abrir_interfaz.bat`):
1. **Pestaña "Habilidades":**
   - "Agregar ejemplo" → el de tu clase. Editalo con la tecla, la **duración real** de la habilidad y la **duración de la animación**.
   - Probá que rechace F11, duración 0, y que avise con la tecla `z`.
   - Guardá y fijate que `profiles\personajes\Farfarfa.json` tenga la habilidad, y que haya una copia `.bak`.
2. **Con el juego, primero en modo Observar:** Iniciar → en el log tiene que aparecer "Habilidad … lanzada" enseguida (al iniciar se activan todas), y la tabla tiene que mostrar el tiempo restante.
3. **De verdad, `--max-minutos` corto, montado:** al arrancar se tiene que bajar (Ctrl+G), apretar la tecla, esperar la animación y subirse. Mirá que:
   - la habilidad se active de verdad;
   - `skill_timing.mount_wait_s` (1.5 s) alcance para bajarse y subirse; si no, subilo en `config.json`;
   - cuando vence, la vuelva a lanzar cuando está buscando, explorando o pegándole a un metin, y nunca mientras recoge;
   - después de lanzarla pegándole a un metin, vuelva a clickearlo y siga pegando.
4. **F12 en medio de un lanzamiento:** tiene que cortar sin dejar teclas apretadas.
5. **Con la lista vacía** (o todas desactivadas), el bot tiene que comportarse igual que antes.
6. Contame si `mount_wait_s` y `attack_settle_s` (3 s) necesitan ajuste.

### Rama `desarrollo`: Fase 12 (parte 1), prioridad de metin
En la interfaz, pestaña Control, sección "Prioridad de metin":
1. Encendé "Elegir por prioridad", poné "Metin Negro" arriba y guardá. Fijate que `profiles\zonas\Guabina - Oscuridad y Negro.json` quede con `metin_priority: ["Metin Negro", "Metin de Oscuridad"]` y que haya una copia `.bak`.
2. Con un Oscuridad más cerca y un Negro más lejos a la vista (primero en modo Observar): tiene que elegir el Negro, y el log tiene que decir "Elegido metin_negro por prioridad (más cerca había metin_de_oscuridad)".
3. Apagá el interruptor y guardá: tiene que volver a ir al más cercano.
4. Cambiá de zona o tocá Iniciar con cambios sin guardar: tiene que preguntar.

### Rama `desarrollo`: Fase 13, modo bichos
1. En la pestaña Control, sección "Modo de farmeo": elegí "Bichos", tecla F4 (o la del ítem que atrae monstruos) y cada cuántos segundos, y Guardar. Probá guardar con una tecla F11/F12 o segundos 0: tiene que avisar y no guardar. Cambiá de zona o tocá Iniciar con cambios sin guardar: tiene que preguntar.
2. Primero en modo Observar, después en serio, parado adentro de la zona: tras la cuenta regresiva el estado tiene que ser BICHOS (no BUSCAR), aprieta el señuelo enseguida y el log dice "Bichos: uso el señuelo (F4); proximo en N s".
3. Con monstruos cerca: ESPACIO en tramos de ~2 s. Sin monstruos ~3 s y objetos a la vista: pasa a RECOGER (se baja, Z, se sube) y vuelve a BICHOS. Cada 60 s recoge también si queda a lo sumo 1 monstruo.
4. Sin monstruos 20 s (y el señuelo todavía no vence): camina un tramo corto adentro de la zona, sin girar la cámara y sin clics.
5. Arrancando afuera, o cerca del borde: VOLVER y de vuelta a BICHOS. Captcha en medio de la pelea: suelta todo y suena la alarma. Con habilidades configuradas se lanzan entre ESPACIOS.
6. Calibrá `zone.bichos` (`min_monsters`, `space_s`, `clear_s`, `pickup_every_s`, `pickup_max_monsters`, `idle_s`) según cuántos bichos aparecen y cuánto tardan. Verificá que `monsters_near` (radio `vision.monsters.near_radius_px`) cuente bien los bichos que atrae el señuelo.
7. En el historial de la interfaz, la sesión tiene que mostrar "Modo de farmeo: Bichos | señuelos usados: N".

### Rama `desarrollo`: Fase 12 (parte 2), historial y objetos por tipo
En la interfaz (`abrir_interfaz.bat`), con el juego:
1. Corré una sesión corta con objetos en el piso. En la pestaña Control, "Objetos de esta sesión" tiene que mostrar "Objeto 1", "Objeto 2"… con la cantidad, y el log "Objeto recogido: … (total en la sesion: N)".
2. Tienen que agruparse bien: dos objetos con el mismo nombre en el mismo tipo, y nombres distintos en tipos distintos. Si un mismo objeto aparece como dos "Objeto N", o dos distintos como uno solo, avisame (se ajusta `items_catalog.match` en `config.json`).
3. Pestaña "Objetos" (con el bot detenido): miniaturas de las etiquetas. Doble clic en una y ponele el nombre real; un nombre vacío tiene que rechazarse.
4. Pestaña "Historial": la sesión aparece arriba con fecha, duración, metin, objetos y motivo. Elegila para ver el detalle (metin y objetos por tipo, con el nombre nuevo) y mirá los totales Hoy / Total.
5. Cortá una sesión con Detener (motivo "detenido desde la interfaz"), con F12 y con `--max-minutos` (motivo "tope de tiempo"): las tres tienen que quedar en el historial. Con Observar no tiene que agregar nada. Los datos están en `data\historial.jsonl` y `data\objetos\`.

### GitHub: decidir qué hacer con `samples/`
El repo está subido sin la carpeta `samples/`: 16 capturas, ~35 MB. Muestran el nombre del personaje (Farfarfa), el servidor y el chat con nombres y mensajes de otros jugadores. Los tests de visión las necesitan, así que, sin ellas, quien clone el repo no puede correr esos tests. El nombre "Farfarfa" igual aparece en `ESPECIFICACION.md`, `PROGRESS.md` y en dos plantillas (`templates/items/owner.png` y `templates/panel_names/farfarfa.png`), que el bot necesita.
- **Para subirlas**, desde `D:\proyectos\metin-bot`:
  1. Borrar la línea `samples/` (y sus dos comentarios) del final de `.gitignore`.
  2. `git add .gitignore samples`
  3. `git commit -m "Agregar capturas de samples para los tests"`
  4. `git push`
- **Para dejarlas afuera:** no hacer nada. Opcional: cambiar el comentario de `.gitignore` por "capturas privadas, no se suben".
- Si en algún momento `git push` pide iniciar sesión, se abre la ventana de GitHub en el navegador: iniciá sesión y el push sigue solo.

### Fase 7: `calibrate.py` (después de la prueba de la Fase 6b)
Desde una terminal en `D:\proyectos\metin-bot`, con el juego abierto. **`calibrate.py` no manda teclas ni clics.** Solo escucha F8, F9 y F10 para que marques momentos mientras jugás, y F12 o Ctrl+C cancelan sin guardar. Esas teclas no se le ocultan al juego: fijate que no hagan nada raro en él. Antes de guardar, todo muestra el antes → después, pregunta `s/n` y hace una copia `config.json.bak`.
1. `.venv\Scripts\python.exe calibrate.py resumen`: lista los valores que siguen siendo estimaciones y cómo medir cada uno.
2. `.venv\Scripts\python.exe calibrate.py zonas`: abrí `debug\calibrate_zonas.png` y fijate que las zonas grises cubran la interfaz (chat, minimapa, etc.) y que las coordenadas y el panel se lean bien.
3. `.venv\Scripts\python.exe calibrate.py velocidad`: pasá al juego, apretá F9 y cabalgá en línea recta unos 10 s. Propone `coords_filter.max_speed`. Confirmá con `s` si el número parece razonable.
4. (Solo si querés rehacer la zona) `.venv\Scripts\python.exe calibrate.py zona`: caminá a cada esquina y apretá F9 (F8 borra el último vértice, F10 termina). Mirá `debug\calibrate_zona.png` antes de confirmar.
5. (Solo para un tipo de metin nuevo) `.venv\Scripts\python.exe calibrate.py metin "Nombre del metin"`: con el nombre naranja visible, poné el mouse encima y apretá F9. Después, con el metin seleccionado, otro F9 para la plantilla del panel.
6. Contame si F8, F9 o F10 hicieron algo en el juego y si alguna lectura salió mal.

### Fase 6b: segunda prueba del bot (con modo debug)
Desde una terminal en `D:\proyectos\metin-bot` (como administrador si el juego corre como administrador). La mano cerca de **F12**, y no gires la cámara con el mouse mientras corre.
1. Parate adentro de la zona, mejor lejos del borde y sin nada seleccionado: `.venv\Scripts\python.exe run_bot.py --max-minutos 10`. La consola avisa que el modo debug está activo.
2. Mirá sobre todo lo que falló la vez pasada:
   - **Metin lejos, sin el nombre:** tiene que caminar hacia la piedra (estado ACERCARSE) hasta que aparezca el nombre, y ahí atacar o ignorarla si es de otro tipo.
   - **Rodeado de bichos:** primero barra espaciadora ~2 s y después W.
   - **Trabado en un borde o risco sin bichos:** gira la cámara y prueba una dirección distinta, sin repetir el círculo de clics. Si sigue trabado, va al centro.
   - **Metin seleccionado al que no llega**, o un monstruo seleccionado por error: barra espaciadora y otro clic en el metin.
   - **Exploración:** tramos rectos por adentro de la zona, parar, girar la cámara 360°, y otra dirección. Sin acercarse a los bordes.
3. **Al terminar, pasame:**
   - El resumen final y `logs\bot.log`, que ahora dice por qué no eligió cada metin: tipo, fuera de zona, descartado…
   - La carpeta **`debug\run_<fecha_hora>\`**, con capturas anotadas de cada cambio de estado, descarte y destrabe, y `events.log`. Si algo raro pasó, decime más o menos a qué hora o en qué estado, y busco la imagen.
4. Contame también:
   - Si el barrido de cámara da más o menos una vuelta completa.
   - Si la barra espaciadora en tramos (suelta y aprieta cada 0,5 s) igual empuja a los bichos.
   - Si W hace avanzar montado.

### Fase 6: el bot completo
Resultado de Enzo (2026-11-19, `--max-minutos 3`):
- Resumen del bot: 1 metin roto, 2 objetos, 4 destrabes. Tiempos: ATACAR 95 s, VOLVER 63 s, EXPLORAR 12 s.
- ✅ Atacó el primer metin (lo tenía enfrente), lo confirmó en el panel y recogió los objetos.
- ❌ Al girar la cámara se veían 2–3 metin, pero no fue hacia ninguno. Probablemente estaban lejos, sin el nombre naranja dibujado (ya se sabía: ver "Decisiones").
- ❌ Caminando hacia (595, 171), casi el centro, en 7 s estaba fuera del polígono: la dirección que estimó para la cámara estaba mal.
- ❌ Se trabó en el borde. El destrabe hacía un círculo de clics y volvía a trabarse: el log dice siempre "intento 1/4 (tipo 0)", porque el contador vive dentro de cada entrada a DESTRABAR. Además, 2 u de movimiento ya cuentan como "destrabado", así que nunca cambia de estrategia ni de punto de vuelta.
- ❌ VOLVER tardó 63 s (dos "tiempo agotado volviendo" de 20 s cada uno).
- ❌ Montado, yendo a un metin cercano, clickeó un monstruo ("se seleccionó otra cosa") y después se trabó yendo al metin.
- **Reglas del juego que dio Enzo** (requisitos para la Fase 6b):
  - A) Trabado con monstruos alrededor (nombres rojos con "Lv" cerca del personaje): **barra espaciadora ~2 s** (montado, el ataque empuja a los bichos), después **W** para avanzar, y recién ahí seguir. Es la **primera** estrategia cuando hay monstruos cerca.
  - B) Metin confirmado pero la vida no baja y las coordenadas no cambian (o giran en círculo): lo bloquean los bichos. Lo mismo que en A, y después re-clic en el metin. Si clickeó un monstruo pegado por error: barra espaciadora, en lugar de ir a pelearlo.
  - C) Trabado en un borde, risco o pared sin monstruos: girar bastante la cámara (Q/E) buscando espacio abierto o un metin, e ir hacia ahí. No repetir clics alrededor del mismo lugar.
  - D) Trabado varias veces seguidas, o sin metin después de un rato: ir al **centro** del polígono y buscar desde ahí.
  - E) Buscar = tramos en línea recta dentro del polígono (unos segundos), parar, girar la cámara para mirar alrededor y, si no hay metin, otra dirección dentro del polígono. Siempre adentro, alejándose de los bordes.
  - Reconocer la piedra de lejos (brillo rojo, humo), caminar hacia ella hasta que aparezca el nombre y recién ahí confirmar el tipo. Si está fuera de la zona, que el log lo diga.
  - Modo debug para la próxima prueba: una captura anotada en cada cambio de estado (sobre todo en EXPLORAR, DESTRABAR y cuando descarta un metin), y el log tiene que decir por qué descarta cada metin detectado.

Desde una terminal en `D:\proyectos\metin-bot` (**como administrador si el juego corre como administrador**). Tené siempre la mano cerca de **F12**: el mouse a la esquina superior izquierda también corta, y F11 pausa. **No gires la cámara con el mouse mientras corre.**
1. **Observar (no manda nada):** `.venv\Scripts\python.exe run_bot.py --observar --max-minutos 3`.
   - Poné vos el juego al frente cuando lo pida: en este modo no lo activa solo.
   - En la consola vas a ver los estados y lo que haría ("clic en …", "Z", "girar cámara"). Mirá que detecte los metin correctos y que las decisiones tengan sentido.
   - Como no se mueve nada, es normal que repita intentos o pase a explorar.
2. **De verdad, corto y supervisado:**
   - Parate **adentro de la zona**, cerca de un Metin de Oscuridad o Negro, sin nada seleccionado.
   - `.venv\Scripts\python.exe run_bot.py --max-minutos 3`. Después de la cuenta regresiva de 5 s, tendría que:
     1. Atacar el metin y confirmarlo en el panel.
     2. Al romperlo, recoger con Z.
     3. Buscar otro, girando la cámara y caminando, sin salir de la zona.
3. **Más largo:** `.venv\Scripts\python.exe run_bot.py --max-minutos 15`, mirando de vez en cuando.
4. **Si aparece el captcha:** tiene que sonar la alarma y el bot quedar quieto. Resolvelo vos y tiene que seguir solo.
5. **Contame:**
   - Qué hizo bien y qué mal: se trabó, salió de la zona, atacó otra cosa, no recogió, etc.
   - El resumen que imprime al final.
   - **Mandame o dejá a mano `logs\bot.log`**: de ahí saco la velocidad real, cuánto gira la cámara y los tiempos para calibrar.

### Fase 5: input, pánico y pausa
Resultado de Enzo (2026-11-19): ✅ "los resultados están perfectos". No informó si la rueda en −3 aleja o acerca, porque el loop no usa zoom.

Desde una terminal en `D:\proyectos\metin-bot`. **Si el juego corre como administrador, abrí la terminal como administrador**: si no, el script avisa y no manda nada. Este script **sí manda teclas y clics al juego**: parate en un lugar tranquilo, sin monstruos cerca y sin nada seleccionado.
1. **Primero, en simulación** (no manda nada): `.venv\Scripts\python.exe test_input.py --simular`. Leé los 7 pasos que imprime.
2. **De verdad:** `.venv\Scripts\python.exe test_input.py`. Hay una cuenta regresiva de 5 s, el juego viene al frente y se anuncia cada paso en la consola antes de hacerlo. Mirá que pase esto:
   1. La cámara gira a la izquierda (Q) y después a la derecha (E).
   2. Zoom: rueda −3 y +3. **Anotá si −3 alejó o acercó.**
   3. Z (recoger).
   4. Te bajás del caballo (Ctrl+G) y a los 2 s te volvés a subir.
   5. Caminás un poco hacia adelante (W).
   6. Clic en el piso, debajo del personaje: caminás hasta ahí.
   7. Escape (deselecciona).
3. Por partes, si algo falló: `--solo camara`, `--solo teclas` o `--solo mouse`.
4. **F12:** corrélo de nuevo y apretá F12 en el medio (por ejemplo, mientras gira la cámara). Tiene que decir "Cortado por pánico (F12)", frenar enseguida y no dejar nada trabado: la cámara no sigue girando y el personaje no sigue caminando.
5. **Esquina:** corrélo y llevá el mouse a la esquina superior izquierda de la pantalla. Tiene que cortar por "esquina".
6. **F11:** corrélo y apretá F11 mientras gira la cámara. La cámara tiene que frenar (la tecla se suelta) y la consola decir "Pausado". F11 otra vez: "Reanudado", y sigue lo que faltaba.
7. **Foco:** en el medio de un paso, pasá a otra ventana (Alt+Tab). El bot tiene que dejar de mandar teclas, avisar que el juego no está al frente y seguir cuando vuelvas al juego.
8. Contame qué pasos no hicieron lo anunciado, cuánto tardó en cortar con F12 y el resultado del zoom.

### Fase 4: objetos, yang, captcha, muerte y alarma
Resultado de Enzo (2026-11-19):
- ✅ Objetos: `Objetos: 10 (8 tuyos, 2 publico, 0 de otros) | yang: 0 | elegido: (952, 560)`, igual que en pantalla. Los 8 eran de "Farfarfa's": Piedra de Vitalidad+2, Shuriken, Uniforme Negro, Trozo de Armadura Rota, Poción Morada(M), Piedra de Golpe Mortal+1, Piedra de Vitalidad+1 y Piedra Contra Guerrero+2. Los 2 públicos: Porcelana Rota y un Uniforme Negro. El elegido es el más cercano. Ciclo de detección: 82 ms.
- ✅ Salió "Juego al frente y estable, sigo.".
- ✅ A3: en esa misma captura, el inventario sale sombreado como ventana, igual que el Mapa grande.
- Dato de Enzo: el "Bot Verify" aparece **siempre en el mismo lugar** (a la derecha, como en `captcha.png`). Se puede buscar en una zona fija y dejar la búsqueda en toda la pantalla como respaldo.
- Para más adelante: B con el juego minimizado, muerte (paso 5), alarma (paso 6) y lo pendiente de la Fase 3. Si aparecen objetos de otro jugador, Enzo guarda la captura.

Desde una terminal en `D:\proyectos\metin-bot`, con el juego abierto. `detect_preview.py` no manda clics ni teclas: trae el juego al frente, captura, imprime lo que detecta y guarda `debug\detect_preview.png`.
1. **Objetos:** después de romper un metin, con objetos en el piso, corré `.venv\Scripts\python.exe detect_preview.py`.
   - La línea `Objetos: N (x tuyos, y publico, z de otros) | yang: Y | elegido: (…)` tiene que coincidir con lo que ves.
   - En la imagen, los objetos se marcan en verde, el yang en gris y el elegido con un círculo amarillo.
2. **Yang:** con yang en el piso, tiene que salir en gris y nunca como elegido.
3. **Objetos de otro jugador** (si aparece alguno, por ejemplo de alguien que farmea cerca): tienen que salir en naranja, contados en "de otros". **Guardá esa escena** con `.venv\Scripts\python.exe capture_preview.py --out samples\real_objetos_de_otro.png`: hoy ese caso solo está probado con imágenes sintéticas.
4. **Captcha:** cuando aparezca el "Bot Verify", antes de resolverlo corré `detect_preview.py`: tiene que decir `Captcha: si`. Si podés, guardalo también con `capture_preview.py --out samples\real_captcha_2.png`. Sirve, sobre todo, si aparece en otra posición.
5. **Muerte** (solo si pasa): `detect_preview.py` tiene que decir `Muerte: si`.
6. **Alarma:** para escuchar cómo suena y ajustar `alarm.freq`, `beep_ms` y `pause_ms` en `config.json`, corré esto. Suena 3 segundos:
   `.venv\Scripts\python.exe -c "import time; from bot.config import load_config; from bot.alarm import Alarm; a=Alarm(load_config('config.json')); a.start('prueba'); time.sleep(3); a.stop()"`
7. Si algo falla, guardá la escena con `capture_preview.py --out samples\real_<nombre>.png` y contame qué esperabas.

### Para repetir (correcciones del 2026-11-18)
Desde una terminal en `D:\proyectos\metin-bot`, con el juego abierto. Ninguno de estos comandos manda clics ni teclas.
1. **Foco (B):**
   - Con la PowerShell adelante del juego, sin `Start-Sleep`: `.venv\Scripts\python.exe capture_preview.py`. La captura guardada (`debug\capture_preview.png`) tiene que mostrar el juego, sin la PowerShell. Va a tardar ~0.5 s más que antes, a propósito.
   - Repetí con el juego **minimizado**: se tiene que restaurar y la captura tiene que salir bien.
   - Lo mismo con `.venv\Scripts\python.exe detect_preview.py`.
2. **Inventario (A3):**
   - Con el inventario compacto en su lugar de siempre y el Mapa grande abiertos: `.venv\Scripts\python.exe detect_preview.py`. En `debug\detect_preview.png` **los dos** tienen que salir sombreados, con un margen alrededor.
   - Si hay un metin pegado al inventario, la consola tiene que decir `permitido=False` para ese metin.
   - Repetí con el inventario en modo completo (con el panel de estadísticas), con el inventario movido a otro lugar y, si podés, con las páginas I y IV.
3. **Fase 3, lo que faltó:**
   - Pegale a un metin (vida parcial): la vida que imprime tiene que coincidir con la pantalla.
   - Movete a 3 o 4 lugares y verificá las coordenadas.
   - Seleccioná a tu personaje (`Panel: other`) y no selecciones nada (`Panel: ninguno`).
4. Si algo falla, guardá la escena con `.venv\Scripts\python.exe capture_preview.py --out samples\real_<nombre>.png` y contame qué esperabas.

### Fase 3: panel y coordenadas
Resultado de Enzo (2026-11-18):
- ✅ Metin Negro seleccionado: sale en verde (metin_negro 1.00), con `target_metin metin_negro | vida 63450/63450` y coordenadas (449, 172), todo igual que en pantalla.
- ✅ Con un monstruo seleccionado sale `Panel: other`.
- Sin probar todavía: vida parcial de un metin (paso 2), coordenadas en varios lugares (paso 4) y tu personaje seleccionado o nada seleccionado (paso 3).

Desde una terminal en `D:\proyectos\metin-bot`, con el juego abierto. `detect_preview.py` no manda clics ni teclas: solo trae el juego al frente, captura e imprime lo que lee (y guarda `debug\detect_preview.png`).
1. Seleccioná un Metin de Oscuridad (clic sobre él) y corré `.venv\Scripts\python.exe detect_preview.py`. En la consola:
   - `Panel: target_metin metin_de_oscuridad … | vida X/Y (barra Z)`: X/Y tiene que coincidir exacto con el número del panel en pantalla.
   - `Coordenadas: (x, y)`: tiene que coincidir con lo que muestra el juego debajo del minimapa.
2. Pegale a un Metin Negro un rato (vida parcial) y repetí: `target_metin metin_negro`, la vida coincide y la barra es aproximadamente vida/máxima.
3. Seleccioná un monstruo o a tu personaje: tiene que decir `Panel: other …`. Sin nada seleccionado: `Panel: ninguno`.
4. Movete a 3 o 4 lugares distintos de la zona (si podés, alguno con coordenadas de menos dígitos) y verificá que las coordenadas coincidan siempre.
5. De paso, repetí A2 de la Fase 2: ahora el Metin Negro tiene que salir en verde (objetivo).
6. Si alguna lectura no coincide, guardá esa escena con `.venv\Scripts\python.exe capture_preview.py --out samples\real_<nombre>.png` y contame qué decía la pantalla y qué leyó el bot.

### Fase 2
Resultado de Enzo (2026-11-18):
- A1 ✅: con un Metin de Oscuridad a media distancia salió el nombre en verde con score 1.00, el círculo sobre la piedra y la línea.
- A2 ✅: el Metin Negro salió en rojo con score 1.00, como estaba especificado. A partir de eso Enzo cambió el requisito para que también lo ataque.
- A2 con la lista nueva ✅: el Metin Negro sale en verde.
- **A3 ❌: el inventario no se detectaba** en su lugar de siempre (abajo a la derecha, con la página III seleccionada). El Mapa grande sí. El metin elegido quedó con el clic en (1739, 449), pegado a la barra de título del inventario (y≈472). Captura del caso: `samples/real_inventario_abierto.png`. Se está corrigiendo la detección y se agrega un margen de seguridad alrededor de las ventanas.
- **B ⚠️: captura demasiado temprana.** Con la PowerShell adelante, `capture_preview.py` dijo "Ventana al frente." y el juego vino adelante, pero la captura salió con la PowerShell encima (`debug/bug_foco_powershell_tapando.png`). Con `Start-Sleep -Seconds 5` antes, sale bien. El caso minimizado no se probó. Se corrige con `GameWindow.ensure_ready()`: verifica que nada tape el juego y espera a que se estabilice.

Desde una terminal en `D:\proyectos\metin-bot`, con el juego abierto.

**A. Detección en vivo** (no manda clics ni teclas; solo trae el juego al frente y captura):
1. Parate a media distancia de un Metin de Oscuridad, con su nombre naranja visible: a distancia el juego no dibuja el nombre y el bot no lo ve. Corré `.venv\Scripts\python.exe detect_preview.py` y abrí `debug\detect_preview.png`. Tiene que aparecer:
   - El nombre del metin en verde ("metin_de_oscuridad 1.00").
   - Un círculo amarillo sobre la piedra (donde clickearía).
   - Una línea desde tu personaje.
2. Repetí con un Metin Negro o uno de Avaricia a la vista: tiene que salir en rojo (ignorado).
3. Repetí con el inventario y el Mapa grande abiertos, mejor si los movés a otro lugar: tienen que aparecer sombreados en gris, y si un metin queda detrás, su clic tiene que salir como no permitido.
4. Si algo sale mal, guardá esa misma escena con `.venv\Scripts\python.exe capture_preview.py --out samples\real_<nombre>.png` y contame qué esperabas ver.

**B. Traer el juego al frente:**
1. Con la terminal adelante del juego: `.venv\Scripts\python.exe capture_preview.py`. Tiene que decir "Ventana al frente.", el juego tiene que quedar adelante y guardarse la captura.
2. Minimizá el juego y repetí: se tiene que restaurar y quedar adelante.
3. Si en algún caso aparece "El juego no está al frente. Hacé clic en la ventana…" (con un beep), hacé clic en el juego: tiene que decir "Juego al frente, sigo." y capturar. Si no hacés nada, a los 30 s sale sin capturar.
4. Si aparece el aviso de que el juego corre como administrador, abrí la terminal como administrador y repetí.
5. Contame en cuál de los casos tuviste que hacer clic vos.
