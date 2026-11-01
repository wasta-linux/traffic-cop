"""What the tray icon shows: its icon, tooltip and menu, from the daemon's status.

Pure: no tkinter or jeepney, so it's tested without a display or a bus.
The window turns each status reply into a ``TrayState`` and hands it to
the tray thread (``tray.Tray.show``), which shows it with ``sni.py``.

The icon shows the state: a monochrome app icon, bold while the limits
are on, faint while paused, faint with an X when the daemon can't be
reached (icons/, made by tools/tray-icons.py).

``Icon`` and ``MenuItem`` live here rather than in sni.py, which imports
jeepney, so the window can build a state without it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import resources
from typing import Any

from trafficcop.common.i18n import _


@dataclass(frozen=True)
class MenuItem:
    key: str  # passed to on_click; unique in the menu (it fixes the item's id)
    label: str = ""
    enabled: bool = True
    visible: bool = True
    separator: bool = False


@dataclass(frozen=True)
class Icon:
    """What the panel shows: a named icon file, with a drawn fallback.

    ``name`` is a file in the ``icon_dir`` given to StatusNotifier, without
    its extension (``<name>.svg``). Panels look it up there through the
    ``IconThemePath`` property, as Chromium and Electron do in snaps: the
    panel runs outside the snap, but can read the snap's files under
    /snap. If a panel can't load it, it uses ``IconPixmap``: a circle in
    ``colour``.
    """

    name: str
    colour: str  # "#rrggbb"


# The fallback colours are the window's (gui/window.py, which can't be
# imported without tkinter).
ON = Icon("wasta-traffic-cop-on-symbolic", "#3BA55C")
PAUSED = Icon("wasta-traffic-cop-paused-symbolic", "#A3A8B4")
OFFLINE = Icon("wasta-traffic-cop-offline-symbolic", "#D64545")

# Menu item keys, which come back as clicks (tray.Tray.events).
TOGGLE, OPEN, QUIT = "toggle", "open", "quit"


@dataclass(frozen=True)
class TrayState:
    icon: Icon
    tooltip: str
    items: tuple[MenuItem, ...]


def tray_state(status: dict[str, Any] | None, may_change: bool) -> TrayState:
    """The tray's look for a daemon status (None: the daemon can't be reached).

    ``may_change``: the user may turn the limits on and off (an admin;
    ``editor.may_change``). The daemon has the last word either way.
    """
    if status is None:
        icon, tooltip = OFFLINE, _("Traffic Cop isn't running")
    elif status.get("paused"):
        icon, tooltip = PAUSED, _("Limits off")
    else:
        icon, tooltip = ON, _("Limits on")
    # Always the same items; the switch is hidden, not removed, while the
    # daemon can't be reached (see sni.py on item ids).
    paused = status is not None and bool(status.get("paused"))
    items = (
        MenuItem(
            TOGGLE,
            _("Turn limits on") if paused else _("Turn limits off"),
            enabled=may_change,
            visible=status is not None,
        ),
        MenuItem(OPEN, _("Open Traffic Cop")),
        MenuItem("separator", separator=True),
        MenuItem(QUIT, _("Quit")),
    )
    return TrayState(icon, tooltip, items)


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
