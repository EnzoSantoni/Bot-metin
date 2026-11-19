"""Lista las ventanas abiertas para identificar el titulo del juego.

Uso: .venv\\Scripts\\python.exe list_windows.py [filtro]
"""
import sys

from bot.capture import enable_dpi_awareness, get_dpi_scale, list_windows


def main() -> None:
    enable_dpi_awareness()
    flt = sys.argv[1] if len(sys.argv) > 1 else None
    for w in list_windows(flt):
        r = w.client_rect_on_screen
        print(f'hwnd={w.hwnd:<10} "{w.title}"  cliente=({r.x},{r.y}) {r.w}x{r.h}  '
              f'dpi={get_dpi_scale(w.hwnd):.2f}  minimizada={w.minimized}')
    print("\nCopiá el título (o una parte única) a `window_title` en config.json.")


if __name__ == "__main__":
    main()
