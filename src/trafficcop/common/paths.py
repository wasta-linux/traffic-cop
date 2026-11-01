"""Where Traffic Cop keeps its files.

This is the only module that knows. The daemon and its clients both call
``paths.get()``::

              snap                           development (not in a snap)
    config    $SNAP_DATA/traffic-cop.yaml    /var/lib/traffic-cop-dev/traffic-cop.yaml
    socket    $SNAP_COMMON/traffic-cop.sock  /run/traffic-cop-dev/traffic-cop.sock
    state     $SNAP_DATA/state.json          /var/lib/traffic-cop-dev/state.json
    removing  $SNAP_COMMON/removing          /run/traffic-cop-dev/removing

The state file holds what the daemon remembers between runs (whether it's
paused); see ``supervisor.load_state``. The ``removing`` file is a note from
the snap's remove hook (snap/hooks/remove) asking running windows to quit:
the window looks for it on each status poll, and the daemon deletes a stale
one when it starts.

The window's login file (``autostart_file``) is per user, so it's apart
from ``get()``, which the root daemon calls too.

* The snap's config and state are in ``$SNAP_DATA``, which is per revision: snapd copies
  it forward on refresh and restores the old copy on revert, so an older
  revision never sees a config written by a newer one.
* The snap's socket is in ``$SNAP_COMMON``, shared by all revisions, so its
  path doesn't change across refreshes.
* Outside a snap is only for development, e.g. ``sudo .venv/bin/traffic-copd``.
  The ``-dev`` directories keep it clear of the 1.x deb's
  ``/etc/traffic-cop.yaml`` and ``traffic-cop.service`` on the same machine.

The snap is detected from ``$SNAP``, which snapd sets for every snap app, and
the paths come from snapd's variables rather than a hardcoded snap name. The
daemon creates missing directories.

The default config is package data; see ``config.default_text()``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

CONFIG_NAME = "traffic-cop.yaml"
SOCKET_NAME = "traffic-cop.sock"
STATE_NAME = "state.json"
# Must match the file snap/hooks/remove creates.
REMOVING_NAME = "removing"

DEV_CONFIG = Path("/var/lib/traffic-cop-dev") / CONFIG_NAME
DEV_SOCKET = Path("/run/traffic-cop-dev") / SOCKET_NAME
DEV_STATE = DEV_CONFIG.parent / STATE_NAME
DEV_REMOVING = DEV_SOCKET.parent / REMOVING_NAME
# Must match the gui app's ``autostart:`` in snap/snapcraft.yaml.
AUTOSTART_NAME = "traffic-cop.desktop"


@dataclass(frozen=True)
class Paths:
    config: Path
    socket: Path
    state: Path
    removing: Path  # beside the socket: root writes it, users can read it
    snap: bool  # False: a development run from a source checkout


def get(env: Mapping[str, str] | None = None) -> Paths:
    """Return the paths for this run (default: from ``os.environ``)."""
    env = os.environ if env is None else env
    if env.get("SNAP"):
        try:
            data, common = Path(env["SNAP_DATA"]), Path(env["SNAP_COMMON"])
        except KeyError as e:
            raise RuntimeError(
                f"running in a snap, but ${e.args[0]} isn't set"
            ) from None
        return Paths(
            config=data / CONFIG_NAME,
            socket=common / SOCKET_NAME,
            state=data / STATE_NAME,
            removing=common / REMOVING_NAME,
            snap=True,
        )
    return Paths(
        config=DEV_CONFIG,
        socket=DEV_SOCKET,
        state=DEV_STATE,
        removing=DEV_REMOVING,
        snap=False,
    )


def autostart_file(env: Mapping[str, str] | None = None) -> Path | None:
    """The desktop file that starts the window at login; None outside a snap.

    snapd starts snap apps at login itself (``snap userd --autostart``, run
    from /etc/xdg/autostart): it looks for
    ``~/snap/*/current/.config/autostart/*.desktop`` and runs the app whose
    ``autostart:`` key names the file (snapd's usersession/autostart). In
    the snap that directory is ``$SNAP_USER_DATA/.config/autostart``, which
    goes away with the snap, so no login entry is left behind. A development
    run has none, rather than writing to the real ~/.config/autostart.
    """
    env = os.environ if env is None else env
    user_data = env.get("SNAP_USER_DATA")
    if not env.get("SNAP") or not user_data:
        return None
    return Path(user_data) / ".config" / "autostart" / AUTOSTART_NAME
