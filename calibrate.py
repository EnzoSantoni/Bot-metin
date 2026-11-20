"""Herramienta de calibracion (pasiva: lee la pantalla y escucha hotkeys, NUNCA manda teclas ni clics al juego).

Uso: .venv\\Scripts\\python.exe calibrate.py <ventana|zona|velocidad|metin|tono|personaje|perfiles|zonas|camara|resumen> [opciones]
Cada valor se guarda en el perfil activo que le corresponde (ventana -> servidor, zona -> zona, velocidad -> personaje,
metin -> plantillas + zona, camara -> personaje), o en config.json si ese valor esta definido ahi (lo pisa). Los que escriben piden confirmacion (s/n, o --si), hacen config.json.bak y validan antes de guardar.
F12 o Ctrl+C cancelan sin guardar.

Opciones para la interfaz grafica (la consola no las necesita):
  --ui          las preguntas salen como una linea "@@ASK <pregunta>" y la respuesta se lee de stdin (una linea).
  --en-perfil   guarda en el perfil activo (p. ej. zona -> perfil de zona, con .bak) aunque config.json defina ese valor.
"""
import argparse
import sys

from bot import calibration as cal
from bot.config import ConfigError
from bot.profiles import build_config
from bot.ui.controller import ASK_TAG

NEEDS_GAME = {"zona", "velocidad", "metin", "tono", "personaje", "zonas", "camara"}   # subcomandos que capturan el juego


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default="config.json", help="ruta de la config (default: config.json)")
    common.add_argument("--si", action="store_true", help="no pedir confirmacion antes de guardar")
    common.add_argument("--servidor", default=None, help="perfil de servidor (default: el de config.json o Guabina)")
    common.add_argument("--personaje", default=None, help="perfil de personaje (default: el de config.json o Farfarfa)")
    common.add_argument("--zona", default=None, help="perfil de zona (default: el de config.json o Ciudad 1)")
    common.add_argument("--ui", action="store_true", help="modo interfaz: preguntas como '@@ASK ...' y respuesta por stdin")
    common.add_argument("--en-perfil", action="store_true",
                        help="guardar en el perfil activo aunque config.json defina ese valor (lo usa la interfaz)")
    ap = argparse.ArgumentParser(description="Calibracion pasiva de metin-bot (no manda input al juego).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ventana", parents=[common], help="elegir la ventana del juego y medir su cliente")
    p.add_argument("--filtro", default=None, help="solo ventanas cuyo titulo contenga este texto")
    p = sub.add_parser("zona", parents=[common], help="marcar los vertices de la zona de farmeo (F9/F8/F10)")
    p.add_argument("--tolerancia", type=float, default=None, help="tambien cambia farm_zone.outside_tolerance")
    p = sub.add_parser("velocidad", parents=[common], help="medir la velocidad a caballo y proponer coords_filter.max_speed")
    p.add_argument("--segundos", type=float, default=10.0, help="duracion de la medicion (default: 10)")
    p = sub.add_parser("metin", parents=[common], help="agregar un tipo de metin (plantillas de etiqueta y panel)")
    p.add_argument("nombre", help='nombre del metin, ej. "Metin de Oscuridad"')
    p = sub.add_parser("personaje", parents=[common], help="crear el perfil de un personaje con su nombre amarillo (F9)")
    p.add_argument("nombre", help='nombre del personaje, ej. "Farfarfa"')
    p.add_argument("--clase", default=None, help='clase del personaje, ej. "guerrero" o "sura" (texto libre)')
    p = sub.add_parser("perfiles", parents=[common], help="listar los perfiles o migrar config.json a los perfiles activos")
    p.add_argument("accion", choices=("listar", "migrar"))
    p = sub.add_parser("tono", parents=[common], help="medir el tono de la piedra de un metin (pista opcional far_metin.hue_hints)")
    p.add_argument("nombre", help='nombre del metin, ej. "Metin de Oscuridad"')
    p.add_argument("--capturas", type=int, default=3, help="maximo de capturas (default: 3)")
    sub.add_parser("zonas", parents=[common], help="guardar debug/calibrate_zonas.png para revisar las zonas excluidas")
    sub.add_parser("camara", parents=[common], help="medir el angulo vertical de la camara (horizonte) y guardarlo como referencia")
    sub.add_parser("resumen", parents=[common], help="valores que siguen siendo estimaciones y como medirlos")
    return ap


def ui_ask(prompt: str) -> str:
    """Pregunta para la interfaz: una linea @@ASK con la pregunta y la respuesta en la proxima linea de stdin."""
    print(ASK_TAG + prompt.strip(), flush=True)
    line = sys.stdin.readline()
    if not line:
        raise EOFError
    return line


def make_session(args: argparse.Namespace, cfg: dict) -> cal.Session:
    s = cal.Session(cfg=cfg, config_path=args.config, yes=args.si, prefer_profile=args.en_perfil)
    if args.ui:
        s.ask = ui_ask
    if args.cmd == "ventana":
        from bot.capture import enable_dpi_awareness, list_windows
        enable_dpi_awareness()
        s.list_windows_fn = list_windows
    elif args.cmd in NEEDS_GAME:
        from bot.capture import GameWindow
        s.window = GameWindow(cfg["window_title"], tuple(cfg["expected_client_size"]),
                              cfg["window"]["settle_ms"], cfg["window"]["unobstructed_poll_ms"])
    return s


def run(args: argparse.Namespace, s: cal.Session) -> int:
    if args.cmd == "ventana":
        return cal.cmd_ventana(s, args.filtro)
    if args.cmd == "zona":
        return cal.cmd_zona(s, args.tolerancia)
    if args.cmd == "velocidad":
        return cal.cmd_velocidad(s, args.segundos)
    if args.cmd == "metin":
        return cal.cmd_metin(s, args.nombre)
    if args.cmd == "personaje":
        return cal.cmd_personaje(s, args.nombre, args.clase)
    if args.cmd == "perfiles":
        return cal.cmd_perfiles(s, args.accion)
    if args.cmd == "tono":
        return cal.cmd_tono(s, args.nombre, args.capturas)
    if args.cmd == "zonas":
        return cal.cmd_zonas(s)
    if args.cmd == "camara":
        return cal.cmd_camara(s)
    return cal.cmd_resumen(s)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = build_config(args.config, args.servidor, args.personaje, args.zona, warn=False)
        for o in cfg["_profiles"]["overrides"] if args.cmd != "perfiles" else []:   # `perfiles listar` ya los muestra
            print(f"Aviso: {o['message']}")
        return run(args, make_session(args, cfg))
    except (cal.Cancelled, KeyboardInterrupt) as e:
        print(f"\nCancelado{': ' + str(e) if str(e) else ''}. No se guardo nada.")
        return 1
    except ConfigError as e:
        print(f"Config invalida: {e}")
        return 1
    except Exception as e:  # noqa: BLE001 - mensajes claros para los errores de ventana
        from bot.capture import WindowMinimizedError, WindowNotFoundError
        if isinstance(e, (WindowNotFoundError, WindowMinimizedError)):
            print(f"Problema con la ventana del juego: {e}")
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
