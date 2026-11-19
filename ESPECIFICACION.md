# Especificación del bot (texto original de Enzo)

> Fuente de verdad de los requisitos. No editar salvo que Enzo cambie un requisito; las decisiones de implementación van en PROGRESS.md.

Quiero construir un bot de farmeo para Metin2 (servidor privado que permite este tipo de programas) en Python, en Windows. El bot NO se inyecta en el juego ni lee su memoria: solo captura la pantalla, analiza la imagen y simula mouse y teclado.

## Pantalla
- El juego corre en MODO VENTANA a 1920x1080, ocupando todo el monitor principal (medido por Enzo el 2026-11-18: el área de cliente real mide 1858x1057, DPI 1.00; título de la ventana "Guabina"). Ubicá la ventana del juego por su título (pywin32 o pygetwindow) y capturá solo el área de cliente (sin barra de título), traduciendo bien las coordenadas de clic. El título de la ventana tiene que ser configurable; si no lo sabés, agregá un script que liste las ventanas abiertas para que yo lo identifique.
- Elementos de la interfaz (ver capturas en ./samples):
  - Arriba al centro, al seleccionar algo: un panel con nombre y nivel (ej. "Lv.30 (Nivel 5.) Metin de Oscuridad"), una barra roja de vida y el número de vida "actual/máxima" (ej. "18.131/80.250", con punto como separador de miles). Al romperse o morir lo seleccionado, el panel desaparece.
  - Arriba a la derecha: minimapa. Debajo: nombre del mapa y canal ("Guabina, Canal General"), y las COORDENADAS del personaje entre paréntesis, ej. "(678, 114)".
  - Abajo al centro: chat. Abajo a la derecha: log de experiencia. Arriba a la izquierda: íconos de buffs y a veces un cartel "Caballo Militar Lleno". A la izquierda: botones de misiones. Arriba al centro pueden aparecer anuncios del servidor. Excluí todas estas zonas de la detección.
  - El personaje está siempre cerca del centro de la pantalla, montado a caballo, con su nombre amarillo encima ("Lv 25 Farfarfa").
  - Los números de daño flotan en amarillo y rojo: ignoralos.

## Cómo se ven las cosas
- Metin: piedra con un anillo o brillo rojo en la base y su NOMBRE EN NARANJA flotando encima, SIN el prefijo "Lv" (ej. "Metin de Oscuridad"). Los monstruos tienen el nombre en rojo con "Lv XX" en verde: no confundirlos. Detectá los metin principalmente por su etiqueta naranja y verificá el nombre: solo hay que atacar los tipos configurados en config.json, en una lista para poder agregar o quitar tipos sin tocar el código: "target_metin_names": ["Metin de Oscuridad", "Metin Negro"] (cambiado por Enzo el 2026-11-18; antes era un solo tipo). Las piedras de los distintos tipos se ven iguales o parecidas: lo que los distingue es el nombre. Hay otros metin en el mapa (ej. "Metin de Avaricia", ver metin_avaricia_seleccionado.png) que hay que ignorar.
- El panel superior también aparece al seleccionar monstruos (ej. "Lv.28 (Nivel 4.) Se-Rang") o a mi propio personaje (muestra "Farfarfa" con un botón "Desmontar", ver yang_en_piso.png). Solo cuenta como metin seleccionado si el nombre del panel coincide con cualquiera de los nombres de target_metin_names. Si seleccionó a mi personaje o a un monstruo por error, deseleccionar con Escape y volver a buscar.
- Objetos en el piso: un recuadro oscuro con el nombre del objeto en letras blancas. Si encima dice el nombre de mi personaje en amarillo ("Farfarfa's"), es un objeto reservado para mí; si está solo en blanco, es público. Los dos son objetos válidos para recoger (ver items_en_piso.png).
- Yang (la moneda): recuadros que dicen "Yang" (ver yang_en_piso.png). NO hay que recogerlos, porque se recogen de a uno y hacen perder mucho tiempo. Excluilos.
- Captcha (ver captcha.png): cada tanto aparece un panel "Bot Verify" (en la captura está a la derecha, pero detectalo en cualquier posición). Arriba muestra 4 números grandes en blanco (ej. "6 1 1 6") y al lado una casilla de entrada; abajo hay un teclado numérico con botones 1-9 y 0, "Clear", "Delete" y "OK". Al detectarlo: pausar el loop, leer los 4 números, clickear "Clear", clickear los botones del teclado en orden y después "OK". Verificar que el panel desapareció. Configurable con "solve_captcha": true. Si no logra leer los números con suficiente confianza, o el panel sigue después de 2 intentos, pausarse y hacer sonar una alarma para que lo resuelva yo.
- Muerte (ver muerte.png): aparecen arriba a la izquierda los botones "Volver a Empezar Aquí" y "Volver a Empezar en la Ciudad".

## Controles del juego (todos configurables en config.json)
- Clic izquierdo en el piso: el personaje camina hasta ahí. Clic izquierdo sobre un enemigo o metin: lo ataca hasta romperlo.
- También: W A S D para moverse y barra espaciadora para atacar hacia adelante.
- Cámara: Q y E la giran (también clic derecho arrastrando). Rueda del mouse: zoom.
- Z: recoge UN objeto cercano por cada vez que se aprieta.
- Ctrl izquierdo + G: bajarse o subirse al caballo.
- F1: habilidad de daño que dura 100 segundos. Para activarla hay que bajarse del caballo, apretar F1, esperar unos 2 segundos de animación y volver a subirse. Es OPCIONAL: apagada por defecto ("use_buff": false).
- El personaje es cuerpo a cuerpo (sura o guerrero) y ataca montado.
- Las pociones se toman solas: no hay que gestionarlas.

## Loop principal (máquina de estados)
1. BUSCAR: capturar y detectar metin de los tipos configurados. Si hay varios, elegir el más cercano al personaje, de cualquiera de los tipos, dentro de la zona de farmeo o de su franja de tolerancia.
2. ATACAR: clic izquierdo sobre el metin. Confirmar que se seleccionó leyendo el panel superior (nombre y vida). Leer la vida con la barra roja y, de ser posible, con el número. Si la vida no baja durante X segundos, reintentar el clic; si sigue sin bajar, descartarlo y buscar otro.
3. Al romperse (el panel desaparece o la vida llega a 0), pasar a RECOGER.
4. RECOGER: detectar etiquetas de objetos que no sean yang. Mientras queden, apretar Z o hacer clic sobre la etiqueta para ir hasta el objeto. Opción configurable de bajarse del caballo para recoger con clic (montado a veces cuesta) y volver a subirse al terminar. Máximo de intentos y de tiempo para no trabarse. Terminar cuando no quedan objetos válidos.
5. EXPLORAR: si no hay metin visible, primero girar la cámara con Q/E en pasos para mirar alrededor. Si sigue sin haber, caminar por la zona con clics en el piso.
6. Volver a BUSCAR. Repetir hasta que yo lo detenga.
- En cualquier estado, revisar en cada ciclo si apareció el captcha o la pantalla de muerte.

## Zona de farmeo y trabas
- (Cambiado por Enzo el 2026-11-18; antes era un rectángulo.) La zona de farmeo es un POLÍGONO en coordenadas del juego, medido en sus esquinas con las coordenadas de debajo del minimapa. Los vértices van en orden (contorno sin cruces): (439, 141) → (753, 91) → (702, 203) → (436, 196). En config.json: "farm_zone": {"polygon": [[439,141],[753,91],[702,203],[436,196]], "outside_tolerance": 30}.
- Es un límite flexible, no una pared: casi todos los metin aparecen adentro, salvo algunas excepciones cerca del borde. El personaje puede salir un poco del polígono, pero siempre tiene que volver adentro. outside_tolerance es cuánto puede alejarse del borde hacia afuera, en unidades de coordenadas del juego, y es configurable.
- Leé las coordenadas de debajo del minimapa en cada ciclo. Para saber si una coordenada está adentro y a qué distancia del borde, usá un test de punto en polígono con distancia (ej. cv2.pointPolygonTest con measureDist=True).
- Metin: atacar los que estén adentro del polígono o dentro de la franja de tolerancia. Ignorar los que estén más lejos.
- Exploración: buscar siempre dentro del polígono. Si el personaje sale (por ir a un metin cerca del borde, o empujado por monstruos), al terminar ese metin tiene que volver hacia adentro, en dirección al centro del polígono. Si supera la tolerancia, volver adentro de inmediato.
- Ejemplo: un Metin Negro en (453, 161) queda adentro.
- Los monstruos bloquean el paso. Si las coordenadas no cambian durante N segundos mientras intenta moverse, ejecutar una rutina para destrabarse (moverse hacia otro lado, girar, reintentar). Lo mismo si se traba yendo a un metin.

## Lectura de texto
Hace falta leer: nombre en el panel superior, número de vida, coordenadas, etiquetas "Yang" y los números del captcha. Evaluá la opción más liviana y confiable: plantillas de caracteres recortadas de las samples (la fuente de la interfaz es siempre la misma) o un OCR (pytesseract o easyocr). Elegí y justificá en PROGRESS.md. Si requiere instalar algo aparte de pip, explicalo paso a paso en el README.

## Muerte
- Versión base: si detecta la pantalla de muerte, detenerse y hacer sonar una alarma.
- Fase final opcional ("auto_revive": false): clickear "Volver a Empezar Aquí", reactivar la habilidad si está habilitada, abrir el inventario, usar el objeto para llamar al caballo, montarse y volver a buscar. Hacela solo cuando todo lo demás esté probado, y pedime las capturas que necesites (inventario, objeto del caballo).

## Stack
- Python 3.14 (cambiado por Enzo el 2026-11-18; originalmente 3.12), con entorno virtual (venv) dentro del proyecto y requirements.txt.
- Captura: mss. Visión: opencv-python y numpy. Input: pydirectinput (scan codes, necesario para juegos DirectX), detrás de una interfaz para poder cambiarlo. Teclas de pánico y pausa: keyboard.

## Requisitos generales
- Botón de pánico: F12 detiene el bot al instante. Además, si muevo el mouse a la esquina superior izquierda, se detiene. F11: pausar y reanudar.
- Cuenta regresiva de 5 segundos al iniciar.
- Pequeñas variaciones aleatorias en tiempos y posiciones de clic.
- Toda la configuración en config.json, con comentarios o un config.example con la explicación de cada campo.
- test_input.py: manda teclas y clics de prueba para que yo verifique en el juego que llegan.
- calibrate.py: me ayuda a definir regiones de pantalla, colores y la zona de farmeo, guardándolos en config.json.
- Modo debug: guarda capturas con rectángulos sobre lo detectado y el estado actual.
- Logs claros en consola y en archivo de cada cambio de estado.
- Código modular: captura, detección de metin, lectura del panel, lectura de coordenadas, detección de objetos, captcha, muerte, input y máquina de estados.
- Tests (pytest) de toda la detección contra ./samples.
- README en español: instalación, calibración, pruebas, uso, qué ajustar si algo falla, y la aclaración de que puede hacer falta ejecutar como administrador si el juego corre como administrador.

## Material disponible (./samples, capturas reales a 1920x1080)
- metin_oscuridad_seleccionado_vida_llena.png: metin seleccionado, vida 80.250/80.250, con su etiqueta naranja.
- metin_oscuridad_combate_vida_parcial.png: pegándole al metin, vida parcial, rodeado de monstruos.
- metin_cerca_sin_seleccionar.png: metin cerca del personaje, sin panel superior.
- metin_oscuridad_a_distancia.png, metin_muy_lejos.png, explorando_zona.png: metin a distintas distancias, entre monstruos y en pendientes.
- metin_avaricia_seleccionado.png: otro tipo de metin, a ignorar.
- items_en_piso.png: objetos en el piso después de romper un metin.
- yang_en_piso.png: yang en el piso y panel de mi propio personaje seleccionado.
- captcha.png: panel Bot Verify.
- muerte.png: pantalla de muerte.
Usalas para desarrollar y PROBAR la detección sin el juego abierto. No inventes cómo se ve el juego. Si te falta alguna captura, anotala en PROGRESS.md y seguí con lo que puedas.

## Forma de trabajo (tengo plan Pro y el límite de uso es reducido)
- Fase 0: creá en .claude/agents/ estos subagentes y frená para que yo abra una sesión nueva:
  - "implementer" (model: sonnet): escribe el código de un módulo puntual.
  - "vision-tester" (model: sonnet): prueba la detección contra ./samples y ajusta umbrales hasta que los tests pasen.
  - "reviewer" (model: haiku): revisa el código terminado y verifica que cumpla los requisitos.
- Vos (sesión principal) planificás, repartís tareas y revisás; delegá el código grande.
- Fases: 1) estructura, venv, captura de la ventana; 2) detección de metin por etiqueta y filtro por nombre; 3) panel superior, vida y coordenadas; 4) objetos vs yang, captcha y muerte; 5) input, test_input.py, pánico y pausa; 6) máquina de estados, zona de farmeo y destrabe; 7) calibración, debug y README; 8) opcional: buff F1 y auto-revivir.
- Al terminar cada fase, actualizá PROGRESS.md con lo hecho, lo pendiente y lo que tengo que probar yo en el juego ("a probar por Enzo", con pasos detallados). Si la sesión se corta por límite, retomá desde ese archivo.
- Antes de empezar la fase 1, mostrame el plan y esperá mi OK.
