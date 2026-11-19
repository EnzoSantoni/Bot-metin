"""Ventana tkinter del bot. Toda la logica no visual vive en `bot/ui/controller.py`; aca solo hay widgets y el
polling del subproceso con `after()` (el hilo de tkinter nunca se bloquea leyendo)."""
from __future__ import annotations

import logging
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable

from bot.ui import controller as ctl

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
                 confirm: Callable[[str, str], bool] | None = None, notify: Callable[[str, str], None] | None = None) -> None:
        self.root, self.settings_path, self.config_path = root, Path(settings_path), config_path
        self.profiles_root = profiles_root
        self.proc = process or ctl.BotProcess()
        self.command_builder = command_builder or (lambda o: ctl.build_command(o))
        self._confirm = confirm or (lambda t, m: messagebox.askokcancel(t, m, parent=root))
        self._notify = notify or (lambda t, m: messagebox.showerror(t, m, parent=root))
        self.phase = ctl.STOPPED
        self.paused = False
        self._got_end = False
        self._last_log_error = ""
        self._after_id: str | None = None
        self._kill_id: str | None = None

        self.profiles = ctl.available_profiles(profiles_root)
        self.opts = ctl.load_settings(self.settings_path, self.profiles)
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
        main = ttk.Frame(r, padding=8)
        main.pack(fill="both", expand=True)

        prof = ttk.LabelFrame(main, text="Perfiles", padding=6)
        prof.pack(fill="x")
        self.combos: dict[str, ttk.Combobox] = {}
        self.vars: dict[str, tk.StringVar] = {}
        for i, (kind, label) in enumerate(KIND_LABELS):
            ttk.Label(prof, text=label).grid(row=i, column=0, sticky="w", padx=(0, 8), pady=2)
            var = tk.StringVar(value=getattr(self.opts, kind))
            cb = ttk.Combobox(prof, textvariable=var, values=self.profiles.get(kind, []), state="readonly", width=40)
            cb.grid(row=i, column=1, sticky="ew", pady=2)
            cb.bind("<<ComboboxSelected>>", lambda _e: self.refresh_overrides())
            self.vars[kind], self.combos[kind] = var, cb
        prof.columnconfigure(1, weight=1)
        self.warn_label = tk.Label(prof, text="", fg=COLOR_WARN, justify="left", anchor="w", wraplength=420)
        self.warn_label.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 0))

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
                                          ("captchas", "Captchas"), ("unstucks", "Destrabes"), ("time", "Tiempo"))):
            ttk.Label(st, text=label + ":").grid(row=i // 2, column=(i % 2) * 2, sticky="w", padx=(0, 4))
            v = tk.StringVar(value="-")
            ttk.Label(st, textvariable=v, font=("Segoe UI", 10, "bold")).grid(row=i // 2, column=(i % 2) * 2 + 1,
                                                                             sticky="w", padx=(0, 16))
            self.stat_vars[key] = v
        self.info_var = tk.StringVar(value="")
        tk.Label(st, textvariable=self.info_var, anchor="w", justify="left", wraplength=420).grid(
            row=3, column=0, columnspan=4, sticky="ew", pady=(4, 0))

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
        self.opts = o
        ctl.save_settings(o, self.settings_path)
        try:
            self.proc.start(self.command_builder(o))
        except OSError as e:
            self._notify("No se pudo iniciar", str(e))
            return
        self._got_end, self.paused, self._last_log_error = False, False, ""
        self.info_var.set("")
        for v in self.stat_vars.values():
            v.set("-")
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


def run() -> None:
    """Abre la ventana y entra al mainloop (punto de entrada de gui.py)."""
    root = tk.Tk()
    App(root)
    root.mainloop()
