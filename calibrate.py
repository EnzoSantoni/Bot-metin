"""Herramienta de calibracion (pasiva: lee la pantalla y escucha hotkeys, NUNCA manda teclas ni clics al juego).

Uso: .venv\\Scripts\\python.exe calibrate.py <ventana|zona|velocidad|metin|zonas|camara|resumen> [opciones]
Los que escriben config.json piden confirmacion (s/n, o --si), hacen config.json.bak y validan antes de guardar.
F12 o Ctrl+C cancelan sin guardar.
"""
import argparse
import sys

from bot import calibration as cal
from bot.config import ConfigError, load_config

NEEDS_GAME = {"zona", "velocidad", "metin", "zonas", "camara"}   # subcomandos que capturan el juego


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default="config.json", help="ruta de la config (default: config.json)")
    common.add_argument("--si", action="store_true", help="no pedir confirmacion antes de guardar")
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
    sub.add_parser("zonas", parents=[common], help="guardar debug/calibrate_zonas.png para revisar las zonas excluidas")
    sub.add_parser("camara", parents=[common], help="medir el angulo vertical de la camara (horizonte) y guardarlo como referencia")
    sub.add_parser("resumen", parents=[common], help="valores que siguen siendo estimaciones y como medirlos")
    return ap


def make_session(args: argparse.Namespace, cfg: dict) -> cal.Session:
    s = cal.Session(cfg=cfg, config_path=args.config, yes=args.si)
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
    if args.cmd == "zonas":
        return cal.cmd_zonas(s)
    if args.cmd == "camara":
        return cal.cmd_camara(s)
    return cal.cmd_resumen(s)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
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
