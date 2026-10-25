"""The dialog for adding or changing an app.

    Name       [Zoom                        ]
    ↓          [70       ] [kB/s v]   blank: no limit
    ↑          [35       ] [kB/s v]
    Recognise the app by
      [Snap package      v] [zoom-client       ] [−]
      [Program (full path)v] [/opt/zoom/zoom    ] [−]
      [+]
    (error message, if any)
    [Remove]                          [Cancel] [Save]

It edits an ``editor.AppDraft`` and hands it to ``on_save``; the main window
does the talking to the daemon and then calls ``close`` or ``show_error``.
For users who may not change the config, every field is read-only and there
is no Save button. Modal: the main window waits until it's closed.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from trafficcop.common.i18n import _
from trafficcop.gui.editor import UNITS, AppDraft, Limit, rule_keys

ERROR = "#B3261E"


class AppDialog(tk.Toplevel):
    def __init__(
        self,
        master: tk.Misc,
        draft: AppDraft,
        *,
        new: bool,
        editable: bool,
        on_save: Callable[[AppDraft], None],
        on_remove: Callable[[], None] | None = None,
    ):
        super().__init__(master)
        self.title(_("Add an app") if new else _("Change an app"))
        self.transient(master.winfo_toplevel())
        self.resizable(True, False)
        self.configure(background="white")
        self._editable = editable
        self._on_save = on_save
        self._fixed = list(draft.fixed_rules)
        self._unit_labels = {u: _(u) for u in UNITS}
        self._key_labels = dict(rule_keys())

        body = ttk.Frame(self, padding=16)
        body.grid(sticky="nsew")
        self.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        label_pad = (0, 12)
        ttk.Label(body, text=_("Name")).grid(
            row=0, column=0, sticky="w", padx=label_pad, pady=4
        )
        self.name = tk.StringVar(value=draft.name)
        self._name_entry = ttk.Entry(body, textvariable=self.name, width=32)
        self._name_entry.grid(row=0, column=1, columnspan=3, sticky="ew", pady=4)

        self.limits: dict[str, tuple[tk.StringVar, tk.StringVar]] = {}
        for row, (key, arrow, limit) in enumerate(
            (("download", "↓", draft.download), ("upload", "↑", draft.upload)),
            start=1,
        ):
            ttk.Label(body, text=arrow).grid(
                row=row, column=0, sticky="w", padx=label_pad, pady=4
            )
            number = tk.StringVar(value=limit.number)
            unit = tk.StringVar(value=self._unit_labels[limit.unit])
            pair = ttk.Frame(body)  # keeps the number and its unit together
            pair.grid(row=row, column=1, sticky="w", pady=4)
            ttk.Entry(pair, textvariable=number, width=10).grid(row=0, column=0)
            ttk.Combobox(
                pair,
                textvariable=unit,
                values=list(self._unit_labels.values()),
                state="readonly",
                width=7,
            ).grid(row=0, column=1, padx=(6, 0))
            if key == "download":
                ttk.Label(pair, text=_("blank: no limit"), style="Dim.TLabel").grid(
                    row=0, column=2, padx=(12, 0)
                )
            self.limits[key] = (number, unit)

        ttk.Label(body, text=_("Recognise the app by")).grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(12, 4)
        )
        self._rules_frame = ttk.Frame(body)
        self._rules_frame.grid(row=4, column=0, columnspan=4, sticky="ew")
        self._rules_frame.columnconfigure(1, weight=1)
        self._rules: list[tuple[tk.StringVar, tk.StringVar, list[tk.Widget]]] = []
        for key, value in draft.rules:
            self._add_rule(key, value)
        self._draw_fixed()
        self._add_button = ttk.Button(
            body, text="+", width=3, command=self._add_rule, style="Secondary.TButton"
        )
        self._add_button.grid(row=5, column=0, sticky="w", pady=(4, 0))
        if not draft.rules and not draft.fixed_rules:
            self._add_rule()

        self.error = ttk.Label(body, foreground=ERROR, wraplength=420)
        self.error.grid(row=6, column=0, columnspan=4, sticky="w", pady=(10, 0))
        if not editable:
            self.error.configure(
                text=_("Only administrators can change apps."), style="Dim.TLabel"
            )

        buttons = ttk.Frame(body)
        buttons.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        buttons.columnconfigure(1, weight=1)
        if on_remove is not None and editable:
            ttk.Button(
                buttons, text=_("Remove"), command=on_remove, style="Secondary.TButton"
            ).grid(row=0, column=0, sticky="w")
        self.cancel = ttk.Button(
            buttons,
            text=_("Cancel") if editable else _("Close"),
            command=self.close,
            style="Secondary.TButton",
        )
        self.cancel.grid(row=0, column=2, sticky="e")
        self.save: ttk.Button | None = None
        if editable:
            self.save = ttk.Button(
                buttons, text=_("Save"), command=self._save, style="Primary.TButton"
            )
            self.save.grid(row=0, column=3, sticky="e", padx=(8, 0))
            self.bind("<Return>", lambda e: self._save())
        self.bind("<Escape>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)

        if not editable:
            self._set_state(body, "disabled")
            self.cancel.state(["!disabled"])
        self._name_entry.focus_set()
        self.wait_visibility()
        self.grab_set()

    # --- Rules ---------------------------------------------------------------------

    def _add_rule(self, key: str = "snap", value: str = "") -> None:
        row = len(self._rules) + len(self._fixed)
        key_var = tk.StringVar(value=self._key_labels.get(key, key))
        value_var = tk.StringVar(value=value)
        combo = ttk.Combobox(
            self._rules_frame,
            textvariable=key_var,
            values=list(self._key_labels.values()),
            state="readonly",
            width=24,
        )
        combo.grid(row=row, column=0, sticky="w", pady=2)
        entry = ttk.Entry(self._rules_frame, textvariable=value_var, width=28)
        entry.grid(row=row, column=1, sticky="ew", padx=6, pady=2)
        widgets: list[tk.Widget] = [combo, entry]
        item = (key_var, value_var, widgets)
        remove = ttk.Button(
            self._rules_frame,
            text="−",
            width=3,
            command=lambda: self._remove(item),
            style="Secondary.TButton",
        )
        remove.grid(row=row, column=2, pady=2)
        widgets.append(remove)
        self._rules.append(item)
        if value == "":
            entry.focus_set()

    def _remove(self, item: tuple[tk.StringVar, tk.StringVar, list[tk.Widget]]) -> None:
        for w in item[2]:
            w.destroy()
        self._rules.remove(item)

    def _draw_fixed(self) -> None:
        """Multi-key rules from the config file: shown, removable, not editable."""
        for i, rule in enumerate(self._fixed):
            text = " + ".join(
                f"{self._key_labels.get(k, k)} = {v}" for k, v in rule.items()
            )
            label = ttk.Label(self._rules_frame, text=text, style="Dim.TLabel")
            label.grid(row=100 + i, column=0, columnspan=2, sticky="w", pady=2)
            button = ttk.Button(
                self._rules_frame, text="−", width=3, style="Secondary.TButton"
            )
            button.configure(
                command=lambda r=rule, w=(label, button): self._drop_fixed(r, w)
            )
            button.grid(row=100 + i, column=2, pady=2)

    def _drop_fixed(self, rule: dict[str, str], widgets: tuple[tk.Widget, ...]) -> None:
        self._fixed.remove(rule)
        for w in widgets:
            w.destroy()

    # --- Results ------------------------------------------------------------------

    def draft(self) -> AppDraft:
        units = {label: unit for unit, label in self._unit_labels.items()}
        keys = {label: key for key, label in self._key_labels.items()}

        def limit(name: str) -> Limit:
            number, unit = self.limits[name]
            return Limit(number.get(), units.get(unit.get(), "kB/s"))

        return AppDraft(
            name=self.name.get(),
            download=limit("download"),
            upload=limit("upload"),
            rules=[(keys.get(k.get(), k.get()), v.get()) for k, v, _w in self._rules],
            fixed_rules=list(self._fixed),
        )

    def _save(self) -> None:
        self.show_error("")
        if self.save is not None:
            self.save.state(["disabled"])
        self._on_save(self.draft())

    def show_error(self, message: str) -> None:
        self.error.configure(text=message)
        if self.save is not None and message:
            self.save.state(["!disabled"])

    def close(self) -> None:
        self.grab_release()
        self.destroy()

    @staticmethod
    def _set_state(widget: tk.Misc, state: str) -> None:
        for child in widget.winfo_children():
            if isinstance(child, ttk.Combobox | ttk.Entry | ttk.Button):
                child.state([state])
            AppDialog._set_state(child, state)
