from typing import Any

import pytest

from trafficcop.gui.model import Direction, build_view, level

STATUS: dict[str, Any] = {
    "version": "2.0.0.dev0",
    "paused": False,
    "wan": "wlp0s20f3",
    "shaping": "wlp0s20f3",
    "config": "/x.yaml",
    "apps": [
        {"name": "Firefox", "download": 560000, "upload": None, "units": ["a.scope"]},
        {"name": "Zoom", "download": 280000, "upload": 280000, "units": []},
        {"name": "Busy", "download": 8000, "upload": None, "units": ["a", "b"]},
    ],
}
USAGE: dict[str, Any] = {
    "apps": {
        "Firefox": {"up_rate": 2500.0, "down_rate": 45800.0, "up_total": 1_200_000,
                    "down_total": 31_000_000},
        "Busy": {"up_rate": 0.0, "down_rate": 5000.0, "up_total": 0, "down_total": 10},
    }
}  # fmt: skip


def test_connected():
    view = build_view(STATUS, USAGE)
    assert view.connected and view.paused is False
    assert view.headline == "Shaping traffic on wlp0s20f3"
    assert view.detail == "Traffic Cop 2.0.0.dev0"
    assert [r.name for r in view.rows] == ["Firefox", "Zoom", "Busy"]


def test_rows():
    firefox, zoom, busy = build_view(STATUS, USAGE).rows
    assert firefox.running and firefox.instances == 1
    assert firefox.down == Direction("45.8 / 70 kB/s", 45800 / 70000, "31.0 MB", "ok")
    assert firefox.up == Direction("2.5 kB/s", None, "1.2 MB")  # unlimited: no bar
    assert not zoom.running and zoom.instances == 0
    assert zoom.down == Direction("0.0 / 35 kB/s", 0.0, "0 B", "ok")  # no usage
    assert busy.instances == 2
    assert busy.down.fraction == 1.0  # over the limit: the bar is just full
    assert busy.down.level == "full"


def test_paused():
    view = build_view({**STATUS, "paused": True}, USAGE)
    assert view.paused is True
    assert view.headline == "Paused (not shaping; still metering)"


def test_paused_bars_are_not_coloured():
    busy = build_view({**STATUS, "paused": True}, USAGE).rows[2]
    assert busy.down.fraction == 1.0  # still shows how much of the limit
    assert busy.down.level == "paused"  # greyed out: nothing is limited


def test_before_the_first_usage():
    view = build_view(STATUS, None)
    assert view.rows[0].down.text == "0.0 / 70 kB/s"


def test_unreachable():
    view = build_view(None, None, FileNotFoundError(2, "x"))
    assert not view.connected and view.paused is None and view.rows == ()
    assert view.headline == "Traffic Cop isn't running"
    assert view.detail.startswith("Start it with: sudo ")


def test_no_apps():
    view = build_view({**STATUS, "apps": []}, {"apps": {}})
    assert view.connected and view.rows == ()


@pytest.mark.parametrize(
    ("fraction", "expected"),
    [
        (0.0, "ok"),
        (0.75, "ok"),
        (0.949, "ok"),
        (0.95, "full"),
        (1.0, "full"),
    ],
)
def test_level(fraction: float, expected: str):
    assert level(fraction) == expected
