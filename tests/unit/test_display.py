from pathlib import Path

import pytest

from trafficcop.common import display


@pytest.mark.parametrize(
    ("rate", "text"),
    [(0, "0 B/s"), (999, "999 B/s"), (70_000, "70.0 kB/s"), (1_500_000, "1.5 MB/s")],
)
def test_human_rate(rate: float, text: str):
    assert display.human_rate(rate) == text


def test_human_limit():
    assert display.human_limit(560_000) == "70.0 kB/s"  # 70kbps in the config
    assert display.human_limit(None) == "-"


def test_human_bytes():
    assert display.human_bytes(31_000_000) == "31.0 MB"
    assert display.human_bytes(512) == "512 B"


@pytest.mark.parametrize(
    ("changes", "text"),
    [
        ({}, "shaping traffic on wlp0s20f3"),
        ({"paused": True}, "paused (not shaping; still metering)"),
        (
            {"shaping": None},
            "not shaping: setting up tc on wlp0s20f3 failed (see the log)",
        ),
        ({"shaping": None, "wan": None}, "not shaping: no route to the internet"),
    ],
)
def test_state_text(changes, text):
    status = {"paused": False, "wan": "wlp0s20f3", "shaping": "wlp0s20f3", **changes}
    assert display.state_text(status) == text


SNAP_ENV = {
    "SNAP": "/snap/traffic-cop/42",
    "SNAP_DATA": "/var/snap/traffic-cop/42",
    "SNAP_COMMON": "/var/snap/traffic-cop/common",
    "SNAP_NAME": "traffic-cop",
}


def test_start_hint_in_the_snap():
    assert display.start_hint(SNAP_ENV) == "sudo snap start traffic-cop"
    parallel = {**SNAP_ENV, "SNAP_INSTANCE_NAME": "traffic-cop_test"}
    assert display.start_hint(parallel) == "sudo snap start traffic-cop_test"


def test_start_hint_in_a_venv(tmp_path: Path):
    (tmp_path / "traffic-copd").write_text("#!/bin/sh\n")
    argv0 = str(tmp_path / "traffic-cop-ctl")
    assert display.start_hint({}, argv0) == f"sudo {tmp_path}/traffic-copd"


def test_start_hint_without_a_daemon_script(tmp_path: Path):
    assert (
        display.start_hint({}, str(tmp_path / "traffic-cop-ctl")) == "sudo traffic-copd"
    )


@pytest.mark.parametrize(
    ("rate", "limit_bits", "text"),
    [
        (45_800, 560_000, "45.8 / 70 kB/s"),
        (900, 500_000, "0.9 / 62.5 kB/s"),  # same unit as the limit
        (118_400, 1_000_000, "118.4 / 125 kB/s"),
        (0, 64_000, "0.0 / 8 kB/s"),
        (1_234_567, 16_000_000, "1.2 / 2 MB/s"),
        (36_000, 280_000, "36.0 / 35 kB/s"),  # briefly over the limit
        (500, 6_400, "500 / 800 B/s"),
    ],
)
def test_rate_of_limit(rate: float, limit_bits: int, text: str):
    assert display.rate_of_limit(rate, limit_bits) == text
