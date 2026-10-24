"""Where Traffic Cop keeps its files.

This is the only module that knows. The daemon and its clients both call
``paths.get()``::

              snap                           development (not in a snap)
    config    $SNAP_DATA/traffic-cop.yaml    /var/lib/traffic-cop-dev/traffic-cop.yaml
    socket    $SNAP_COMMON/traffic-cop.sock  /run/traffic-cop-dev/traffic-cop.sock

* The snap's config is in ``$SNAP_DATA``, which is per revision: snapd copies
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

DEV_CONFIG = Path("/var/lib/traffic-cop-dev") / CONFIG_NAME
DEV_SOCKET = Path("/run/traffic-cop-dev") / SOCKET_NAME


@dataclass(frozen=True)
class Paths:
    config: Path
    socket: Path
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
        return Paths(config=data / CONFIG_NAME, socket=common / SOCKET_NAME, snap=True)
    return Paths(config=DEV_CONFIG, socket=DEV_SOCKET, snap=False)
