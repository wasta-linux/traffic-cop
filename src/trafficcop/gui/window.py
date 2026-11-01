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

Until the store connects the daemon's interfaces automatically, a store
install's daemon may lack them and die at startup. So the window checks
(``connections.missing``, on a thread since it runs snapctl) at startup,
whenever the daemon stops answering, and from the helper's "Check again".
While plugs are missing, a "Connect…" button replaces the limits switch and
opens the helper (``gui/connections.py``) with the command to run; it also
opens on its own, once per launch, when the window is shown (so not at a
hidden start at login).

Look: ttk's "alt" theme, restyled with the Wasta Linux website's palette
(its dark slate and blue, from the Arc theme) on a white body, and SIL's
Andika font (see ``fonts.py``). The bar column takes all the spare width,
and the text columns have fixed minimum widths, so bars keep one length
that follows the window, not the numbers. A bar is Wasta blue, and
turns yellow when the limit is holding its app back (``model.FULL``): limited,
not stopped, so a caution colour rather than an alarm.

The tray icon (indicator/tray.py, on its own thread) is fed from the same
status polling: each render hands it a ``menu.TrayState``, and its clicks
come back through ``_dispatch``. While it's shown, the close button only
hides the window (as most apps with a tray icon do); its "Open" shows the
window again and "Quit" ends the process. Without a tray, the close button
quits. Usage isn't polled while the window is hidden.

Changes: the "Limits on" button (a ttk toggle button: pressed means on)
sends pause/resume. "+" adds an app, clicking an app's name opens it for
changing or removing (``dialog.AppDialog``), and the "…" menu restores the
default apps. Each change fetches the current config, edits it
(``editor.py``) and sends it back with ``set-config``. The daemon decides
who may (see server.py); for other users the editor is read-only.
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tkinter import font, messagebox, ttk
from typing import Any

from trafficcop.common import autostart, config, connections, paths, protocol
from trafficcop.common.i18n import _
from trafficcop.gui import editor, fonts
from trafficcop.gui.client import Client, Reply
from trafficcop.gui.connections import ConnectionsDialog
from trafficcop.gui.dialog import AppDialog
from trafficcop.gui.model import AppRow, Direction, Level, View, build_view
from trafficcop.indicator import menu
from trafficcop.indicator.tray import NO_TRAY, NOT_ALLOWED, READY, Tray

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
SLATE_PALE = "#A3A8B4"  # paused bars: present but not in force
BLUE = "#5294E2"
BLUE_DARK = "#3B7DD8"
WHITE = "#FFFFFF"
TEXT = SLATE
DIM = "#7C818C"
ON_SLATE_DIM = "#C3C7D0"
TROUGH = "#E4E7EB"
FIELD_GREY = "#EEF0F2"
GREEN = "#3BA55C"
YELLOW = "#E8B400"

BAR_STYLES: dict[Level, str] = {
    "ok": "Ok.Horizontal.TProgressbar",
    "full": "Full.Horizontal.TProgressbar",
    "paused": "Paused.Horizontal.TProgressbar",
}
BAR_COLOURS: dict[Level, str] = {"ok": BLUE, "full": YELLOW, "paused": SLATE_PALE}


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
    # Disabled header buttons sink into the header (its colour, dim text, a
    # thin outline): only paler text on the usual slate looked just as
    # clickable. "solid" draws the border in one colour, bordercolor.
    style.map(
        "Limits.Toolbutton",
        background=[
            ("disabled", SLATE),
            ("selected", "active", BLUE_DARK),
            ("selected", BLUE),
            ("active", "#6B7080"),
        ],
        foreground=[("disabled", DIM)],
        relief=[("disabled", "solid"), ("selected", "sunken")],
        bordercolor=[("disabled", SLATE_LIGHT)],
    )
    for level, name in BAR_STYLES.items():
        style.configure(
            name,
            background=BAR_COLOURS[level],
            troughcolor=TROUGH,
            borderwidth=0,
            thickness=10,
        )
    style.configure(
        "Header.TButton",
        background=SLATE_LIGHT,
        foreground=WHITE,
        padding=(12, 4),
        relief="raised",
        borderwidth=1,
    )
    style.map(
        "Header.TButton",
        background=[("disabled", SLATE), ("active", "#6B7080")],
        foreground=[("disabled", DIM)],
        relief=[("disabled", "solid")],
        bordercolor=[("disabled", SLATE_LIGHT)],
    )
    # "Connect…" stands in for the limits switch, in blue since it's the one
    # thing to do.
    style.configure(
        "Connect.TButton",
        background=BLUE,
        foreground=WHITE,
        padding=(14, 6),
        relief="raised",
        borderwidth=1,
    )
    style.map("Connect.TButton", background=[("active", BLUE_DARK)])
    style.configure("Link.TLabel", foreground=TEXT)
    # Dialog buttons: blue for the main action, slate (like the header's "+")
    # for the rest; both stand out on white.
    for name, colour, hover in (
        ("Primary.TButton", BLUE, BLUE_DARK),
        ("Secondary.TButton", SLATE_LIGHT, "#6B7080"),
    ):
        style.configure(
            name,
            background=colour,
            foreground=WHITE,
            padding=(12, 4),
            relief="raised",
            borderwidth=1,
        )
        style.map(
            name,
            background=[("disabled", TROUGH), ("pressed", SLATE), ("active", hover)],
            foreground=[("disabled", DIM)],
        )
    # Pick-from-a-list and read-only fields get a light grey fill, so they
    # stand apart from fields you type in.
    style.map(
        "TCombobox",
        fieldbackground=[("disabled", FIELD_GREY), ("readonly", FIELD_GREY)],
    )
    style.map("TEntry", fieldbackground=[("disabled", FIELD_GREY)])
    # The app dialog has a light grey background, so the fields you type in
    # stand out in white. Its drop-downs and read-only fields go one grey
    # darker (TROUGH), or they'd vanish into the background.
    style.configure("Dialog.TFrame", background=FIELD_GREY)
    style.configure("Dialog.TLabel", background=FIELD_GREY, foreground=TEXT)
    style.configure("DialogDim.TLabel", background=FIELD_GREY, foreground=DIM)
    style.map(
        "Dialog.TCombobox",
        fieldbackground=[("disabled", TROUGH), ("readonly", TROUGH)],
    )
    style.map("Dialog.TEntry", fieldbackground=[("disabled", TROUGH)])
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
    def __init__(
        self,
        root: tk.Tk,
        client: Client,
        tray: Tray | None = None,
        hidden: bool = False,
        autostart_file: Path | None = None,
        removing_file: Path | None = None,
        check_connections: Callable[[], tuple[str, ...] | None] = connections.missing,
    ):
        """``hidden``: start with only the tray icon (quits if there's no tray).

        ``autostart_file``: the login file (``paths.autostart_file``); None
        (outside the snap) leaves out the "Start in the tray at login" item.

        ``removing_file``: the snap's remove hook writes it (``paths.Paths.removing``);
        see ``_being_removed``.

        ``check_connections``: returns the daemon's unconnected plugs, or
        None if unknown; run on a thread. Tests pass a fake.
        """
        self.root = root
        self.client = client
        self.tray = tray
        self.autostart_file = autostart_file
        self.removing_file = removing_file
        self.started = time.time()
        self.tray_ready = False  # the icon is shown, so closing may only hide
        self.hidden = hidden
        self.closed = False  # destroyed: nothing more to do
        self.status: dict[str, Any] | None = None
        self.usage: dict[str, Any] | None = None
        self.error: Exception | None = None
        self.busy = False  # a pause/resume request is in flight
        self._rows: dict[str, _RowWidgets] = {}
        self.check_connections = check_connections
        self.missing: tuple[str, ...] = ()  # the daemon's unconnected plugs
        self._checking = False  # a check is running on its thread
        self._checked: queue.Queue[tuple[str, ...] | None] = queue.Queue()
        self.helper: ConnectionsDialog | None = None
        self._helper_opened = False  # it opens on its own only once per launch

        root.title("Traffic Cop")
        root.protocol("WM_DELETE_WINDOW", self.close)
        if hidden:
            root.withdraw()  # before Tk first maps it, so it never flashes up
        fonts.apply(root)
        _style(root)
        root.minsize(760, 200)
        root.columnconfigure(0, weight=1)
        bold = font.nametofont("TkDefaultFont").copy()
        bold.configure(weight="bold")
        big = font.nametofont("TkDefaultFont").copy()
        big.configure(weight="bold", size=big.cget("size") + 2)
        self._fonts = (bold, big)  # keep references: Tk forgets unreferenced fonts
        ttk.Style(root).configure("Header.TButton", font=big)  # "+" and "…" read small

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
        self.switch.grid(row=0, column=3, rowspan=2, sticky="e")
        # In the switch's place while plugs are missing (see _render).
        self.connect = ttk.Button(
            header,
            text=_("Connect\u2026"),
            command=self.open_helper,
            style="Connect.TButton",
            cursor="hand2",
        )
        self.connect.grid(row=0, column=3, rowspan=2, sticky="e")
        self.connect.grid_remove()
        self.add = ttk.Button(
            header,
            text="+",
            style="Header.TButton",
            command=self._add_app,
            cursor="hand2",
            width=2,
        )
        self.add.grid(row=0, column=1, rowspan=2, sticky="e", padx=(0, 6))
        # A tk.Menu isn't themed by ttk. Without colours it had Tk's own
        # grey (#d9d9d9), darker than anything in the palette. On the light
        # grey, Tk's hover colour (#ececec) would barely show, so the item
        # under the pointer is blue.
        self.menu = tk.Menu(
            root,
            tearoff=False,
            background=FIELD_GREY,
            foreground=TEXT,
            activebackground=BLUE,
            activeforeground=WHITE,
            selectcolor=TEXT,  # the tick of "Start in the tray at login"
            relief="solid",
            borderwidth=1,
        )
        self.menu.add_command(label=_("Add an app\u2026"), command=self._add_app)
        self.menu.add_command(
            label=_("Restore the default apps\u2026"), command=self._restore_defaults
        )
        self.start_at_login = tk.BooleanVar(
            value=autostart_file is not None and autostart.enabled(autostart_file)
        )
        if autostart_file is not None:
            self.menu.add_separator()
            self.menu.add_checkbutton(
                label=_("Start in the tray at login"),
                variable=self.start_at_login,
                command=self._set_start_at_login,
            )
        self.more = ttk.Button(
            header,
            text="\u2026",
            style="Header.TButton",
            command=self._show_menu,
            cursor="hand2",
            width=2,
        )
        self.more.grid(row=0, column=2, rowspan=2, sticky="e", padx=(0, 12))
        self.may_change = editor.may_change()
        self.dialog: AppDialog | None = None

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
        self._check()

    # --- Polling -------------------------------------------------------------------

    def _dispatch(self) -> None:
        # Scheduled first: a tray event may destroy the window, after which
        # ``after`` would fail.
        self.root.after(DISPATCH_MS, self._dispatch)
        self.client.dispatch()
        while not self._checked.empty():
            self._on_connections(self._checked.get_nowait())
        if self.tray is not None:
            for event in self.tray.events():
                if not self.closed:
                    self._on_tray(event)

    def _poll_status(self) -> None:
        if self._being_removed():
            self._quit()  # as the tray's Quit: the icon goes, the lock is freed
            return
        self.client.request("status", self._on_status, once=True)
        self.root.after(STATUS_MS, self._poll_status)

    def _being_removed(self) -> bool:
        """Whether the snap is being removed, so this copy should quit.

        snapd doesn't stop a snap's apps when it removes the snap (unless
        ``snap remove --terminate``), so without this the tray icon would
        stay, with an empty menu, after the files are gone. The remove hook
        runs as root and can't reach this process, so it leaves a note
        (snap/hooks/remove) and waits longer than ``STATUS_MS`` before snapd
        goes on. A note older than this window is stale: the daemon deletes
        those at startup, but a window may start first.
        """
        if self.removing_file is None:
            return False
        try:
            return self.removing_file.stat().st_mtime > self.started
        except OSError:  # usually: there's no note
            return False

    def _poll_usage(self) -> None:
        if self.status is not None and not self.hidden:
            self.client.request("usage", self._on_usage, once=True)
        self.root.after(USAGE_MS, self._poll_usage)

    def _on_status(self, reply: Reply) -> None:
        if reply.ok:
            self.status, self.error = reply.result, None
        elif reply.unreachable:
            if self.status is not None:  # it just stopped answering: why?
                self._check()
            self.status, self.usage, self.error = None, None, reply.error
        self._render()

    def _on_usage(self, reply: Reply) -> None:
        if reply.ok:
            self.usage = reply.result
            self._render()

    # --- Showing, hiding, and the tray ------------------------------------------------

    def show(self) -> None:
        """Bring the window back (from the tray, or for a second launch)."""
        self.hidden = False
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self._open_helper_once()

    def close(self) -> None:
        """The close button: hide while the tray icon can bring it back, else quit."""
        if self.tray_ready:
            self.hidden = True
            self.root.withdraw()
        else:
            self._quit()

    def _quit(self) -> None:
        self.closed = True
        self.root.destroy()  # ends the main loop

    def _on_tray(self, event: str) -> None:
        if event == READY:
            self.tray_ready = True
            self._default_start_at_login()
        elif event in (NO_TRAY, NOT_ALLOWED):
            self.tray_ready = False
            if self.hidden:  # nothing would ever show it
                self._quit()
        elif event == menu.TOGGLE:
            self.switch.invoke()  # as if pressed: does nothing while disabled
        elif event == menu.OPEN:
            self.show()
        elif event == menu.QUIT:
            self._quit()

    # --- Missing connections ---------------------------------------------------------

    def _check(self) -> None:
        """Check the daemon's plugs on a thread; ``_dispatch`` gets the answer."""
        if self._checking:
            return
        self._checking = True

        def run() -> None:
            try:
                result = self.check_connections()
            except Exception:  # unknown: show nothing rather than a wrong fix
                result = None
            self._checked.put(result)

        threading.Thread(target=run, name="check-connections", daemon=True).start()

    def _on_connections(self, missing: tuple[str, ...] | None) -> None:
        self._checking = False
        self.missing = missing or ()
        if self.helper is not None and self.helper.winfo_exists():
            if self.missing:
                self.helper.set_missing(self.missing)
            else:
                self.helper.close()  # all connected; the status poll reconnects
        self._open_helper_once()
        self._render()

    def _open_helper_once(self) -> None:
        """Open the helper on its own: once per launch, and only while shown."""
        if self.missing and not self.hidden and not self._helper_opened:
            self.open_helper()

    def open_helper(self) -> None:
        self._helper_opened = True
        if self.helper is not None and self.helper.winfo_exists():
            self.helper.lift()
            return
        self.helper = ConnectionsDialog(
            self.root,
            paths.instance_name(),
            self.missing,
            may_change=self.may_change,
            on_check=self._check,
        )

    # --- Starting at login ---------------------------------------------------------

    def _default_start_at_login(self) -> None:
        """On by default, once there's a tray to start in (see autostart.py)."""
        if self.autostart_file is None:
            return
        try:
            autostart.set_default(self.autostart_file)
        except OSError:
            return  # not worth a message; the menu item shows it's off
        self.start_at_login.set(autostart.enabled(self.autostart_file))

    def _set_start_at_login(self) -> None:
        assert self.autostart_file is not None
        try:
            autostart.set_enabled(self.autostart_file, self.start_at_login.get())
        except OSError as e:
            messagebox.showerror(
                "Traffic Cop",
                _("Can't change starting at login: {error}").format(error=e),
                parent=self.root,
            )
            self.start_at_login.set(autostart.enabled(self.autostart_file))

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

    # --- Changing apps ---------------------------------------------------------------

    def _show_menu(self) -> None:
        x = self.more.winfo_rootx()
        y = self.more.winfo_rooty() + self.more.winfo_height()
        self.menu.tk_popup(x, y)

    def _add_app(self) -> None:
        self._open_dialog(editor.AppDraft(), None)

    def _edit_app(self, name: str) -> None:
        def got(reply: Reply) -> None:
            entry = (reply.result or {}).get("apps", {}).get(name) if reply.ok else None
            if entry is None:
                self._report(reply)
                return
            self._open_dialog(editor.AppDraft.from_entry(name, entry), name)

        self.client.request("get-config", got)

    def _open_dialog(self, draft: editor.AppDraft, old_name: str | None) -> None:
        if self.dialog is not None and self.dialog.winfo_exists():
            self.dialog.lift()
            return

        def save(draft: editor.AppDraft) -> None:
            try:
                name, entry = draft.to_entry()
            except editor.EditError as e:
                dialog.show_error(str(e))
                return
            self._change(
                lambda cfg: editor.with_app(cfg, old_name, name, entry), dialog
            )

        def remove() -> None:
            if messagebox.askyesno(
                _("Remove an app"),
                _("Remove {name}? Its limits will stop applying.").format(
                    name=old_name
                ),
                parent=dialog,
            ):
                self._change(
                    lambda cfg: editor.without_app(cfg, old_name or ""), dialog
                )

        dialog = AppDialog(
            self.root,
            draft,
            new=old_name is None,
            editable=self.may_change,
            on_save=save,
            on_remove=remove if old_name is not None else None,
        )
        self.dialog = dialog

    def _restore_defaults(self) -> None:
        if not messagebox.askyesno(
            _("Restore the default apps"),
            _("Replace all your apps and limits with the defaults?"),
            parent=self.root,
        ):
            return
        self.client.request(
            "set-config", self._changed(None), {"yaml": config.default_text()}
        )

    def _change(
        self, edit: Callable[[dict[str, Any]], dict[str, Any]], dialog: AppDialog
    ) -> None:
        """Fetch the config, apply ``edit``, send it back; report to ``dialog``."""

        def got(reply: Reply) -> None:
            if not reply.ok:
                dialog.show_error(self._message(reply))
                return
            try:
                new = edit(reply.result)
            except editor.EditError as e:
                dialog.show_error(str(e))
                return
            self.client.request("set-config", self._changed(dialog), {"config": new})

        self.client.request("get-config", got)

    def _changed(self, dialog: AppDialog | None) -> Callable[[Reply], None]:
        def done(reply: Reply) -> None:
            if reply.ok:
                self.status = reply.result
                self._render()
                if dialog is not None and dialog.winfo_exists():
                    dialog.close()
            elif dialog is not None and dialog.winfo_exists():
                dialog.show_error(self._message(reply))
            else:
                self._report(reply)

        return done

    def _message(self, reply: Reply) -> str:
        if reply.permission_denied:
            return _("Only administrators can change apps.")
        if reply.unreachable:
            return _("Traffic Cop isn't running")
        if isinstance(reply.error, protocol.ProtocolError):
            return reply.error.message  # e.g. a config error, from the daemon
        return str(reply.error)

    def _report(self, reply: Reply) -> None:
        if not reply.ok:
            messagebox.showerror("Traffic Cop", self._message(reply), parent=self.root)

    # --- Drawing -----------------------------------------------------------------

    def _render(self) -> None:
        if self.tray is not None:
            self.tray.show(menu.tray_state(self.status, self.may_change))
        view = build_view(self.status, self.usage, self.error, self.missing)
        self.headline.configure(text=view.headline)
        self.detail.configure(text=view.detail)
        if view.paused is not None:
            self.active.set(not view.paused)
        if view.connected:
            text = _("Limits on") if self.active.get() else _("Limits off")
        else:
            # Unknown without the daemon: claim neither on nor off.
            self.active.set(False)
            text = _("Limits")
        self.switch.configure(text=text)
        self.switch.state(
            ["!disabled"] if view.connected and not self.busy else ["disabled"]
        )
        if self.missing:
            self.switch.grid_remove()
            self.connect.grid()
        else:
            self.connect.grid_remove()
            self.switch.grid()
        for button in (self.add, self.more):
            button.state(["!disabled"] if view.connected else ["disabled"])
        self._render_rows(view)

    def _render_rows(self, view: View) -> None:
        names = [row.name for row in view.rows]
        if names != list(self._rows):
            for widgets in self._rows.values():
                widgets.destroy()
            self._rows = {name: self._make_row(i, name) for i, name in enumerate(names)}
        if view.connected and not view.rows:
            self.empty.grid(row=0, column=0, columnspan=C_TOTAL + 1, sticky="w")
        else:
            self.empty.grid_remove()
        for row in view.rows:
            self._fill_row(self._rows[row.name], row)

    def _make_row(self, index: int, app: str) -> _RowWidgets:
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
        name = ttk.Label(self.table, font=self._fonts[0], cursor="hand2")
        name.grid(row=r + 1, column=C_NAME, rowspan=2, sticky="w", padx=(0, 16))
        for w in (dot, name):  # click an app to change it
            w.bind("<Button-1>", lambda e, app=app: self._edit_app(app))
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
