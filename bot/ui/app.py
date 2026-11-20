"""Ventana tkinter del bot. Toda la logica no visual vive en `bot/ui/controller.py`; aca solo hay widgets y el
polling del subproceso con `after()` (el hilo de tkinter nunca se bloquea leyendo)."""
from __future__ import annotations

import logging
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable

from bot.config import ConfigError
from bot.history import load_history
from bot.item_catalog import ItemCatalog
from bot.ui import calibrate_runner as cr
from bot.ui import farm_mode_editor as fme
from bot.ui import controller as ctl
from bot.ui import history_view as hv
from bot.ui import overrides as ov
from bot.ui import profile_dialogs
from bot.ui import skills_editor as se
from bot.ui import validators as vd
from bot.ui import zone_view
from bot.ui.calibrate_dialog import CalibrateDialog
from bot.ui.character_tab import CharacterTab
from bot.ui.farm_mode_editor import MODE_LABELS, MODES, FarmModeEditor
from bot.ui.metin_editor import MetinEditor
from bot.ui.widgets import (COLOR_BG, COLOR_ERROR, COLOR_MUTED, COLOR_WARN, GAP, GAP_S, PAD, WRAP, OverrideStrip, ScrollFrame, Tooltip,
                            ValidatedEntry, add_tooltip, apply_style, heading_tooltips, make_fluid, note, section, tab_frame)

logger = logging.getLogger(__name__)

_minutes_error = vd.non_negative_number
POLL_MS = 100
LOG_MAX_LINES = 200
REMINDER = "F12 o mouse a la esquina sup. izq.: detener  |  F11: pausa"
KIND_LABELS = (("servidor", "Servidor"), ("personaje", "Personaje"), ("zona", "Zona"))
MIN_SIZE = (900, 640)
DEFAULT_GEOMETRY = "1000x800"    # pensado para un segundo monitor; se pisa con la geometria guardada
ZONE_CANVAS_SIZE = (640, 300)
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
               ("surrounded", "Rodeado", "Veces que el bot pulsó ESPACIO por estar rodeado de monstruos sin avanzar."),
               ("lures", "Señuelos usados", "Señuelos usados (solo modo bichos)."),
               ("deaths", "Muertes (revividas)", "Veces que murió el personaje y cuántas veces logró revivir."),
               ("coords", "Coordenadas fallidas", "Porcentaje de lecturas de coordenadas (debajo del minimapa) que no salieron. "
                                                  "Si es alto, revisá la captura o el tamaño de la ventana."))
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
                 ask_name: Callable[[str, str], str | None] | None = None,
                 inform: Callable[[str, str], None] | None = None,
                 ask_profile_name: Callable[..., tuple[str, bool] | None] | None = None,
                 pick_file: Callable[[str, Path], str | None] | None = None) -> None:
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
        self._inform = inform or (lambda t, m: messagebox.showinfo(t, m, parent=root))
        # (titulo, texto, validador, **opciones) -> (nombre, casilla) o None (cancelar)
        self._ask_profile_name = ask_profile_name or (lambda title, intro, validator, **kw: profile_dialogs.ask_name(
            root, title, intro, validator, **kw))
        self._pick_file = pick_file or (lambda title, initial: filedialog.askopenfilename(
            parent=root, title=title, initialdir=str(initial), filetypes=[("Imagen PNG", "*.png"), ("Todos", "*.*")]) or None)
        d_dir, d_file, d_match = ctl.data_paths(config_path, profiles_root)
        self.catalog = ItemCatalog(Path(catalog_dir) if catalog_dir is not None else d_dir, d_match)
        self.history_file = Path(history_file) if history_file is not None else d_file
        self.sessions: list[dict] = []
        self._thumbs: dict[str, tk.PhotoImage] = {}
        self.phase = ctl.STOPPED
        self.paused = False
        self.auto_paused = False
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
        self._config_keys: ov.Keys = []
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
        # franja amarilla general (arriba de todo) y una por pestana afectada; las tres usan el mismo boton de migrar
        self.banner = OverrideStrip(r, self.migrate_config, pack_opts={"padx": GAP_S, "pady": (GAP_S, 0), "before": self.notebook})
        self.tabs: dict[str, ttk.Frame] = {}
        self.strips: dict[str, OverrideStrip] = {}
        for key, title in TAB_TITLES:
            self.tabs[key] = tab_frame(self.notebook, title)
            if key in ov.TAB_LABELS:
                holder = ttk.Frame(self.tabs[key])
                holder.pack(fill="x")
                self.strips[key] = OverrideStrip(holder, self.migrate_config)
        self._build_control(self.tabs["control"])
        self._build_metin(self.tabs["metin"])
        self._build_zone(self.tabs["zona"])
        self._build_bichos(self.tabs["bichos"])
        self._build_character(self.tabs["personaje"])
        self._build_skills(self.tabs["habilidades"])
        self._build_history(self.tabs["historial"])
        self._build_items(self.tabs["objetos"])
        self.refresh_tab_titles()

    def refresh_tab_titles(self) -> None:
        """Titulo de cada pestana que edita perfiles con '*' si tiene cambios sin guardar."""
        char = getattr(self, "char_tab", None)
        dirty = {"metin": self.metin.dirty, "bichos": self.farm.dirty, "habilidades": self.editor.dirty,
                 "personaje": bool(char and char.editor.dirty)}
        for key, title in TAB_TITLES:
            if key in self.tabs:
                self.notebook.tab(self.tabs[key], text=ctl.tab_title(title, dirty.get(key, False)))

    def _build_control(self, tab: ttk.Frame) -> None:
        """Pestana Control en dos columnas: izquierda perfiles y comandos, derecha estadisticas y log."""
        tab.columnconfigure(0, weight=2, minsize=320)
        tab.columnconfigure(1, weight=3)
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
        self.warn_label = make_fluid(ttk.Label(prof, text="", style="Warn.TLabel", justify="left", wraplength=WRAP - 120))
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
        make_fluid(ttk.Label(farm, text="Los detalles de bichos están en la pestaña Modo bichos.", style="Muted.TLabel",
                             wraplength=WRAP - 120)).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(GAP_S, 0))

        opt = section(left, "Opciones")
        opt.pack(fill="x", pady=(GAP, 0))
        ttk.Label(opt, text="Tiempo máximo (min)").grid(row=0, column=0, sticky="nw", pady=GAP_S)
        self.minutes_field = ValidatedEntry(opt, _minutes_error, value=f"{self.opts.max_minutes:g}", width=8,
                                            tooltip="Minutos que corre el bot antes de detenerse solo. 0 = sin límite.")
        self.minutes_field.grid(row=0, column=1, sticky="w", padx=GAP)
        self.minutes_var, self.minutes_entry = self.minutes_field.var, self.minutes_field.entry
        ttk.Label(opt, text="Parar a las (HH:MM)").grid(row=1, column=0, sticky="nw", pady=GAP_S)
        self.stop_at_field = ValidatedEntry(opt, vd.optional_hhmm, value=self.opts.stop_at, width=8,
                                            tooltip="Hora local (HH:MM, 24 h) a la que el bot termina solo. Vacío = no parar a una hora. "
                                                    "Si esa hora ya pasó hoy, es mañana. No corta un captcha ni la recogida de objetos.")
        self.stop_at_field.grid(row=1, column=1, sticky="w", padx=GAP)
        self.stop_at_var, self.stop_at_entry = self.stop_at_field.var, self.stop_at_field.entry
        ttk.Label(opt, text="Parar tras N metin").grid(row=2, column=0, sticky="nw", pady=GAP_S)
        self.stop_metins_field = ValidatedEntry(opt, vd.non_negative_int, value=str(self.opts.stop_after_metins), width=8,
                                                tooltip="El bot termina solo después de romper N metin (termina de recoger antes de parar). "
                                                        "0 = sin tope.")
        self.stop_metins_field.grid(row=2, column=1, sticky="w", padx=GAP)
        self.stop_metins_var, self.stop_metins_entry = self.stop_metins_field.var, self.stop_metins_field.entry
        self.debug_var = tk.BooleanVar(value=self.opts.debug)
        self.observe_var = tk.BooleanVar(value=self.opts.observar)
        self.top_var = tk.BooleanVar(value=self.opts.always_on_top)
        self.checks = [ttk.Checkbutton(opt, text="Debug", variable=self.debug_var),
                       ttk.Checkbutton(opt, text="Observar (sin input)", variable=self.observe_var)]
        add_tooltip(self.checks[0], "Guarda imágenes de lo que ve el bot en la carpeta debug. Útil para revisar errores.")
        add_tooltip(self.checks[1], "El bot mira y registra pero no manda teclas ni clics al juego. Sirve para probar la detección.")
        self.checks[0].grid(row=3, column=0, sticky="w")
        self.checks[1].grid(row=3, column=1, columnspan=2, sticky="w", padx=GAP)
        top_check = ttk.Checkbutton(opt, text="Siempre encima", variable=self.top_var, command=self._apply_window_prefs)
        top_check.grid(row=4, column=0, sticky="w")
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
        make_fluid(ttk.Label(left, text=REMINDER, style="Muted.TLabel", wraplength=WRAP - 120, justify="left")).pack(
            fill="x", pady=(GAP_S, 0))

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
        self.schedule_var = tk.StringVar(value="")
        sched_label = ttk.Label(st, textvariable=self.schedule_var, justify="left", wraplength=WRAP + 40)
        sched_label.grid(row=(len(STAT_FIELDS) + 1) // 2 + 1, column=0, columnspan=4, sticky="ew")
        add_tooltip(sched_label, "Parada programada de esta corrida (hora y/o cantidad de metin).")

        live = section(right, "Objetos de esta sesión")
        live.pack(fill="x", pady=(GAP, 0))
        self.live_tree = ttk.Treeview(live, columns=("name", "count"), show="headings", height=3, selectmode="none")
        self.live_tree.heading("name", text="Objeto")
        self.live_tree.heading("count", text="Cantidad")
        self.live_tree.column("name", width=300, anchor="w")
        self.live_tree.column("count", width=80, anchor="center", stretch=False)
        self.live_tree.pack(fill="x")
        heading_tooltips(self.live_tree, {"name": "Tipo de objeto recogido en esta corrida (con el nombre de la pestaña Objetos).",
                                          "count": "Cuántos recogió el bot en esta corrida."})

        lf = section(right, "Log")
        lf.pack(fill="both", expand=True, pady=(GAP, 0))
        self.log = tk.Text(lf, height=10, wrap="word", state="disabled", font=("Consolas", 9))
        sb = ttk.Scrollbar(lf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        add_tooltip(self.log, "Mensajes del bot en vivo. Amarillo = aviso, rojo = error.")
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

    # ------------------------------------------------------------------ personaje
    def _build_character(self, tab: ttk.Frame) -> None:
        self.char_tab = CharacterTab(self, tab)

    def options_enabled(self) -> bool:
        """True si el bot esta detenido (se pueden editar perfiles)."""
        return ctl.button_state(self.phase, self.paused).options

    @property
    def calibrating(self) -> bool:
        """True si hay una ventana guiada de calibracion abierta."""
        return self._calib is not None

    def inform(self, title: str, message: str) -> None:
        self._inform(title, message)

    def ask_profile_name(self, title: str, intro: str, validator: Callable[[str], str | None], **kw) -> tuple[str, bool] | None:
        return self._ask_profile_name(title, intro, validator, **kw)

    def pick_file(self, title: str, initial: Path) -> str | None:
        return self._pick_file(title, initial)

    def show_tab(self, key: str) -> None:
        """Pasa a la pestana `key` (ej. 'zona')."""
        if key in self.tabs:
            self.notebook.select(self.tabs[key])

    def refresh_profile_lists(self) -> None:
        """Vuelve a leer los perfiles del disco y actualiza los selectores de Control y las listas de Personaje."""
        self.profiles = ctl.available_profiles(self.profiles_root)
        for kind, cb in self.combos.items():
            cb.configure(values=self.profiles.get(kind, []))
        if hasattr(self, "char_tab"):
            self.char_tab.refresh_lists()

    def select_profile(self, kind: str, name: str) -> None:
        """Elige un perfil como si se lo eligiera en Control (con la pregunta por cambios sin guardar)."""
        if name in self.profiles.get(kind, []):
            self.vars[kind].set(name)
            self._on_profile_selected(kind)

    def confirm_unsaved_all(self, what: str) -> bool:
        """Pregunta por cada editor con cambios sin guardar. False = cancelar la accion."""
        return (self._confirm_unsaved(what) and self._confirm_unsaved_priority(what) and self._confirm_unsaved_farm(what)
                and self._confirm_unsaved_character(what))

    def _confirm_unsaved_character(self, what: str) -> bool:
        """Como `_confirm_unsaved`, para las opciones del personaje. False = cancelar la accion."""
        if not self.char_tab.editor.dirty:
            return True
        ans = self._ask_save("Opciones del personaje sin guardar",
                             f"Hay cambios sin guardar en las opciones de '{self.char_tab.editor.personaje}'.\n"
                             f"¿Guardarlas antes de {what}?")
        if ans is None:
            return False
        return self.char_tab.save() if ans else True

    # ------------------------------------------------------------------ modo bichos
    def _build_bichos(self, tab: ttk.Frame) -> None:
        note(tab, "Modo bichos: el bot usa un señuelo cada cierto tiempo, pelea con los monstruos que llegan (barra espaciadora) "
                  "y después recoge los objetos. Se guarda en el perfil de la zona elegida.", fluid=True).pack(
            fill="x", pady=(0, GAP))
        self.farm_scroll = ScrollFrame(tab)
        self.farm_scroll.pack(fill="both", expand=True)
        self._build_farm_mode(self.farm_scroll.body)

    # ------------------------------------------------------------------ historial
    HISTORY_COLS = (("date", "Fecha", 120), ("duration", "Duración", 90), ("metins", "Metin", 55), ("items", "Objetos", 60),
                    ("reason", "Motivo", 150))

    HISTORY_TIPS = {"date": "Cuándo empezó la corrida.", "duration": "Cuánto duró la corrida.",
                    "metins": "Metin rotos en la corrida.", "items": "Objetos recogidos en la corrida.",
                    "reason": "Por qué terminó la corrida (tiempo, detenido a mano, captcha, muerte...)."}

    def _build_history(self, tab: ttk.Frame) -> None:
        note(tab, "Corridas anteriores del bot. Elegí una para ver el detalle de lo que rompió y recogió.",
             fluid=True).pack(fill="x")
        top = section(tab, "Resumen")
        top.pack(fill="x", pady=(GAP, 0))
        self.hist_today = tk.StringVar(value="")
        self.hist_total = tk.StringVar(value="")
        for var in (self.hist_today, self.hist_total):
            ttk.Label(top, textvariable=var, justify="left", wraplength=WRAP + 120).pack(fill="x", pady=(0, GAP_S))
        btn = ttk.Button(top, text="Recargar", command=self.refresh_history)
        btn.pack(anchor="w")
        add_tooltip(btn, "Vuelve a leer el historial del disco.")
        box = section(tab, "Corridas")
        box.pack(fill="both", expand=True, pady=(GAP, 0))
        frame = ttk.Frame(box)
        frame.pack(fill="both", expand=True)
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
        heading_tooltips(self.hist_tree, self.HISTORY_TIPS)
        det = section(tab, "Detalle de la corrida")
        det.pack(fill="x", pady=(GAP, 0))
        self.hist_detail = tk.Text(det, height=10, wrap="word", state="disabled", font=("Consolas", 9), borderwidth=1,
                                   relief="solid")
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

    ITEM_TIPS = {"#0": "Imagen de la etiqueta del objeto, tal como la ve el bot.",
                 "name": "Nombre que le pusiste (doble clic para cambiarlo).",
                 "count": "Cuántos recogiste en total, sumando todas las corridas."}

    def _build_items(self, tab: ttk.Frame) -> None:
        note(tab, "Tipos de objeto recogidos. El bot los agrupa por cómo se ve la etiqueta; ponele el nombre real "
                  "(doble clic o Renombrar). El nombre también cambia en el historial.", fluid=True).pack(fill="x")
        box = section(tab, "Objetos recogidos")
        box.pack(fill="both", expand=True, pady=(GAP, 0))
        frame = ttk.Frame(box)
        frame.pack(fill="both", expand=True)
        ttk.Style(self.root).configure("Items.Treeview", rowheight=self.ITEM_THUMB_MAX_H + 4)
        self.items_tree = ttk.Treeview(frame, columns=("name", "count"), show="tree headings", height=8, selectmode="browse",
                                       style="Items.Treeview")
        self.items_tree.heading("#0", text="Etiqueta")
        self.items_tree.heading("name", text="Nombre")
        self.items_tree.heading("count", text="Cantidad total")
        self.items_tree.column("#0", width=170, stretch=False, anchor="center")
        self.items_tree.column("name", width=150, anchor="w")
        self.items_tree.column("count", width=90, anchor="center", stretch=False)
        sb = ttk.Scrollbar(frame, command=self.items_tree.yview)
        self.items_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.items_tree.pack(side="left", fill="both", expand=True)
        self.items_tree.bind("<Double-1>", lambda _e: self.rename_selected_item())
        heading_tooltips(self.items_tree, self.ITEM_TIPS)
        bar = ttk.Frame(box)
        bar.pack(fill="x", pady=(GAP, 0))
        self.btn_rename = ttk.Button(bar, text="Renombrar", command=self.rename_selected_item)
        self.btn_rename.pack(side="left", padx=(0, GAP))
        add_tooltip(self.btn_rename, "Cambia el nombre del objeto elegido (también se ve en el historial). Solo con el bot detenido.")
        btn = ttk.Button(bar, text="Recargar", command=self.refresh_items)
        btn.pack(side="left")
        add_tooltip(btn, "Vuelve a leer el catálogo de objetos del disco.")
        self.items_state = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.items_state, style="Warn.TLabel", justify="left", wraplength=WRAP + 120).pack(
            fill="x", pady=(GAP_S, 0))
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
    METIN_TIPS = {"prio": "Orden de prioridad: 1 se ataca primero. Solo cuenta si \"Elegir por prioridad\" está marcado.",
                  "attack": "Clic en la casilla para marcar o desmarcar: el bot solo ataca los tipos marcados.",
                  "name": "Nombre del tipo de metin tal como aparece en el juego.",
                  "tpl": "\"Sí\" = el bot ya aprendió cómo se ve la etiqueta de ese metin. Sin plantilla no lo puede encontrar."}
    METIN_COLS = (("prio", "Prioridad", 80), ("attack", "Atacar", 60), ("name", "Tipo de metin", 220), ("tpl", "Plantilla", 70))

    def _build_metin(self, tab: ttk.Frame) -> None:
        note(tab, "Tipos de metin de la zona elegida. Marcá los que el bot tiene que atacar (clic en la casilla). Con "
                  "\"Elegir por prioridad\" el bot va primero por los de más arriba.", fluid=True).pack(fill="x")
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
        heading_tooltips(self.metin_tree, self.METIN_TIPS)
        bar = ttk.Frame(box)
        bar.pack(fill="x")
        self.prio_buttons: list[ttk.Widget] = []
        for text, cmd, w, tip in (("↑", lambda: self.move_priority(-1), 3, "Sube el tipo elegido en la prioridad."),
                                  ("↓", lambda: self.move_priority(1), 3, "Baja el tipo elegido en la prioridad."),
                                  ("Guardar", self.save_priority, 8, "Guarda en el perfil de zona (con copia .bak)."),
                                  ("Descartar cambios", self.reload_metin_types, 17,
                                   "Vuelve a leer los tipos del perfil de zona y descarta lo que cambiaste sin guardar.")):
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
        self.metin_warn.configure(text="\n".join(self.metin.warnings()))
        if hasattr(self, "tabs"):
            self.refresh_tab_titles()

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

    # ------------------------------------------------------------------ migrar config.json a los perfiles
    def migrate_config(self) -> None:
        """Boton "Migrar config.json a los perfiles": explica, pide confirmacion y corre `calibrate.py perfiles migrar`
        en la ventana guiada. Nunca migra sin que la persona confirme (aca y de nuevo cuando el script pregunta)."""
        if not self.options_enabled() or self._calib is not None:
            return
        keys = ov.read_config_keys(self.config_path)
        if not keys:
            self._inform("Migrar config.json", "config.json no tiene valores de servidor, personaje ni zona: no hay nada para migrar.")
            self.refresh_overrides()
            return
        if not self.confirm_unsaved_all("migrar (la migración vuelve a leer los perfiles)"):
            return
        selection = {k: self.vars[k].get() for k, _ in KIND_LABELS}
        if not self._confirm("Migrar config.json a los perfiles", ov.migration_explanation(keys, selection)):
            return
        self._open_calibrate(
            cr.MIGRAR_TITLE, cr.MIGRAR_STEPS,
            lambda _n: cr.build_calibrate_command("perfiles-migrar", servidor=selection["servidor"],
                                                  personaje=selection["personaje"], zona=selection["zona"],
                                                  config=self._calib_config()),
            lambda rc: self.reload_all(), autostart=True)

    def reload_all(self) -> None:
        """Vuelve a leer del disco perfiles, config.json y todos los editores (descarta lo no guardado)."""
        self.refresh_profile_lists()
        pj, zn = self.vars["personaje"].get(), self.vars["zona"].get()
        self.editor.load(pj)
        self.char_tab.load(pj)
        self.metin.load(zn, self._labels_dir())
        self.farm.load(zn)
        self._last_personaje, self._last_zona = pj, zn
        self.refresh_skills()
        self.refresh_priority()
        self.refresh_farm_mode()
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
        self.btn_calib_pitch = ttk.Button(top, text="Calibrar ángulo vertical", command=self.calibrate_pitch)
        self.btn_calib_pitch.pack(side="left", padx=GAP)
        add_tooltip(self.btn_calib_pitch, "Mide el ángulo vertical de la cámara tal como la dejaste y lo guarda como referencia "
                                          "(camera_pitch.ref_horizon_frac, en el perfil del personaje). El bot la corrige solo.")
        btn_refresh = ttk.Button(top, text="Recargar", command=self.refresh_zone)
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
                  "para ver cuántas veces y hace cuánto). Punto rojo: el personaje, con su rastro.", fluid=True).pack(
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

    def calibrate_pitch(self) -> None:
        """Abre la guia de `calibrate.py camara` (mide el horizonte; guarda en el perfil del personaje)."""
        if self.metin.locked or self._calib is not None:
            return
        self._open_calibrate(
            cr.CAMARA_TITLE, cr.CAMARA_STEPS,
            lambda _n: cr.build_calibrate_command("camara", servidor=self.vars["servidor"].get(),
                                                  personaje=self.vars["personaje"].get(), zona=self.vars["zona"].get(),
                                                  config=self._calib_config()),
            lambda rc: self.refresh_overrides())

    # ------------------------------------------------------------------ modo de farmeo
    FARM_ADVANCED = (
        ("min_monsters", "Monstruos mínimos:", "Cuántos monstruos tiene que haber cerca para que el bot empiece a pelear "
                                               "(1 o más)."),
        ("clear_s", "Segundos sin monstruos (s):", "Cuánto tiempo sin monstruos cerca hace falta para dar la pelea por "
                                                   "terminada y pasar a recoger objetos."),
        ("pickup_every_s", "Recoger cada (s):", "Cada cuántos segundos recoge los objetos del piso aunque todavía haya "
                                                "algunos monstruos."),
        ("pickup_max_monsters", "Recoger con hasta (monstruos):", "Cantidad máxima de monstruos cerca con la que se anima a "
                                                                  "recoger cuando le toca. 0 = solo recoge sin monstruos cerca."),
        ("idle_s", "Caminar a otro lugar tras (s):", "Segundos sin monstruos (y sin señuelo listo) antes de caminar a otro "
                                                     "punto de la zona."))

    def _build_farm_mode(self, parent: ttk.Frame) -> None:
        box = section(parent, "Modo de farmeo y señuelo")
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="Modo:").grid(row=0, column=0, sticky="w", padx=(0, GAP), pady=GAP_S)
        self.farm_mode_var = tk.StringVar(value=MODE_LABELS[self.farm.mode])
        self.farm_combo = ttk.Combobox(box, textvariable=self.farm_mode_var, values=[t for _, t in MODES], state="readonly",
                                       width=10)
        self.farm_combo.grid(row=0, column=1, sticky="w", pady=GAP_S)
        add_tooltip(self.farm_combo, "Metin: rompe metin. Bichos: pelea con los monstruos que atrae con el señuelo.")

        def field(host: ttk.Frame, row: int, label: str, validator, value: str, tip: str, hint: str = "") -> ValidatedEntry:
            lab = ttk.Label(host, text=label)
            lab.grid(row=row, column=0, sticky="nw", padx=(0, GAP), pady=GAP_S)
            add_tooltip(lab, tip)
            f = ValidatedEntry(host, validator, value=value, width=8, tooltip=tip)
            f.grid(row=row, column=1, sticky="w")
            if hint:
                note(host, hint, wrap=WRAP).grid(row=row, column=2, sticky="nw", padx=(GAP, 0), pady=GAP_S)
            f.var.trace_add("write", lambda *_a: self._on_farm_edit())
            return f

        self.farm_key_field = field(box, 1, "Tecla del señuelo:", vd.game_key, self.farm.lure_key,
                                    "Tecla del juego del objeto que atrae monstruos (ej. f4). No puede ser F11 ni F12.")
        self.farm_secs_field = field(box, 2, "Usar el señuelo cada (s):", vd.positive_number, self.farm.seconds,
                                     "Cada cuántos segundos usa el señuelo. Tiene que ser mayor que 0.")
        self.farm_adv_fields: dict[str, ValidatedEntry] = {}
        self.farm_adv_fields["space_s"] = field(
            box, 3, "Barra espaciadora (s):", vd.positive_number, self.farm.advanced["space_s"],
            "Cuánto tiempo mantiene apretada la barra espaciadora (ataca) en cada tramo. Tiene que ser mayor que 0.")
        self.farm_key_var, self.farm_key_entry = self.farm_key_field.var, self.farm_key_field.entry
        self.farm_secs_var, self.farm_secs_entry = self.farm_secs_field.var, self.farm_secs_field.entry

        self.adv_open = tk.BooleanVar(value=False)
        self.adv_toggle = ttk.Checkbutton(parent, text="Avanzado ▸", variable=self.adv_open, style="Toolbutton",
                                          command=self._toggle_farm_advanced)
        self.adv_toggle.pack(anchor="w", pady=(GAP, 0))
        add_tooltip(self.adv_toggle, "Muestra u oculta los ajustes finos del modo bichos. Los valores por defecto sirven "
                                     "para empezar.")
        self.adv_box = section(parent, "Ajustes avanzados")
        self.adv_box.columnconfigure(1, weight=1)
        validators = {k: fn for k, fn, _ in fme.ADVANCED}
        for i, (key, label, tip) in enumerate(self.FARM_ADVANCED):
            lab = ttk.Label(self.adv_box, text=label)
            lab.grid(row=i, column=0, sticky="nw", padx=(0, GAP), pady=GAP_S)
            add_tooltip(lab, tip)
            f = ValidatedEntry(self.adv_box, validators[key], value=self.farm.advanced[key], width=8, tooltip=tip)
            f.grid(row=i, column=1, sticky="w")
            note(self.adv_box, tip, wrap=WRAP).grid(row=i, column=2, sticky="nw", padx=(GAP, 0), pady=GAP_S)
            f.var.trace_add("write", lambda *_a: self._on_farm_edit())
            self.farm_adv_fields[key] = f

        self.farm_bar = ttk.Frame(parent)
        self.farm_bar.pack(fill="x", pady=(GAP, 0))
        self.farm_save_btn = ttk.Button(self.farm_bar, text="Guardar", command=self.save_farm_mode)
        self.farm_discard_btn = ttk.Button(self.farm_bar, text="Descartar cambios", command=self.discard_farm_mode)
        for b, tip in ((self.farm_save_btn, "Guarda el modo, el señuelo y los ajustes de bichos en el perfil de zona "
                                            "(con copia .bak)."),
                       (self.farm_discard_btn, "Vuelve a leer el perfil de zona y descarta lo que cambiaste sin guardar.")):
            b.pack(side="left", padx=(0, GAP))
            add_tooltip(b, tip)
        self.farm_state = tk.StringVar(value="")
        ttk.Label(self.farm_bar, textvariable=self.farm_state, style="Warn.TLabel").pack(side="left")
        self.farm_mode_var.trace_add("write", lambda *_a: self._on_farm_edit())

    def _toggle_farm_advanced(self) -> None:
        """Muestra u oculta la seccion Avanzado de Modo bichos."""
        if self.adv_open.get():
            self.adv_box.pack(fill="x", pady=(GAP_S, 0), before=self.farm_bar)
        else:
            self.adv_box.pack_forget()
        self.adv_toggle.configure(text="Avanzado ▾" if self.adv_open.get() else "Avanzado ▸")

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
        for k, f in self.farm_adv_fields.items():
            self.farm.set_advanced(k, f.get())
        self.refresh_farm_mode()

    def refresh_farm_mode(self) -> None:
        """Vuelca el editor en los campos y redibuja el indicador de cambios sin guardar."""
        self._farm_syncing = True
        try:
            pairs = [(self.farm_mode_var, MODE_LABELS[self.farm.mode]), (self.farm_key_var, self.farm.lure_key),
                     (self.farm_secs_var, self.farm.seconds), (self.quick_mode_var, MODE_LABELS[self.farm.mode])]
            pairs += [(f.var, self.farm.advanced[k]) for k, f in self.farm_adv_fields.items()]
            for var, value in pairs:
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
            self.refresh_tab_titles()

    def save_farm_mode(self) -> bool:
        try:
            self.farm.save()
        except (ValueError, ConfigError, OSError) as e:
            self._notify("No se pudo guardar el modo de farmeo", str(e))
            return False
        self.refresh_farm_mode()
        return True

    def discard_farm_mode(self) -> None:
        """Descartar cambios: vuelve a leer el modo y los ajustes de bichos del perfil de zona."""
        if self.farm.locked:
            return
        self.farm.load(self.vars["zona"].get())
        self.refresh_farm_mode()

    def _confirm_unsaved_farm(self, what: str) -> bool:
        """Como `_confirm_unsaved_priority`, para el modo de farmeo de la zona. False = cancelar la accion."""
        if not self.farm.dirty:
            return True
        ans = self._ask_save("Modo de farmeo sin guardar",
                             f"Hay cambios sin guardar en el modo de farmeo de '{self.farm.zona}'.\n"
                             f"¿Guardarlos antes de {what}?")
        if ans is None:
            return False
        return self.save_farm_mode() if ans else True

    # ------------------------------------------------------------------ habilidades
    SKILL_COLS = (("enabled", "Activa", 55), ("name", "Nombre", 150), ("key", "Tecla", 70), ("duration_s", "Duración (s)", 95),
                  ("requires_dismount", "Bajarse", 70), ("animation_s", "Animación (s)", 100), ("remaining", "Restante", 80))
    SKILL_TIPS = {"enabled": "Si está en \"sí\", el bot usa la habilidad. Con \"no\" queda guardada pero no se lanza.",
                  "name": "Nombre de la habilidad (solo para que la reconozcas).",
                  "key": "Tecla del juego que la lanza (ej. F1, 4, ctrl+g).",
                  "duration_s": "Cuántos segundos dura el efecto. Cuando se acaba, el bot la vuelve a lanzar.",
                  "requires_dismount": "\"sí\" = el bot se baja del caballo para lanzarla.",
                  "animation_s": "Segundos que tarda la animación al lanzarla; el bot espera ese tiempo.",
                  "remaining": "Tiempo que falta para volver a lanzarla (mm:ss), en vivo mientras el bot corre. "
                               "\"ya\" = le toca ahora."}

    def _build_skills(self, tab: ttk.Frame) -> None:
        note(tab, "Habilidades del personaje elegido. Los ejemplos son un punto de partida: ajustá la duración y la animación "
                  "a las reales. Doble clic en una fila para editarla.", fluid=True).pack(fill="x")
        box = section(tab, "Habilidades")
        box.pack(fill="both", expand=True, pady=(GAP, 0))
        frame = ttk.Frame(box)
        frame.pack(fill="both", expand=True)
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
        heading_tooltips(self.tree, self.SKILL_TIPS)
        bar = ttk.Frame(box)
        bar.pack(fill="x", pady=(GAP, 0))
        self.skill_buttons: list[ttk.Widget] = []
        for text, cmd, tip in (
                ("Agregar", self.add_skill, "Agrega una habilidad nueva (se abre una ventana para completarla)."),
                ("Editar", self.edit_skill, "Edita la habilidad elegida. También con doble clic."),
                ("Quitar", self.remove_skill, "Saca la habilidad elegida de la lista."),
                ("\u2191", lambda: self.move_skill(-1), "Sube la habilidad elegida."),
                ("\u2193", lambda: self.move_skill(1), "Baja la habilidad elegida."),
                ("Guardar", self.save_skills, "Guarda las habilidades en el perfil del personaje (con copia .bak)."),
                ("Descartar cambios", self.discard_skills,
                 "Vuelve a leer las habilidades del perfil y descarta lo que cambiaste sin guardar.")):
            b = ttk.Button(bar, text=text, width=17 if len(text) > 8 else (8 if len(text) > 1 else 3), command=cmd)
            b.pack(side="left", padx=(0, GAP_S))
            add_tooltip(b, tip)
            self.skill_buttons.append(b)
        self.example_menu = tk.Menu(self.root, tearoff=0)
        for i, (label, key) in enumerate(se.EXAMPLES):
            self.example_menu.add_command(label=f"{label} - {key}", command=lambda i=i: self.add_example(i))
        self.btn_example = ttk.Menubutton(bar, text="Agregar ejemplo", menu=self.example_menu)
        self.btn_example.pack(side="left", padx=(0, GAP_S))
        add_tooltip(self.btn_example, "Agrega una habilidad de ejemplo para completar después.")
        self.skill_buttons.append(self.btn_example)
        self.skills_state = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.skills_state, style="Warn.TLabel", justify="left", wraplength=WRAP + 120).pack(
            fill="x", pady=(GAP_S, 0))
        self.refresh_skills()

    def discard_skills(self) -> None:
        """Descartar cambios: vuelve a leer las habilidades del perfil."""
        if self.editor.locked:
            return
        self.editor.load(self.vars["personaje"].get())
        self.refresh_skills()

    @staticmethod
    def _skill_row(sk: dict, remaining: dict) -> tuple:
        rem = remaining.get((sk.get("name"), sk.get("key")))
        return ("sí" if sk.get("enabled") else "no", sk.get("name", ""), sk.get("key", ""), f"{sk.get('duration_s', 0):g}",
                "sí" if sk.get("requires_dismount") else "no", f"{sk.get('animation_s', 0):g}",
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
            parts.append("bloqueada mientras el bot corre: los cambios aplican en la próxima sesión")
        self.skills_state.set(" - ".join(parts))
        if hasattr(self, "tabs"):
            self.refresh_tab_titles()

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
                             f"¿Guardarlos antes de {what}?")
        if ans is None:
            return False
        if ans:
            return self.save_skills()
        return True

    def _on_profile_selected(self, kind: str) -> None:
        if kind == "personaje":
            new = self.vars["personaje"].get()
            if new != self._last_personaje:
                if not (self._confirm_unsaved("cambiar de personaje") and self._confirm_unsaved_character("cambiar de personaje")):
                    self.vars["personaje"].set(self._last_personaje)
                    self.char_tab.sync_selection("personaje")
                    return
                self._last_personaje = new
                self.editor.load(new)
                self.refresh_skills()
                self.char_tab.load(new)
        if kind == "zona":
            new = self.vars["zona"].get()
            if new != self._last_zona:
                if not (self._confirm_unsaved_priority("cambiar de zona") and self._confirm_unsaved_farm("cambiar de zona")):
                    self.vars["zona"].set(self._last_zona)
                    self.char_tab.sync_selection("zona")
                    return
                self._last_zona = new
                self.metin.load(new, self._labels_dir())
                self.refresh_priority()
                self.farm.load(new)
                self.refresh_farm_mode()
        if kind in self.char_tab.lists:
            self.char_tab.sync_selection(kind)
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
        try:
            stop_at = ctl.normalize_hhmm(self.stop_at_var.get())
        except ValueError as e:
            raise ValueError("La hora de parada tiene que tener el formato HH:MM (ej. 23:30) o estar vacía.") from e
        o = ctl.UiOptions(max_minutes=ctl.parse_minutes(self.minutes_var.get()), debug=bool(self.debug_var.get()),
                          stop_at=stop_at, stop_after_metins=ctl.parse_stop_metins(self.stop_metins_var.get()),
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
        # lo que config.json pisa lo cubren las franjas amarillas; aca solo quedan los errores de la config
        self.warn_label.configure(text="\n".join(w for w in warnings if not w.startswith("config.json pisa")))
        self._config_keys = ov.read_config_keys(self.config_path)
        self.banner.set_text(ov.banner_text(self._config_keys))
        by_tab = ov.names_by_tab(self._config_keys)
        for key, strip in self.strips.items():
            strip.set_text(ov.tab_text(by_tab.get(key, [])))
        if hasattr(self, "metin_warn"):
            self.refresh_priority(select=self._selected_metin())

    # ------------------------------------------------------------------ acciones
    def start(self) -> None:
        if self.phase != ctl.STOPPED:
            return
        try:
            o = self.read_options()
        except ValueError as e:
            self._notify("Opciones inválidas", str(e))
            return
        errors = ctl.validate_options(o)
        if errors:
            self._notify("Opciones inválidas", "\n".join(errors))
            return
        if not self._confirm_unsaved("iniciar (el bot usa el perfil guardado)"):
            return
        if not self._confirm_unsaved_priority("iniciar (el bot usa el perfil guardado)"):
            return
        if not self._confirm_unsaved_farm("iniciar (el bot usa el perfil guardado)"):
            return
        if not self._confirm_unsaved_character("iniciar (el bot usa el perfil guardado)"):
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
        self.auto_paused = False
        self.schedule_var.set(ctl.schedule_text(o, 0))
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
            self._append_log("El bot no terminó a tiempo: se lo termina a la fuerza.", "WARNING")
            self.proc.kill()

    def on_close(self) -> None:
        if self.proc.running:
            if not self._confirm("Salir", "El bot está corriendo. Se va a detener y se cierra la ventana. ¿Continuar?"):
                return
            self.proc.stop(ctl.STOP_GRACE_S)
        self.save_settings()
        if self._kill_id is not None:
            self.root.after_cancel(self._kill_id)
            self._kill_id = None
        self.proc.kill()   # (si sigue vivo; ademas cierra las tuberias)
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
        for w in (self.minutes_entry, self.stop_at_entry, self.stop_metins_entry, *self.checks):
            w.configure(state="normal" if b.options else "disabled")
        self.paused_label.configure(text=ctl.paused_text(self.paused, self.auto_paused) if self.phase == ctl.RUNNING else "")
        self.editor.locked = not b.options
        for w in self.skill_buttons:
            w.configure(state="normal" if b.options else "disabled")
        self.metin.locked = not b.options
        for w in (self.prio_check, *self.prio_buttons):
            w.configure(state="normal" if b.options else "disabled")
        idle = b.options and self._calib is None   # las guias de calibracion necesitan el bot detenido y de a una
        for w in (self.btn_add_metin, self.btn_redo_zone, self.btn_calib_pitch):
            w.configure(state="normal" if idle else "disabled")
        self.banner.set_enabled(idle)
        for strip in self.strips.values():
            strip.set_enabled(idle)
        self.char_tab.set_locked(not b.options, idle)
        self.quick_combo.configure(state="readonly" if b.options else "disabled")
        self.refresh_priority(select=self._selected_metin())
        self.farm.locked = not b.options
        for w in (self.farm_combo, self.farm_key_entry, self.farm_secs_entry, *(f.entry for f in self.farm_adv_fields.values()),
                  self.farm_save_btn, self.farm_discard_btn):
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
            self.info_var.set(f"Terminó: {p.end.get('reason', '-')}")
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
        v["surrounded"].set(str(s.surrounded))
        v["lures"].set(str(s.lures) if s.farm_mode == "bichos" else "-")
        v["deaths"].set(f"{s.deaths} ({s.revives})")
        v["coords"].set(f"{100.0 * s.coords_failed / s.coords_total:.1f}%" if s.coords_total else "-")
        self.update_zone_position(s)
        self.live_tree.delete(*self.live_tree.get_children())
        for name, n in hv.live_item_rows(s.items_by_type):
            self.live_tree.insert("", "end", values=(name, n))
        self.refresh_skills(select=self._selected_skill(), status=s)
        if s.phase == "countdown" and s.countdown > 0:
            self.info_var.set(f"Arrancando en {s.countdown}... (poné el juego al frente)")
        elif not self._got_end:
            self.info_var.set("")
        self.schedule_var.set(ctl.schedule_text(self.opts, s.metins))
        auto = s.paused and s.auto_paused
        if s.paused != self.paused or auto != self.auto_paused:
            self.paused, self.auto_paused = s.paused, auto
            self.refresh_buttons()

    def _on_exit(self) -> None:
        """El subproceso cerro stdout: termino. Si fallo sin @@END, se avisa con el ultimo error del log."""
        self.proc.wait_closed(2.0)
        rc = self.proc.returncode
        if self._kill_id is not None:
            self.root.after_cancel(self._kill_id)
            self._kill_id = None
        was_stopping = self.phase == ctl.STOPPING
        self.paused = self.auto_paused = False
        self._set_phase(ctl.STOPPED)
        if rc not in (0, None) and not self._got_end and not was_stopping:
            msg = self._last_log_error or "El bot terminó con un error antes de arrancar."
            self.info_var.set(f"El bot terminó con error (código {rc}): {msg}")
            self._append_log(f"El bot terminó con error (código {rc})", "ERROR")
            self._notify("El bot no pudo arrancar", f"{msg}\n\nRevisá el log de la ventana.")
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

    FIELDS = (("name", "Nombre:", vd.non_empty, "Nombre de la habilidad (solo para que la reconozcas)."),
              ("key", "Tecla (ej. F1, 4, ctrl+g):", vd.game_key, "Tecla del juego que la lanza. No puede ser F11 ni F12."),
              ("duration_s", "Duración (s):", vd.positive_number,
               "Cuántos segundos dura el efecto. Cuando se acaba, el bot la vuelve a lanzar."),
              ("animation_s", "Animación (s):", vd.non_negative_number,
               "Segundos que tarda la animación al lanzarla; el bot espera ese tiempo."))

    def __init__(self, parent: tk.Misc, skill: dict | None, controls: dict[str, str]) -> None:
        self.controls = controls
        self.result: dict | None = None
        sk = skill or se.new_skill()
        self.top = tk.Toplevel(parent)
        self.top.title("Habilidad")
        self.top.transient(parent)
        self.top.resizable(False, False)
        initial = {"name": sk.get("name", ""), "key": sk.get("key", ""),
                   "duration_s": f"{sk.get('duration_s', se.DEFAULT_DURATION_S):g}",
                   "animation_s": f"{sk.get('animation_s', se.DEFAULT_ANIMATION_S):g}"}
        self.dismount = tk.BooleanVar(value=bool(sk.get("requires_dismount", True)))
        self.enabled = tk.BooleanVar(value=bool(sk.get("enabled", True)))
        f = ttk.Frame(self.top, padding=PAD)
        f.pack(fill="both", expand=True)
        self.fields: dict[str, ValidatedEntry] = {}
        for i, (k, label, validator, tip) in enumerate(self.FIELDS):
            lab = ttk.Label(f, text=label)
            lab.grid(row=i, column=0, sticky="nw", pady=GAP_S, padx=(0, GAP))
            add_tooltip(lab, tip)
            fld = ValidatedEntry(f, validator, value=initial[k], width=26, tooltip=tip)
            fld.grid(row=i, column=1, sticky="ew")
            fld.var.trace_add("write", lambda *_: self.validate())
            self.fields[k] = fld
        self.vars = {k: fld.var for k, fld in self.fields.items()}
        chk = ttk.Checkbutton(f, text="Bajarse del caballo para lanzarla", variable=self.dismount)
        chk.grid(row=4, column=0, columnspan=2, sticky="w", pady=(GAP_S, 0))
        add_tooltip(chk, "Si está marcado, el bot se baja del caballo antes de lanzarla.")
        chk2 = ttk.Checkbutton(f, text="Activa", variable=self.enabled)
        chk2.grid(row=5, column=0, columnspan=2, sticky="w")
        add_tooltip(chk2, "Si no está marcada, queda guardada pero el bot no la lanza.")
        self.msg = tk.Label(f, text="", justify="left", anchor="w", wraplength=360, background=COLOR_BG)
        self.msg.grid(row=6, column=0, columnspan=2, sticky="ew", pady=GAP)
        bar = ttk.Frame(f)
        bar.grid(row=7, column=0, columnspan=2, sticky="e")
        self.btn_ok = ttk.Button(bar, text="Aceptar", command=self.accept)
        self.btn_ok.pack(side="left", padx=GAP_S)
        ttk.Button(bar, text="Cancelar", command=self.top.destroy).pack(side="left", padx=GAP_S)
        f.columnconfigure(1, weight=1)
        self.validate()
        try:
            self.top.grab_set()
        except tk.TclError:
            pass

    def read(self) -> tuple[dict, list[str]]:
        """Habilidad armada con los campos y los errores de parseo de los numeros."""
        errs, nums = [], {}
        for k, label in (("duration_s", "La duración"), ("animation_s", "La animación")):
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
        # los validadores se llaman directo: el orden de los traces de Tk no garantiza que el campo ya se haya revalidado
        field_ok = all(fn(self.vars[k].get()) is None for k, _l, fn, _t in self.FIELDS) if hasattr(self, "vars") else True
        self.btn_ok.configure(state="disabled" if errs or not field_ok else "normal")
        return not errs and field_ok

    def accept(self) -> None:
        if self.validate():
            self.result = self.read()[0]
            self.top.destroy()


def run() -> None:
    """Abre la ventana y entra al mainloop (punto de entrada de gui.py)."""
    root = tk.Tk()
    App(root)
    root.mainloop()
