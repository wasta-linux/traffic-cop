"""Smoke test for the tkinter window; skipped without tkinter or a display."""

import os
import time
from pathlib import Path
from typing import Any

import pytest

from tests.unit.test_gui_model import STATUS, USAGE

tk = pytest.importorskip("tkinter")
pytest.importorskip("tkinter.ttk")
if not os.environ.get("DISPLAY"):
    pytest.skip("no display", allow_module_level=True)

from trafficcop.gui.client import Client  # noqa: E402
from trafficcop.gui.window import MainWindow  # noqa: E402


def pump(root: Any, client: Client, seconds: float = 0.3) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        client.dispatch()
        root.update()
        time.sleep(0.01)


def test_window_shows_status_and_rows():
    answers = {"status": STATUS, "usage": USAGE, "pause": {**STATUS, "paused": True}}
    client = Client(Path("/x"), lambda cmd, args, path: answers[cmd])
    root = tk.Tk()
    try:
        window = MainWindow(root, client)
        pump(root, client)
        assert window.headline.cget("text") == "Shaping traffic on wlp0s20f3"
        assert tk.ttk.Style(root).theme_use() == "alt"
        assert list(window._rows) == ["Firefox", "Zoom", "Busy"]  # pyright: ignore[reportPrivateUsage]
        assert window.active.get() and window.switch.cget("text") == "Limits on"
        assert "selected" in window.switch.state()  # pressed = on
        window.switch.invoke()
        pump(root, client)
        assert window.headline.cget("text").startswith("Paused")
        assert not window.active.get()
        assert window.switch.cget("text") == "Limits off"
    finally:
        root.destroy()
        client.close()
