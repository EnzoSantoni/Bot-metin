"""Dialogo modal tkinter para pedir el nombre de un perfil nuevo (Duplicar / Nueva), con validacion en vivo y una casilla
opcional. La logica de validacion y copia esta en `bot/ui/profile_ops.py`."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from bot.ui.widgets import GAP, GAP_S, PAD, WRAP, ValidatedEntry, add_tooltip, note


class NameDialog:
    """Pide un nombre (validado) y, si se pasa `check_text`, una casilla. `result` = (nombre, casilla) o None si se cancela."""

    def __init__(self, parent: tk.Misc, title: str, intro: str, validator: Callable[[str], str | None], *,
                 label: str = "Nombre nuevo:", initial: str = "", check_text: str = "", check_default: bool = True,
                 check_tip: str = "", name_tip: str = "Nombre del perfil nuevo. Puede llevar espacios y guiones.") -> None:
        self.result: tuple[str, bool] | None = None
        self.top = tk.Toplevel(parent)
        self.top.title(title)
        self.top.transient(parent)
        self.top.resizable(False, False)
        f = ttk.Frame(self.top, padding=PAD)
        f.pack(fill="both", expand=True)
        note(f, intro, style="TLabel", wrap=WRAP).pack(fill="x")
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(GAP, 0))
        ttk.Label(row, text=label).pack(side="left")
        self.entry = ValidatedEntry(row, validator, value=initial, width=34, on_change=self._refresh, tooltip=name_tip)
        self.entry.pack(side="left", padx=GAP)
        self.check_var = tk.BooleanVar(value=check_default)
        if check_text:
            chk = ttk.Checkbutton(f, text=check_text, variable=self.check_var)
            chk.pack(anchor="w", pady=(GAP_S, 0))
            if check_tip:
                add_tooltip(chk, check_tip)
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(GAP, 0))
        self.btn_cancel = ttk.Button(bar, text="Cancelar", command=self.top.destroy)
        self.btn_cancel.pack(side="right")
        self.btn_ok = ttk.Button(bar, text="Aceptar", command=self.accept)
        self.btn_ok.pack(side="right", padx=GAP)
        self.entry.entry.bind("<Return>", lambda _e: self.accept())
        self._refresh()
        try:
            self.top.grab_set()
        except tk.TclError:
            pass
        self.entry.entry.focus_set()

    def _refresh(self) -> None:
        if hasattr(self, "btn_ok"):
            self.btn_ok.configure(state="normal" if self.entry.valid else "disabled")

    def accept(self) -> None:
        if self.entry.valid:
            self.result = (self.entry.get(), bool(self.check_var.get()))
            self.top.destroy()


def ask_name(parent: tk.Misc, title: str, intro: str, validator: Callable[[str], str | None],
             **kw) -> tuple[str, bool] | None:
    """Abre el dialogo, espera a que se cierre y devuelve (nombre, casilla) o None."""
    dlg = NameDialog(parent, title, intro, validator, **kw)
    parent.wait_window(dlg.top)
    return dlg.result
