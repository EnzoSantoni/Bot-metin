"""Pestana Personaje (tkinter): perfiles de personaje y de zona (crear, duplicar, capturar nombre amarillo) y las opciones
del personaje elegido (recoger a pie, revivir al morir, pociones). La logica pura esta en `character_editor.py` y
`profile_ops.py`; las acciones que tocan el resto de la ventana (calibrar, cambiar de perfil) las hace `App`."""
from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

from bot.config import PROJECT_ROOT, ConfigError
from bot.ui import calibrate_runner as cr
from bot.ui import profile_ops as po
from bot.ui.character_editor import TEXT_VALIDATORS, CharacterEditor, relative_template_path
from bot.ui.widgets import (GAP, GAP_S, PAD, WRAP, ScrollFrame, ValidatedEntry, add_tooltip, note, section)

if TYPE_CHECKING:
    from bot.ui.app import App

logger = logging.getLogger(__name__)

LIST_HEIGHT = 4
TIPS = {
    "dismount": "Si está marcado, el personaje se baja del caballo para recoger los objetos a pie y vuelve a montar. "
                "Si no, intenta recoger a caballo.",
    "auto_revive": "Si está marcado, al morir el bot intenta revivir solo (\"Volver a empezar aquí\"). Si no, se detiene al morir.",
    "horse_key": "Tecla de la barra rápida del objeto que llama al caballo (ej. f5). Vacío = no usarla. No puede ser F11 ni F12.",
    "horse_item_template": "Imagen (PNG) del icono del objeto que llama al caballo, para buscarlo en el inventario. "
                           "Vacío = no usarla. Se usa si no hay tecla.",
    "button_wait_s": "Segundos que espera el bot antes de hacer clic en el botón de revivir.",
    "max_revives": "Cuántas veces como máximo revive en una corrida. Al superarlo, el bot se detiene. 0 = no revive nunca.",
    "recast_skills": "Si está marcado, al revivir vuelve a lanzar las habilidades con temporizador.",
    "hp_key": "Tecla de la poción de vida (ej. f2). No puede ser F11 ni F12.",
    "mp_key": "Tecla de la poción de maná (ej. f3). No puede ser F11 ni F12.",
    "potions_auto": "Si está marcado, el bot usa las pociones solo cuando hacen falta.",
}


class CharacterTab:
    """Contenido de la pestana Personaje. `app` aporta los perfiles elegidos, los dialogos y las ventanas guiadas."""

    def __init__(self, app: "App", tab: ttk.Frame) -> None:
        self.app, self.editor = app, CharacterEditor(app.profiles_root)
        self.editor.load(app.opts.personaje)
        self._syncing = False
        self.scroll = ScrollFrame(tab)
        self.scroll.pack(fill="both", expand=True)
        body = self.scroll.body
        self.lists: dict[str, tk.Listbox] = {}
        self.buttons: dict[str, ttk.Button] = {}
        self._build_lists(body)
        self._build_options(body)
        self.refresh_lists()
        self.refresh_options()

    # ------------------------------------------------------------------ perfiles
    def _list_box(self, parent: ttk.Frame, kind: str, buttons: tuple[tuple[str, str, object, str], ...]) -> None:
        """Lista de perfiles de `kind` (elegir uno = elegirlo en Control) con sus botones a la derecha."""
        row = ttk.Frame(parent)
        row.pack(fill="x")
        frame = ttk.Frame(row)
        frame.pack(side="left", fill="both", expand=True)
        lb = tk.Listbox(frame, height=LIST_HEIGHT, exportselection=False, activestyle="none", borderwidth=1, relief="solid",
                        highlightthickness=0, selectbackground="#1a73e8", selectforeground="#ffffff")
        sb = ttk.Scrollbar(frame, command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        lb.pack(side="left", fill="both", expand=True)
        lb.bind("<<ListboxSelect>>", lambda _e, k=kind: self._on_list_select(k))
        add_tooltip(lb, "Elegir un perfil acá es lo mismo que elegirlo en la pestaña Control: es el que usa el bot y el "
                        "que editan las demás pestañas.")
        self.lists[kind] = lb
        col = ttk.Frame(row)
        col.pack(side="left", fill="y", padx=(GAP, 0))
        for key, text, cmd, tip in buttons:
            b = ttk.Button(col, text=text, command=cmd)
            b.pack(fill="x", pady=(0, GAP_S))
            add_tooltip(b, tip)
            self.buttons[key] = b

    def _build_lists(self, body: ttk.Frame) -> None:
        box = section(body, "Perfiles de personaje")
        box.pack(fill="x")
        note(box, "Cada personaje tiene su perfil: habilidades, recoger a pie, revivir y cómo se ve su nombre amarillo (el "
                  "que aparece sobre sus objetos).", fluid=True).pack(fill="x", pady=(0, GAP_S))
        self._list_box(box, "personaje", (
            ("crear", "Crear…", self.create_character,
             "Crea un perfil nuevo: pide el nombre y guía la captura del nombre amarillo en el juego (con F9)."),
            ("duplicar_pj", "Duplicar…", self.duplicate_character,
             "Copia el perfil elegido con otro nombre (habilidades y opciones). Si es el mismo personaje conserva su nombre "
             "amarillo; si es otro, hay que capturarlo."),
            ("capturar", "Capturar nombre amarillo", self.capture_owner_name,
             "Vuelve a capturar en el juego el nombre amarillo del perfil elegido (guía paso a paso con F9).")))
        zbox = section(body, "Perfiles de zona")
        zbox.pack(fill="x", pady=(GAP, 0))
        note(zbox, "Una zona guarda el polígono de farmeo, los tipos de metin y su prioridad, el modo (metin o bichos) y los "
                   "ajustes de bichos. El mapa de metin no se copia: empieza vacío.", fluid=True).pack(
            fill="x", pady=(0, GAP_S))
        self._list_box(zbox, "zona", (
            ("duplicar_zona", "Duplicar…", self.duplicate_zone,
             "Copia la zona elegida con otro nombre (sin el mapa de metin). No cambia la zona elegida."),
            ("nueva_zona", "Nueva…", self.new_zone,
             "Crea una zona nueva como copia de la elegida, la elige y te lleva a la pestaña Zona para rehacer el polígono.")))

    def refresh_lists(self) -> None:
        """Vuelve a llenar las listas con los perfiles del disco y marca los elegidos en Control."""
        self._syncing = True
        try:
            for kind, lb in self.lists.items():
                names = self.app.profiles.get(kind, [])
                lb.delete(0, "end")
                for n in names:
                    lb.insert("end", n)
                self.sync_selection(kind)
        finally:
            self._syncing = False

    def sync_selection(self, kind: str) -> None:
        """Marca en la lista el perfil que está elegido en Control."""
        lb, cur = self.lists[kind], self.app.vars[kind].get()
        names = list(lb.get(0, "end"))
        lb.selection_clear(0, "end")
        if cur in names:
            lb.selection_set(names.index(cur))
            lb.see(names.index(cur))

    def _on_list_select(self, kind: str) -> None:
        if self._syncing:
            return
        sel = self.lists[kind].curselection()
        if sel:
            self.app.select_profile(kind, self.lists[kind].get(sel[0]))
        self.sync_selection(kind)   # si se canceló el cambio, vuelve a marcar el que estaba

    # ------------------------------------------------------------------ opciones
    def _build_options(self, body: ttk.Frame) -> None:
        self.opt_box = section(body, "Opciones del personaje")
        self.opt_box.pack(fill="x", pady=(GAP, 0))
        self.opt_box.columnconfigure(1, weight=1)
        self.vars: dict[str, tk.Variable] = {}
        self.fields: dict[str, ValidatedEntry] = {}
        self.checks: dict[str, ttk.Checkbutton] = {}
        r = 0
        r = self._check(r, "dismount", "Recoger a pie")
        ttk.Separator(self.opt_box).grid(row=r, column=0, columnspan=3, sticky="ew", pady=GAP)
        r += 1
        ttk.Label(self.opt_box, text="Revivir al morir", style="Big.TLabel").grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1
        r = self._check(r, "auto_revive", "Revivir al morir")
        r = self._entry(r, "horse_key", "Tecla del caballo:", 8)
        r = self._entry(r, "horse_item_template", "Objeto del caballo (imagen):", 40, browse=True)
        r = self._entry(r, "button_wait_s", "Espera antes del botón (s):", 8)
        r = self._entry(r, "max_revives", "Revivir como máximo (veces):", 8)
        r = self._check(r, "recast_skills", "Volver a lanzar las habilidades al revivir")
        self.potions_frame = ttk.Frame(self.opt_box)
        self.potions_frame.grid(row=r, column=0, columnspan=3, sticky="ew")
        self.potions_frame.columnconfigure(1, weight=1)
        ttk.Separator(self.potions_frame).grid(row=0, column=0, columnspan=3, sticky="ew", pady=GAP)
        ttk.Label(self.potions_frame, text="Pociones", style="Big.TLabel").grid(row=1, column=0, columnspan=3, sticky="w")
        self._entry(2, "hp_key", "Tecla de vida:", 8, parent=self.potions_frame)
        self._entry(3, "mp_key", "Tecla de maná:", 8, parent=self.potions_frame)
        self._check(4, "potions_auto", "Usar pociones automáticamente", parent=self.potions_frame)
        r += 1
        bar = ttk.Frame(self.opt_box)
        bar.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(GAP, 0))
        self.btn_save = ttk.Button(bar, text="Guardar", command=self.save)
        self.btn_discard = ttk.Button(bar, text="Descartar cambios", command=self.discard)
        for b, tip in ((self.btn_save, "Guarda las opciones en el perfil del personaje (con copia .bak)."),
                       (self.btn_discard, "Vuelve a leer las opciones del perfil y descarta lo que cambiaste sin guardar.")):
            b.pack(side="left", padx=(0, GAP))
            add_tooltip(b, tip)
        self.state_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.state_var, style="Warn.TLabel").pack(side="left")

    def _check(self, row: int, key: str, text: str, parent: ttk.Frame | None = None) -> int:
        var = tk.BooleanVar(value=bool(self.editor.values[key]))
        chk = ttk.Checkbutton(parent or self.opt_box, text=text, variable=var)
        chk.grid(row=row, column=0, columnspan=3, sticky="w", pady=GAP_S)
        add_tooltip(chk, TIPS[key])
        var.trace_add("write", lambda *_a: self._on_edit())
        self.vars[key], self.checks[key] = var, chk
        return row + 1

    def _entry(self, row: int, key: str, label: str, width: int, *, browse: bool = False,
               parent: ttk.Frame | None = None) -> int:
        host = parent or self.opt_box
        lab = ttk.Label(host, text=label)
        lab.grid(row=row, column=0, sticky="nw", padx=(0, GAP), pady=GAP_S)
        add_tooltip(lab, TIPS[key])
        field = ValidatedEntry(host, TEXT_VALIDATORS[key], value=str(self.editor.values[key]), width=width, tooltip=TIPS[key])
        field.grid(row=row, column=1, sticky="w")
        field.var.trace_add("write", lambda *_a: self._on_edit())
        self.vars[key], self.fields[key] = field.var, field
        if browse:
            col = ttk.Frame(host)
            col.grid(row=row, column=2, sticky="nw", padx=(GAP, 0), pady=GAP_S)
            pick = ttk.Button(col, text="Elegir archivo…", command=self.pick_horse_item)
            pick.pack(side="left")
            add_tooltip(pick, "Elegí la imagen PNG del icono del objeto que llama al caballo.")
            clear = ttk.Button(col, text="Quitar", command=lambda: field.set(""))
            clear.pack(side="left", padx=(GAP_S, 0))
            add_tooltip(clear, "Deja el campo vacío: no se usa el objeto del inventario.")
            self.buttons["pick_horse"], self.buttons["clear_horse"] = pick, clear
        return row + 1

    def pick_horse_item(self) -> None:
        """Selector de archivo del icono del caballo (se guarda relativo al proyecto si esta adentro)."""
        path = self.app.pick_file("Elegir el icono del objeto del caballo", PROJECT_ROOT / "templates")
        if path:
            self.fields["horse_item_template"].set(relative_template_path(path))

    def _on_edit(self) -> None:
        """Cambio en un widget: se vuelca al editor (si no lo estamos llenando nosotros)."""
        if self._syncing:
            return
        if self.editor.locked:
            self.refresh_options()
            return
        for k, var in self.vars.items():
            self.editor.values[k] = var.get()
        self.refresh_state()

    def refresh_options(self) -> None:
        """Vuelca el editor en los widgets y actualiza el titulo, las pociones y el indicador de cambios."""
        self._syncing = True
        try:
            for k, var in self.vars.items():
                if var.get() != self.editor.values[k]:
                    var.set(self.editor.values[k])
        finally:
            self._syncing = False
        name = self.editor.personaje or "(ninguno)"
        self.opt_box.configure(text=f"Opciones de {name}")
        if self.editor.has_potions:
            self.potions_frame.grid()
        else:
            self.potions_frame.grid_remove()
        self.refresh_state()

    def refresh_state(self) -> None:
        parts = []
        if self.editor.dirty:
            parts.append("sin guardar")
        if self.editor.locked:
            parts.append("bloqueado mientras el bot corre")
        self.state_var.set(" - ".join(parts))
        self.app.refresh_tab_titles()

    def set_locked(self, locked: bool, idle: bool) -> None:
        """Bloquea todo con el bot corriendo; `idle` = ademas no hay otra ventana guiada abierta."""
        self.editor.locked = locked
        for lb in self.lists.values():
            lb.configure(state="disabled" if locked else "normal")
        for k, chk in self.checks.items():
            chk.configure(state="disabled" if locked else "normal")
        for f in self.fields.values():
            f.entry.configure(state="disabled" if locked else "normal")
        guided = {"crear", "capturar"}
        for k, b in self.buttons.items():
            ok = idle if k in guided else not locked
            b.configure(state="normal" if ok else "disabled")
        self.btn_save.configure(state="disabled" if locked else "normal")
        self.btn_discard.configure(state="disabled" if locked else "normal")
        self.refresh_options()

    # ------------------------------------------------------------------ guardar / descartar
    def load(self, personaje: str) -> None:
        self.editor.load(personaje)
        self.refresh_options()

    def discard(self) -> None:
        """Descartar cambios: vuelve a leer el perfil."""
        self.load(self.editor.personaje)

    def save(self) -> bool:
        try:
            self.editor.save()
        except (ValueError, ConfigError, OSError) as e:
            self.app._notify("No se pudieron guardar las opciones del personaje", str(e))
            return False
        self.refresh_options()
        self.app.refresh_overrides()
        return True

    # ------------------------------------------------------------------ crear / duplicar
    def _guard(self) -> bool:
        """Antes de tocar perfiles: nada corriendo y sin cambios sin guardar pendientes (o el usuario eligio)."""
        a = self.app
        return a.options_enabled() and a.confirm_unsaved_all("continuar")

    def create_character(self) -> None:
        """Crear: `calibrate.py personaje "<Nombre>"` con la guia en pantalla."""
        a = self.app
        if not a.options_enabled() or a.calibrating:
            return
        existing = list(a.profiles.get("personaje", []))
        made: list[str] = []

        def factory(name: str) -> list[str]:
            made.append(name)
            return cr.build_calibrate_command("personaje", nombre=name, servidor=a.vars["servidor"].get(),
                                              zona=a.vars["zona"].get(), config=a._calib_config())

        def done(rc: int | None) -> None:
            a.refresh_profile_lists()
            if made and rc == 0 and po.profile_exists("personaje", made[-1], a.profiles_root):
                a.select_profile("personaje", made[-1])

        a._open_calibrate(cr.PERSONAJE_TITLE, cr.PERSONAJE_STEPS, factory, done, name_label="Nombre del personaje:",
                          name_validator=lambda t: po.validate_new_profile_name("personaje", t, existing, a.profiles_root),
                          name_tooltip="Nombre del personaje tal como aparece en el juego (es el nombre del perfil y el "
                                       "del nombre amarillo sobre sus objetos).")

    def capture_owner_name(self) -> None:
        """Capturar nombre amarillo del perfil elegido: `calibrate.py personaje "<Perfil>"`."""
        a = self.app
        name = a.vars["personaje"].get()
        if not name or not a.options_enabled() or a.calibrating or not a.confirm_unsaved_all("capturar el nombre amarillo"):
            return
        a._open_calibrate(cr.PERSONAJE_CAPTURE_TITLE, cr.PERSONAJE_STEPS,
                          lambda _n: cr.build_calibrate_command("personaje", nombre=name, servidor=a.vars["servidor"].get(),
                                                                zona=a.vars["zona"].get(), config=a._calib_config()),
                          lambda rc: (a.refresh_profile_lists(), self.load(self.editor.personaje), a.refresh_overrides()))

    def duplicate_character(self) -> None:
        a = self.app
        src = a.vars["personaje"].get()
        if not src or not self._guard():
            return
        res = a.ask_profile_name(
            "Duplicar personaje", f"Se copia el perfil '{src}' (habilidades y opciones) con otro nombre.",
            lambda t: po.validate_new_profile_name("personaje", t, root=a.profiles_root),
            check_text="Es el mismo personaje (usa el mismo nombre amarillo)", check_default=True,
            check_tip="Marcalo si es el mismo personaje con otra configuración: se conserva su nombre amarillo. Si es otro "
                      "personaje, desmarcalo: después hay que capturar su nombre amarillo.")
        if res is None:
            return
        name, same = res
        try:
            notes = po.duplicate_character_profile(src, name, same, a.profiles_root)
        except (ConfigError, OSError, ValueError) as e:
            a._notify("No se pudo duplicar el personaje", str(e))
            return
        a.refresh_profile_lists()
        a.inform("Personaje duplicado", f"Se creó el perfil '{name}' como copia de '{src}'. No se cambió el personaje elegido."
                 + ("\n\n" + "\n".join(notes) if notes else ""))

    def _copy_zone(self, switch: bool) -> None:
        a = self.app
        src = a.vars["zona"].get()
        if not src or not self._guard():
            return
        title = "Zona nueva" if switch else "Duplicar zona"
        res = a.ask_profile_name(title, f"Se copia la zona '{src}' (polígono, tipos de metin, prioridad, modo y bichos) con "
                                        "otro nombre. El mapa de metin no se copia: empieza vacío.",
                                 lambda t: po.validate_new_profile_name("zona", t, root=a.profiles_root))
        if res is None:
            return
        name = res[0]
        try:
            po.duplicate_zone_profile(src, name, a.profiles_root)
        except (ConfigError, OSError, ValueError) as e:
            a._notify("No se pudo copiar la zona", str(e))
            return
        a.refresh_profile_lists()
        if switch:
            a.select_profile("zona", name)
            a.show_tab("zona")
            a.inform("Zona nueva", f"Se creó la zona '{name}' como copia de '{src}' y quedó elegida. Ahora usá \"Rehacer la "
                                   "zona\" en esta pestaña para marcar su polígono.")
        else:
            a.inform("Zona duplicada", f"Se creó la zona '{name}' como copia de '{src}'. No se cambió la zona elegida.")

    def duplicate_zone(self) -> None:
        self._copy_zone(False)

    def new_zone(self) -> None:
        self._copy_zone(True)
