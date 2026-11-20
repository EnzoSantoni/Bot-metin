"""Ventana tkinter del bot. Toda la logica no visual vive en `bot/ui/controller.py`; aca solo hay widgets y el
polling del subproceso con `after()` (el hilo de tkinter nunca se bloquea leyendo)."""
from __future__ import annotations

import logging
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Callable

from bot.config import ConfigError
from bot.history import load_history
from bot.item_catalog import ItemCatalog
from bot.ui import controller as ctl
from bot.ui import history_view as hv
from bot.ui import skills_editor as se
from bot.ui.farm_mode_editor import MODE_LABELS, MODES, FarmModeEditor
from bot.ui.priority_editor import PriorityEditor

logger = logging.getLogger(__name__)

POLL_MS = 100
LOG_MAX_LINES = 200
REMINDER = "F12 o mouse a la esquina sup. izq.: detener  |  F11: pausa"
KIND_LABELS = (("servidor", "Servidor"), ("personaje", "Personaje"), ("zona", "Zona"))
COLOR_WARN, COLOR_ERROR, COLOR_PAUSE = "#b36b00", "#c00000", "#c00000"
MIN_SIZE = (460, 560)
WS_MINIMIZEBOX = 0x00020000
GWL_STYLE = -16


class App:
    """Ventana principal. `process` y `command_builder` se pueden inyectar (tests)."""

    def __init__(self, root: tk.Tk, *, settings_path: str | Path = ctl.SETTINGS_FILE, config_path: str = "config.json",
                 profiles_root: str | Path | None = None, process: ctl.BotProcess | None = None,
                 command_builder: Callable[[ctl.UiOptions], list[str]] | None = None,
                 confirm: Callable[[str, str], bool] | None = None, notify: Callable[[str, str], None] | None = None,
                 ask_save: Callable[[str, str], bool | None] | None = None,
                 catalog_dir: str | Path | None = None, history_file: str | Path | None = None,
                 ask_name: Callable[[str, str], str | None] | None = None) -> None:
        self.root, self.settings_path, self.config_path = root, Path(settings_path), config_path
        self.profiles_root = profiles_root
        self.proc = process or ctl.BotProcess()
        self.command_builder = command_builder or (lambda o: ctl.build_command(o))
        self._confirm = confirm or (lambda t, m: messagebox.askokcancel(t, m, parent=root))
        self._notify = notify or (lambda t, m: messagebox.showerror(t, m, parent=root))
        # True = guardar, False = seguir sin guardar, None = cancelar
        self._ask_save = ask_save or (lambda t, m: messagebox.askyesnocancel(t, m, parent=root))
        # Nombre nuevo para un objeto: (nombre actual, texto del pedido) -> nombre o None (cancelar)
        self._ask_name = ask_name or (lambda cur, msg: simpledialog.askstring("Renombrar objeto", msg, initialvalue=cur,
                                                                               parent=root))
        d_dir, d_file, d_match = ctl.data_paths(config_path, profiles_root)
        self.catalog = ItemCatalog(Path(catalog_dir) if catalog_dir is not None else d_dir, d_match)
        self.history_file = Path(history_file) if history_file is not None else d_file
        self.sessions: list[dict] = []
        self._thumbs: dict[str, tk.PhotoImage] = {}
        self.phase = ctl.STOPPED
        self.paused = False
        self._got_end = False
        self._last_log_error = ""
        self._last_status: ctl.Status | None = None
        self._after_id: str | None = None
        self._kill_id: str | None = None

        self.profiles = ctl.available_profiles(profiles_root)
        self.opts = ctl.load_settings(self.settings_path, self.profiles)
        self.editor = se.SkillsEditor(profiles_root)
        self.editor.load(self.opts.personaje)
        self._last_personaje = self.opts.personaje
        self.prio = PriorityEditor(profiles_root)
        self.prio.load(self.opts.zona)
        self.farm = FarmModeEditor(profiles_root)
        self.farm.load(self.opts.zona)
        self._last_zona = self.opts.zona
        self._build()
        self._apply_window_prefs()
        self.refresh_overrides()
        self.refresh_buttons()
        self._after_id = root.after(POLL_MS, self._poll)

    # ------------------------------------------------------------------ construccion
    def _build(self) -> None:
        r = self.root
        r.title("Metin Bot")
        r.minsize(*MIN_SIZE)
        r.protocol("WM_DELETE_WINDOW", self.on_close)
        self.notebook = ttk.Notebook(r)
        self.notebook.pack(fill="both", expand=True)
        main = ttk.Frame(self.notebook, padding=8)
        skills_tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(main, text="Control")
        self.notebook.add(skills_tab, text="Habilidades")
        self._build_skills(skills_tab)
        history_tab = ttk.Frame(self.notebook, padding=8)
        items_tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(history_tab, text="Historial")
        self.notebook.add(items_tab, text="Objetos")
        self._build_history(history_tab)
        self._build_items(items_tab)

        prof = ttk.LabelFrame(main, text="Perfiles", padding=6)
        prof.pack(fill="x")
        self.combos: dict[str, ttk.Combobox] = {}
        self.vars: dict[str, tk.StringVar] = {}
        for i, (kind, label) in enumerate(KIND_LABELS):
            ttk.Label(prof, text=label).grid(row=i, column=0, sticky="w", padx=(0, 8), pady=2)
            var = tk.StringVar(value=getattr(self.opts, kind))
            cb = ttk.Combobox(prof, textvariable=var, values=self.profiles.get(kind, []), state="readonly", width=40)
            cb.grid(row=i, column=1, sticky="ew", pady=2)
            cb.bind("<<ComboboxSelected>>", lambda _e, k=kind: self._on_profile_selected(k))
            self.vars[kind], self.combos[kind] = var, cb
        prof.columnconfigure(1, weight=1)
        self.warn_label = tk.Label(prof, text="", fg=COLOR_WARN, justify="left", anchor="w", wraplength=420)
        self.warn_label.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        self._build_priority(main)
        self._build_farm_mode(main)

        opt = ttk.LabelFrame(main, text="Opciones", padding=6)
        opt.pack(fill="x", pady=6)
        ttk.Label(opt, text="Tiempo maximo (min, 0 = sin limite)").grid(row=0, column=0, sticky="w")
        self.minutes_var = tk.StringVar(value=f"{self.opts.max_minutes:g}")
        self.minutes_entry = ttk.Entry(opt, textvariable=self.minutes_var, width=8)
        self.minutes_entry.grid(row=0, column=1, sticky="w", padx=8)
        self.debug_var = tk.BooleanVar(value=self.opts.debug)
        self.observe_var = tk.BooleanVar(value=self.opts.observar)
        self.top_var = tk.BooleanVar(value=self.opts.always_on_top)
        self.checks = [ttk.Checkbutton(opt, text="Debug", variable=self.debug_var),
                       ttk.Checkbutton(opt, text="Observar (sin input)", variable=self.observe_var)]
        self.checks[0].grid(row=1, column=0, sticky="w")
        self.checks[1].grid(row=1, column=1, columnspan=2, sticky="w")
        ttk.Checkbutton(opt, text="Siempre encima", variable=self.top_var, command=self._apply_window_prefs).grid(
            row=2, column=0, sticky="w")

        bar = ttk.Frame(main)
        bar.pack(fill="x")
        self.btn_start = ttk.Button(bar, text="Iniciar", command=self.start)
        self.btn_pause = ttk.Button(bar, text="Pausar", command=self.toggle_pause)
        self.btn_stop = ttk.Button(bar, text="Detener", command=self.stop)
        for b in (self.btn_start, self.btn_pause, self.btn_stop):
            b.pack(side="left", expand=True, fill="x", padx=2)

        self.paused_label = tk.Label(main, text="", fg=COLOR_PAUSE, font=("Segoe UI", 16, "bold"))
        self.paused_label.pack(fill="x")
        st = ttk.LabelFrame(main, text="Estadisticas", padding=6)
        st.pack(fill="x")
        self.stat_vars: dict[str, tk.StringVar] = {}
        for i, (key, label) in enumerate((("state", "Estado"), ("metins", "Metin rotos"), ("items", "Objetos"),
                                          ("captchas", "Captchas"), ("unstucks", "Destrabes"), ("time", "Tiempo"),
                                          ("skills_cast", "Habilidades lanzadas"), ("lures", "Señuelos usados"))):
            ttk.Label(st, text=label + ":").grid(row=i // 2, column=(i % 2) * 2, sticky="w", padx=(0, 4))
            v = tk.StringVar(value="-")
            ttk.Label(st, textvariable=v, font=("Segoe UI", 10, "bold")).grid(row=i // 2, column=(i % 2) * 2 + 1,
                                                                             sticky="w", padx=(0, 16))
            self.stat_vars[key] = v
        self.info_var = tk.StringVar(value="")
        tk.Label(st, textvariable=self.info_var, anchor="w", justify="left", wraplength=420).grid(
            row=4, column=0, columnspan=4, sticky="ew", pady=(4, 0))

        live = ttk.LabelFrame(main, text="Objetos de esta sesión", padding=4)
        live.pack(fill="x", pady=(6, 0))
        self.live_tree = ttk.Treeview(live, columns=("name", "count"), show="headings", height=3, selectmode="none")
        self.live_tree.heading("name", text="Objeto")
        self.live_tree.heading("count", text="Cantidad")
        self.live_tree.column("name", width=300, anchor="w")
        self.live_tree.column("count", width=80, anchor="center", stretch=False)
        self.live_tree.pack(fill="x")

        lf = ttk.LabelFrame(main, text="Log", padding=4)
        lf.pack(fill="both", expand=True, pady=6)
        self.log = tk.Text(lf, height=10, wrap="word", state="disabled", font=("Consolas", 9))
        sb = ttk.Scrollbar(lf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log.tag_configure("WARNING", foreground=COLOR_WARN)
        self.log.tag_configure("ERROR", foreground=COLOR_ERROR)
        self.log.tag_configure("CRITICAL", foreground=COLOR_ERROR)
        ttk.Label(main, text=REMINDER, foreground="#444").pack(fill="x")

    # ------------------------------------------------------------------ historial
    HISTORY_COLS = (("date", "Fecha", 120), ("duration", "Duración", 90), ("metins", "Metin", 55), ("items", "Objetos", 60),
                    ("reason", "Motivo", 150))

    def _build_history(self, tab: ttk.Frame) -> None:
        top = ttk.Frame(tab)
        top.pack(fill="x")
        self.hist_today = tk.StringVar(value="")
        self.hist_total = tk.StringVar(value="")
        for var in (self.hist_today, self.hist_total):
            tk.Label(top, textvariable=var, anchor="w", justify="left", wraplength=420).pack(fill="x", pady=(0, 4))
        ttk.Button(top, text="Actualizar", command=self.refresh_history).pack(anchor="w")
        frame = ttk.Frame(tab)
        frame.pack(fill="both", expand=True, pady=4)
        self.hist_tree = ttk.Treeview(frame, columns=[c[0] for c in self.HISTORY_COLS], show="headings", height=7,
                                      selectmode="browse")
        for cid, label, width in self.HISTORY_COLS:
            self.hist_tree.heading(cid, text=label)
            self.hist_tree.column(cid, width=width, anchor="w" if cid in ("date", "reason") else "center",
                                  stretch=cid == "reason")
        sb = ttk.Scrollbar(frame, command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.hist_tree.pack(side="left", fill="both", expand=True)
        self.hist_tree.bind("<<TreeviewSelect>>", lambda _e: self._show_session_detail())
        self.hist_detail = tk.Text(tab, height=10, wrap="word", state="disabled", font=("Consolas", 9))
        self.hist_detail.pack(fill="x")
        self.refresh_history()

    def refresh_history(self) -> None:
        """Recarga el archivo del historial y redibuja tabla y totales (conserva la sesión elegida si sigue)."""
        keep = self.hist_tree.selection()
        self.sessions = load_history(self.history_file)
        self.hist_tree.delete(*self.hist_tree.get_children())
        for r in hv.session_rows(self.sessions):
            self.hist_tree.insert("", "end", iid=str(r.index), values=(r.date, r.duration, r.metins, r.items, r.reason))
        today, total = hv.totals_texts(self.sessions, self.catalog)
        self.hist_today.set(today)
        self.hist_total.set(total)
        if keep and self.hist_tree.exists(keep[0]):
            self.hist_tree.selection_set(keep[0])
        self._show_session_detail()

    def _show_session_detail(self) -> None:
        sel = self.hist_tree.selection()
        text = hv.session_detail(self.sessions[int(sel[0])], self.catalog) if sel else "Elegí una sesión para ver el detalle."
        self.hist_detail.configure(state="normal")
        self.hist_detail.delete("1.0", "end")
        self.hist_detail.insert("end", text)
        self.hist_detail.configure(state="disabled")

    # ------------------------------------------------------------------ objetos (catálogo)
    ITEM_THUMB_MAX_H = 34

    def _build_items(self, tab: ttk.Frame) -> None:
        ttk.Label(tab, text="Tipos de objeto recogidos. El bot los agrupa por cómo se ve la etiqueta; ponele el nombre real "
                            "(doble clic o Renombrar). El nombre también cambia en el historial.", wraplength=420,
                  justify="left").pack(fill="x")
        frame = ttk.Frame(tab)
        frame.pack(fill="both", expand=True, pady=4)
        ttk.Style(self.root).configure("Items.Treeview", rowheight=self.ITEM_THUMB_MAX_H + 4)
        self.items_tree = ttk.Treeview(frame, columns=("name", "count"), show="tree headings", height=8, selectmode="browse",
                                       style="Items.Treeview")
        self.items_tree.heading("#0", text="Etiqueta")
        self.items_tree.heading("name", text="Nombre")
        self.items_tree.heading("count", text="Cantidad total")
        self.items_tree.column("#0", width=170, stretch=False)
        self.items_tree.column("name", width=150, anchor="w")
        self.items_tree.column("count", width=90, anchor="center", stretch=False)
        sb = ttk.Scrollbar(frame, command=self.items_tree.yview)
        self.items_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.items_tree.pack(side="left", fill="both", expand=True)
        self.items_tree.bind("<Double-1>", lambda _e: self.rename_selected_item())
        bar = ttk.Frame(tab)
        bar.pack(fill="x")
        self.btn_rename = ttk.Button(bar, text="Renombrar", command=self.rename_selected_item)
        self.btn_rename.pack(side="left", padx=2)
        ttk.Button(bar, text="Actualizar", command=self.refresh_items).pack(side="left", padx=2)
        self.items_state = tk.StringVar(value="")
        tk.Label(tab, textvariable=self.items_state, anchor="w", fg=COLOR_WARN, justify="left", wraplength=420).pack(fill="x")
        self.refresh_items()

    def _thumbnail(self, path: str | None) -> tk.PhotoImage | None:
        """Miniatura del PNG (reducida si es muy alta); None si no hay o no se puede cargar."""
        if not path:
            return None
        try:
            img = tk.PhotoImage(master=self.root, file=path)
            if img.height() > self.ITEM_THUMB_MAX_H:
                img = img.subsample(-(-img.height() // self.ITEM_THUMB_MAX_H))
            return img
        except tk.TclError:
            logger.warning("No se pudo cargar la miniatura %s", path)
            return None

    def refresh_items(self) -> None:
        """Recarga el catálogo del disco y redibuja la tabla (conserva la selección)."""
        keep = self.items_tree.selection()
        if not self.proc.running:   # con el bot corriendo el archivo lo escribe él: no se pisa
            self.catalog.reload()
        self.items_tree.delete(*self.items_tree.get_children())
        self._thumbs = {}
        for r in hv.catalog_rows(self.catalog):
            img = self._thumbnail(r.thumbnail)
            kw = {}
            if img is not None:
                self._thumbs[r.id] = img
                kw["image"] = img
            self.items_tree.insert("", "end", iid=r.id, values=(r.name, r.count), **kw)
        if keep and self.items_tree.exists(keep[0]):
            self.items_tree.selection_set(keep[0])
        self._refresh_items_state()

    def _refresh_items_state(self) -> None:
        locked = self.phase != ctl.STOPPED
        self.btn_rename.configure(state="disabled" if locked else "normal")
        self.items_state.set("Renombrar se habilita al terminar la corrida." if locked else
                             ("" if len(self.catalog) else "Todavía no hay objetos recogidos."))

    def rename_selected_item(self) -> bool:
        """Pide el nombre nuevo del objeto elegido y lo guarda en el catálogo. False si no se cambió nada."""
        sel = self.items_tree.selection()
        if not sel or self.phase != ctl.STOPPED:
            return False
        entry = self.catalog.get(sel[0])
        if entry is None:
            return False
        name = self._ask_name(entry.name, f"Nombre para '{entry.name}':")
        if name is None:
            return False
        err = hv.rename_item(self.catalog, entry.id, name)
        if err:
            self._notify("No se pudo renombrar", err)
            return False
        self.refresh_items()
        self.refresh_history()
        return True

    # ------------------------------------------------------------------ prioridad de metin
    def _build_priority(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="Prioridad de metin", padding=6)
        box.pack(fill="x", pady=(6, 0))
        self.prio_var = tk.BooleanVar(value=self.prio.enabled)
        self.prio_check = ttk.Checkbutton(box, text="Elegir por prioridad (arriba = primero)", variable=self.prio_var,
                                          command=self._on_prio_toggle)
        self.prio_check.grid(row=0, column=0, columnspan=4, sticky="w")
        self.prio_list = tk.Listbox(box, height=3, exportselection=False, activestyle="none")
        self.prio_list.grid(row=1, column=0, sticky="ew", pady=2)
        box.columnconfigure(0, weight=1)
        self.prio_buttons: list[ttk.Widget] = []
        for col, (text, cmd, w) in enumerate((("↑", lambda: self.move_priority(-1), 3),
                                              ("↓", lambda: self.move_priority(1), 3),
                                              ("Guardar", self.save_priority, 8)), start=1):
            b = ttk.Button(box, text=text, width=w, command=cmd)
            b.grid(row=1, column=col, padx=2)
            self.prio_buttons.append(b)
        self.prio_state = tk.StringVar(value="")
        tk.Label(box, textvariable=self.prio_state, anchor="w", fg=COLOR_WARN).grid(row=2, column=0, columnspan=4, sticky="ew")
        self.refresh_priority()

    def refresh_priority(self, select: int | None = None) -> None:
        """Redibuja la lista, el interruptor y el indicador de cambios sin guardar."""
        self.prio_list.delete(0, "end")
        for i, n in enumerate(self.prio.names):
            self.prio_list.insert("end", f"{i + 1}. {n}" if self.prio.enabled else n)
        if select is not None and 0 <= select < len(self.prio.names):
            self.prio_list.selection_set(select)
        self.prio_var.set(self.prio.enabled)
        parts = []
        if self.prio.dirty:
            parts.append("sin guardar")
        if self.prio.locked:
            parts.append("bloqueada mientras el bot corre")
        self.prio_state.set(" - ".join(parts))

    def _selected_priority(self) -> int | None:
        sel = self.prio_list.curselection()
        return int(sel[0]) if sel else None

    def _on_prio_toggle(self) -> None:
        if self.prio.locked:
            self.prio_var.set(self.prio.enabled)
            return
        self.prio.set_enabled(self.prio_var.get())
        self.refresh_priority(select=self._selected_priority())

    def move_priority(self, delta: int) -> None:
        i = self._selected_priority()
        if i is None or self.prio.locked:
            return
        self.refresh_priority(select=self.prio.move(i, delta))

    def save_priority(self) -> bool:
        try:
            self.prio.save()
        except (ValueError, ConfigError, OSError) as e:
            self._notify("No se pudo guardar la prioridad", str(e))
            return False
        self.refresh_priority(select=self._selected_priority())
        return True

    def _confirm_unsaved_priority(self, what: str) -> bool:
        """Como `_confirm_unsaved`, para la prioridad de la zona. False = cancelar la accion."""
        if not self.prio.dirty:
            return True
        ans = self._ask_save("Prioridad sin guardar",
                             f"Hay cambios sin guardar en la prioridad de metin de '{self.prio.zona}'.\n"
                             f"Guardarlos antes de {what}?")
        if ans is None:
            return False
        return self.save_priority() if ans else True

    # ------------------------------------------------------------------ modo de farmeo
    def _build_farm_mode(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="Modo de farmeo", padding=6)
        box.pack(fill="x", pady=(6, 0))
        ttk.Label(box, text="Modo:").grid(row=0, column=0, sticky="w")
        self.farm_mode_var = tk.StringVar(value=MODE_LABELS[self.farm.mode])
        self.farm_combo = ttk.Combobox(box, textvariable=self.farm_mode_var, values=[t for _, t in MODES], state="readonly",
                                       width=10)
        self.farm_combo.grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(box, text="Tecla del señuelo:").grid(row=0, column=2, sticky="e", padx=(10, 0))
        self.farm_key_var = tk.StringVar(value=self.farm.lure_key)
        self.farm_key_entry = ttk.Entry(box, textvariable=self.farm_key_var, width=6)
        self.farm_key_entry.grid(row=0, column=3, sticky="w", padx=4)
        ttk.Label(box, text="Cada (s):").grid(row=0, column=4, sticky="e", padx=(10, 0))
        self.farm_secs_var = tk.StringVar(value=self.farm.seconds)
        self.farm_secs_entry = ttk.Entry(box, textvariable=self.farm_secs_var, width=6)
        self.farm_secs_entry.grid(row=0, column=5, sticky="w", padx=4)
        self.farm_save_btn = ttk.Button(box, text="Guardar", width=8, command=self.save_farm_mode)
        self.farm_save_btn.grid(row=0, column=6, padx=(10, 0))
        self.farm_state = tk.StringVar(value="")
        tk.Label(box, textvariable=self.farm_state, anchor="w", fg=COLOR_WARN).grid(row=1, column=0, columnspan=7, sticky="ew")
        for v in (self.farm_mode_var, self.farm_key_var, self.farm_secs_var):
            v.trace_add("write", lambda *_a: self._on_farm_edit())

    def _farm_mode_value(self) -> str:
        text = self.farm_mode_var.get()
        return next((k for k, t in MODES if t == text), "metin")

    def _on_farm_edit(self) -> None:
        if getattr(self, "_farm_syncing", False):
            return
        if self.farm.locked:
            self.refresh_farm_mode()
            return
        self.farm.set_values(self._farm_mode_value(), self.farm_key_var.get(), self.farm_secs_var.get())
        self.refresh_farm_mode()

    def refresh_farm_mode(self) -> None:
        """Vuelca el editor en los campos y redibuja el indicador de cambios sin guardar."""
        self._farm_syncing = True
        try:
            for var, value in ((self.farm_mode_var, MODE_LABELS[self.farm.mode]), (self.farm_key_var, self.farm.lure_key),
                               (self.farm_secs_var, self.farm.seconds)):
                if var.get() != value:   # no pisar lo que se esta escribiendo (moveria el cursor)
                    var.set(value)
        finally:
            self._farm_syncing = False
        parts = []
        if self.farm.dirty:
            parts.append("sin guardar")
        if self.farm.locked:
            parts.append("bloqueado mientras el bot corre")
        self.farm_state.set(" - ".join(parts))

    def save_farm_mode(self) -> bool:
        try:
            self.farm.save()
        except (ValueError, ConfigError, OSError) as e:
            self._notify("No se pudo guardar el modo de farmeo", str(e))
            return False
        self.refresh_farm_mode()
        return True

    def _confirm_unsaved_farm(self, what: str) -> bool:
        """Como `_confirm_unsaved_priority`, para el modo de farmeo de la zona. False = cancelar la accion."""
        if not self.farm.dirty:
            return True
        ans = self._ask_save("Modo de farmeo sin guardar",
                             f"Hay cambios sin guardar en el modo de farmeo de '{self.farm.zona}'.\n"
                             f"Guardarlos antes de {what}?")
        if ans is None:
            return False
        return self.save_farm_mode() if ans else True

    # ------------------------------------------------------------------ habilidades
    SKILL_COLS = (("enabled", "Activa", 50), ("name", "Nombre", 140), ("key", "Tecla", 60), ("duration_s", "Duracion (s)", 85),
                  ("requires_dismount", "Bajarse", 60), ("animation_s", "Animacion (s)", 95), ("remaining", "Restante", 70))

    def _build_skills(self, tab: ttk.Frame) -> None:
        ttk.Label(tab, text="Habilidades del personaje elegido. Los ejemplos son un punto de partida: ajusta la duracion "
                            "y la animacion a las reales.", wraplength=420, justify="left").pack(fill="x")
        frame = ttk.Frame(tab)
        frame.pack(fill="both", expand=True, pady=4)
        self.tree = ttk.Treeview(frame, columns=[c[0] for c in self.SKILL_COLS], show="headings", height=8,
                                 selectmode="browse")
        for cid, label, width in self.SKILL_COLS:
            self.tree.heading(cid, text=label)
            self.tree.column(cid, width=width, anchor="w" if cid == "name" else "center", stretch=cid == "name")
        sb = ttk.Scrollbar(frame, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda _e: self.edit_skill())
        bar = ttk.Frame(tab)
        bar.pack(fill="x")
        self.skill_buttons: list[ttk.Widget] = []
        for text, cmd in (("Agregar", self.add_skill), ("Editar", self.edit_skill), ("Quitar", self.remove_skill),
                          ("\u2191", lambda: self.move_skill(-1)), ("\u2193", lambda: self.move_skill(1)),
                          ("Guardar", self.save_skills)):
            b = ttk.Button(bar, text=text, width=8 if len(text) > 1 else 3, command=cmd)
            b.pack(side="left", padx=2)
            self.skill_buttons.append(b)
        self.example_menu = tk.Menu(self.root, tearoff=0)
        for i, (label, key) in enumerate(se.EXAMPLES):
            self.example_menu.add_command(label=f"{label} - {key}", command=lambda i=i: self.add_example(i))
        self.btn_example = ttk.Menubutton(bar, text="Agregar ejemplo", menu=self.example_menu)
        self.btn_example.pack(side="left", padx=2)
        self.skill_buttons.append(self.btn_example)
        self.skills_state = tk.StringVar(value="")
        tk.Label(tab, textvariable=self.skills_state, anchor="w", fg=COLOR_WARN, justify="left", wraplength=420).pack(fill="x")
        self.refresh_skills()

    @staticmethod
    def _skill_row(sk: dict, remaining: dict) -> tuple:
        rem = remaining.get((sk.get("name"), sk.get("key")))
        return ("si" if sk.get("enabled") else "no", sk.get("name", ""), sk.get("key", ""), f"{sk.get('duration_s', 0):g}",
                "si" if sk.get("requires_dismount") else "no", f"{sk.get('animation_s', 0):g}",
                "" if rem is None else se.remaining_text(rem))

    def refresh_skills(self, select: int | None = None, status: ctl.Status | None = None) -> None:
        """Redibuja la tabla (con el tiempo restante si hay @@STATUS) y el indicador de cambios sin guardar."""
        if status is not None:
            self._last_status = status
        status = status or (self._last_status if self.editor.locked else None)
        remaining = {(e["name"], e["key"]): e["remaining_s"] for e in (status.skills if status else [])}
        self.tree.delete(*self.tree.get_children())
        for i, sk in enumerate(self.editor.skills):
            self.tree.insert("", "end", iid=str(i), values=self._skill_row(sk, remaining))
        if select is not None and 0 <= select < len(self.editor.skills):
            self.tree.selection_set(str(select))
        self._refresh_skills_state()

    def _refresh_skills_state(self) -> None:
        parts = []
        if self.editor.dirty:
            parts.append("sin guardar")
        if self.editor.locked:
            parts.append("bloqueada mientras el bot corre: los cambios aplican a la proxima sesion")
        self.skills_state.set(" - ".join(parts))
        self.notebook.tab(1, text="Habilidades *" if self.editor.dirty else "Habilidades")

    def _selected_skill(self) -> int | None:
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def _controls(self) -> dict[str, str]:
        return se.controls_for(self.vars["servidor"].get(), self.profiles_root)

    def _edit_dialog(self, skill: dict | None) -> dict | None:
        dlg = SkillDialog(self.root, skill, self._controls())
        self.root.wait_window(dlg.top)
        return dlg.result

    def add_skill(self) -> None:
        if self.editor.locked:
            return
        sk = self._edit_dialog(None)
        if sk is not None:
            self.editor.add(sk)
            self.refresh_skills(select=len(self.editor.skills) - 1)

    def edit_skill(self) -> None:
        i = self._selected_skill()
        if i is None or self.editor.locked:
            return
        sk = self._edit_dialog(self.editor.skills[i])
        if sk is not None:
            self.editor.update(i, sk)
            self.refresh_skills(select=i)

    def remove_skill(self) -> None:
        i = self._selected_skill()
        if i is None or self.editor.locked:
            return
        self.editor.remove(i)
        self.refresh_skills(select=min(i, len(self.editor.skills) - 1))

    def move_skill(self, delta: int) -> None:
        i = self._selected_skill()
        if i is None or self.editor.locked:
            return
        self.refresh_skills(select=self.editor.move(i, delta))

    def add_example(self, index: int) -> None:
        if self.editor.locked:
            return
        self.editor.add_example(index)
        self.refresh_skills(select=len(self.editor.skills) - 1)

    def save_skills(self) -> bool:
        """Guarda en el perfil. False (y aviso) si hay errores."""
        try:
            self.editor.save()
        except (ValueError, ConfigError, OSError) as e:
            self._notify("No se pudieron guardar las habilidades", str(e))
            return False
        self.refresh_skills(select=self._selected_skill())
        return True

    def _confirm_unsaved(self, what: str) -> bool:
        """Si hay cambios sin guardar pregunta si guardar. False = cancelar la accion."""
        if not self.editor.dirty:
            return True
        ans = self._ask_save("Habilidades sin guardar",
                             f"Hay cambios sin guardar en las habilidades de '{self.editor.personaje}'.\n"
                             f"Guardarlos antes de {what}?")
        if ans is None:
            return False
        if ans:
            return self.save_skills()
        return True

    def _on_profile_selected(self, kind: str) -> None:
        if kind == "personaje":
            new = self.vars["personaje"].get()
            if new != self._last_personaje:
                if not self._confirm_unsaved("cambiar de personaje"):
                    self.vars["personaje"].set(self._last_personaje)
                    return
                self._last_personaje = new
                self.editor.load(new)
                self.refresh_skills()
        if kind == "zona":
            new = self.vars["zona"].get()
            if new != self._last_zona:
                if not (self._confirm_unsaved_priority("cambiar de zona") and self._confirm_unsaved_farm("cambiar de zona")):
                    self.vars["zona"].set(self._last_zona)
                    return
                self._last_zona = new
                self.prio.load(new)
                self.refresh_priority()
                self.farm.load(new)
                self.refresh_farm_mode()
        self.refresh_overrides()

    # ------------------------------------------------------------------ ventana
    def _apply_window_prefs(self) -> None:
        """Posicion/tamano guardados, siempre encima y sin minimizar."""
        r = self.root
        if self.opts.geometry and not getattr(self, "_geometry_applied", False):
            try:
                r.geometry(self.opts.geometry)
            except tk.TclError:
                logger.warning("Geometria guardada invalida: %s", self.opts.geometry)
            self._geometry_applied = True
        r.attributes("-topmost", bool(self.top_var.get()))
        self._disable_minimize()

    def _disable_minimize(self) -> None:
        """Sin boton de minimizar (Win32: quita WS_MINIMIZEBOX) y, por si algo la minimiza igual (Win+D, Win+M,
        la barra de tareas), vuelve sola con `<Unmap>`. Es lo mas confiable en Windows: quitar solo el boton no
        alcanza para Win+M, y reaccionar solo al evento deja el boton visible."""
        r = self.root
        if sys.platform == "win32":
            try:
                import ctypes
                r.update_idletasks()
                user32 = ctypes.windll.user32
                hwnd = user32.GetParent(r.winfo_id()) or r.winfo_id()
                style = user32.GetWindowLongW(hwnd, GWL_STYLE)
                user32.SetWindowLongW(hwnd, GWL_STYLE, style & ~WS_MINIMIZEBOX)
            except Exception:  # noqa: BLE001 - el respaldo de abajo cubre este caso
                logger.exception("No se pudo quitar el boton de minimizar")
        r.bind("<Unmap>", self._on_unmap)

    def _on_unmap(self, event: tk.Event) -> None:
        if event.widget is self.root and self.root.state() == "iconic":
            self.root.after(50, self._restore)

    def _restore(self) -> None:
        try:
            if self.root.state() == "iconic":
                self.root.deiconify()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ opciones
    def read_options(self) -> ctl.UiOptions:
        """Opciones actuales de los widgets (ValueError si el tiempo es invalido)."""
        o = ctl.UiOptions(max_minutes=ctl.parse_minutes(self.minutes_var.get()), debug=bool(self.debug_var.get()),
                          observar=bool(self.observe_var.get()), always_on_top=bool(self.top_var.get()),
                          geometry=self._geometry())
        for kind, _ in KIND_LABELS:
            setattr(o, kind, self.vars[kind].get())
        return o

    def _geometry(self) -> str:
        try:
            g = self.root.geometry()
        except tk.TclError:
            return self.opts.geometry
        return g if ctl._GEOMETRY_RE.fullmatch(g) else self.opts.geometry

    def save_settings(self) -> None:
        try:
            o = self.read_options()
        except ValueError:
            o = ctl.UiOptions(**{**self.opts.__dict__, "geometry": self._geometry()})
        self.opts = o
        ctl.save_settings(o, self.settings_path)

    def refresh_overrides(self) -> None:
        o = ctl.UiOptions(**{**self.opts.__dict__, **{k: self.vars[k].get() for k, _ in KIND_LABELS}})
        warnings = ctl.override_warnings(o, self.config_path, self.profiles_root)
        self.warn_label.configure(text="\n".join(warnings))

    # ------------------------------------------------------------------ acciones
    def start(self) -> None:
        if self.phase != ctl.STOPPED:
            return
        try:
            o = self.read_options()
        except ValueError as e:
            self._notify("Opciones invalidas", str(e))
            return
        errors = ctl.validate_options(o)
        if errors:
            self._notify("Opciones invalidas", "\n".join(errors))
            return
        if not self._confirm_unsaved("iniciar (el bot usa el perfil guardado)"):
            return
        if not self._confirm_unsaved_priority("iniciar (el bot usa el perfil guardado)"):
            return
        if not self._confirm_unsaved_farm("iniciar (el bot usa el perfil guardado)"):
            return
        self.opts = o
        ctl.save_settings(o, self.settings_path)
        try:
            self.proc.start(self.command_builder(o))
        except OSError as e:
            self._notify("No se pudo iniciar", str(e))
            return
        self._got_end, self.paused, self._last_log_error = False, False, ""
        self._last_status = None
        self.info_var.set("")
        for v in self.stat_vars.values():
            v.set("-")
        self.live_tree.delete(*self.live_tree.get_children())
        self._append_log("--- iniciando el bot ---", "INFO")
        self._set_phase(ctl.RUNNING)

    def toggle_pause(self) -> None:
        if self.phase == ctl.RUNNING:
            self.proc.send(ctl.CMD_PAUSE)   # el estado real vuelve en el proximo @@STATUS

    def stop(self) -> None:
        if self.phase != ctl.RUNNING:
            return
        self._set_phase(ctl.STOPPING)
        self.proc.send(ctl.CMD_STOP)
        self._kill_id = self.root.after(int(ctl.STOP_GRACE_S * 1000), self._force_kill)

    def _force_kill(self) -> None:
        self._kill_id = None
        if self.proc.running:
            self._append_log("El bot no termino a tiempo: se lo termina a la fuerza.", "WARNING")
            self.proc.kill()

    def on_close(self) -> None:
        if self.proc.running:
            if not self._confirm("Salir", "El bot esta corriendo. Se va a detener y se cierra la ventana. Continuar?"):
                return
            self.proc.stop(ctl.STOP_GRACE_S)
        self.save_settings()
        if self._after_id is not None:
            self.root.after_cancel(self._after_id)
            self._after_id = None
        self.root.destroy()

    # ------------------------------------------------------------------ estado y polling
    def _set_phase(self, phase: str) -> None:
        self.phase = phase
        self.refresh_buttons()

    def refresh_buttons(self) -> None:
        b = ctl.button_state(self.phase, self.paused)
        self.btn_start.configure(state="normal" if b.start else "disabled")
        self.btn_pause.configure(state="normal" if b.pause else "disabled", text=b.pause_label)
        self.btn_stop.configure(state="normal" if b.stop else "disabled")
        for cb in self.combos.values():
            cb.configure(state="readonly" if b.options else "disabled")
        for w in (self.minutes_entry, *self.checks):
            w.configure(state="normal" if b.options else "disabled")
        self.paused_label.configure(text="PAUSADO" if self.paused and self.phase == ctl.RUNNING else "")
        self.editor.locked = not b.options
        for w in self.skill_buttons:
            w.configure(state="normal" if b.options else "disabled")
        self.prio.locked = not b.options
        for w in (self.prio_check, *self.prio_buttons):
            w.configure(state="normal" if b.options else "disabled")
        self.refresh_priority(select=self._selected_priority())
        self.farm.locked = not b.options
        for w in (self.farm_combo, self.farm_key_entry, self.farm_secs_entry, self.farm_save_btn):
            w.configure(state=("readonly" if w is self.farm_combo else "normal") if b.options else "disabled")
        self.refresh_farm_mode()
        self.refresh_skills(select=self._selected_skill())
        if hasattr(self, "btn_rename"):
            self._refresh_items_state()

    def _poll(self) -> None:
        try:
            for line in self.proc.drain():
                if line is None:
                    self._on_exit()
                else:
                    self.handle_line(line)
        except Exception:  # noqa: BLE001 - un error de formato no debe matar el polling
            logger.exception("Error procesando la salida del bot")
        self._after_id = self.root.after(POLL_MS, self._poll)

    def handle_line(self, raw: str) -> None:
        p = ctl.parse_line(raw)
        if p.kind == "status" and p.status is not None:
            self._show_status(p.status)
        elif p.kind == "end" and p.end is not None:
            self._got_end = True
            self.info_var.set(f"Termino: {p.end.get('reason', '-')}")
        elif p.text.strip():
            self._append_log(p.text, p.level)
            if p.level in ("ERROR", "CRITICAL"):
                self._last_log_error = p.text

    def _show_status(self, s: ctl.Status) -> None:
        v = self.stat_vars
        v["state"].set(s.state or "-")
        v["metins"].set(str(s.metins))
        v["items"].set(str(s.items))
        v["captchas"].set(str(s.captchas))
        v["unstucks"].set(str(s.unstucks))
        v["time"].set(ctl.format_elapsed(s.elapsed_s))
        v["skills_cast"].set(str(s.skills_cast))
        v["lures"].set(str(s.lures) if s.farm_mode == "bichos" else "-")
        self.live_tree.delete(*self.live_tree.get_children())
        for name, n in hv.live_item_rows(s.items_by_type):
            self.live_tree.insert("", "end", values=(name, n))
        self.refresh_skills(select=self._selected_skill(), status=s)
        if s.phase == "countdown" and s.countdown > 0:
            self.info_var.set(f"Arrancando en {s.countdown}... (pone el juego al frente)")
        elif not self._got_end:
            self.info_var.set("")
        if s.paused != self.paused:
            self.paused = s.paused
            self.refresh_buttons()

    def _on_exit(self) -> None:
        """El subproceso cerro stdout: termino. Si fallo sin @@END, se avisa con el ultimo error del log."""
        self.proc.wait_closed(2.0)
        rc = self.proc.returncode
        if self._kill_id is not None:
            self.root.after_cancel(self._kill_id)
            self._kill_id = None
        was_stopping = self.phase == ctl.STOPPING
        self.paused = False
        self._set_phase(ctl.STOPPED)
        if rc not in (0, None) and not self._got_end and not was_stopping:
            msg = self._last_log_error or "El bot termino con un error antes de arrancar."
            self.info_var.set(f"El bot termino con error (codigo {rc}): {msg}")
            self._append_log(f"El bot termino con error (codigo {rc})", "ERROR")
            self._notify("El bot no pudo arrancar", f"{msg}\n\nRevisa el log de la ventana.")
        elif not self._got_end:
            self.info_var.set("Detenido")
        self._append_log("--- bot detenido ---", "INFO")
        self.refresh_history()   # la corrida que termino ya quedo en el archivo
        self.refresh_items()

    def _append_log(self, text: str, level: str = "INFO") -> None:
        lg = self.log
        at_bottom = lg.yview()[1] >= 0.999
        lg.configure(state="normal")
        lg.insert("end", text + "\n", level if level in ("WARNING", "ERROR", "CRITICAL") else ())
        extra = int(lg.index("end-1c").split(".")[0]) - LOG_MAX_LINES
        if extra > 1:
            lg.delete("1.0", f"{extra}.0")
        lg.configure(state="disabled")
        if at_bottom:
            lg.see("end")


class SkillDialog:
    """Dialogo de edicion de una habilidad con validacion en vivo (Aceptar deshabilitado con errores)."""

    def __init__(self, parent: tk.Misc, skill: dict | None, controls: dict[str, str]) -> None:
        self.controls = controls
        self.result: dict | None = None
        sk = skill or se.new_skill()
        self.top = tk.Toplevel(parent)
        self.top.title("Habilidad")
        self.top.transient(parent)
        self.vars = {"name": tk.StringVar(value=sk.get("name", "")), "key": tk.StringVar(value=sk.get("key", "")),
                     "duration_s": tk.StringVar(value=f"{sk.get('duration_s', se.DEFAULT_DURATION_S):g}"),
                     "animation_s": tk.StringVar(value=f"{sk.get('animation_s', se.DEFAULT_ANIMATION_S):g}")}
        self.dismount = tk.BooleanVar(value=bool(sk.get("requires_dismount", True)))
        self.enabled = tk.BooleanVar(value=bool(sk.get("enabled", True)))
        f = ttk.Frame(self.top, padding=8)
        f.pack(fill="both", expand=True)
        for i, (k, label) in enumerate((("name", "Nombre"), ("key", "Tecla (ej. F1, 4, ctrl+g)"), ("duration_s", "Duracion (s)"),
                                        ("animation_s", "Animacion (s)"))):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="w", pady=2, padx=(0, 8))
            ttk.Entry(f, textvariable=self.vars[k], width=24).grid(row=i, column=1, sticky="ew", pady=2)
            self.vars[k].trace_add("write", lambda *_: self.validate())
        ttk.Checkbutton(f, text="Bajarse del caballo para lanzarla", variable=self.dismount).grid(
            row=4, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(f, text="Activa", variable=self.enabled).grid(row=5, column=0, columnspan=2, sticky="w")
        self.msg = tk.Label(f, text="", justify="left", anchor="w", wraplength=320)
        self.msg.grid(row=6, column=0, columnspan=2, sticky="ew", pady=4)
        bar = ttk.Frame(f)
        bar.grid(row=7, column=0, columnspan=2, sticky="e")
        self.btn_ok = ttk.Button(bar, text="Aceptar", command=self.accept)
        self.btn_ok.pack(side="left", padx=2)
        ttk.Button(bar, text="Cancelar", command=self.top.destroy).pack(side="left", padx=2)
        f.columnconfigure(1, weight=1)
        self.validate()
        try:
            self.top.grab_set()
        except tk.TclError:
            pass

    def read(self) -> tuple[dict, list[str]]:
        """Habilidad armada con los campos y los errores de parseo de los numeros."""
        errs, nums = [], {}
        for k, label in (("duration_s", "La duracion"), ("animation_s", "La animacion")):
            try:
                nums[k] = se.parse_seconds(self.vars[k].get(), label)
            except ValueError as e:
                errs.append(str(e))
                nums[k] = 0.0
        sk = {"name": self.vars["name"].get().strip(), "key": self.vars["key"].get().strip(), "duration_s": nums["duration_s"],
              "requires_dismount": bool(self.dismount.get()), "animation_s": nums["animation_s"],
              "enabled": bool(self.enabled.get())}
        return sk, errs

    def validate(self) -> bool:
        sk, errs = self.read()
        if not errs:
            errs = se.validate_skill(sk)
        warns = se.key_warnings(sk, self.controls) if not errs else []
        self.msg.configure(text="\n".join(errs or warns), fg=COLOR_ERROR if errs else COLOR_WARN)
        self.btn_ok.configure(state="disabled" if errs else "normal")
        return not errs

    def accept(self) -> None:
        if self.validate():
            self.result = self.read()[0]
            self.top.destroy()


def run() -> None:
    """Abre la ventana y entra al mainloop (punto de entrada de gui.py)."""
    root = tk.Tk()
    App(root)
    root.mainloop()
