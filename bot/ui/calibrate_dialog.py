"""Ventana tkinter que corre `calibrate.py` (metin, zona, personaje, migrar) como subproceso: instrucciones paso a paso, salida en vivo y las
preguntas del script respondibles desde la ventana (Si/No o texto por stdin). La logica pura esta en calibrate_runner.py."""
from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk
from typing import Callable

from bot.ui import calibrate_runner as cr
from bot.ui import controller as ctl
from bot.ui.widgets import (COLOR_ERROR, COLOR_WARN, GAP, GAP_S, PAD, WRAP, ValidatedEntry, add_tooltip, note)

logger = logging.getLogger(__name__)

POLL_MS = 100
OUTPUT_MAX_LINES = 400
STOP_WAIT_S = 2.0


class CalibrateDialog:
    """Ventana de calibracion. `command_factory(nombre)` arma la linea de comandos al apretar Empezar. `name_validator`
    (si se pasa) agrega un campo de nombre obligatorio. `on_finish(codigo)` se llama al terminar el proceso."""

    def __init__(self, parent: tk.Misc, title: str, steps: tuple[str, ...], command_factory: Callable[[str], list[str]],
                 on_finish: Callable[[int | None], None], *, name_label: str = "",
                 name_validator: Callable[[str], str | None] | None = None, process: ctl.BotProcess | None = None,
                 name_tooltip: str = "", autostart: bool = False) -> None:
        self.command_factory, self.on_finish = command_factory, on_finish
        self.proc = process or ctl.BotProcess()
        self.started = False
        self.finished = False
        self.pending_question: str | None = None
        self._after_id: str | None = None
        self.top = tk.Toplevel(parent)
        self.top.title(title)
        self.top.transient(parent)
        self.top.minsize(560, 520)
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        f = ttk.Frame(self.top, padding=PAD)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=title, style="Big.TLabel").pack(anchor="w")
        for step in steps:
            note(f, step, style="TLabel", wrap=WRAP + 80).pack(fill="x", pady=(GAP_S, 0))
        self.name_entry: ValidatedEntry | None = None
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(GAP, 0))
        if name_validator is not None:
            ttk.Label(row, text=name_label or "Nombre:").pack(side="left")
            self.name_entry = ValidatedEntry(row, name_validator, width=32, on_change=self._refresh_start,
                                             tooltip=name_tooltip or "Nombre del tipo de metin tal como se ve en el juego, por ejemplo Metin de Hielo.")
            self.name_entry.pack(side="left", padx=GAP)
        self.btn_start = ttk.Button(row, text="Empezar", command=self.start)
        self.btn_start.pack(side="left")
        add_tooltip(self.btn_start, "Arranca la calibración. Después poné el juego al frente y seguí los pasos.")
        self.btn_close = ttk.Button(row, text="Cancelar", command=self.close)
        self.btn_close.pack(side="right")
        add_tooltip(self.btn_close, "Corta la calibración sin guardar nada.")
        out = ttk.Frame(f)
        out.pack(fill="both", expand=True, pady=GAP)
        self.output = tk.Text(out, height=12, wrap="word", state="disabled", font=("Consolas", 9))
        sb = ttk.Scrollbar(out, command=self.output.yview)
        self.output.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.output.pack(side="left", fill="both", expand=True)
        self.output.tag_configure("ERROR", foreground=COLOR_ERROR)
        self.output.tag_configure("ASK", foreground=COLOR_WARN, font=("Consolas", 9, "bold"))
        self.question = ttk.LabelFrame(f, text="El programa pregunta", padding=PAD)
        self.question_var = tk.StringVar(value="")
        ttk.Label(self.question, textvariable=self.question_var, wraplength=WRAP + 80, justify="left").pack(fill="x")
        self.answer_row = ttk.Frame(self.question)
        self.answer_row.pack(fill="x", pady=(GAP, 0))
        self.btn_yes = ttk.Button(self.answer_row, text="Sí", command=lambda: self.answer(cr.ANSWER_YES))
        self.btn_no = ttk.Button(self.answer_row, text="No", command=lambda: self.answer(cr.ANSWER_NO))
        self.text_var = tk.StringVar(value="")
        self.text_entry = ttk.Entry(self.answer_row, textvariable=self.text_var, width=30)
        self.btn_send = ttk.Button(self.answer_row, text="Enviar", command=lambda: self.answer(self.text_var.get()))
        self.text_entry.bind("<Return>", lambda _e: self.answer(self.text_var.get()))
        self._refresh_start()
        if autostart:
            self.start()

    # ------------------------------------------------------------------ estado
    def _refresh_start(self) -> None:
        if not hasattr(self, "btn_start") or not hasattr(self, "name_entry"):   # se llama durante la construccion
            return
        ok = not self.started and (self.name_entry is None or self.name_entry.valid)
        self.btn_start.configure(state="normal" if ok else "disabled")
        if self.name_entry is not None:
            self.name_entry.entry.configure(state="disabled" if self.started else "normal")

    def start(self) -> None:
        """Arranca el subproceso (no hace nada si ya arranco o el nombre es invalido)."""
        if self.started or (self.name_entry is not None and not self.name_entry.valid):
            return
        name = self.name_entry.get().strip() if self.name_entry is not None else ""
        try:
            cmd = self.command_factory(name)
            self.proc.start(cmd)
        except (OSError, ValueError, RuntimeError) as e:
            self._write(f"No se pudo iniciar la calibración: {e}", "ERROR")
            return
        self.started = True
        self._refresh_start()
        self._write("--- calibración iniciada: poné el juego al frente y seguí los pasos ---")
        self._after_id = self.top.after(POLL_MS, self._poll)

    # ------------------------------------------------------------------ salida y preguntas
    def _write(self, text: str, tag: str = "") -> None:
        o = self.output
        o.configure(state="normal")
        o.insert("end", text + "\n", tag or ())
        extra = int(o.index("end-1c").split(".")[0]) - OUTPUT_MAX_LINES
        if extra > 1:
            o.delete("1.0", f"{extra}.0")
        o.configure(state="disabled")
        o.see("end")

    def handle_line(self, raw: str) -> None:
        """Una linea de la salida del script: pregunta (@@ASK) o texto para mostrar."""
        kind, text = cr.parse_calibrate_line(raw)
        if kind == "ask":
            self.show_question(text)
        elif text.strip():
            self._write(text, "ERROR" if ctl.log_level(text) in ("ERROR", "CRITICAL") else "")

    def show_question(self, prompt: str) -> None:
        self.pending_question = prompt
        self._write("? " + cr.question_text(prompt), "ASK")
        self.question_var.set(cr.question_text(prompt))
        for w in self.answer_row.winfo_children():
            w.pack_forget()
        if cr.is_yes_no(prompt):
            self.btn_yes.pack(side="left", padx=GAP_S)
            self.btn_no.pack(side="left", padx=GAP_S)
        else:
            self.text_var.set("")
            self.text_entry.pack(side="left", padx=GAP_S)
            self.btn_send.pack(side="left", padx=GAP_S)
            self.text_entry.focus_set()
        self.question.pack(fill="x")

    def answer(self, text: str) -> bool:
        """Manda la respuesta a la pregunta pendiente por stdin. False si no hay pregunta o el proceso ya no escucha."""
        if self.pending_question is None:
            return False
        ok = self.proc.send(text)
        shown = {cr.ANSWER_YES: "Sí", cr.ANSWER_NO: "No"}.get(text, text) if cr.is_yes_no(self.pending_question) else text
        self._write(f"> {shown}")
        self.pending_question = None
        self.question.pack_forget()
        return ok

    def _poll(self) -> None:
        try:
            for line in self.proc.drain():
                if line is None:
                    self._on_exit()
                    return
                self.handle_line(line)
        except Exception:  # noqa: BLE001 - un error de formato no debe matar el polling
            logger.exception("Error leyendo la salida de calibrate.py")
        self._after_id = self.top.after(POLL_MS, self._poll)

    def _on_exit(self) -> None:
        self.proc.wait_closed(STOP_WAIT_S)
        rc = self.proc.returncode
        self.finished = True
        self._after_id = None
        self.pending_question = None
        self.question.pack_forget()
        self._write(f"--- terminó (código {rc}) ---" if rc not in (None, 0) else "--- terminó ---",
                    "ERROR" if rc not in (None, 0) else "")
        self.btn_close.configure(text="Cerrar")
        try:
            self.on_finish(rc)
        except Exception:  # noqa: BLE001
            logger.exception("Error al terminar la calibración")

    # ------------------------------------------------------------------ cierre
    def kill(self) -> None:
        """Corta el subproceso (no guarda nada: calibrate.py solo escribe tras la confirmacion)."""
        if self._after_id is not None:
            try:
                self.top.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        self.proc.kill()

    def close(self) -> None:
        """Cancelar / Cerrar: si el proceso sigue vivo lo corta."""
        running = self.started and not self.finished
        self.kill()
        if running:
            try:
                self.on_finish(None)
            except Exception:  # noqa: BLE001
                logger.exception("Error al cancelar la calibración")
        try:
            self.top.destroy()
        except tk.TclError:
            pass
