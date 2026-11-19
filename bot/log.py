"""Configuracion de logging (consola + archivo rotativo)."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from bot.config import resolve_path

FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_MARK = "_metin_bot_handler"


def setup_logging(cfg: dict) -> None:
    """Configura el logger raiz. Idempotente."""
    lc = cfg["log"]
    log_dir = resolve_path(lc["dir"])
    log_dir.mkdir(parents=True, exist_ok=True)
    # Crear los handlers nuevos ANTES de quitar los viejos: si falla el archivo,
    # el logger no queda sin salida.
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stderr),
        RotatingFileHandler(log_dir / lc["file"], maxBytes=5 * 1024 * 1024,
                            backupCount=3, encoding="utf-8"),
    ]
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, _MARK, False):
            root.removeHandler(h)
            h.close()
    root.setLevel(logging.getLevelName(str(lc["level"]).upper()))
    fmt = logging.Formatter(FORMAT)
    for h in handlers:
        h.setFormatter(fmt)
        setattr(h, _MARK, True)
        root.addHandler(h)
