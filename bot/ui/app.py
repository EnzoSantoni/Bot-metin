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
from bot.ui import calibrate_runner as cr
from bot.ui import controller as ctl
from bot.ui import history_view as hv
from bot.ui import skills_editor as se
from bot.ui import validators as vd
from bot.ui import zone_view
from bot.ui.calibrate_dialog import CalibrateDialog
from bot.ui.farm_mode_editor import MODE_LABELS, MODES, FarmModeEditor
from bot.ui.metin_editor import MetinEditor
from bot.ui.widgets import (COLOR_ERROR, COLOR_MUTED, COLOR_WARN, GAP, GAP_S, PAD, WRAP, Tooltip, ValidatedEntry, add_tooltip,
                            apply_style, note, section, tab_frame)

logger = logging.getLogger(__name__)

_minutes_error = vd.non_negative_number
POLL_MS = 100
LOG_MAX_LINES = 200
REMINDER = "F12 o mouse a la esquina sup. izq.: detener  |  F11: pausa"
KIND_LABELS = (("servidor", "Servidor"), ("personaje", "Personaje"), ("zona", "Zona"))
MIN_SIZE = (900, 640)
DEFAULT_GEOMETRY = "1000x800"    # pensado para un segundo monitor; se pisa con la geometria guardada
ZONE_CANVAS_SIZE = (640, 420)
SPOT_R, PLAYER_R = 4, 6          # radios (px) de los puntos del mapa y del personaje en el dibujo de la zona
ZONE_COLORS = {"zone": "#2e8b57", "zone_fill": "#e3f4e8", "tolerance": "#e08a00", "vertex": "#d4a900", "spot": "#1a73e8",
               "trail": "#e6a1a1", "player": "#d93025"}
TAB_TITLES = (("control", "Control"), ("metin", "Metin"), ("zona", "Zona"), ("bichos", "Modo bichos"),
              ("personaje", "Personaje"), ("habilidades", "Habilidades"), ("historial", "Historial"), ("objetos", "Objetos"))
PROFILE_TIPS = {"servidor": "Perfil del servidor: teclas del juego y particularidades del servidor.",
                "personaje": "Perfil del personaje: habilidades y cómo se ve su nombre.",
                "zona": "Perfil de la zona: polígono de farmeo, tipos de metin y modo de farmeo."}
STAT_FIELDS = (("state", "Estado", "Qué está haciendo el bot ahora."), ("time", "Tiempo", "Tiempo de la corrida."),
               ("metins", "Metin rotos", "Metin rotos en esta corrida."), ("items", "Objetos", "Objetos recogidos."),
               ("captchas", "Captchas", "Captchas detectados."), ("unstucks", "Destrabes", "Veces que se destrabó."),
               ("skills_cast", "Habilidades lanzadas", "Habilidades que el bot lanzó."),
               ("lures", "Señuelos usados", "Señuelos usados (solo modo bichos)."),
               ("deaths", "Muertes (revividas)", "Veces que murió el personaje y cuántas veces logró revivir."))
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
        self.metin = MetinEditor(profiles_root)
        self.metin.load(self.opts.zona, self._labels_dir())
        self.farm = FarmModeEditor(profiles_root)
        self.farm.load(self.opts.zona)
        self._last_zona = self.opts.zona
        self._calib: CalibrateDialog | None = None
        self._override_warnings: list[str] = []
        self.trail = zone_view.Trail()
        self.cur_pos: tuple[float, float] | None = None
        self._zone_metins = -1
        self._zone = zone_view.ZoneData()
        self._spots_px: list[tuple[float, float]] = []
        self._build()
        self._apply_window_prefs()
        self.refresh_overrides()
        self.refresh_buttons()
        self._after_id = root.after(POLL_MS, self._poll)

    # ------------------------------------------------------------------ construccion
    def _build(self) -> None:
        r = self.root
        apply_style(r)
        r.title("Metin Bot")
        r.minsize(*MIN_SIZE)
        r.protocol("WM_DELETE_WINDOW", self.on_close)
        self.notebook = ttk.Notebook(r)
        self.notebook.pack(fill="both", expand=True, padx=GAP_S, pady=GAP_S)
        self.tabs: dict[str, ttk.Frame] = {}
        for key, title in TAB_TITLES:
            self.tabs[key] = tab_frame(self.notebook, title)
        self._build_control(self.tabs["control"])
        self._build_metin(self.tabs["metin"])
        self._build_zone(self.tabs["zona"])
        self._build_bichos(self.tabs["bichos"])
        self._build_character(self.tabs["personaje"])
        self._build_skills(self.tabs["habilidades"])
        self._build_history(self.tabs["historial"])
        self._build_items(self.tabs["objetos"])
        self._refresh_tab_titles()

    def _refresh_tab_titles(self) -> None:
        """Titulo de cada pestana que edita perfiles con '*' si tiene cambios sin guardar."""
        dirty = {"metin": self.metin.dirty, "bichos": self.farm.dirty, "habilidades": self.editor.dirty}
        for key, title in TAB_TITLES:
            if key in self.tabs:
                self.notebook.tab(self.tabs[key], text=ctl.tab_title(title, dirty.get(key, False)))

    def _build_control(self, tab: ttk.Frame) -> None:
        """Pestana Control en dos columnas: izquierda perfiles y comandos, derecha estadisticas y log."""
        tab.columnconfigure(0, weight=1, uniform="cols")
        tab.columnconfigure(1, weight=2, uniform="cols")
        tab.rowconfigure(0, weight=1)
        left, right = ttk.Frame(tab), ttk.Frame(tab)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, GAP))
        right.grid(row=0, column=1, sticky="nsew")

        prof = section(left, "Perfiles")
        prof.pack(fill="x")
        self.combos: dict[str, ttk.Combobox] = {}
        self.vars: dict[str, tk.StringVar] = {}
        for i, (kind, label) in enumerate(KIND_LABELS):
            ttk.Label(prof, text=label).grid(row=i, column=0, sticky="w", padx=(0, GAP), pady=GAP_S)
            var = tk.StringVar(value=getattr(self.opts, kind))
            cb = ttk.Combobox(prof, textvariable=var, values=self.profiles.get(kind, []), state="readonly", width=34)
            cb.grid(row=i, column=1, sticky="ew", pady=GAP_S)
            cb.bind("<<ComboboxSelected>>", lambda _e, k=kind: self._on_profile_selected(k))
            add_tooltip(cb, PROFILE_TIPS[kind])
            self.vars[kind], self.combos[kind] = var, cb
        prof.columnconfigure(1, weight=1)
        self.warn_label = ttk.Label(prof, text="", style="Warn.TLabel", justify="left", wraplength=WRAP - 120)
        self.warn_label.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(GAP_S, 0))

        farm = section(left, "Modo de farmeo")
        farm.pack(fill="x", pady=(GAP, 0))
        ttk.Label(farm, text="Modo:").grid(row=0, column=0, sticky="w", padx=(0, GAP))
        self.quick_mode_var = tk.StringVar(value=MODE_LABELS[self.farm.mode])
        self.quick_combo = ttk.Combobox(farm, textvariable=self.quick_mode_var, values=[t for _, t in MODES],
                                        state="readonly", width=12)
        self.quick_combo.grid(row=0, column=1, sticky="w")
        self.quick_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_quick_mode())
        add_tooltip(self.quick_combo, "Metin: rompe metin. Bichos: pelea con los monstruos que atrae con el señuelo. "
                                      "Se guarda en el perfil de zona. Los detalles de bichos están en su pestaña.")
        ttk.Label(farm, text="Los detalles de bichos están en la pestaña Modo bichos.", style="Muted.TLabel",
                  wraplength=WRAP - 120).grid(row=1, column=0, columnspan=2, sticky="w", pady=(GAP_S, 0))

        opt = section(left, "Opciones")
        opt.pack(fill="x", pady=(GAP, 0))
        ttk.Label(opt, text="Tiempo máximo (min)").grid(row=0, column=0, sticky="nw", pady=GAP_S)
        self.minutes_field = ValidatedEntry(opt, _minutes_error, value=f"{self.opts.max_minutes:g}", width=8,
                                            tooltip="Minutos que corre el bot antes de detenerse solo. 0 = sin límite.")
        self.minutes_field.grid(row=0, column=1, sticky="w", padx=GAP)
        self.minutes_var, self.minutes_entry = self.minutes_field.var, self.minutes_field.entry
        self.debug_var = tk.BooleanVar(value=self.opts.debug)
        self.observe_var = tk.BooleanVar(value=self.opts.observar)
        self.top_var = tk.BooleanVar(value=self.opts.always_on_top)
        self.checks = [ttk.Checkbutton(opt, text="Debug", variable=self.debug_var),
                       ttk.Checkbutton(opt, text="Observar (sin input)", variable=self.observe_var)]
        add_tooltip(self.checks[0], "Guarda imágenes de lo que ve el bot en la carpeta debug. Útil para revisar errores.")
        add_tooltip(self.checks[1], "El bot mira y registra pero no manda teclas ni clics al juego. Sirve para probar la detección.")
        self.checks[0].grid(row=1, column=0, sticky="w")
        self.checks[1].grid(row=1, column=1, columnspan=2, sticky="w", padx=GAP)
        top_check = ttk.Checkbutton(opt, text="Siempre encima", variable=self.top_var, command=self._apply_window_prefs)
        top_check.grid(row=2, column=0, sticky="w")
        add_tooltip(top_check, "Mantiene esta ventana por encima de las demás.")

        bar = ttk.Frame(left)
        bar.pack(fill="x", pady=(GAP, 0))
        self.btn_start = ttk.Button(bar, text="Iniciar", command=self.start)
        self.btn_pause = ttk.Button(bar, text="Pausar", command=self.toggle_pause)
        self.btn_stop = ttk.Button(bar, text="Detener", command=self.stop)
        for b, tip in ((self.btn_start, "Arranca el bot con los perfiles elegidos (cuenta regresiva para poner el juego al frente)."),
                       (self.btn_pause, "Pausa o reanuda el bot. También con F11."),
                       (self.btn_stop, "Detiene el bot. También con F12 o llevando el mouse a la esquina superior izquierda.")):
            b.pack(side="left", expand=True, fill="x", padx=GAP_S)
            add_tooltip(b, tip)
        self.paused_label = ttk.Label(left, text="", style="Paused.TLabel", anchor="center")
        self.paused_label.pack(fill="x")
        ttk.Label(left, text=REMINDER, style="Muted.TLabel", wraplength=WRAP - 120, justify="left").pack(fill="x", pady=(GAP_S, 0))

        st = section(right, "Estadísticas en vivo")
        st.pack(fill="x")
        self.stat_vars: dict[str, tk.StringVar] = {}
        for i, (key, label, tip) in enumerate(STAT_FIELDS):
            lab = ttk.Label(st, text=label + ":")
            lab.grid(row=i // 2, column=(i % 2) * 2, sticky="w", padx=(0, GAP_S), pady=1)
            add_tooltip(lab, tip)
            v = tk.StringVar(value="-")
            ttk.Label(st, textvariable=v, style="Big.TLabel").grid(row=i // 2, column=(i % 2) * 2 + 1, sticky="w",
                                                                    padx=(0, PAD * 2))
            self.stat_vars[key] = v
        self.info_var = tk.StringVar(value="")
        ttk.Label(st, textvariable=self.info_var, justify="left", wraplength=WRAP + 40).grid(
            row=(len(STAT_FIELDS) + 1) // 2, column=0, columnspan=4, sticky="ew", pady=(GAP_S, 0))

        live = section(right, "Objetos de esta sesión")
        live.pack(fill="x", pady=(GAP, 0))
        self.live_tree = ttk.Treeview(live, columns=("name", "count"), show="headings", height=3, selectmode="none")
        self.live_tree.heading("name", text="Objeto")
        self.live_tree.heading("count", text="Cantidad")
        self.live_tree.column("name", width=300, anchor="w")
        self.live_tree.column("count", width=80, anchor="center", stretch=False)
        self.live_tree.pack(fill="x")

        lf = section(right, "Log")
        lf.pack(fill="both", expand=True, pady=(GAP, 0))
        self.log = tk.Text(lf, height=10, wrap="word", state="disabled", font=("Consolas", 9))
        sb = ttk.Scrollbar(lf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log.tag_configure("WARNING", foreground=COLOR_WARN)
        self.log.tag_configure("ERROR", foreground=COLOR_ERROR)
        self.log.tag_configure("CRITICAL", foreground=COLOR_ERROR)

    def _on_quick_mode(self) -> None:
        """Selector rapido de Control: cambia el modo y lo guarda en el perfil de zona (con el resto de bichos como esta)."""
        if self.farm.locked:
            self.refresh_farm_mode()
            return
        mode = next((k for k, t in MODES if t == self.quick_mode_var.get()), "metin")
        self.farm.set_values(mode, self.farm.lure_key, self.farm.seconds)
        if not self.save_farm_mode():
            self.refresh_farm_mode()

    # ------------------------------------------------------------------ personaje (placeholder de la tanda 2)
    def _build_character(self, tab: ttk.Frame) -> None:
        box = section(tab, "Personaje")
        box.pack(fill="x")
        ttk.Label(box, text="Próximamente", style="Big.TLabel").pack(anchor="w")
        note(box, "Acá vas a poder crear y duplicar perfiles de personaje y de zona, y configurar recoger a pie y revivir.").pack(
            anchor="w", pady=(GAP_S, 0))

    # ------------------------------------------------------------------ modo bichos
    def _build_bichos(self, tab: ttk.Frame) -> None:
        note(tab, "Modo bichos: el bot usa un señuelo cada cierto tiempo y pelea con los monstruos que llegan.").pack(
            fill="x", pady=(0, GAP))
        self._build_farm_mode(tab)

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

    # ------------------------------------------------------------------ metin (tipos y prioridad)
    METIN_COLS = (("prio", "Prioridad", 80), ("attack", "Atacar", 60), ("name", "Tipo de metin", 220), ("tpl", "Plantilla", 70))

    def _build_metin(self, tab: ttk.Frame) -> None:
        note(tab, "Tipos de metin de la zona elegida. Marcá los que el bot tiene que atacar (clic en la casilla). Con "
                  "\"Elegir por prioridad\" el bot va primero por los de más arriba.", wrap=WRAP + 120).pack(fill="x")
        self.metin_warn = ttk.Label(tab, text="", style="Warn.TLabel", justify="left", wraplength=WRAP + 120)
        self.metin_warn.pack(fill="x", pady=(GAP_S, 0))
        box = section(tab, "Tipos de metin")
        box.pack(fill="both", expand=True, pady=(GAP, 0))
        self.prio_var = tk.BooleanVar(value=self.metin.enabled)
        self.prio_check = ttk.Checkbutton(box, text="Elegir por prioridad (arriba = primero)", variable=self.prio_var,
                                          command=self._on_prio_toggle)
        self.prio_check.pack(anchor="w")
        add_tooltip(self.prio_check, "Si está apagado, el bot ataca el metin más cercano de cualquier tipo marcado.")
        frame = ttk.Frame(box)
        frame.pack(fill="both", expand=True, pady=GAP)
        self.metin_tree = ttk.Treeview(frame, columns=[c[0] for c in self.METIN_COLS], show="headings", height=8,
                                       selectmode="browse")
        for cid, label, width in self.METIN_COLS:
            self.metin_tree.heading(cid, text=label)
            self.metin_tree.column(cid, width=width, anchor="w" if cid == "name" else "center", stretch=cid == "name")
        sb = ttk.Scrollbar(frame, command=self.metin_tree.yview)
        self.metin_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.metin_tree.pack(side="left", fill="both", expand=True)
        self.metin_tree.bind("<Button-1>", self._on_metin_click)
        self.metin_tree.bind("<space>", lambda _e: self.toggle_metin(self._selected_metin()))
        add_tooltip(self.metin_tree, "Clic en la casilla de Atacar para marcar o desmarcar. Plantilla = el bot ya aprendió "
                                     "cómo se ve la etiqueta de ese metin.")
        bar = ttk.Frame(box)
        bar.pack(fill="x")
        self.prio_buttons: list[ttk.Widget] = []
        for text, cmd, w, tip in (("↑", lambda: self.move_priority(-1), 3, "Sube el tipo elegido en la prioridad."),
                                  ("↓", lambda: self.move_priority(1), 3, "Baja el tipo elegido en la prioridad."),
                                  ("Guardar", self.save_priority, 8, "Guarda en el perfil de zona (con copia .bak).")):
            b = ttk.Button(bar, text=text, width=w, command=cmd)
            b.pack(side="left", padx=GAP_S)
            add_tooltip(b, tip)
            self.prio_buttons.append(b)
        self.btn_add_metin = ttk.Button(bar, text="Agregar tipo nuevo", command=self.add_metin_type)
        self.btn_add_metin.pack(side="right", padx=GAP_S)
        add_tooltip(self.btn_add_metin, "Enseña al bot un tipo de metin nuevo (abre una guía paso a paso).")
        self.prio_state = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.prio_state, style="Warn.TLabel", wraplength=WRAP + 120, justify="left").pack(
            fill="x", pady=(GAP_S, 0))
        self.refresh_priority()

    def refresh_priority(self, select: int | None = None) -> None:
        """Redibuja la tabla, el interruptor y el indicador de cambios sin guardar."""
        self.metin_tree.delete(*self.metin_tree.get_children())
        for i, row in enumerate(self.metin.rows()):
            self.metin_tree.insert("", "end", iid=str(i), values=row)
        if select is not None and 0 <= select < len(self.metin.types):
            self.metin_tree.selection_set(str(select))
        self.prio_var.set(self.metin.enabled)
        parts = []
        parts += self.metin.errors()
        if self.metin.dirty:
            parts.append("sin guardar")
        if self.metin.locked:
            parts.append("bloqueado mientras el bot corre")
        self.prio_state.set(" - ".join(parts))
        self.metin_warn.configure(text="\n".join(self.metin.warnings() + self._override_text("target_metin_names")))
        if hasattr(self, "tabs"):
            self._refresh_tab_titles()

    def _override_text(self, key: str) -> list[str]:
        """Avisos de config.json que pisan `key` (para que se vea por que guardar en el perfil no cambia nada)."""
        return [w for w in getattr(self, "_override_warnings", []) if key in w]

    def _selected_metin(self) -> int | None:
        sel = self.metin_tree.selection()
        return int(sel[0]) if sel else None

    def _on_metin_click(self, event: tk.Event) -> None:
        if self.metin_tree.identify_region(event.x, event.y) != "cell" or self.metin_tree.identify_column(event.x) != "#2":
            return
        row = self.metin_tree.identify_row(event.y)
        if row:
            self.metin_tree.selection_set(row)
            self.toggle_metin(int(row))

    def toggle_metin(self, index: int | None) -> None:
        """Marca o desmarca "Atacar" del tipo `index`."""
        if index is None or self.metin.locked or not 0 <= index < len(self.metin.types):
            return
        self.metin.set_attack(index, not self.metin.types[index].attack)
        self.refresh_priority(select=index)

    def _on_prio_toggle(self) -> None:
        if self.metin.locked:
            self.prio_var.set(self.metin.enabled)
            return
        self.metin.set_enabled(self.prio_var.get())
        self.refresh_priority(select=self._selected_metin())

    def move_priority(self, delta: int) -> None:
        i = self._selected_metin()
        if i is None or self.metin.locked:
            return
        self.refresh_priority(select=self.metin.move(i, delta))

    def save_priority(self) -> bool:
        """Guarda tipos y prioridad en el perfil de zona. False (y aviso) si no es valido."""
        try:
            self.metin.save()
        except (ValueError, ConfigError, OSError) as e:
            self._notify("No se pudieron guardar los tipos de metin", str(e))
            return False
        self.refresh_priority(select=self._selected_metin())
        return True

    def _confirm_unsaved_priority(self, what: str) -> bool:
        """Como `_confirm_unsaved`, para los tipos de metin de la zona. False = cancelar la accion."""
        if not self.metin.dirty:
            return True
        ans = self._ask_save("Tipos de metin sin guardar",
                             f"Hay cambios sin guardar en los tipos de metin de '{self.metin.zona}'.\n"
                             f"¿Guardarlos antes de {what}?")
        if ans is None:
            return False
        return self.save_priority() if ans else True

    def _labels_dir(self) -> Path:
        return ctl.labels_dir(self.config_path, self.profiles_root)

    def reload_metin_types(self) -> None:
        """Vuelve a leer los tipos (perfil de zona + plantillas) descartando lo no guardado."""
        self.metin.reload_types(self._labels_dir())
        self.refresh_priority()

    def add_metin_type(self) -> None:
        """Abre la guia de `calibrate.py metin` (subproceso con salida en vivo)."""
        if self.metin.locked or self._calib is not None or not self._confirm_unsaved_priority("agregar un tipo nuevo"):
            return
        existing = self.metin.names
        zona = self.vars["zona"].get()
        self._open_calibrate(
            cr.METIN_TITLE, cr.METIN_STEPS,
            lambda name: cr.build_calibrate_command("metin", nombre=name, servidor=self.vars["servidor"].get(),
                                                    personaje=self.vars["personaje"].get(), zona=zona,
                                                    config=self._calib_config()),
            self._after_metin_calibrate, name_label="Nombre del tipo:",
            name_validator=lambda t: cr.validate_metin_name(t, existing))

    def _after_metin_calibrate(self, rc: int | None) -> None:
        self.reload_metin_types()
        self.refresh_overrides()
        self.refresh_zone()

    # ------------------------------------------------------------------ calibrate (ventanas guiadas)
    def _calib_config(self) -> str | None:
        """`--config` para calibrate.py: solo si la ventana usa una config distinta de la del proyecto."""
        return None if self.config_path == "config.json" else str(self.config_path)

    def _open_calibrate(self, title: str, steps: tuple[str, ...], factory: Callable[[str], list[str]],
                        on_done: Callable[[int | None], None], **kw) -> None:
        def finish(rc: int | None) -> None:
            self._calib = None
            self.refresh_buttons()
            on_done(rc)
        self._calib = CalibrateDialog(self.root, title, steps, factory, finish, **kw)
        self.refresh_buttons()

    # ------------------------------------------------------------------ zona (dibujo)
    def _build_zone(self, tab: ttk.Frame) -> None:
        top = ttk.Frame(tab)
        top.pack(fill="x")
        self.btn_redo_zone = ttk.Button(top, text="Rehacer la zona", command=self.redo_zone)
        self.btn_redo_zone.pack(side="left")
        add_tooltip(self.btn_redo_zone, "Marcá de nuevo las esquinas de la zona de farmeo caminando por el juego (guía paso a paso).")
        btn_refresh = ttk.Button(top, text="Actualizar", command=self.refresh_zone)
        btn_refresh.pack(side="left", padx=GAP)
        add_tooltip(btn_refresh, "Vuelve a leer la zona y el mapa de metin.")
        self.zone_pos_var = tk.StringVar(value=zone_view.position_text(None, ""))
        ttk.Label(top, textvariable=self.zone_pos_var, style="Big.TLabel").pack(side="right")
        self.zone_note = ttk.Label(tab, text="", style="Warn.TLabel", justify="left", wraplength=WRAP * 2)
        self.zone_note.pack(fill="x", pady=(GAP_S, 0))
        box = section(tab, "Mapa de la zona")
        box.pack(fill="both", expand=True, pady=(GAP, 0))
        self.zone_canvas = tk.Canvas(box, background="#ffffff", highlightthickness=1, highlightbackground="#c4c7c5",
                                     width=ZONE_CANVAS_SIZE[0], height=ZONE_CANVAS_SIZE[1])
        self.zone_canvas.pack(fill="both", expand=True)
        self._zone_tip = Tooltip(self.zone_canvas, "")
        self._zone_tip_idx: int | None = None
        self.zone_canvas.bind("<Configure>", lambda _e: self.draw_zone())
        self.zone_canvas.bind("<Motion>", self._on_zone_motion)
        self.zone_canvas.bind("<Leave>", lambda _e: self._hide_zone_tip())
        note(box, "Verde: zona de farmeo. Naranja: franja de tolerancia. Puntos azules: donde se rompieron metin (pasá el mouse "
                  "para ver cuántas veces y hace cuánto). Punto rojo: el personaje, con su rastro.", wrap=WRAP * 2).pack(
            fill="x", pady=(GAP_S, 0))
        self.refresh_zone()

    def refresh_zone(self) -> None:
        """Relee la zona y el mapa de metin de los perfiles elegidos y redibuja."""
        self._zone = zone_view.load_zone_data(self.config_path, self.vars["servidor"].get(), self.vars["personaje"].get(),
                                              self.vars["zona"].get(), self.profiles_root)
        self.zone_note.configure(text=self._zone.error)
        self.draw_zone()

    def draw_zone(self) -> None:
        c = self.zone_canvas
        c.delete("all")
        z = self._zone
        w = max(c.winfo_width(), 50) if c.winfo_width() > 1 else ZONE_CANVAS_SIZE[0]
        h = max(c.winfo_height(), 50) if c.winfo_height() > 1 else ZONE_CANVAS_SIZE[1]
        if not z.polygon:
            c.create_text(w / 2, h / 2, text="No hay zona para dibujar.", fill=COLOR_MUTED)
            self._spots_px = []
            return
        tr = zone_view.fit_transform(z.polygon, z.tolerance, w, h)
        poly = zone_view.polygon_to_canvas(z.polygon, tr)
        for contour in zone_view.tolerance_outline(poly, z.tolerance * tr.scale, w, h):
            c.create_polygon([v for p in contour for v in p], outline=ZONE_COLORS["tolerance"], fill="", width=2, dash=(6, 4))
        c.create_polygon([v for p in poly for v in p], outline=ZONE_COLORS["zone"], fill=ZONE_COLORS["zone_fill"], width=2)
        for i, ((px, py), (gx, gy)) in enumerate(zip(poly, z.polygon), 1):
            c.create_oval(px - 4, py - 4, px + 4, py + 4, fill=ZONE_COLORS["vertex"], outline="")
            c.create_text(px + 8, py - 8, text=f"{i} ({gx:.0f}, {gy:.0f})", anchor="w", fill=COLOR_MUTED)
        self._spots_px = [tr.apply(s.x, s.y) for s in z.spots]
        for sx, sy in self._spots_px:
            c.create_oval(sx - SPOT_R, sy - SPOT_R, sx + SPOT_R, sy + SPOT_R, fill=ZONE_COLORS["spot"], outline="#ffffff")
        trail = [tr.apply(x, y) for x, y in self.trail.points]
        if len(trail) >= 2:
            c.create_line([v for p in trail for v in p], fill=ZONE_COLORS["trail"], width=2)
        if self.cur_pos is not None:
            px, py = tr.apply(*self.cur_pos)
            c.create_oval(px - PLAYER_R, py - PLAYER_R, px + PLAYER_R, py + PLAYER_R, fill=ZONE_COLORS["player"], outline="#ffffff", width=2)

    def _on_zone_motion(self, event: tk.Event) -> None:
        idx = zone_view.nearest_spot(self._spots_px, event.x, event.y)
        if idx == self._zone_tip_idx:
            return
        self._zone_tip_idx = idx
        if idx is None or idx >= len(self._zone.spots):
            self._zone_tip.hide()
        else:
            self._zone_tip.show(event.x_root + 14, event.y_root + 14, zone_view.spot_tooltip(self._zone.spots[idx]))

    def _hide_zone_tip(self) -> None:
        self._zone_tip_idx = None
        self._zone_tip.hide()

    def update_zone_position(self, s: ctl.Status) -> None:
        """Posicion y rastro del personaje de un @@STATUS (redibuja solo si cambio algo)."""
        changed = self.trail.add(s.pos) or s.pos != self.cur_pos
        self.cur_pos = s.pos
        self.zone_pos_var.set(zone_view.position_text(s.pos, s.zone_status))
        if s.metins != self._zone_metins:   # se rompio un metin: el mapa tiene un punto nuevo o uno actualizado
            self._zone_metins = s.metins
            self.refresh_zone()
        elif changed:
            self.draw_zone()

    def redo_zone(self) -> None:
        """Abre la guia de `calibrate.py zona` (subproceso con salida en vivo)."""
        if self.metin.locked or self._calib is not None:
            return
        self._open_calibrate(
            cr.ZONA_TITLE, cr.ZONA_STEPS,
            lambda _n: cr.build_calibrate_command("zona", servidor=self.vars["servidor"].get(),
                                                  personaje=self.vars["personaje"].get(), zona=self.vars["zona"].get(),
                                                  config=self._calib_config()),
            lambda rc: (self.refresh_overrides(), self.refresh_zone()))

    # ------------------------------------------------------------------ modo de farmeo
    def _build_farm_mode(self, parent: ttk.Frame) -> None:
        box = section(parent, "Modo de farmeo y señuelo")
        box.pack(fill="x")
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
        for w, tip in ((self.farm_combo, "Metin: rompe metin. Bichos: pelea con los monstruos que atrae con el señuelo."),
                       (self.farm_key_entry, "Tecla del juego que usa el señuelo (ej. f4). No puede ser F11 ni F12."),
                       (self.farm_secs_entry, "Cada cuántos segundos usa el señuelo. Tiene que ser mayor que 0."),
                       (self.farm_save_btn, "Guarda el modo y el señuelo en el perfil de zona (con copia .bak).")):
            add_tooltip(w, tip)
        self.farm_state = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.farm_state, style="Warn.TLabel").grid(row=1, column=0, columnspan=7, sticky="ew")
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
                               (self.farm_secs_var, self.farm.seconds), (self.quick_mode_var, MODE_LABELS[self.farm.mode])):
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
        if hasattr(self, "tabs"):
            self._refresh_tab_titles()

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
        if hasattr(self, "tabs"):
            self._refresh_tab_titles()

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
                self.metin.load(new, self._labels_dir())
                self.refresh_priority()
                self.farm.load(new)
                self.refresh_farm_mode()
        self.refresh_overrides()
        self.trail.clear()
        self.refresh_zone()

    # ------------------------------------------------------------------ ventana
    def _apply_window_prefs(self) -> None:
        """Posicion/tamano guardados, siempre encima y sin minimizar."""
        r = self.root
        if not getattr(self, "_geometry_applied", False):
            try:
                r.geometry(self.opts.geometry or DEFAULT_GEOMETRY)
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
        self._override_warnings = warnings
        self.warn_label.configure(text="\n".join(warnings))
        if hasattr(self, "metin_warn"):
            self.refresh_priority(select=self._selected_metin())

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
        self.trail.clear()
        self.cur_pos, self._zone_metins = None, -1
        self.zone_pos_var.set(zone_view.position_text(None, ""))
        self.draw_zone()
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
        if self._calib is not None:
            self._calib.kill()
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
        self.metin.locked = not b.options
        for w in (self.prio_check, *self.prio_buttons):
            w.configure(state="normal" if b.options else "disabled")
        idle = b.options and self._calib is None   # las guias de calibracion necesitan el bot detenido y de a una
        for w in (self.btn_add_metin, self.btn_redo_zone):
            w.configure(state="normal" if idle else "disabled")
        self.quick_combo.configure(state="readonly" if b.options else "disabled")
        self.refresh_priority(select=self._selected_metin())
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
        v["deaths"].set(f"{s.deaths} ({s.revives})")
        self.update_zone_position(s)
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
