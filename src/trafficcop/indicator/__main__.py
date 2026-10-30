"""traffic-cop-indicator: Traffic Cop's tray icon.

A separate process from the window, started at login: it lives all
session, and D-Bus and Tk event loops don't mix. Its menu turns the limits
on and off, opens the window, or quits the icon (never the daemon).

The icon shows the state: a monochrome app icon, bold while the limits
are on, faint while paused, faint with an X when the daemon can't be
reached (icons/, made by tools/tray-icons.py). Like the window, it needs no
privileges and only talks to the daemon's socket.
"""

from __future__ import annotations

import argparse
import fcntl
import logging
import os
import signal
import sys
from collections.abc import Sequence
from importlib import resources
from pathlib import Path
from typing import Any

from trafficcop import __version__
from trafficcop.common import launch, paths, protocol
from trafficcop.common.i18n import _
from trafficcop.gui.editor import may_change

log = logging.getLogger("traffic-cop-indicator")

POLL_SECONDS = 3.0
# Icon names (files in icons/) and fallback colours, for panels that can't
# load the files. The colours are the window's (gui/window.py, which can't
# be imported without tkinter).
ON = ("wasta-traffic-cop-on-symbolic", "#3BA55C")
PAUSED = ("wasta-traffic-cop-paused-symbolic", "#A3A8B4")
OFFLINE = ("wasta-traffic-cop-offline-symbolic", "#D64545")
LOCK_NAME = "traffic-cop-indicator.lock"


def take_lock():
    """Lock out a second copy, which would add a second icon to the tray.

    The window starts the indicator every time it opens, and so will
    login, so the indicator itself makes sure only one runs. flock is
    released by the kernel when the process ends, even in a crash, so the
    lock never goes stale. In a snap, $XDG_RUNTIME_DIR is shared by all of
    the snap's apps (/run/user/<uid>/snap.<instance>).

    Returns the open lock file, which must stay open (None if there's no
    runtime directory); raises BlockingIOError if another copy holds it.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        return None
    lock = open(Path(runtime) / LOCK_NAME, "w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return lock


def icon_dir() -> str:
    """The icons' directory, as the panel (outside the snap) sees it.

    Inside a snap, the package is under /snap/<snap name>/..., but on the
    host the snap is mounted at /snap/<instance name>/.... They differ for a
    parallel install (e.g. wasta-traffic-cop_test), where the snap name's
    path on the host is a *different* install, so the name is swapped.

    Only the name: what follows may be the revision ($SNAP, /snap/…/x2) or
    the "current" link, which is how Python finds its packages in the snap
    (seen in the test instance; matching on $SNAP missed it), and both exist
    on the host.
    """
    path = str(resources.files("trafficcop.indicator") / "icons")
    name = os.environ.get("SNAP_NAME")
    instance = os.environ.get("SNAP_INSTANCE_NAME")
    prefix = f"/snap/{name}/"
    if name and instance and path.startswith(prefix):
        path = f"/snap/{instance}/" + path[len(prefix) :]
    return path


class Indicator:
    def __init__(self, socket_path: Path):
        from trafficcop.indicator import sni

        self.sni = sni
        self.socket_path = socket_path
        self.may_change = may_change()
        self.status: dict[str, Any] | None = None
        self.item = sni.StatusNotifier(
            "wasta-traffic-cop",
            "Traffic Cop",
            on_click=self.on_click,
            icon_dir=icon_dir(),
        )

    def run(self) -> None:
        self.refresh()  # the first icon and menu, before the panel reads them
        self.item.register()
        while not self.item.stopped:
            self.item.serve(POLL_SECONDS)
            self.refresh()

    def call(self, cmd: str) -> None:
        """Run a daemon command that returns the status (status/pause/resume)."""
        try:
            self.status = protocol.call(cmd, path=self.socket_path, timeout=2.0)
        except OSError as e:
            self.status = None
            log.debug("daemon unreachable: %s", e)
        except protocol.ProtocolError as e:
            # e.g. permission denied; the menu item is disabled for
            # non-admins, but the daemon has the last word.
            log.warning("%s failed: %s", cmd, e.message)

    def refresh(self) -> None:
        self.call("status")
        self.show()

    def show(self) -> None:
        sni = self.sni
        if self.status is None:
            icon, tooltip = OFFLINE, _("Traffic Cop isn't running")
        elif self.status.get("paused"):
            icon, tooltip = PAUSED, _("Limits off")
        else:
            icon, tooltip = ON, _("Limits on")
        # Always the same items; the switch is hidden, not removed, while
        # the daemon can't be reached (see sni.py on item ids).
        paused = self.status is not None and bool(self.status.get("paused"))
        items = [
            sni.MenuItem(
                "toggle",
                _("Turn limits on") if paused else _("Turn limits off"),
                enabled=self.may_change,
                visible=self.status is not None,
            ),
            sni.MenuItem("open", _("Open Traffic Cop")),
            sni.MenuItem("separator", separator=True),
            sni.MenuItem("quit", _("Quit")),
        ]
        self.item.set_icon(sni.Icon(*icon))
        self.item.set_tooltip(tooltip)
        self.item.set_items(items)

    def on_click(self, key: str) -> None:
        if key == "toggle" and self.status is not None:
            self.call("resume" if self.status.get("paused") else "pause")
            self.show()
        elif key == "open":
            launch.start("traffic-cop", self.socket_path)
        elif key == "quit":
            self.item.stopped = True


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="traffic-cop-indicator", description=_("The Traffic Cop tray icon.")
    )
    parser.add_argument(
        "--socket",
        type=Path,
        help=_("daemon socket (default: {path})").format(path=paths.get().socket),
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="traffic-cop-indicator: %(message)s",
    )
    try:
        _lock = take_lock()  # held until this process exits
    except BlockingIOError:
        log.debug("already running")
        return 0

    # Windows it opens are never waited for; let the kernel reap them.
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)

    try:
        from trafficcop.indicator import sni
    except ImportError:
        log.error("the tray icon needs jeepney (sudo apt install python3-jeepney)")
        return 1
    try:
        indicator = Indicator(args.socket or paths.get().socket)
    except (OSError, KeyError) as e:  # KeyError: no session bus address at all
        log.error("can't connect to the D-Bus session bus: %s", e)
        return 1
    try:
        indicator.run()
    except sni.NoTray:
        # Plain GNOME has no tray; nothing to do, and not an error.
        log.info("no tray on this desktop (no StatusNotifierWatcher); exiting")
    except sni.NotAllowed as e:
        log.error("the session bus refused the tray icon: %s", e)
        instance = os.environ.get("SNAP_INSTANCE_NAME")
        if instance:
            log.error(
                "connect the unity7 interface: sudo snap connect %s:unity7", instance
            )
        return 1
    except KeyboardInterrupt:
        pass
    finally:
        indicator.item.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
