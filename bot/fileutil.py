"""Utilidades de archivos compartidas. Sin dependencias del resto del bot."""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def atomic_write_text(path: str | Path, text: str, encoding: str = "utf-8") -> None:
    """Escribe `<archivo>.tmp` y lo mueve con `os.replace`: si algo falla el original queda intacto y no queda el .tmp.
    Propaga el error (OSError) al llamador."""
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    try:
        with open(tmp, "w", encoding=encoding, newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            logger.debug("No se pudo borrar %s", tmp)
        raise
