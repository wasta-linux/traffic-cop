"""traffic-cop: the Traffic Cop window.

Shows what traffic-copd is doing and each app's live rates, with a switch
to turn the limits on and off. Needs no privileges; it only talks to the
daemon's socket.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from trafficcop import __version__
from trafficcop.common import paths
from trafficcop.common.i18n import _


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="traffic-cop", description=_("The Traffic Cop window.")
    )
    parser.add_argument(
        "--socket",
        type=Path,
        help=_("daemon socket (default: {path})").format(path=paths.get().socket),
    )
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

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

    fonts.register()  # before Tk starts, so it can see the snap's Andika

    try:
        root = tk.Tk(className="traffic-cop")
    except tk.TclError as e:  # e.g. no display
        message = _("can't open a window: {error}").format(error=e)
        print(f"traffic-cop: {message}", file=sys.stderr)
        return 1
    client = Client(args.socket)
    try:
        MainWindow(root, client)
        root.mainloop()
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
