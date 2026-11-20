"""Base visual comun de la interfaz: paleta y estilo ttk unico, espaciados constantes, tooltip con retardo, entrada
validada y marcos de seccion. Los validadores puros (sin tkinter) viven en `bot/ui/validators.py`."""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Callable

# --- espaciados (px) usados en todas las pestanas
PAD = 8            # margen exterior de una pestana
GAP = 6            # separacion entre secciones
GAP_S = 3          # separacion minima entre widgets de una misma fila
SECTION_PAD = 8    # relleno interno de un marco de seccion
WRAP = 440         # ancho de ajuste de linea de los textos largos de una columna

# --- paleta
COLOR_BG = "#f2f3f5"
COLOR_FG = "#202124"
COLOR_MUTED = "#5f6368"
COLOR_WARN = "#b36b00"
COLOR_ERROR = "#c00000"
COLOR_OK = "#1e7e34"
COLOR_FIELD_BAD = "#fde8e8"
COLOR_ACCENT = "#1a73e8"
FONT_FAMILY = "Segoe UI"
FONT_SIZE = 9

TOOLTIP_DELAY_MS = 500
TOOLTIP_WRAP = 320


def apply_style(root: tk.Misc) -> ttk.Style:
    """Aplica la paleta y el estilo ttk unico a toda la ventana (tema `clam`, que respeta los colores)."""
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            tkfont.nametofont(name, root=root).configure(family=FONT_FAMILY, size=FONT_SIZE)
        except tk.TclError:
            pass
    try:
        root.configure(background=COLOR_BG)
    except tk.TclError:
        pass
    style.configure(".", background=COLOR_BG, foreground=COLOR_FG, font=(FONT_FAMILY, FONT_SIZE))
    style.configure("TFrame", background=COLOR_BG)
    style.configure("TLabel", background=COLOR_BG, foreground=COLOR_FG)
    style.configure("Muted.TLabel", foreground=COLOR_MUTED)
    style.configure("Warn.TLabel", foreground=COLOR_WARN)
    style.configure("Error.TLabel", foreground=COLOR_ERROR, font=(FONT_FAMILY, FONT_SIZE - 1))
    style.configure("Big.TLabel", font=(FONT_FAMILY, FONT_SIZE + 1, "bold"))
    style.configure("Paused.TLabel", foreground=COLOR_ERROR, font=(FONT_FAMILY, 16, "bold"))
    style.configure("TLabelframe", background=COLOR_BG, bordercolor="#c4c7c5")
    style.configure("TLabelframe.Label", background=COLOR_BG, foreground=COLOR_ACCENT, font=(FONT_FAMILY, FONT_SIZE, "bold"))
    style.configure("TCheckbutton", background=COLOR_BG)
    style.configure("TButton", padding=(10, 4))
    style.configure("TNotebook", background=COLOR_BG)
    style.configure("TNotebook.Tab", padding=(12, 5))
    style.configure("Treeview", background="#ffffff", fieldbackground="#ffffff")
    style.configure("TEntry", padding=3)
    style.configure("Invalid.TEntry", fieldbackground=COLOR_FIELD_BAD, bordercolor=COLOR_ERROR)
    style.map("Invalid.TEntry", fieldbackground=[("disabled", COLOR_BG), ("!disabled", COLOR_FIELD_BAD)])
    return style


def section(parent: tk.Misc, title: str) -> ttk.LabelFrame:
    """Marco de seccion con titulo y relleno uniforme (el llamador lo ubica con pack o grid)."""
    return ttk.LabelFrame(parent, text=title, padding=SECTION_PAD)


def tab_frame(notebook: ttk.Notebook, title: str) -> ttk.Frame:
    """Pestana nueva con el margen uniforme."""
    f = ttk.Frame(notebook, padding=PAD)
    notebook.add(f, text=title)
    return f


def note(parent: tk.Misc, text: str, style: str = "Muted.TLabel", wrap: int = WRAP, fluid: bool = False) -> ttk.Label:
    """Texto explicativo corto (varias lineas, color atenuado). Con `fluid` (para los que van con pack fill="x") el
    ajuste de linea sigue el ancho de la ventana."""
    lab = ttk.Label(parent, text=text, style=style, wraplength=wrap, justify="left")
    if fluid:
        make_fluid(lab)
    return lab


def make_fluid(label: ttk.Label) -> ttk.Label:
    """Hace que el ajuste de linea de `label` siga su ancho real (para los que van con fill="x" o sticky="ew")."""
    label.bind("<Configure>", lambda e: label.configure(wraplength=max(120, e.width - 4)))
    return label


class Tooltip:
    """Texto de ayuda al pasar el mouse por un widget (aparece tras un retardo corto y se va al salir o hacer clic)."""

    def __init__(self, widget: tk.Misc, text: str | Callable[[], str], delay_ms: int = TOOLTIP_DELAY_MS) -> None:
        self.widget, self.text, self.delay_ms = widget, text, delay_ms
        self._after: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")
        widget.bind("<Destroy>", self.hide, add="+")

    def _current_text(self) -> str:
        return self.text() if callable(self.text) else self.text

    def _schedule(self, _event: tk.Event | None = None) -> None:
        self._cancel()
        self._after = self.widget.after(self.delay_ms, self._show_under_widget)

    def _cancel(self) -> None:
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def _show_under_widget(self) -> None:
        self._after = None
        try:
            self.show(self.widget.winfo_rootx() + 12, self.widget.winfo_rooty() + self.widget.winfo_height() + 4)
        except tk.TclError:
            pass

    def show(self, x: int, y: int, text: str | None = None) -> None:
        """Muestra el tooltip en (x, y) de pantalla (tambien lo usa el canvas de la zona con texto propio)."""
        self.hide()
        msg = text if text is not None else self._current_text()
        if not msg:
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        try:
            tip.attributes("-topmost", True)
        except tk.TclError:
            pass
        tk.Label(tip, text=msg, justify="left", background="#fffbe6", foreground=COLOR_FG, relief="solid", borderwidth=1,
                 wraplength=TOOLTIP_WRAP, padx=6, pady=3).pack()
        self._tip = tip

    def hide(self, _event: tk.Event | None = None) -> None:
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


def add_tooltip(widget: tk.Misc, text: str | Callable[[], str]) -> Tooltip:
    """Atajo: agrega un tooltip a `widget` y lo devuelve."""
    return Tooltip(widget, text)


class ValidatedEntry(ttk.Frame):
    """Campo de texto con validacion en vivo: si el valor no es valido se marca en rojo y se muestra un mensaje corto.
    `validator(texto) -> None | mensaje`. `entry` y `var` quedan accesibles; `valid` / `error` dicen el estado actual."""

    def __init__(self, parent: tk.Misc, validator: Callable[[str], str | None], *, value: str = "", width: int = 10,
                 tooltip: str = "", on_change: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self.validator, self.on_change = validator, on_change
        self.var = tk.StringVar(value=value)
        self.entry = ttk.Entry(self, textvariable=self.var, width=width)
        self.entry.pack(fill="x")
        self.msg = tk.StringVar(value="")
        self._msg_label = ttk.Label(self, textvariable=self.msg, style="Error.TLabel")   # solo se ve con error
        self.error: str | None = None
        if tooltip:
            add_tooltip(self.entry, tooltip)
        self.var.trace_add("write", lambda *_a: self.revalidate())
        self.revalidate()

    @property
    def valid(self) -> bool:
        return self.error is None

    def get(self) -> str:
        return self.var.get()

    def set(self, value: str) -> None:
        self.var.set(value)

    def revalidate(self) -> None:
        """Vuelve a validar el texto actual y actualiza la marca roja y el mensaje."""
        self.error = self.validator(self.var.get())
        self.entry.configure(style="Invalid.TEntry" if self.error else "TEntry")
        self.msg.set(self.error or "")
        if self.error:
            self._msg_label.pack(fill="x")
        else:
            self._msg_label.pack_forget()
        if self.on_change is not None:
            self.on_change()


COLOR_BANNER_BG = "#fff3cd"
COLOR_BANNER_FG = "#664d03"
COLOR_BANNER_BORDER = "#e0b000"


class OverrideStrip(tk.Frame):
    """Franja amarilla de aviso ("config.json pisa estas claves") con el botón de migrar. Se muestra solo si tiene texto.
    `anchor_pack` son las opciones de pack con las que se ubica cuando aparece (por ejemplo `before=notebook`)."""

    def __init__(self, parent: tk.Misc, on_migrate: Callable[[], None], *, pack_opts: dict | None = None) -> None:
        super().__init__(parent, background=COLOR_BANNER_BG, highlightthickness=1, highlightbackground=COLOR_BANNER_BORDER,
                         padx=PAD, pady=GAP_S)
        self.pack_opts = {"fill": "x", "pady": (0, GAP), **(pack_opts or {})}
        self.label = tk.Label(self, text="", background=COLOR_BANNER_BG, foreground=COLOR_BANNER_FG, justify="left",
                              anchor="w", wraplength=700)
        self.label.pack(side="left", fill="x", expand=True)
        self.button = ttk.Button(self, text="Migrar config.json a los perfiles", command=on_migrate)
        self.button.pack(side="right", padx=(PAD, 0))
        add_tooltip(self.button, "Mueve a los perfiles lo que config.json define, para poder editarlo desde acá. "
                                 "Pide confirmación y guarda config.json.bak.")
        self.bind("<Configure>", lambda e: self.label.configure(wraplength=max(200, e.width - self.button.winfo_reqwidth() - 4 * PAD)))
        self.visible = False

    def set_text(self, text: str) -> None:
        """Muestra la franja con `text` o la oculta si está vacío."""
        self.label.configure(text=text)
        if text and not self.visible:
            self.pack(**self.pack_opts)
            self.visible = True
        elif not text and self.visible:
            self.pack_forget()
            self.visible = False

    def set_enabled(self, enabled: bool) -> None:
        self.button.configure(state="normal" if enabled else "disabled")


class ScrollFrame(ttk.Frame):
    """Marco con barra de scroll vertical (se usa en las pestañas largas para que la ventana pueda achicarse).
    Los widgets van en `.body`; la rueda del mouse mueve el scroll mientras el puntero está encima."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.canvas = tk.Canvas(self, background=COLOR_BG, highlightthickness=0, borderwidth=0)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.vbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda _e: self._update())
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind("<Enter>", lambda _e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda _e: self.canvas.unbind_all("<MouseWheel>"))

    def _on_canvas(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._win, width=event.width)
        self._update()

    def _update(self) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        need = self.body.winfo_reqheight() > self.canvas.winfo_height() > 1
        if not need:
            self.canvas.yview_moveto(0)

    def _wheel(self, event: tk.Event) -> None:
        if self.body.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")


def heading_tooltips(tree: ttk.Treeview, tips: dict[str, str]) -> Tooltip:
    """Tooltip por columna al pasar el mouse por el encabezado de una tabla. `tips`: id de columna ("#0" para la del árbol)
    -> texto. Devuelve el Tooltip (su `show`/`hide` se pueden usar a mano)."""
    tip = Tooltip(tree, "")
    state: dict[str, str | None] = {"col": None}

    def column_id(event: tk.Event) -> str | None:
        if tree.identify_region(event.x, event.y) != "heading":
            return None
        num = tree.identify_column(event.x)
        if num == "#0":
            return "#0"
        try:
            return str(tree.cget("displaycolumns")[int(num[1:]) - 1]) if tree.cget("displaycolumns") != ("#all",) \
                else str(tree.cget("columns")[int(num[1:]) - 1])
        except (ValueError, IndexError, tk.TclError):
            return None

    def on_motion(event: tk.Event) -> None:
        col = column_id(event)
        if col == state["col"]:
            return
        state["col"] = col
        text = tips.get(col or "")
        if text:
            tip.show(event.x_root + 12, event.y_root + 18, text)
        else:
            tip.hide()

    def on_leave(_event: tk.Event) -> None:
        state["col"] = None
        tip.hide()

    tree.bind("<Motion>", on_motion, add="+")
    tree.bind("<Leave>", on_leave, add="+")
    tip.heading_column = lambda event: column_id(event)   # type: ignore[attr-defined]  # para probarlo sin mouse real
    return tip
