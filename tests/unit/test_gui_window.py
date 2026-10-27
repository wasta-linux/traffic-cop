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


class FakeDaemon:
    """Answers like the daemon, with a config that set-config replaces."""

    def __init__(self) -> None:
        from trafficcop.common import config

        self.config = config.load_default().to_dict()
        self.sent: list[tuple[str, Any]] = []

    def __call__(self, cmd: str, args: Any, path: Any) -> Any:
        from trafficcop.common import config

        self.sent.append((cmd, args))
        if cmd == "get-config":
            return self.config
        if cmd == "set-config":
            new = (
                config.parse(args["config"])
                if "config" in args
                else config.loads(args["yaml"])
            )
            self.config = new.to_dict()
        apps = [
            {"name": a, "download": None, "upload": None, "units": []}
            for a in self.config["apps"]
        ]
        if cmd == "usage":
            return {"apps": {}}
        return {**STATUS, "apps": apps}


def make(daemon: FakeDaemon, may_change: bool = True) -> tuple[Any, Client, MainWindow]:
    client = Client(Path("/x"), daemon)
    root = tk.Tk()
    window = MainWindow(root, client)
    window.may_change = may_change
    pump(root, client)
    return root, client, window


def test_add_an_app():
    daemon = FakeDaemon()
    root, client, window = make(daemon)
    try:
        window.add.invoke()
        pump(root, client, 0.1)
        dialog = window.dialog
        assert dialog is not None
        dialog.name.set("Skype")
        dialog.limits["download"][0].set("50")
        dialog._rules[0][1].set("skype")  # pyright: ignore[reportPrivateUsage]
        dialog._save()  # pyright: ignore[reportPrivateUsage]
        pump(root, client)
        assert not dialog.winfo_exists()
        assert daemon.config["apps"]["Skype"] == {
            "download": "400kbit",
            "match": [{"snap": "skype"}],
        }
        assert "Skype" in window._rows  # pyright: ignore[reportPrivateUsage]
    finally:
        root.destroy()
        client.close()


def test_edit_shows_errors_and_keeps_the_dialog():
    daemon = FakeDaemon()
    root, client, window = make(daemon)
    try:
        window._edit_app("Zoom")  # pyright: ignore[reportPrivateUsage]
        pump(root, client)
        dialog = window.dialog
        assert dialog is not None and dialog.name.get() == "Zoom"
        dialog.name.set("Firefox")  # clashes
        dialog._save()  # pyright: ignore[reportPrivateUsage]
        pump(root, client)
        assert "already an app called Firefox" in dialog.error.cget("text")
        assert dialog.winfo_exists()
        dialog.close()
    finally:
        root.destroy()
        client.close()


def test_read_only_for_other_users():
    daemon = FakeDaemon()
    root, client, window = make(daemon, may_change=False)
    try:
        window._edit_app("Zoom")  # pyright: ignore[reportPrivateUsage]
        pump(root, client)
        dialog = window.dialog
        assert dialog is not None and dialog.save is None
        assert "Only administrators" in dialog.error.cget("text")
        dialog.close()
    finally:
        root.destroy()
        client.close()
