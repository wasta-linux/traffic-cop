"""Which of the daemon's interfaces aren't connected, and the command to connect them.

The daemon's privileged plugs (``DAEMON_PLUGS``) don't connect on their
own until the store grants auto-connection, and a strict snap can't
connect its own plugs. Without ``firewall-control`` the daemon dies at
startup (libnftables exits the process when it can't open its netlink
socket, so Python can't catch it), and systemd gives up after a few
restarts. So the window and the CLI check for themselves and show the
command that fixes it (``fix_command``).

The check is ``snapctl is-connected --list`` (read in snapd 2.77.1,
overlord/hookstate/ctlcmd/is_connected.go):

- any user may run it (it's in ``nonRootAllowed``), and snapd's AppArmor
  template allows ``/usr/bin/snapctl`` and its socket, so no plug is needed;
- it lists the connected plugs and slots of the whole snap, one name per
  line, so the ``gui`` app sees the daemon's plugs too.

To compare by hand: ``snap connections wasta-traffic-cop`` lists the same
plugs, connected or not.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from trafficcop.common import paths

# The daemon's plugs that don't connect automatically. Must match the
# daemon's plugs in snap/snapcraft.yaml, apart from network-bind (which does).
DAEMON_PLUGS = ("network-control", "firewall-control", "system-observe")
DOCS = {plug: f"https://snapcraft.io/docs/{plug}-interface" for plug in DAEMON_PLUGS}
TIMEOUT = 5.0  # seconds; snapctl asks snapd, which answers quickly when it's up


def missing(
    env: Mapping[str, str] | None = None,
    run: Callable[..., Any] = subprocess.run,
) -> tuple[str, ...] | None:
    """The ``DAEMON_PLUGS`` that aren't connected; None if that can't be known.

    None outside the snap, or if snapctl fails: the callers then show
    nothing, rather than a fix for a problem that may not be there.
    """
    env = os.environ if env is None else env
    if not paths.get(env).snap:
        return None
    try:
        done = run(
            ["snapctl", "is-connected", "--list"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):  # not found, or timed out
        return None
    if done.returncode != 0:
        return None
    connected = set(done.stdout.split())
    return tuple(plug for plug in DAEMON_PLUGS if plug not in connected)


def fix_command(instance: str, plugs: Iterable[str]) -> str:
    """One line that connects ``plugs`` and restarts the daemon.

    ``snap connect`` takes one plug at a time; sudo remembers the password
    for the rest of the line. ``restart`` also starts a daemon that systemd
    has given up on. A command, so not translated.
    """
    steps = [f"sudo snap connect {instance}:{plug}" for plug in plugs]
    steps.append(f"sudo snap restart {instance}.daemon")
    return " && ".join(steps)
