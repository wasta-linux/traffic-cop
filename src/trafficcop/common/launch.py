"""Start Traffic Cop's other programs: the window and the tray icon.

They're installed beside the interpreter: in the snap all are in $SNAP/bin,
in a venv in .venv/bin, which may not be on PATH. (Not beside sys.argv[0],
which is the module's file under ``python -m``.) The interpreter's path
isn't resolved: in a venv it's a symlink out of it.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def start(program: str, socket_path: Path | None = None) -> None:
    """Start a program in the background, talking to the same daemon socket."""
    beside = Path(sys.executable).with_name(program)
    found = str(beside) if beside.exists() else shutil.which(program)
    if found is None:
        log.error("can't find the %s program", program)
        return
    command = [found]
    if socket_path is not None:
        command += ["--socket", str(socket_path)]
    subprocess.Popen(command, start_new_session=True)
