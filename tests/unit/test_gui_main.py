import os
import signal
from pathlib import Path

import pytest

from trafficcop.common import instance
from trafficcop.gui import __main__ as gui_main


def test_second_launch_hands_over_and_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(signal, "signal", lambda signum, handler: None)
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append((pid, sig)))

    def no_tk(*args, **kwargs):
        raise AssertionError("the second copy mustn't open a window")

    monkeypatch.setattr("tkinter.Tk", no_tk, raising=False)
    first = instance.take_lock()
    assert first is not None
    try:
        assert gui_main.main([]) == 0
    finally:
        first.close()
    assert killed == [(os.getpid(), signal.SIGUSR1)]
