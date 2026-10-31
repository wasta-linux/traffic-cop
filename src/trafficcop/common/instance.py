"""Keep Traffic Cop's window to one copy per user session.

The window and its tray icon are one process, and closing the window only
hides it while the icon is shown, so a second launch (from the menu or a
terminal) must not open a second window: it asks the running copy to show
its own and exits.

The first copy holds an flock on a file in $XDG_RUNTIME_DIR, with its PID
written inside, and the second sends that PID SIGUSR1 (see
gui/__main__.py). flock is released by the kernel when the process ends,
even in a crash, so the lock never goes stale. In a snap, $XDG_RUNTIME_DIR
is the snap's own (/run/user/<uid>/snap.<instance>), shared by all its apps.

Why a signal rather than a socket or a D-Bus name: it needs no new snap
plug. snapd's AppArmor template lets a snap's processes signal each other
(``signal peer=snap.@{SNAP_INSTANCE_NAME}.*``, seen in the gui app's
profile); a socket would need ``network-bind``, a D-Bus name a ``dbus`` slot.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import TextIO

LOCK_NAME = "traffic-cop.lock"


class AlreadyRunning(Exception):
    """Another copy holds the lock. ``pid`` is None if it hasn't written it yet."""

    def __init__(self, pid: int | None):
        super().__init__(f"already running (pid {pid})")
        self.pid = pid


def take_lock(name: str = LOCK_NAME) -> TextIO | None:
    """Become the only copy, or raise AlreadyRunning with the holder's PID.

    Returns the open lock file, which must stay open for as long as the
    process runs (None if there's no runtime directory: then any number of
    copies may run).
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        return None
    # "a+", not "w": the file mustn't be emptied before we hold the lock,
    # or a copy that loses the race would erase the holder's PID.
    lock = open(Path(runtime) / name, "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.seek(0)
        text = lock.read().strip()
        lock.close()
        raise AlreadyRunning(int(text) if text.isdigit() else None) from None
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()
    return lock
