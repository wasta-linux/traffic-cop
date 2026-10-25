"""The main window: daemon state, a button for the limits, and live rates.

Layout::

    +---------------------------------------------------------------+
    | Shaping traffic on wlp0s20f3                  [ Limits on ]   |  <- slate band
    | Traffic Cop 2.0.0                                             |
    +---------------------------------------------------------------+
    | (o) Firefox    v [##########--------]  45.8 / 70 kB/s  31.2 MB |  <- white
    |                ^ [#-----------------]   2.5 / 50 kB/s   1.2 MB |
    |---------------------------------------------------------------|
    | ( ) Spotify    v [------------------]    0.0 / 16 kB/s   4.1 MB |
    |                ^ [------------------]    0.0 / 8 kB/s   92.0 kB |
    +---------------------------------------------------------------+

Each app takes one visual row: a dot (filled green when running, a hollow
ring when not; "x2" after the name for several instances), the name, then
its download bar above its upload bar, marked with arrows. The rate text is
"rate / limit unit" and the dim figure after it the total since the daemon
started. Apart from the header, the table has no words, so it reads the
same in any language.

The window polls the daemon through ``Client`` (``usage`` every second,
``status`` every few seconds) and redraws from ``model.build_view``. If the
daemon isn't running it says how to start it and keeps trying.

Look: ttk's "alt" theme, restyled with the Wasta Linux website's palette
(its dark slate and blue, from the Arc theme) on a white body, and SIL's
Andika font (see ``fonts.py``). The bar column takes all the spare width,
and the text columns have fixed minimum widths, so bars keep one length
that follows the window, not the numbers. A bar is Wasta blue, and
turns yellow when the limit is holding its app back (``model.FULL``): limited,
not stopped, so a caution colour rather than an alarm.

Read-only apart from the "Limits on" button (a ttk toggle button: pressed
means on), which sends pause/resume; the daemon decides who may (see
server.py), and a refusal is shown in a message box.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import font, messagebox, ttk
from typing import Any

from trafficcop.common.i18n import _
from trafficcop.gui import fonts
from trafficcop.gui.client import Client, Reply
from trafficcop.gui.model import AppRow, Direction, Level, View, build_view

DISPATCH_MS = 100
USAGE_MS = 1000
STATUS_MS = 3000
THEME = "alt"
RATE_MIN_WIDTH = 130  # pixels; fits "118.4 / 125 kB/s" in Andika 10
TOTAL_MIN_WIDTH = 70  # fits "210.4 MB"
ROW_GAP = 8  # pixels on each side of the rule between apps
DOT = 12  # running indicator, pixels
ARROWS = {"down": "\u2193", "up": "\u2191"}  # in Andika
# Table columns
C_DOT, C_NAME, C_ARROW, C_BAR, C_RATE, C_TOTAL = range(6)

# Wasta Linux website palette (wastalinux.org, from the Arc theme)
SLATE = "#383C4A"
SLATE_LIGHT = "#5C6170"
BLUE = "#5294E2"
BLUE_DARK = "#3B7DD8"
WHITE = "#FFFFFF"
TEXT = SLATE
DIM = "#7C818C"
ON_SLATE_DIM = "#C3C7D0"
TROUGH = "#E4E7EB"
GREEN = "#3BA55C"
YELLOW = "#E8B400"

BAR_STYLES: dict[Level, str] = {
    "ok": "Ok.Horizontal.TProgressbar",
    "full": "Full.Horizontal.TProgressbar",
}
BAR_COLOURS: dict[Level, str] = {"ok": BLUE, "full": YELLOW}


def _style(root: tk.Tk) -> ttk.Style:
    style = ttk.Style(root)
    if THEME in style.theme_names():
        style.theme_use(THEME)
    root.configure(background=WHITE)
    style.configure(".", background=WHITE, foreground=TEXT)
    style.configure("TFrame", background=WHITE)
    style.configure("TLabel", background=WHITE, foreground=TEXT)
    style.configure("Dim.TLabel", foreground=DIM)
    style.configure("Rule.TFrame", background=TROUGH)
    style.configure("Header.TFrame", background=SLATE)
    style.configure("Header.TLabel", background=SLATE, foreground=WHITE)
    style.configure("HeaderDim.TLabel", background=SLATE, foreground=ON_SLATE_DIM)
    style.configure(
        "Limits.Toolbutton",
        background=SLATE_LIGHT,
        foreground=WHITE,
        padding=(14, 6),
        relief="raised",
        borderwidth=1,
    )
    style.map(
        "Limits.Toolbutton",
        background=[
            ("disabled", SLATE_LIGHT),
            ("selected", "active", BLUE_DARK),
            ("selected", BLUE),
            ("active", "#6B7080"),
        ],
        foreground=[("disabled", ON_SLATE_DIM)],
        relief=[("selected", "sunken")],
    )
    for level, name in BAR_STYLES.items():
        style.configure(
            name,
            background=BAR_COLOURS[level],
            troughcolor=TROUGH,
            borderwidth=0,
            thickness=10,
        )
    return style


@dataclass
class _DirectionWidgets:
    arrow: ttk.Label
    bar: ttk.Progressbar
    text: ttk.Label
    total: ttk.Label

    def widgets(self) -> tuple[tk.Misc, ...]:
        return (self.arrow, self.bar, self.text, self.total)


@dataclass
class _RowWidgets:
    dot: tk.Canvas
    name: ttk.Label
    down: _DirectionWidgets
    up: _DirectionWidgets
    rule: ttk.Frame | None  # above the row; None for the first app

    def destroy(self) -> None:
        for w in (self.dot, self.name, *self.down.widgets(), *self.up.widgets()):
            w.destroy()
        if self.rule is not None:
            self.rule.destroy()


class MainWindow:
    def __init__(self, root: tk.Tk, client: Client):
        self.root = root
        self.client = client
        self.status: dict[str, Any] | None = None
        self.usage: dict[str, Any] | None = None
        self.error: Exception | None = None
        self.busy = False  # a pause/resume request is in flight
        self._rows: dict[str, _RowWidgets] = {}

        root.title("Traffic Cop")
        fonts.apply(root)
        _style(root)
        root.minsize(760, 200)
        root.columnconfigure(0, weight=1)
        bold = font.nametofont("TkDefaultFont").copy()
        bold.configure(weight="bold")
        big = font.nametofont("TkDefaultFont").copy()
        big.configure(weight="bold", size=big.cget("size") + 2)
        self._fonts = (bold, big)  # keep references: Tk forgets unreferenced fonts

        header = ttk.Frame(root, style="Header.TFrame", padding=(16, 12))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        self.headline = ttk.Label(header, style="Header.TLabel", font=big)
        self.headline.grid(row=0, column=0, sticky="w")
        self.detail = ttk.Label(header, style="HeaderDim.TLabel")
        self.detail.grid(row=1, column=0, sticky="w")
        self.active = tk.BooleanVar(value=True)
        self.switch = ttk.Checkbutton(
            header,
            text=_("Limits on"),
            variable=self.active,
            command=self._toggle,
            style="Limits.Toolbutton",
            cursor="hand2",
        )
        self.switch.grid(row=0, column=1, rowspan=2, sticky="e")

        self.table = ttk.Frame(root, padding=(16, 10, 16, 12))
        self.table.grid(row=1, column=0, sticky="nsew")
        root.rowconfigure(1, weight=1)
        # The bars take the spare width; the text columns have fixed minimums,
        # so a bar's length follows the window, not the numbers beside it.
        self.table.columnconfigure(C_BAR, weight=1)
        self.table.columnconfigure(C_RATE, minsize=RATE_MIN_WIDTH)
        self.table.columnconfigure(C_TOTAL, minsize=TOTAL_MIN_WIDTH)
        self.empty = ttk.Label(
            self.table, text=_("No apps configured."), style="Dim.TLabel"
        )

        self._render()
        self._dispatch()
        self._poll_status()
        self._poll_usage()

    # --- Polling -------------------------------------------------------------------

    def _dispatch(self) -> None:
        self.client.dispatch()
        self.root.after(DISPATCH_MS, self._dispatch)

    def _poll_status(self) -> None:
        self.client.request("status", self._on_status, once=True)
        self.root.after(STATUS_MS, self._poll_status)

    def _poll_usage(self) -> None:
        if self.status is not None:
            self.client.request("usage", self._on_usage, once=True)
        self.root.after(USAGE_MS, self._poll_usage)

    def _on_status(self, reply: Reply) -> None:
        if reply.ok:
            self.status, self.error = reply.result, None
        elif reply.unreachable:
            self.status, self.usage, self.error = None, None, reply.error
        self._render()

    def _on_usage(self, reply: Reply) -> None:
        if reply.ok:
            self.usage = reply.result
            self._render()

    # --- The limits button -----------------------------------------------------------

    def _toggle(self) -> None:
        cmd = "resume" if self.active.get() else "pause"
        self.busy = True
        self._render()
        self.client.request(cmd, self._on_toggled)

    def _on_toggled(self, reply: Reply) -> None:
        self.busy = False
        if reply.ok:
            self.status = reply.result
        elif reply.permission_denied:
            messagebox.showwarning(
                _("Not allowed"),
                _("Only administrators can turn the limits on or off."),
                parent=self.root,
            )
        elif reply.unreachable:
            self.status, self.error = None, reply.error
        else:
            messagebox.showerror("Traffic Cop", reply.message, parent=self.root)
        self._render()

    # --- Drawing -----------------------------------------------------------------

    def _render(self) -> None:
        view = build_view(self.status, self.usage, self.error)
        self.headline.configure(text=view.headline)
        self.detail.configure(text=view.detail)
        if view.paused is not None:
            self.active.set(not view.paused)
        self.switch.configure(
            text=_("Limits on") if self.active.get() else _("Limits off")
        )
        self.switch.state(
            ["!disabled"] if view.connected and not self.busy else ["disabled"]
        )
        self._render_rows(view)

    def _render_rows(self, view: View) -> None:
        names = [row.name for row in view.rows]
        if names != list(self._rows):
            for widgets in self._rows.values():
                widgets.destroy()
            self._rows = {name: self._make_row(i) for i, name in enumerate(names)}
        if view.connected and not view.rows:
            self.empty.grid(row=0, column=0, columnspan=C_TOTAL + 1, sticky="w")
        else:
            self.empty.grid_remove()
        for row in view.rows:
            self._fill_row(self._rows[row.name], row)

    def _make_row(self, index: int) -> _RowWidgets:
        r = 3 * index  # per app: a rule (except the first), then down and up
        rule = None
        if index:
            rule = ttk.Frame(self.table, style="Rule.TFrame", height=1)
            rule.grid(
                row=r, column=0, columnspan=C_TOTAL + 1, sticky="ew", pady=ROW_GAP
            )

        def direction(row: int, key: str) -> _DirectionWidgets:
            arrow = ttk.Label(self.table, text=ARROWS[key])
            arrow.grid(row=row, column=C_ARROW, padx=(0, 6))
            bar = ttk.Progressbar(self.table, maximum=1.0, style=BAR_STYLES["ok"])
            bar.grid(row=row, column=C_BAR, sticky="ew")
            text = ttk.Label(self.table)
            text.grid(row=row, column=C_RATE, sticky="e", padx=(12, 0))
            total = ttk.Label(self.table, style="Dim.TLabel")
            total.grid(row=row, column=C_TOTAL, sticky="e", padx=(12, 0))
            return _DirectionWidgets(arrow, bar, text, total)

        dot = tk.Canvas(
            self.table,
            width=DOT,
            height=DOT,
            background=WHITE,
            highlightthickness=0,
            borderwidth=0,
        )
        dot.grid(row=r + 1, column=C_DOT, rowspan=2, padx=(0, 8))
        name = ttk.Label(self.table, font=self._fonts[0])
        name.grid(row=r + 1, column=C_NAME, rowspan=2, sticky="w", padx=(0, 16))
        return _RowWidgets(
            dot, name, direction(r + 1, "down"), direction(r + 2, "up"), rule
        )

    def _fill_row(self, widgets: _RowWidgets, row: AppRow) -> None:
        name = row.name if row.instances <= 1 else f"{row.name} \u00d7{row.instances}"
        widgets.name.configure(text=name, foreground=TEXT if row.running else DIM)
        dot = widgets.dot
        dot.delete("all")
        if row.running:
            dot.create_oval(1, 1, DOT - 1, DOT - 1, fill=GREEN, outline=GREEN)
        else:
            dot.create_oval(1.5, 1.5, DOT - 1.5, DOT - 1.5, outline=DIM, width=1.5)
        for w, d in ((widgets.down, row.down), (widgets.up, row.up)):
            self._fill_direction(w, d)

    @staticmethod
    def _fill_direction(widgets: _DirectionWidgets, d: Direction) -> None:
        widgets.text.configure(text=d.text)
        widgets.total.configure(text=d.total)
        if d.fraction is None:
            widgets.bar.grid_remove()  # unlimited: nothing to fill up
        else:
            widgets.bar.grid()
            widgets.bar.configure(value=d.fraction, style=BAR_STYLES[d.level or "ok"])
