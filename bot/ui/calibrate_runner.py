"""Logica pura (sin tkinter) para correr `calibrate.py metin` / `calibrate.py zona` desde la interfaz: armado de la linea
de comandos, validacion del nombre, parseo de la salida (preguntas @@ASK) y los textos paso a paso de cada ventana."""
from __future__ import annotations

from pathlib import Path

from bot.config import PROJECT_ROOT
from bot.ui import controller as ctl
from bot.vision.metin import slugify

CALIBRATE = PROJECT_ROOT / "calibrate.py"
ANSWER_YES, ANSWER_NO = "s", "n"

METIN_TITLE = "Agregar tipo de metin nuevo"
METIN_STEPS = (
    "1. Poné el juego al frente y buscá un metin del tipo nuevo que se vea bien, con su etiqueta naranja visible "
    "(sin cartel ni ventana encima).",
    "2. Poné el mouse cerca de esa etiqueta y apretá F9. Acá abajo aparece la etiqueta que eligió el programa: "
    "respondé Sí si es la correcta o No para elegir otra (mové el mouse y F9 de nuevo).",
    "3. Opcional: hacé clic en el metin para seleccionarlo (que se vea el panel de arriba) y apretá F9 otra vez para "
    "guardar también el nombre del panel. F10 saltea este paso.",
    "4. Respondé Sí a la pregunta de guardar. Al terminar se actualiza la lista de tipos.",
    "F12 cancela todo sin guardar. Esta ventana no manda teclas ni clics al juego.",
)

ZONA_TITLE = "Rehacer la zona de farmeo"
ZONA_STEPS = (
    "1. Poné el juego al frente. Parate en una esquina de la zona que querés farmear, con margen: "
    "no te pegues a las paredes ni al borde del mapa, dejá algo de distancia.",
    "2. Apretá F9 para marcar esa esquina. Caminá a la esquina siguiente y apretá F9 de nuevo.",
    "3. Recorré siempre las esquinas en el mismo sentido (todas en sentido del reloj, o todas al revés), sin saltear ni "
    "cruzar: el contorno tiene que dar la vuelta completa.",
    "4. F8 deshace la última esquina marcada. F10 termina (hacen falta al menos 3 esquinas). F12 cancela sin guardar.",
    "5. Al terminar se muestra el resultado y se pregunta si guardar: respondé acá abajo. Se guarda en el perfil de "
    "zona elegido (con copia .bak) y se redibuja la zona.",
)


def validate_metin_name(name: str, existing: list[str]) -> str | None:
    """None si el nombre sirve para un tipo nuevo; si no, el mensaje. `existing` = nombres de los tipos que ya hay."""
    name = name.strip()
    if not name or not slugify(name):
        return "Escribí un nombre para el tipo de metin."
    if name.startswith("-"):
        return "El nombre no puede empezar con un guion."
    if slugify(name) in {slugify(n) for n in existing}:
        return "Ya existe un tipo con ese nombre."
    return None


def build_calibrate_command(sub: str, *, nombre: str | None = None, servidor: str = "", personaje: str = "", zona: str = "",
                            config: str | None = None, python: str | None = None,
                            script: str | Path = CALIBRATE) -> list[str]:
    """Linea de comandos de `calibrate.py <metin|zona> --ui --en-perfil` con los perfiles elegidos. Sin `--si`: las
    preguntas las responde la persona desde la ventana."""
    if sub not in ("metin", "zona"):
        raise ValueError(f"subcomando no soportado desde la interfaz: {sub}")
    cmd = [python or ctl.gui_python(), "-u", str(script), sub]
    if sub == "metin":
        if not nombre or not nombre.strip():
            raise ValueError("falta el nombre del metin")
        cmd.append(nombre.strip())
    cmd += ["--ui", "--en-perfil"]
    if config:
        cmd += ["--config", config]
    for flag, value in (("--servidor", servidor), ("--personaje", personaje), ("--zona", zona)):
        if value:
            cmd += [flag, value]
    return cmd


def parse_calibrate_line(raw: str) -> tuple[str, str]:
    """Linea de la salida de calibrate.py -> ('ask', pregunta) o ('log', texto)."""
    text = raw.rstrip("\r\n")
    if text.startswith(ctl.ASK_TAG):
        return "ask", text[len(ctl.ASK_TAG):].strip()
    return "log", text


def is_yes_no(question: str) -> bool:
    """True si la pregunta es de s/n (se responde con botones Si/No)."""
    return question.rstrip().lower().endswith("(s/n):") or question.rstrip().lower().endswith("(s/n)")


def question_text(question: str) -> str:
    """Pregunta sin el sufijo '(s/n):' para mostrar."""
    q = question.rstrip()
    for suffix in ("(s/n):", "(s/n)"):
        if q.lower().endswith(suffix):
            return q[:-len(suffix)].rstrip()
    return q
