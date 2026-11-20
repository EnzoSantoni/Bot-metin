"""Carga, validacion y guardado de config.json."""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

from bot.fileutil import atomic_write_text
from bot.input.base import VALID_KEYS, parse_keys

logger = logging.getLogger(__name__)

# Raiz del proyecto: las rutas relativas de la config se resuelven contra esta carpeta,
# no contra el directorio desde donde se ejecuta el script.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Las fases siguientes agregan sus campos aca.
DEFAULTS: dict[str, Any] = {
    "window_title": "Metin2",
    "expected_client_size": [1858, 1057],
    "window": {"settle_ms": 500, "unobstructed_poll_ms": 50},
    "log": {"level": "INFO", "dir": "logs", "file": "bot.log"},
    # Mapa de metin: puntos donde se rompio un metin (persisten entre sesiones). Ver config.example.jsonc.
    "spawns": {"enabled": True, "file": "data/mapa_metin.json", "merge_u": 6.0, "respawn_s": 240.0, "revisit_s": 120.0,
               "arrive_u": 5.0, "max_walk_s": 40.0},
    # explore_every_s: cada cuanto guarda una imagen periodica mientras explora; max_images: tope por corrida;
    # jpeg_quality 1-100; max_per_kind: tope de imagenes por tipo de evento (estado, descarte, destrabe, ignorada,
    # equivocada, bloqueado, explorar = la periodica); reason_memory_s: un motivo de descarte que vuelve a aparecer antes
    # de este tiempo no cuenta como nuevo (no genera otra imagen ni otro log).
    "debug": {"enabled": False, "dir": "debug", "explore_every_s": 30.0, "max_images": 400, "jpeg_quality": 80,
              "max_per_kind": {"estado": 150, "descarte": 40, "destrabe": 60, "ignorada": 20, "equivocada": 20,
                               "bloqueado": 20, "explorar": 40},
              "reason_memory_s": 60.0},
    # Alarma sonora (bot/alarm.py): beep de `freq` Hz y `beep_ms` ms, pausa `pause_ms`, en bucle.
    # max_seconds: corta sola a los N segundos (0 = suena hasta stop()).
    "alarm": {"freq": 1000, "beep_ms": 400, "pause_ms": 300, "max_seconds": 0},
    # Teclas del juego (bot/input): "ctrl+g" = combinacion.
    "controls": {
        "attack_forward": "space", "pickup": "z", "mount": "ctrl+g", "buff": "f1",
        "camera_left": "q", "camera_right": "e",
        "move_up": "w", "move_left": "a", "move_down": "s", "move_right": "d", "deselect": "esc",
    },
    # Seguridad (bot/control.py): panico, pausa, esquina de pantalla, cuenta regresiva, corte forzado.
    "safety": {"panic_key": "f12", "pause_key": "f11", "corner_px": 5, "corner_poll_ms": 50,
               "countdown_s": 5, "hard_kill_after_s": 2.0},
    # Input (bot/input): backend y variaciones aleatorias [min, max] en ms.
    "input": {"backend": "pydirectinput", "click_jitter_px": 4, "delay_ms": [60, 140],
              "key_hold_ms": [50, 110], "move_settle_ms": [30, 80]},
    "target_metin_names": ["Metin de Oscuridad", "Metin Negro"],
    # Zona de farmeo poligonal en coordenadas del juego (las que se leen debajo del minimapa).
    "farm_zone": {"polygon": [[439, 141], [753, 91], [702, 203], [436, 196]], "outside_tolerance": 30},
    # Filtro de coordenadas (bot/tracking.py). Un salto es imposible si dist > max_speed*dt + slack.
    # history: lecturas que se recuerdan para detectar trabas.
    "coords_filter": {"max_speed": 30.0, "slack": 2.0, "accept_after": 3, "consistency_dist": 3.0,
                       "max_dt_s": 0.5, "history": 512},
    # Deteccion de "no me muevo" (bot/tracking.py): desplazamiento maximo < min_move en window_s segundos.
    # max_attempts / unstuck_radius_px / back_key_s: rutina de destrabe (bot/bot.py).
    # Episodios de traba (bot/tracking.py::StuckEpisode): resolve_dist = alejarse tanto del origen cierra el episodio;
    # monsters_min = monstruos cerca para que ESPACIO sea la primera estrategia; space_s / forward_s = ESPACIO y luego W;
    # repeat_max episodios en repeat_window_s -> CENTRO; look_steps x look_step_s = giro de camara de MIRAR;
    # min_dir_sep_deg / dir_step_deg = separacion minima entre direcciones probadas y paso de busqueda;
    # episode_ttl_s = un episodio sin intentos nuevos por este tiempo se cierra. (Valores estimados, sin medir.)
    # space_fail_max: con monstruos cerca ESPACIO va primero y se repite hasta fallar tantas veces seguidas; despues rota.
    # forward_chunk_s: W/S se parten en tramos de este largo con la zona chequeada entre tramos.
    # progress_tol: tolerancia (unidades) al comparar distancias para decir que hubo (o no) progreso real.
    # memory_dirs: direcciones probadas que hereda el episodio siguiente.
    "stuck": {"window_s": 3.0, "min_move": 2.0, "max_attempts": 4, "unstuck_radius_px": 200, "back_key_s": 0.6,
              "resolve_dist": 10.0, "monsters_min": 2, "space_s": 2.0, "forward_s": 1.0,
              "repeat_max": 3, "repeat_window_s": 120.0, "look_steps": 4, "look_step_s": 0.8,
              "min_dir_sep_deg": 60.0, "dir_step_deg": 30.0, "episode_ttl_s": 120.0,
              "space_fail_max": 2, "forward_chunk_s": 0.3, "progress_tol": 1.0, "memory_dirs": 5,
              # Modo teclado (navigation.mode): back_s = S de ATRAS; look_walk_s = W despues de girar la vista en MIRAR.
              "back_s": 1.0, "look_walk_s": 1.2},
    # Loop principal (bot/bot.py): ritmo minimo por ciclo y cada cuantos ciclos se busca el captcha en toda la imagen.
    # hold_chunk_s: las teclas sostenidas (ESPACIO, W, S, giros de camara) se parten en tramos de este largo con un
    # chequeo de captcha entre tramos.
    "loop": {"cycle_ms": 150, "captcha_full_search_every": 10, "hold_chunk_s": 0.5},
    # ATACAR: tiempos en s, descarte en minutos, radio de descarte en unidades del juego.
    "attack": {"confirm_timeout_s": 6.0, "hp_stall_s": 8.0, "max_reclicks": 3, "max_seconds": 120.0,
               "broken_confirm_cycles": 2, "discard_minutes": 10.0, "discard_radius": 8.0, "discard_error_frac": 0.3,
               "max_wrong_selections": 3,
               # blocked_s: confirmado, vida sin bajar y quieto/en circulo -> ESPACIO; circle_ratio: recorrido > ratio x
               # desplazamiento neto = en circulo (circle_min_path: recorrido minimo, en unidades); wrong_monsters_min:
               # monstruos cerca para tratar una seleccion equivocada como "monstruo pegado". (Estimados.)
               "blocked_s": 4.0, "circle_ratio": 2.5, "circle_min_path": 4.0, "wrong_monsters_min": 1,
               # blocked_window_frac: fraccion de blocked_s que tiene que cubrir el recorrido para juzgar si esta bloqueado.
               "blocked_window_frac": 0.8,
               # Ir a un metin lejano con clic: el personaje camina solo. Mientras se mueve no cuenta el estancamiento de
               # vida (tope walk_max_s). Trabado en el camino con monstruos cerca: ESPACIO unstick_space_s y reclic.
               "walk_max_s": 25.0, "unstick_space_s": 1.5},
    # RECOGER: mirar look_s si hay objetos validos; si hay, bajarse (dismount) y, objeto por objeto (el mas cercano primero),
    # caminar con W/A/S/D (sin clics) hasta quedar encima y apretar Z. Cada tramo dura step_s (menos si el objeto esta cerca:
    # proporcional a su distancia hasta full_step_px, minimo min_step_s); el punto del objeto es el centro inferior de su
    # etiqueta + item_below_label_px. arrive_px = Z cuando ese punto queda a tanto de los pies. stall_steps tramos sin
    # acercarse al menos stall_gain_px cuentan como un intento (tries_per_item intentos y se ignora). Ver config.example.jsonc.
    "pickup": {"press_interval_ms": 350, "empty_confirm_cycles": 5, "max_presses": 30, "max_seconds": 20.0,
               "dismount": True, "mount_wait_s": 2.0, "look_s": 1.0, "arrive_px": 70, "item_below_label_px": 10,
               "step_s": 0.25, "min_step_s": 0.1, "full_step_px": 200, "stall_steps": 5, "stall_gain_px": 8,
               "track_px": 80, "tries_per_item": 2, "same_spot_u": 3.0},
    # EXPLORAR: barrido de camara (pasos de Q/E) y caminata hacia un punto aleatorio del poligono.
    # border_margin: destino de cada tramo a >= esta distancia del borde; leg_s / leg_units: tope de tiempo y largo de un
    # tramo recto; max_without_metin_s: sin ningun objetivo por tanto tiempo -> CENTRO; min_leg_sep_deg / recent_legs: el
    # tramo nuevo se aparta tanto de los ultimos rumbos. camera_steps x camera_step_s deberia dar ~360 grados. (Estimados.)
    # visited_keep: puntos recientes que la exploracion trata de no repetir; leg_tiebreak_units: desempate al azar
    # (unidades) entre destinos de tramo parecidos.
    "explore": {"camera_steps": 12, "camera_step_s": 0.5, "walk_timeout_s": 20.0, "border_margin": 25.0,
                "leg_s": 10.0, "leg_units": 45.0, "max_without_metin_s": 90.0, "min_leg_sep_deg": 60.0,
                "recent_legs": 3, "visited_keep": 8, "leg_tiebreak_units": 3.0,
                # edge_return_u: adentro a menos de esto del borde con el tramo alejandose del poligono (o un barrido
                # completo sin ver nada util a menos de border_margin del borde) -> VOLVER al centro por coordenadas.
                "edge_return_u": 5.0,
                # Giros de busqueda de la camara en pasos: despues de cada uno se espera settle_s y settle_frames
                # percepciones completas antes del siguiente (tiempo para analizar lo que aparecio).
                "settle_s": 0.6, "settle_frames": 2},
    # ACERCARSE (piedra lejana sin etiqueta): max_seconds de la caminata; ignore_minutes / ignore_radius: posicion que
    # resulto ser de otro tipo; lost_cycles: ciclos sin ver la piedra para abandonar; label_match_px: una etiqueta a
    # esta distancia de la piedra es "esa piedra"; match_px: seguimiento de la piedra entre ciclos;
    # label_exclude_down_px: la piedra con etiqueta se excluye de las lejanas hasta tantos px debajo de la etiqueta.
    # Sin camara lista (la posicion en el juego no se conoce): un metin (con etiqueta o piedra) se ataca/persigue si el
    # personaje esta adentro de la zona o en la franja de tolerancia, sin limite de distancia en pantalla;
    # blind_player_radius = un descarte o ignorado guardado con la posicion del JUGADOR solo aplica a menos de tantas
    # unidades de ese lugar.
    # ACERCARSE = hover (mover el mouse sobre la piedra, sin clic) para leer su etiqueta: espera hover_wait_s, busca una
    # etiqueta a <= hover_label_px de la piedra; tras hover_tries hovers sin etiqueta hace clic igual (el panel de
    # ATACAR confirma el tipo).
    # max_tries_per_spot: aproximaciones a una misma posicion sin llegar a atacar antes de ignorarla.
    # far_monsters_n / far_monsters_px: una piedra lejana SIN humo con tantos monstruos a menos de tantos px es un
    # falso positivo y se ignora.
    "approach": {"max_seconds": 40.0, "ignore_minutes": 10.0, "ignore_radius": 10.0, "lost_cycles": 6,
                 "label_match_px": 100, "match_px": 200, "label_exclude_down_px": 100,
                 "blind_player_radius": 25.0, "max_tries_per_spot": 3,
                 "far_monsters_n": 2, "far_monsters_px": 200,
                 "hover_wait_s": 0.3, "hover_label_px": 120, "hover_tries": 2},
    # CENTRO: llega al centro del poligono a <= arrive_dist unidades (el tope de tiempo es volver.max_seconds).
    "center": {"arrive_dist": 8.0},
    # VOLVER: tiempo acumulado maximo de una "vuelta" (s); lo comparte CENTRO.
    "volver": {"max_seconds": 120.0},
    # CAPTCHA: espera pasiva a que Enzo lo resuelva; poll_s entre lecturas, clear_confirm lecturas seguidas sin panel.
    "captcha_wait": {"poll_s": 0.5, "clear_confirm": 2},
    # Navegacion por realimentacion (bot/navigation.py), pantalla en px y juego en unidades de coordenadas.
    "navigation": {
        "probe_radius_px": 150, "click_radius_px": [60, 120], "arrive_dist": 5.0, "explore_margin": 15.0,
        "min_segment_px": 20, "min_segment_game": 8.0, "min_sin": 0.3, "fit_segments": 8,
        "max_ratio_error": 0.35, "relearn_after_rejects": 4, "phi_smooth_deg": 10.0,
        "angle_step_deg": 15, "max_angle_deg": 60, "shorten_factors": [1.0, 0.75, 0.5],
        # settle_s: sin cambio de coordenadas por tanto tiempo = el personaje se freno; segment_timeout_s: tope de un tramo.
        "settle_s": 1.0, "segment_timeout_s": 8.0,
        # Los clics de caminata y destrabe no caen a menos de N px de un metin que NO es objetivo o esta descartado (clickearlo lo ataca).
        "avoid_metin_px": 70,
        # Rango plausible de la escala pantalla->juego [menor, mayor] en unidades por pixel, solo para acortar los
        # sondeos cerca del borde mientras la camara no esta calibrada (despues se usa la A0 aprendida).
        "scale_hint": [0.15, 0.4],
        # Giro externo de la camara (no hecho por el bot): external_rotation_deg = el giro estimado difiere mas que esto;
        # heading_tolerance_deg = rumbo observado vs esperado en un tramo; zone_guard_margin = el tramo se corta si el
        # personaje baja de -(outside_tolerance - margen); max_walk_px = largo maximo de un tramo; brake_px = clic de freno.
        "external_rotation_deg": 25.0, "heading_tolerance_deg": 30.0, "zone_guard_margin": 10.0,
        "max_walk_px": 120, "brake_px": 12,
        # Sondeos (camara sin calibrar): probe_measure_factor = holgura sobre el largo minimo medible;
        # probe_turn_jitter_deg = variacion al azar del giro de ~90 grados entre sondeos.
        "probe_measure_factor": 1.5, "probe_turn_jitter_deg": 17.0,
        # mode: "teclado" = el bot camina SOLO con W/A/S/D y gira la vista con Q/E (el clic izquierdo queda para atacar y
        # recoger); "clic" = caminar con clics en el piso (modo anterior, usa la camara aprendida).
        "mode": "teclado",
        # Modo teclado (bot/keynav.py): chunk_s = W sostenida entre chequeos; probe_s = W para medir el rumbo cuando no se
        # conoce; align_deg = si el desvio al destino es mayor, primero se gira; turn_deg_per_s = velocidad inicial de
        # giro de Q/E (= 360 / (explore.camera_steps * explore.camera_step_s)), se aprende; max_turn_deg = giro maximo
        # por orden; walk_speed = velocidad inicial de caminata (u/s), se aprende; min_step_s = tramo de W minimo;
        # edge_measure_u = desplazamiento minimo para medir el rumbo cuando el personaje esta pasado de la guardia de zona;
        # view_right_key = tecla (camera_left / camera_right) que gira la VISTA a la derecha (se aprende si esta al reves).
        "keyboard": {"chunk_s": 0.5, "probe_s": 0.4, "align_deg": 20.0, "turn_deg_per_s": 60.0, "max_turn_deg": 180.0,
                     "walk_speed": 15.0, "min_step_s": 0.15, "edge_measure_u": 3.0, "view_right_key": "camera_right"},
    },
    "vision": {
        # Plano del piso (bot/ground.py) para comparar distancias con perspectiva: horizonte a horizon_frac del alto,
        # focal = focal_frac del ancho, pies del personaje a feet_dy_px debajo de su punto, min_dv_px = acota (v - horizonte).
        # (Estimados: robusto con horizon_frac 0.10-0.19.)
        "ground": {"horizon_frac": 0.14, "focal_frac": 0.7, "feet_dy_px": 40, "min_dv_px": 20},
        # Zonas de interfaz ignoradas. dx/dy: esquina superior izquierda respecto del punto de
        # anclaje (medido sobre ./samples). Los rectangulos se recortan al tamano de la imagen.
        "exclusion_zones": [
            {"name": "chat", "anchor": "bottom-center", "dx": -340, "dy": -190, "w": 640, "h": 190},
            {"name": "exp_log", "anchor": "bottom-center", "dx": 265, "dy": -200, "w": 170, "h": 200},
            {"name": "bottom_bar", "anchor": "bottom-left", "dx": 0, "dy": -60, "w": 4000, "h": 60},
            {"name": "buffs", "anchor": "top-left", "dx": 0, "dy": 0, "w": 235, "h": 130},
            {"name": "quests", "anchor": "center-left", "dx": 0, "dy": -260, "w": 112, "h": 170},
            {"name": "top_right", "anchor": "top-right", "dx": -420, "dy": 0, "w": 420, "h": 240},
            {"name": "top_center", "anchor": "top-center", "dx": -400, "dy": 0, "w": 800, "h": 80},
        ],
        "metin": {
            "label_templates_dir": "templates/labels",
            # Naranja de los nombres de metin (texto sin antialiasing, BGR 0,128,255).
            "hsv_lo": [13, 220, 220],
            "hsv_hi": [17, 255, 255],
            # Kernel [ancho, alto] que une las letras de una etiqueta.
            "group_kernel": [9, 3],
            "min_text_w": 40, "max_text_w": 160,
            "min_text_h": 6, "max_text_h": 16,
            "min_pixels": 60,
            # Fraccion maxima del rectangulo de la etiqueta que puede caer en zonas excluidas.
            "max_zone_overlap": 0.5,
            "min_score": 0.85,
            "min_margin": 0.1,
            # Punto de clic respecto del centro de la etiqueta (la piedra queda 55-80 px debajo).
            "click_dx": 0, "click_dy": 65,
        },
        # Piedras de metin sin etiqueta (bot/vision/far_metin.py): nucleo rojo (HSV) con humo encima.
        "far_metin": {
            "core_h_max": 9, "core_s": [100, 205], "core_v_min": 150,
            "smoke_hsv_lo": [14, 8, 105], "smoke_hsv_hi": [45, 80, 230],
            "group_kernel": [9, 9],
            "min_core_pixels": 60, "min_smoke_pixels": 40,
            "min_w": 8, "max_w": 140, "min_h": 8, "max_h": 160,
            "smoke_height_frac": 1.2, "smoke_side_frac": 0.5, "smoke_side_px": 8, "smoke_overlap_frac": 0.15,
            "center_y_frac": 0.85,
            "max_zone_overlap": 0.5, "exclude_overlap": 0.3,
            "core_ref": 300, "smoke_ref": 150,
            # Segundo criterio (piedras sin humo, p. ej. Metin Negro anaranjado): cuerpo claro naranja.
            "alt_enabled": True,
            "alt_bright_lo": [3, 30, 200], "alt_bright_hi": [24, 190, 255],
            "alt_min_bright_px": 100, "alt_min_bright_frac": 0.08, "alt_bright_max_ratio": 1.6,
            "alt_min_ground_frac": 0.3,
        },
        # Etiquetas de monstruos (bot/vision/monsters.py): "Lv XX" verde plano + nombre rojo plano.
        "monsters": {
            "red_bgr": [9, 22, 235], "green_bgr": [51, 255, 152], "color_tol": 6,
            "green_kernel": [9, 1],
            "min_red_pixels": 15, "min_green_pixels": 10,
            "min_green_w": 12, "max_green_w": 40, "min_green_h": 6, "max_green_h": 16,
            "probe_w": 30, "max_label_w": 260,
            "max_gap_px": 10, "word_gap_px": 8,
            "max_zone_overlap": 0.5,
            "near_radius_px": 120,
        },
        # Objetos en el piso (bot/vision/items.py): recuadro con borde negro exacto de 1 px, fondo oscuro
        # translucido y texto blanco sin antialiasing. Con dueno, una linea amarilla (0,255,255) encima del nombre.
        "items": {
            "yang_template": "templates/items/yang.png",   # mascara binaria de la palabra "Yang"
            "border_color": [0, 0, 0],      # BGR del borde del recuadro (exacto)
            "min_edge_run": 21,             # largo minimo (impar) de un borde horizontal negro
            "box_w": [24, 260],             # ancho minimo y maximo del recuadro (borde incluido)
            "box_dy": [14, 30],             # distancia (px) minima y maxima entre borde superior e inferior
            "box_dy_allowed": [14, 30],     # distancias validas: 1 linea (alto 15), 2 lineas (alto 31)
            "box_dy_tol": 1,                # tolerancia (px) sobre box_dy_allowed
            "edge_x_tol": 6,                # diferencia maxima (px) de x/ancho entre el borde de arriba y el de abajo
            "border_min_frac": 0.7,         # fraccion minima negra de los bordes laterales (promedio)
            "white_min": 250,               # canal minimo (B,G,R) del texto blanco
            "yellow_lo": [0, 230, 230],     # BGR minimo del amarillo del dueno
            "yellow_hi": [30, 255, 255],    # BGR maximo del amarillo del dueno
            "text_rows": 13,                # filas de la linea de texto (contadas desde el borde inferior)
            "min_white_px": 15,             # pixeles blancos minimos del nombre
            "yellow_noise_px": 3,           # amarillo en la franja superior por encima de esto = tiene dueno (medido: 0 en publicos)
            "owner_size_tol": 2,            # tolerancia (px) de tamano de la caja amarilla vs la plantilla del dueno
            "max_edges": 1500,              # tope de bordes candidatos por captura (samples: <=24); mas = imagen anomala
            "owner_template": "templates/items/owner.png",  # mascara del amarillo "Farfarfa's" (dueno propio)
            "owner_min_score": 0.95,        # score minimo para reserved; amarillo bajo eso = reserved_other
            "yang_min_score": 0.85,         # score minimo de la plantilla "Yang" para kind="yang"
            "max_zone_overlap": 0.5,        # fraccion maxima del recuadro en zonas excluidas
        },
        # Ventanas de interfaz movibles (inventario, Mapa grande): se buscan por la plantilla de su
        # barra de titulo (templates/ui/<template>.png, recortada desde la esquina del marco) y por un
        # segundo ancla (esquina/borde inferior del marco, "anchor2"), por si el titulo esta tapado o
        # fuera de la imagen. Calza cualquiera de los dos. anchor2.offset = [dx, dy] de la esquina sup.
        # izq. del recorte respecto de la esquina sup. izq. de la ventana (tools/make_ui_template.py --origin).
        # size = [ancho, alto] fijo de la ventana completa.
        # Busqueda en dos pasos: gruesa en gris a escala `coarse_scale` (umbral `coarse_min_score`, mas bajo)
        # y refinada a resolucion completa en una ventanita de +-`refine_margin` px (umbral `min_score`).
        "ui_windows": {
            "min_score": 0.8,
            "coarse_scale": 0.25,
            "coarse_min_score": 0.5,
            "refine_margin": 4,
            # Margen (px) que se agrega por cada lado a las ventanas detectadas en la mascara: ningun clic
            # queda pegado a su borde (la piedra mide ~40 px y el punto de clic puede errar ~10 px).
            "safety_margin": 30,
            "windows": [
                {"name": "mapa_grande", "template": "templates/ui/mapa_grande.png", "size": [214, 289],
                 "anchor2": {"template": "templates/ui/mapa_grande_base.png", "offset": [0, 268]}},
                {"name": "inventario", "template": "templates/ui/inventario.png", "size": [295, 575],
                 "anchor2": {"template": "templates/ui/inventario_base.png", "offset": [0, 539]},
                 # Inventario compacto (sin panel de estadisticas): otro ancho, titulo mas corto; la base
                 # queda tapada por la barra de atajos. Se prueba solo si no calza la variante grande.
                 "alt": [{"template": "templates/ui/inventario_compacto.png", "size": [165, 575]}]},
            ],
        },
        # Panel "Bot Verify" (bot/vision/captcha.py): solo se detecta, no se resuelve. Dos anclas fijas que no
        # dependen de los digitos: el pie (boton OK + "Bot Verify") y el bloque de botones 1-9 (offset = esquina sup. izq.
        # del recorte respecto de la del panel). Busqueda gruesa en gris a `coarse_scale` y refinada a
        # resolucion completa en +-`refine_margin` px. size = [ancho, alto] del panel.
        "captcha": {
            "min_score": 0.8, "coarse_scale": 0.25, "coarse_min_score": 0.5, "refine_margin": 4,
            "size": [152, 194],
            # Zona fija (mismo formato que las zonas excluidas) donde aparece el panel, con ~80 px de holgura.
            "search_region": {"anchor": "center-right", "dx": -400, "dy": -180, "w": 400, "h": 374},
            "footer": {"template": "templates/captcha/pie.png", "offset": [24, 146]},
            "keypad": {"template": "templates/captcha/teclado.png", "offset": [9, 49]},
        },
        # Pantalla de muerte (bot/vision/death.py): botones "Volver a Empezar Aqui" y "...en la Ciudad".
        # search = region (desde la esquina sup. izq. de la imagen) donde se buscan las plantillas.
        "death": {
            "search": {"w": 420, "h": 320},
            "min_score": 0.8, "coarse_scale": 0.5, "coarse_min_score": 0.5, "refine_margin": 4,
            "here_template": "templates/death/aqui.png",
            "city_template": "templates/death/ciudad.png",
        },
        # Posicion del personaje respecto del centro de la imagen.
        "player_offset": [0, 20],
        # Lectura de numeros por plantillas de caracter (bot/vision/digits.py). Un set por fuente.
        # color/color_tol: color BGR del texto (sin antialiasing, +-color_tol por canal);
        # min_score: similitud minima (Jaccard) para aceptar un caracter, si no va como '?';
        # min_margin: ventaja minima del mejor glifo sobre el segundo (8/9 y 8/6 se parecen 0.80);
        # max_gap / max_gap_after_comma: hueco maximo (px) entre caracteres; uno mayor es un caracter tapado ('?').
        "glyphs": {
            "coords": {"dir": "templates/glyphs/coords", "color": [217, 217, 217], "color_tol": 12,
                       "min_score": 0.9, "min_margin": 0.15, "max_gap": 3, "max_gap_after_comma": 7},
            "panel": {"dir": "templates/glyphs/panel", "color": [199, 199, 199], "color_tol": 12,
                      "min_score": 0.9, "min_margin": 0.15, "max_gap": 3, "max_gap_after_comma": 7},
        },
        # Coordenadas del personaje (debajo del minimapa). `search` es la region de busqueda (ancla + dx/dy/w/h);
        # el texto "(x, y)" se busca por su aspecto: linea de texto de alto y ancho validos.
        "coords": {
            "search": {"anchor": "top-right", "dx": -200, "dy": 140, "w": 200, "h": 110},
            "line_w": [20, 90], "line_h": [9, 12],
            "range": [0, 4000],        # valores validos de x e y
            "min_score": 0.9,          # confianza minima de la lectura completa
        },
        # Panel superior (nombre, barra y numero de vida).
        "panel": {
            # Region donde buscar el marco del panel (arriba al centro).
            "search": {"anchor": "top-center", "dx": -450, "dy": 0, "w": 900, "h": 110},
            "frame_color": [85, 85, 85],       # borde del marco (exacto)
            "frame_min_w": 150,                # largo minimo del borde horizontal
            "frame_h": [30, 90],               # alto minimo y maximo del marco
            "frame_x_tol": 2,                  # diferencia maxima (px) de x entre el borde superior e inferior
            "frame_edge_merge": 1,             # filas consecutivas del mismo borde que cuentan como una
            "name_templates_dir": "templates/panel_names",
            "name_color": [199, 199, 199], "name_color_tol": 12,
            # Linea del nombre: x desde el borde izquierdo hasta (borde derecho + x_to_right), y desde el borde superior.
            "name_region": {"x_from_left": 8, "x_to_right": -176, "dy": 14, "h": 16},
            "name_min_score": 0.85, "name_min_margin": 0.1,
            # Barra de vida: tapas doradas a ambos lados dentro de la banda [dy0, dy1] (desde el borde superior).
            "bar": {"band": [17, 24], "search_x": [-200, -24],   # x relativo al borde derecho
                    "cap_diff": 25,                              # dorado: R >= G+diff y G >= B+diff
                    "gold_gap": 4,                               # columnas vacias que separan dos tapas doradas
                    "width": 108, "width_tol": 3,               # ancho interior del recipiente
                    "red_min_r": 110, "red_max_gb": 110, "red_min_rg": 40},
            # Numero "actual/maxima": region desde el borde derecho (dx) y el superior (dy).
            "number_region": {"dx": -165, "w": 115, "dy": 4, "h": 13},
            "hp_ratio_tol": 0.03,              # diferencia maxima entre actual/maxima y la barra
        },
    },
}


class ConfigError(Exception):
    """Configuracion invalida o ilegible."""


def resolve_path(p: str | Path) -> Path:
    """Ruta absoluta: las relativas se toman desde la raiz del proyecto."""
    p = Path(p)
    return p if p.is_absolute() else PROJECT_ROOT / p


# Claves que ya no se usan: si estan en el config.json se ignoran con un WARNING (no es un error).
OBSOLETE_KEYS = (("stuck", "max_no_progress"), ("volver", "alt_targets"), ("approach", "blind_screen_px"),
                 ("pickup", "monster_gap_px"), ("pickup", "still_s"), ("pickup", "walk_timeout_s"))


def _merge(base: dict, over: dict) -> dict:
    """Merge profundo: `over` pisa a `base`; claves desconocidas se conservan."""
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _require_text(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} debe ser un texto no vacio")


def _require_dict(value: Any, name: str) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} debe ser un objeto")
    return value


ANCHORS = ("top-left", "top-center", "top-right", "center-left", "center", "center-right",
           "bottom-left", "bottom-center", "bottom-right")


def _validate_vision(vision: dict) -> None:
    zones = vision.get("exclusion_zones")
    if not isinstance(zones, list):
        raise ConfigError("vision.exclusion_zones debe ser una lista")
    for z in zones:
        if not isinstance(z, dict):
            raise ConfigError("cada zona de vision.exclusion_zones debe ser un objeto")
        _require_text(z.get("name"), "exclusion_zones[].name")
        if z.get("anchor") not in ANCHORS:
            raise ConfigError(f"zona {z.get('name')!r}: anchor invalido {z.get('anchor')!r} (usa {', '.join(ANCHORS)})")
        for k in ("dx", "dy", "w", "h"):
            v = z.get(k)
            if not isinstance(v, int) or isinstance(v, bool):
                raise ConfigError(f"zona {z['name']!r}: {k} debe ser un entero")
        if z["w"] <= 0 or z["h"] <= 0:
            raise ConfigError(f"zona {z['name']!r}: w y h deben ser > 0")
    m = _require_dict(vision.get("metin"), "vision.metin")
    _require_text(m.get("label_templates_dir"), "vision.metin.label_templates_dir")
    for k in ("hsv_lo", "hsv_hi", "group_kernel"):
        v = m.get(k)
        if not isinstance(v, list) or len(v) != (2 if k == "group_kernel" else 3):
            raise ConfigError(f"vision.metin.{k} tiene longitud invalida")
    off = vision.get("player_offset")
    if not isinstance(off, list) or len(off) != 2:
        raise ConfigError("vision.player_offset debe ser [dx, dy]")
    _validate_text_reading(vision)
    _validate_captcha_death(vision)
    _validate_items(vision)
    _validate_far_monsters(vision)


def _validate_far_monsters(vision: dict) -> None:
    """vision.far_metin y vision.monsters."""
    def nums(sec: dict, name: str, keys: tuple[str, ...], positive: tuple[str, ...] = ()) -> None:
        for k in keys:
            v = sec.get(k)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0 or (k in positive and v < 1):
                raise ConfigError(f"vision.{name}.{k} debe ser un numero " + (">= 1" if k in positive else ">= 0"))

    fm = _require_dict(vision.get("far_metin"), "vision.far_metin")
    for k, n in (("core_s", 2), ("smoke_hsv_lo", 3), ("smoke_hsv_hi", 3), ("group_kernel", 2)):
        _require_numbers(fm.get(k), f"vision.far_metin.{k}", n)
    nums(fm, "far_metin", ("core_h_max", "core_v_min", "min_core_pixels", "min_smoke_pixels",
                           "min_w", "max_w", "min_h", "max_h", "smoke_height_frac", "smoke_side_frac",
                           "smoke_side_px", "smoke_overlap_frac", "center_y_frac", "max_zone_overlap",
                           "exclude_overlap", "core_ref", "smoke_ref", "alt_min_bright_px", "alt_min_bright_frac",
                           "alt_bright_max_ratio", "alt_min_ground_frac"), ("min_core_pixels",))
    if not isinstance(fm.get("alt_enabled"), bool):
        raise ConfigError("vision.far_metin.alt_enabled debe ser true o false")
    for k in ("alt_bright_lo", "alt_bright_hi"):
        _require_numbers(fm.get(k), f"vision.far_metin.{k}", 3)
    if fm["core_ref"] <= 0 or fm["smoke_ref"] <= 0:
        raise ConfigError("vision.far_metin.core_ref y smoke_ref deben ser > 0")
    gr = _require_dict(vision.get("ground"), "vision.ground")
    nums(gr, "ground", ("horizon_frac", "focal_frac", "feet_dy_px", "min_dv_px"), ("min_dv_px",))
    if gr["focal_frac"] <= 0:
        raise ConfigError("vision.ground.focal_frac debe ser > 0")
    if gr["horizon_frac"] >= 0.5:
        raise ConfigError("vision.ground.horizon_frac debe ser < 0.5 (el horizonte esta en la mitad de arriba)")
    mo = _require_dict(vision.get("monsters"), "vision.monsters")
    for k, n in (("red_bgr", 3), ("green_bgr", 3), ("green_kernel", 2)):
        _require_numbers(mo.get(k), f"vision.monsters.{k}", n)
    nums(mo, "monsters", ("color_tol", "min_red_pixels", "min_green_pixels", "min_green_w", "max_green_w",
                          "min_green_h", "max_green_h", "probe_w", "max_label_w", "max_gap_px", "word_gap_px",
                          "max_zone_overlap", "near_radius_px"), ("min_red_pixels",))


def _validate_items(vision: dict) -> None:
    """vision.items."""
    it = _require_dict(vision.get("items"), "vision.items")
    _require_text(it.get("yang_template"), "vision.items.yang_template")
    for k, n in (("border_color", 3), ("yellow_lo", 3), ("yellow_hi", 3), ("box_w", 2), ("box_dy", 2)):
        _require_numbers(it.get(k), f"vision.items.{k}", n)
    if it["box_w"][0] > it["box_w"][1] or it["box_dy"][0] > it["box_dy"][1]:
        raise ConfigError("vision.items.box_w / box_dy: el minimo no puede superar al maximo")
    if not isinstance(it.get("min_edge_run"), int) or it["min_edge_run"] < 1 or it["min_edge_run"] % 2 == 0:
        raise ConfigError("vision.items.min_edge_run debe ser un entero impar >= 1")
    for k in ("border_min_frac", "white_min", "text_rows", "min_white_px", "yellow_noise_px", "owner_size_tol", "max_edges",
              "yang_min_score", "max_zone_overlap"):
        if not isinstance(it.get(k), (int, float)) or isinstance(it.get(k), bool) or it[k] < 0:
            raise ConfigError(f"vision.items.{k} debe ser un numero >= 0")


def _require_numbers(value: Any, name: str, n: int) -> None:
    if (not isinstance(value, list) or len(value) != n
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
        raise ConfigError(f"{name} debe ser una lista de {n} numeros")


def _validate_search(search: Any, name: str) -> None:
    s = _require_dict(search, name)
    if s.get("anchor") not in ANCHORS:
        raise ConfigError(f"{name}.anchor invalido {s.get('anchor')!r}")
    for k in ("dx", "dy", "w", "h"):
        if not isinstance(s.get(k), int) or isinstance(s.get(k), bool):
            raise ConfigError(f"{name}.{k} debe ser un entero")
    if s["w"] <= 0 or s["h"] <= 0:
        raise ConfigError(f"{name}: w y h deben ser > 0")


def _validate_text_reading(vision: dict) -> None:
    """vision.glyphs, vision.coords y vision.panel."""
    glyphs = _require_dict(vision.get("glyphs"), "vision.glyphs")
    for set_name in ("coords", "panel"):
        g = _require_dict(glyphs.get(set_name), f"vision.glyphs.{set_name}")
        _require_text(g.get("dir"), f"vision.glyphs.{set_name}.dir")
        _require_numbers(g.get("color"), f"vision.glyphs.{set_name}.color", 3)
        for k in ("color_tol", "min_score", "min_margin", "max_gap", "max_gap_after_comma"):
            if not isinstance(g.get(k), (int, float)) or isinstance(g.get(k), bool) or g[k] < 0:
                raise ConfigError(f"vision.glyphs.{set_name}.{k} debe ser un numero >= 0")
    c = _require_dict(vision.get("coords"), "vision.coords")
    _validate_search(c.get("search"), "vision.coords.search")
    for k in ("line_w", "line_h", "range"):
        _require_numbers(c.get(k), f"vision.coords.{k}", 2)
        if c[k][0] > c[k][1]:
            raise ConfigError(f"vision.coords.{k}: el minimo no puede superar al maximo")
    p = _require_dict(vision.get("panel"), "vision.panel")
    _validate_search(p.get("search"), "vision.panel.search")
    _require_text(p.get("name_templates_dir"), "vision.panel.name_templates_dir")
    _require_numbers(p.get("frame_color"), "vision.panel.frame_color", 3)
    _require_numbers(p.get("name_color"), "vision.panel.name_color", 3)
    _require_numbers(p.get("frame_h"), "vision.panel.frame_h", 2)
    for k in ("frame_min_w", "name_color_tol", "name_min_score", "name_min_margin", "hp_ratio_tol",
              "frame_x_tol", "frame_edge_merge"):
        if not isinstance(p.get(k), (int, float)) or isinstance(p.get(k), bool) or p[k] < 0:
            raise ConfigError(f"vision.panel.{k} debe ser un numero >= 0")
    nr = _require_dict(p.get("name_region"), "vision.panel.name_region")
    for k in ("x_from_left", "x_to_right", "dy", "h"):
        if not isinstance(nr.get(k), int) or isinstance(nr.get(k), bool):
            raise ConfigError(f"vision.panel.name_region.{k} debe ser un entero")
    bar = _require_dict(p.get("bar"), "vision.panel.bar")
    _require_numbers(bar.get("band"), "vision.panel.bar.band", 2)
    _require_numbers(bar.get("search_x"), "vision.panel.bar.search_x", 2)
    for k in ("cap_diff", "gold_gap", "width", "width_tol", "red_min_r", "red_max_gb", "red_min_rg"):
        if not isinstance(bar.get(k), (int, float)) or isinstance(bar.get(k), bool):
            raise ConfigError(f"vision.panel.bar.{k} debe ser un numero")
    num = _require_dict(p.get("number_region"), "vision.panel.number_region")
    for k in ("dx", "w", "dy", "h"):
        if not isinstance(num.get(k), int) or isinstance(num.get(k), bool):
            raise ConfigError(f"vision.panel.number_region.{k} debe ser un entero")


def _orient(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_seg(a, b, c) -> bool:
    return min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])


def _edge_conflict(p1, p2, p3, p4) -> str | None:
    """Relacion entre dos aristas no adyacentes: None, "cruce", "colineal" (solapadas) o "toque"."""
    o1, o2, o3, o4 = _orient(p1, p2, p3), _orient(p1, p2, p4), _orient(p3, p4, p1), _orient(p3, p4, p2)
    if o1 != 0 and o2 != 0 and o3 != 0 and o4 != 0:
        return "cruce" if (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0) else None
    touches = ((o1 == 0 and _on_seg(p1, p2, p3)) or (o2 == 0 and _on_seg(p1, p2, p4))
               or (o3 == 0 and _on_seg(p3, p4, p1)) or (o4 == 0 and _on_seg(p3, p4, p2)))
    if not touches:
        return None
    return "colineal" if o1 == o2 == o3 == o4 == 0 else "toque"


def validate_farm_zone(zone: dict) -> None:
    """Valida farm_zone (>= 3 vertices, sin repetidos, sin espigas ni aristas solapadas o cruzadas, area > 0)."""
    poly = zone.get("polygon")
    if not isinstance(poly, list) or len(poly) < 3:
        raise ConfigError("farm_zone.polygon debe tener al menos 3 vertices [x, y]")
    for v in poly:
        if (not isinstance(v, list) or len(v) != 2
                or not all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in v)):
            raise ConfigError(f"farm_zone.polygon: vertice invalido {v!r} (usa [x, y])")
    n = len(poly)
    for i in range(n):
        if poly[i] == poly[(i + 1) % n]:
            raise ConfigError(f"farm_zone.polygon: el vertice {i + 1} esta repetido consecutivamente ({poly[i]})")
    if all(_orient(poly[0], poly[1], v) == 0 for v in poly[2:]):
        raise ConfigError("farm_zone.polygon tiene area 0: todos los vertices estan alineados")
    for i in range(n):   # espiga: una arista vuelve exactamente sobre la anterior
        a, b, c = poly[i], poly[(i + 1) % n], poly[(i + 2) % n]
        if _orient(a, b, c) == 0 and (b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1]) < 0:
            raise ConfigError(f"farm_zone.polygon tiene una espiga en el vertice {(i + 1) % n + 1}: la arista "
                              "vuelve sobre si misma (aristas colineales solapadas)")
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue  # aristas adyacentes
            kind = _edge_conflict(poly[i], poly[(i + 1) % n], poly[j], poly[(j + 1) % n])
            if kind == "cruce":
                raise ConfigError(f"farm_zone.polygon se cruza consigo mismo (aristas {i + 1} y {j + 1}); "
                                  "ordena los vertices siguiendo el contorno")
            if kind == "colineal":
                raise ConfigError(f"farm_zone.polygon tiene aristas colineales solapadas ({i + 1} y {j + 1})")
            if kind == "toque":
                raise ConfigError(f"farm_zone.polygon: las aristas {i + 1} y {j + 1} se tocan sin ser contiguas")
    if sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n)) == 0:
        raise ConfigError("farm_zone.polygon tiene area 0")
    tol = zone.get("outside_tolerance")
    if not isinstance(tol, (int, float)) or isinstance(tol, bool) or tol < 0:
        raise ConfigError("farm_zone.outside_tolerance debe ser un numero >= 0")


def _validate_alarm(alarm: dict) -> None:
    """Rangos de la alarma (winsound.Beep acepta 37-32767 Hz)."""
    def num(key: str, lo: float, hi: float | None = None) -> None:
        v = alarm.get(key)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v < lo or (hi is not None and v > hi):
            raise ConfigError(f"alarm.{key} debe ser un numero entre {lo} y {hi if hi is not None else 'infinito'}")
    num("freq", 37, 32767)
    num("beep_ms", 1, 10000)
    num("pause_ms", 0, 60000)
    num("max_seconds", 0)


def _validate_captcha_death(vision: dict) -> None:
    """Campos de vision.captcha y vision.death."""
    c = _require_dict(vision.get("captcha"), "vision.captcha")
    for k in ("footer", "keypad"):
        a = _require_dict(c.get(k), f"vision.captcha.{k}")
        _require_text(a.get("template"), f"vision.captcha.{k}.template")
        _require_numbers(a.get("offset"), f"vision.captcha.{k}.offset", 2)
    _require_numbers(c.get("size"), "vision.captcha.size", 2)
    sr = _require_dict(c.get("search_region"), "vision.captcha.search_region")
    if sr.get("anchor") not in ANCHORS:
        raise ConfigError(f"vision.captcha.search_region.anchor invalido (usa {', '.join(ANCHORS)})")
    for k in ("dx", "dy", "w", "h"):
        v = sr.get(k)
        if not isinstance(v, int) or isinstance(v, bool) or (k in ("w", "h") and v <= 0):
            raise ConfigError(f"vision.captcha.search_region.{k} debe ser un entero" + (" > 0" if k in ("w", "h") else ""))
    d = _require_dict(vision.get("death"), "vision.death")
    _require_text(d.get("here_template"), "vision.death.here_template")
    _require_text(d.get("city_template"), "vision.death.city_template")
    s = _require_dict(d.get("search"), "vision.death.search")
    for k in ("w", "h"):
        if not isinstance(s.get(k), int) or s[k] <= 0:
            raise ConfigError(f"vision.death.search.{k} debe ser un entero > 0")
    for name, sec in (("captcha", c), ("death", d)):
        v = sec.get("min_score")
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 < v <= 1:
            raise ConfigError(f"vision.{name}.min_score debe estar entre 0 y 1")


# Acciones de `controls` que usa el codigo (bot/input/game_input.py y el bot).
REQUIRED_CONTROLS = ("attack_forward", "pickup", "mount", "buff", "camera_left", "camera_right",
                     "move_up", "move_left", "move_down", "move_right", "deselect")


def _check_key_spec(spec: str, name: str) -> None:
    """La combinacion tiene que parsear (sin partes vacias) y usar teclas conocidas."""
    try:
        keys = parse_keys(spec)
    except ValueError as e:
        raise ConfigError(f"{name}: {e}") from e
    for k in keys:
        if k not in VALID_KEYS:
            raise ConfigError(f"{name}: la tecla {k!r} (de {spec!r}) no existe")


def _validate_controls_safety_input(cfg: dict) -> None:
    """controls, safety e input."""
    controls = _require_dict(cfg.get("controls"), "controls")
    if not controls:
        raise ConfigError("controls no puede estar vacio")
    missing = [a for a in REQUIRED_CONTROLS if a not in controls]
    if missing:
        raise ConfigError(f"controls: faltan acciones: {', '.join(missing)}")
    for k, v in controls.items():
        _require_text(v, f"controls.{k}")
        _check_key_spec(v, f"controls.{k}")
    s = _require_dict(cfg.get("safety"), "safety")
    _require_text(s.get("panic_key"), "safety.panic_key")
    _require_text(s.get("pause_key"), "safety.pause_key")
    _check_key_spec(s["panic_key"], "safety.panic_key")
    _check_key_spec(s["pause_key"], "safety.pause_key")
    if s["panic_key"].strip().lower() == s["pause_key"].strip().lower():
        raise ConfigError("safety.panic_key y safety.pause_key deben ser distintas")
    reserved = {s["panic_key"].strip().lower(), s["pause_key"].strip().lower()}
    for k, v in controls.items():
        if reserved & set(parse_keys(v)):
            raise ConfigError(f"controls.{k} ({v!r}) usa la tecla de panico o de pausa: "
                              "el bot se pararia con su propio input")
    for k in ("corner_px", "corner_poll_ms", "countdown_s", "hard_kill_after_s"):
        v = s.get(k)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
            raise ConfigError(f"safety.{k} debe ser un numero >= 0")
    if s["corner_poll_ms"] <= 0:
        raise ConfigError("safety.corner_poll_ms debe ser > 0")
    if s["hard_kill_after_s"] <= 0:
        raise ConfigError("safety.hard_kill_after_s debe ser > 0")
    i = _require_dict(cfg.get("input"), "input")
    _require_text(i.get("backend"), "input.backend")
    v = i.get("click_jitter_px")
    if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
        raise ConfigError("input.click_jitter_px debe ser un numero >= 0")
    for k in ("delay_ms", "key_hold_ms", "move_settle_ms"):
        _require_numbers(i.get(k), f"input.{k}", 2)
        if i[k][0] < 0 or i[k][0] > i[k][1]:
            raise ConfigError(f"input.{k} debe ser [min, max] con 0 <= min <= max")


def _validate_tracking_navigation(cfg: dict) -> None:
    """coords_filter, stuck, loop, attack, pickup, explore, captcha_wait y navigation."""
    def nums(sec: str, keys: tuple[str, ...], positive: tuple[str, ...] = ()) -> dict:
        d = _require_dict(cfg.get(sec), sec)
        for k in keys:
            v = d.get(k)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0 or (k in positive and v <= 0):
                raise ConfigError(f"{sec}.{k} debe ser un numero " + (">" if k in positive else ">=") + " 0")
        return d
    nums("coords_filter", ("max_speed", "slack", "accept_after", "consistency_dist", "max_dt_s", "history"),
         ("max_speed", "accept_after", "max_dt_s", "history"))
    nums("stuck", ("window_s", "min_move", "max_attempts", "unstuck_radius_px", "back_key_s",
                   "resolve_dist", "monsters_min", "space_s", "forward_s", "repeat_max", "repeat_window_s", "look_steps",
                   "look_step_s", "min_dir_sep_deg", "dir_step_deg", "episode_ttl_s", "space_fail_max", "forward_chunk_s",
                   "progress_tol", "memory_dirs", "back_s", "look_walk_s"),
         ("window_s", "min_move", "max_attempts", "unstuck_radius_px", "back_key_s", "resolve_dist",
          "repeat_max", "repeat_window_s", "look_step_s", "dir_step_deg", "episode_ttl_s", "space_fail_max",
          "forward_chunk_s", "back_s", "look_walk_s"))
    nums("approach", ("max_seconds", "ignore_minutes", "ignore_radius", "lost_cycles", "label_match_px", "match_px",
                      "label_exclude_down_px", "blind_player_radius", "max_tries_per_spot",
                      "far_monsters_n", "far_monsters_px", "hover_wait_s", "hover_label_px", "hover_tries"),
         ("max_seconds", "ignore_minutes", "ignore_radius", "lost_cycles", "blind_player_radius",
          "max_tries_per_spot", "far_monsters_n", "far_monsters_px", "hover_label_px", "hover_tries"))
    nums("center", ("arrive_dist",), ("arrive_dist",))
    nums("loop", ("cycle_ms", "captcha_full_search_every", "hold_chunk_s"), ("captcha_full_search_every", "hold_chunk_s"))
    nums("attack", ("confirm_timeout_s", "hp_stall_s", "max_reclicks", "max_seconds", "broken_confirm_cycles",
                    "discard_minutes", "discard_radius", "discard_error_frac", "max_wrong_selections", "blocked_s",
                    "circle_ratio", "circle_min_path", "wrong_monsters_min", "blocked_window_frac", "walk_max_s",
                    "unstick_space_s"),
         ("confirm_timeout_s", "hp_stall_s", "max_seconds", "broken_confirm_cycles", "discard_minutes", "discard_radius",
          "max_wrong_selections", "blocked_s", "circle_ratio", "blocked_window_frac", "walk_max_s", "unstick_space_s"))
    nums("pickup", ("press_interval_ms", "empty_confirm_cycles", "max_presses", "max_seconds", "mount_wait_s", "look_s",
                    "arrive_px", "item_below_label_px", "step_s", "min_step_s", "full_step_px", "stall_steps",
                    "stall_gain_px", "track_px", "tries_per_item", "same_spot_u"),
         ("empty_confirm_cycles", "max_presses", "max_seconds", "look_s", "arrive_px", "step_s", "min_step_s",
          "full_step_px", "stall_steps", "track_px", "tries_per_item", "same_spot_u"))
    if not isinstance(cfg["pickup"].get("dismount"), bool):
        raise ConfigError("pickup.dismount debe ser true o false")
    nums("explore", ("camera_steps", "camera_step_s", "walk_timeout_s", "border_margin", "leg_s", "leg_units",
                     "max_without_metin_s", "min_leg_sep_deg", "recent_legs", "visited_keep", "leg_tiebreak_units",
                     "settle_s", "settle_frames", "edge_return_u"),
         ("camera_step_s", "walk_timeout_s", "leg_s", "leg_units", "max_without_metin_s", "visited_keep", "settle_frames"))
    nums("volver", ("max_seconds",), ("max_seconds",))
    nums("captcha_wait", ("poll_s", "clear_confirm"), ("poll_s", "clear_confirm"))
    n = nums("navigation", ("probe_radius_px", "arrive_dist", "explore_margin", "min_segment_px", "min_segment_game",
                            "min_sin", "fit_segments", "max_ratio_error", "relearn_after_rejects", "phi_smooth_deg",
                            "angle_step_deg", "max_angle_deg", "settle_s", "segment_timeout_s", "avoid_metin_px",
                            "external_rotation_deg", "heading_tolerance_deg", "zone_guard_margin", "max_walk_px", "brake_px",
                            "probe_measure_factor", "probe_turn_jitter_deg"),
             ("probe_radius_px", "arrive_dist", "min_segment_px", "fit_segments", "angle_step_deg", "relearn_after_rejects",
              "settle_s", "segment_timeout_s", "external_rotation_deg", "heading_tolerance_deg", "max_walk_px", "brake_px",
              "probe_measure_factor"))
    if n.get("mode") not in ("teclado", "clic"):
        raise ConfigError("navigation.mode debe ser \"teclado\" o \"clic\"")
    kb = _require_dict(n.get("keyboard"), "navigation.keyboard")
    for k in ("chunk_s", "probe_s", "align_deg", "turn_deg_per_s", "max_turn_deg", "walk_speed", "min_step_s", "edge_measure_u"):
        v = kb.get(k)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
            raise ConfigError(f"navigation.keyboard.{k} debe ser un numero > 0")
    if kb.get("view_right_key") not in ("camera_left", "camera_right"):
        raise ConfigError("navigation.keyboard.view_right_key debe ser \"camera_left\" o \"camera_right\"")
    _require_numbers(n.get("click_radius_px"), "navigation.click_radius_px", 2)
    cr = n["click_radius_px"]
    if cr[0] <= 0 or cr[0] > cr[1]:
        raise ConfigError("navigation.click_radius_px debe ser [min, max] con 0 < min <= max")
    _require_numbers(n.get("scale_hint"), "navigation.scale_hint", 2)
    sh = n["scale_hint"]
    if sh[0] <= 0 or sh[0] > sh[1]:
        raise ConfigError("navigation.scale_hint debe ser [menor, mayor] con 0 < menor <= mayor")
    f = n.get("shorten_factors")
    if (not isinstance(f, list) or not f
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v <= 1 for v in f)):
        raise ConfigError("navigation.shorten_factors debe ser una lista no vacia de numeros en (0, 1]")


def validate_config(cfg: dict) -> None:
    """Validacion minima; lanza ConfigError."""
    _require_text(cfg.get("window_title"), "window_title")
    size = cfg.get("expected_client_size")
    if (not isinstance(size, list) or len(size) != 2
            or not all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in size)):
        raise ConfigError("expected_client_size debe ser una lista de 2 enteros > 0, ej. [1858, 1057]")
    window = _require_dict(cfg.get("window"), "window")
    for key in ("settle_ms", "unobstructed_poll_ms"):
        v = window.get(key)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
            raise ConfigError(f"window.{key} debe ser un numero >= 0")
    log = _require_dict(cfg.get("log"), "log")
    level =log.get("level")
    if not isinstance(level, str) or not isinstance(logging.getLevelName(level.upper()), int):
        raise ConfigError(f"log.level invalido: {level!r} (usa DEBUG, INFO, WARNING, ERROR, CRITICAL)")
    _require_text(log.get("dir"), "log.dir")
    _require_text(log.get("file"), "log.file")
    sp = _require_dict(cfg.get("spawns"), "spawns")
    if not isinstance(sp.get("enabled"), bool):
        raise ConfigError("spawns.enabled debe ser true o false")
    _require_text(sp.get("file"), "spawns.file")
    for k in ("merge_u", "respawn_s", "revisit_s", "arrive_u", "max_walk_s"):
        v = sp.get(k)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
            raise ConfigError(f"spawns.{k} debe ser un numero > 0")
    debug = _require_dict(cfg.get("debug"), "debug")
    if not isinstance(debug.get("enabled"), bool):
        raise ConfigError("debug.enabled debe ser true o false")
    _require_text(debug.get("dir"), "debug.dir")
    for k in ("explore_every_s", "max_images", "jpeg_quality"):
        v = debug.get(k)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
            raise ConfigError(f"debug.{k} debe ser un numero > 0")
    v = debug.get("reason_memory_s")
    if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
        raise ConfigError("debug.reason_memory_s debe ser un numero >= 0")
    per = _require_dict(debug.get("max_per_kind"), "debug.max_per_kind")
    for k, v in per.items():
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise ConfigError(f"debug.max_per_kind.{k} debe ser un entero >= 0")
    names = cfg.get("target_metin_names")
    if not isinstance(names, list) or not names:
        raise ConfigError("target_metin_names debe ser una lista no vacia de textos")
    for n in names:
        _require_text(n, "target_metin_names[]")
    validate_farm_zone(_require_dict(cfg.get("farm_zone"), "farm_zone"))
    _validate_alarm(_require_dict(cfg.get("alarm"), "alarm"))
    _validate_controls_safety_input(cfg)
    _validate_tracking_navigation(cfg)
    _validate_vision(_require_dict(cfg.get("vision"), "vision"))


def save_config(cfg: dict, path: str | Path = "config.json") -> None:
    """Guarda la config como JSON indentado UTF-8."""
    atomic_write_text(resolve_path(path), json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")


def load_config(path: str | Path = "config.json") -> dict:
    """Lee config.json y lo mezcla sobre DEFAULTS. Si no existe, lo crea."""
    p = resolve_path(path)
    if not p.exists():
        logger.warning("No existe %s: se crea con los valores por defecto", p)
        cfg = copy.deepcopy(DEFAULTS)
        save_config(cfg, p)
        return cfg
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"{p} no es JSON valido (linea {e.lineno}, columna {e.colno}): {e.msg}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{p}: la raiz debe ser un objeto JSON")
    if "target_metin_name" in data:
        old = data.pop("target_metin_name")
        if "target_metin_names" not in data:
            logger.warning("target_metin_name esta obsoleto: usa target_metin_names (lista). Se convierte a [%r]", old)
            data["target_metin_names"] = [old]
    for sec, key in OBSOLETE_KEYS:
        if isinstance(data.get(sec), dict) and key in data[sec]:
            data[sec].pop(key)
            logger.warning("%s.%s esta obsoleta y se ignora (borrala de %s)", sec, key, p.name)
    cfg = _merge(DEFAULTS, data)
    validate_config(cfg)
    return cfg
