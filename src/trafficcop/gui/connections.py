"""The helper shown when the daemon's interfaces aren't connected.

    Traffic Cop needs your permission
    Its background service needs these connections to limit apps' traffic.
    To connect them, run this command in a terminal (it asks for your password):
    [sudo snap connect wasta-traffic-cop:firewall-control && …] [Copy]
    (Ask an administrator to run it.)          <- only for other users
    firewall-control  mark each app's traffic so it can be limited (…)
                      More about it
    …
                                              [Check again] [Close]

The main window decides when it opens (see ``MainWindow._on_connections``)
and does the checking: "Check again" calls ``on_check``, and the window
then calls ``set_missing`` or closes the helper. Not modal, so the main
window stays usable while the user is in a terminal.

The copied command stays on the clipboard only while this process runs
(X11 asks the owner for it when pasting); the window and its tray icon
keep running, so that's fine.
"""

from __future__ import annotations

import tkinter as tk
import webbrowser
from collections.abc import Callable, Sequence
from tkinter import font, ttk

from trafficcop.common import connections
from trafficcop.common.i18n import _

LINK = "#3B7DD8"  # window.BLUE_DARK; reads on the light grey
WRAP = 460  # pixels


def purposes() -> dict[str, str]:
    """What each plug lets the daemon do (from the store request; see CLAUDE.md)."""
    return {
        "network-control": _("set the speed limits (tc) and see where traffic goes"),
        "firewall-control": _(
            "mark each app's traffic so it can be limited (nftables); it adds"
            " only its own table, and removes it when paused or stopped"
        ),
        "system-observe": _("see which app each program belongs to, for all users"),
    }


class ConnectionsDialog(tk.Toplevel):
    def __init__(
        self,
        master: tk.Misc,
        instance: str,
        missing: Sequence[str],
        *,
        may_change: bool,
        on_check: Callable[[], None],
    ):
        super().__init__(master)
        self.title("Traffic Cop")
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)
        # As in dialog.AppDialog: the window's styles define the colours.
        self.configure(background=ttk.Style(self).lookup("Dialog.TFrame", "background"))
        self._instance = instance
        bold = font.nametofont("TkDefaultFont").copy()
        bold.configure(weight="bold")
        big = bold.copy()
        big.configure(size=big.cget("size") + 2)
        link = font.nametofont("TkDefaultFont").copy()
        link.configure(underline=True)
        self._fonts = (bold, big, link)  # keep references: Tk forgets the others

        body = ttk.Frame(self, padding=16, style="Dialog.TFrame")
        body.grid(sticky="nsew")
        body.columnconfigure(0, weight=1)

        ttk.Label(
            body,
            text=_("Traffic Cop needs your permission"),
            font=big,
            style="Dialog.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            body,
            text=_(
                "Its background service needs these connections to limit apps'"
                " traffic. To connect them, run this command in a terminal (it"
                " asks for your password):"
            ),
            wraplength=WRAP,
            style="Dialog.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 8))

        self.command = tk.StringVar()
        # Read-only rather than disabled, so the text can still be selected.
        ttk.Entry(
            body,
            textvariable=self.command,
            state="readonly",
            width=60,
            style="Dialog.TEntry",
        ).grid(row=2, column=0, sticky="ew")
        ttk.Button(
            body, text=_("Copy"), command=self.copy, style="Primary.TButton"
        ).grid(row=2, column=1, padx=(8, 0))
        row = 3
        if not may_change:
            ttk.Label(
                body,
                text=_("Ask an administrator to run it."),
                style="DialogDim.TLabel",
            ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(6, 0))
            row += 1

        self._plugs = ttk.Frame(body, style="Dialog.TFrame")
        self._plugs.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(12, 0))

        buttons = ttk.Frame(body, style="Dialog.TFrame")
        buttons.grid(row=row + 1, column=0, columnspan=2, sticky="e", pady=(16, 0))
        self.check = ttk.Button(
            buttons, text=_("Check again"), command=on_check, style="Secondary.TButton"
        )
        self.check.grid(row=0, column=0)
        ttk.Button(
            buttons, text=_("Close"), command=self.close, style="Secondary.TButton"
        ).grid(row=0, column=1, padx=(8, 0))
        self.bind("<Escape>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)

        self.set_missing(missing)

    def set_missing(self, missing: Sequence[str]) -> None:
        """Show the command and notes for these plugs."""
        self.command.set(connections.fix_command(self._instance, missing))
        for child in self._plugs.winfo_children():
            child.destroy()
        bold, _big, link = self._fonts
        about = purposes()
        for i, plug in enumerate(missing):
            ttk.Label(self._plugs, text=plug, font=bold, style="Dialog.TLabel").grid(
                row=2 * i, column=0, sticky="nw", padx=(0, 12), pady=(6, 0)
            )
            ttk.Label(
                self._plugs,
                text=about.get(plug, ""),
                wraplength=WRAP - 40,
                style="Dialog.TLabel",
            ).grid(row=2 * i, column=1, sticky="w", pady=(6, 0))
            more = ttk.Label(
                self._plugs,
                text=_("More about it"),
                font=link,
                foreground=LINK,
                cursor="hand2",
                style="Dialog.TLabel",
            )
            more.grid(row=2 * i + 1, column=1, sticky="w")
            url = connections.DOCS.get(plug)
            if url:
                more.bind("<Button-1>", lambda e, url=url: webbrowser.open(url))

    def copy(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.command.get())

    def close(self) -> None:
        self.destroy()
