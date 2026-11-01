"""traffic-cop: the Traffic Cop window and its tray icon.

Shows what traffic-copd is doing and each app's live rates, with a switch
to turn the limits on and off. Needs no privileges; it only talks to the
daemon's socket.

One process owns both the window and the tray icon (indicator/tray.py), as
in most apps with a tray icon: closing the window hides it while the icon
is shown, and only one copy runs per session (common/instance.py). A second
launch asks the running copy to show its window, with SIGUSR1, and exits.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from trafficcop import __version__
from trafficcop.common import instance, paths
from trafficcop.common.i18n import _

if TYPE_CHECKING:
    from trafficcop.gui.window import MainWindow


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="traffic-cop", description=_("The Traffic Cop window.")
    )
    parser.add_argument(
        "--socket",
        type=Path,
        help=_("daemon socket (default: {path})").format(path=paths.get().socket),
    )
    parser.add_argument(
        "--hidden",
        action="store_true",
        help=_("start with only the tray icon, not the window"),
    )
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    # Set before the lock is taken, since a second copy may signal as soon
    # as it is: SIGUSR1's default action would end this process. A request
    # before the window exists is dropped; the window is about to appear.
    window: MainWindow | None = None

    def show_requested(signum: int, frame: object) -> None:
        if window is not None and not window.closed:
            window.root.after_idle(window.show)

    signal.signal(signal.SIGUSR1, show_requested)

    try:
        _lock = instance.take_lock()  # held until this process exits
    except instance.AlreadyRunning as running:
        if running.pid is not None:  # None: it's starting, and will show itself
            try:
                os.kill(running.pid, signal.SIGUSR1)
            except OSError as e:
                message = _("already running, but can't reach it: {error}")
                print(f"traffic-cop: {message.format(error=e)}", file=sys.stderr)
                return 1
        return 0

    try:
        import tkinter as tk
    except ImportError:
        print(
            "traffic-cop: "
            + _("the window needs tkinter (sudo apt install python3-tk)"),
            file=sys.stderr,
        )
        return 1
    from trafficcop.gui import fonts
    from trafficcop.gui.client import Client
    from trafficcop.gui.window import MainWindow
    from trafficcop.indicator import menu
    from trafficcop.indicator.tray import Tray

    fonts.register()  # before Tk starts, so it can see the snap's Andika

    try:
        root = tk.Tk(className="traffic-cop")
    except tk.TclError as e:  # e.g. no display
        message = _("can't open a window: {error}").format(error=e)
        print(f"traffic-cop: {message}", file=sys.stderr)
        return 1

    client = Client(args.socket)
    tray = Tray(menu.icon_dir())
    try:
        window = MainWindow(
            root,
            client,
            tray,
            hidden=args.hidden,
            autostart_file=paths.autostart_file(),
        )
        root.mainloop()
    finally:
        tray.stop()
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
