import io
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from trafficcop.cli import __main__ as cli
from trafficcop.common import config, protocol

STATUS = {
    "version": "2.0.0.dev0",
    "paused": False,
    "wan": "wlp0s20f3",
    "shaping": "wlp0s20f3",
    "config": "/var/lib/traffic-cop-dev/traffic-cop.yaml",
    "apps": [
        {"name": "Firefox", "download": 560000, "upload": 400000,
         "units": ["snap.firefox.firefox-f99a.scope"]},
        {"name": "Snap Updates", "download": 1000000, "upload": None,
         "units": ["snapd.service"]},
        {"name": "Spotify", "download": 128000, "upload": 64000, "units": []},
        {"name": "Zoom", "download": None, "upload": None,
         "units": ["a.scope", "b.scope"]},
    ],
}  # fmt: skip
USAGE = {
    "apps": {
        "Firefox": {"up_rate": 2500.0, "down_rate": 65400.0, "up_total": 1_200_000,
                    "down_total": 31_000_000},
        "Zoom": {"up_rate": 0.0, "down_rate": 120.0, "up_total": 900,
                 "down_total": 4100},
    }
}  # fmt: skip
CONFIG = config.load_default().to_dict()


class FakeDaemon:
    def __init__(self, results: Mapping[str, Any] | None = None):
        self.results = {"status": STATUS, "usage": USAGE, "get-config": CONFIG}
        self.results.update(results or {})
        self.calls: list[tuple[str, Any, Path]] = []
        self.error: Exception | None = None

    def __call__(self, cmd: str, args: Any, path: Path) -> Any:
        self.calls.append((cmd, args, path))
        if self.error:
            raise self.error
        return self.results.get(cmd, STATUS)


def run(daemon: FakeDaemon, *argv: str) -> tuple[int, str]:
    out = io.StringIO()
    code = cli.main(["--socket", "/tmp/t.sock", *argv], call=daemon, out=out)
    return code, out.getvalue()


@pytest.mark.parametrize(
    ("rate", "text"),
    [(0, "0 B/s"), (999, "999 B/s"), (70_000, "70.0 kB/s"), (1_500_000, "1.5 MB/s")],
)
def test_human_rate(rate: float, text: str):
    assert cli.human_rate(rate) == text


def test_human_limit():
    assert cli.human_limit(560_000) == "70.0 kB/s"  # 70kbps in the config
    assert cli.human_limit(None) == "-"


def test_human_bytes():
    assert cli.human_bytes(31_000_000) == "31.0 MB"
    assert cli.human_bytes(512) == "512 B"


def test_status():
    daemon = FakeDaemon()
    code, out = run(daemon, "status")
    assert code == 0
    assert daemon.calls == [("status", None, Path("/tmp/t.sock"))]
    assert out.splitlines()[:2] == [
        "traffic-copd 2.0.0.dev0: shaping traffic on wlp0s20f3",
        "config: /var/lib/traffic-cop-dev/traffic-cop.yaml",
    ]
    assert "\n".join(out.splitlines()[3:]) == (
        "APP           DOWN LIMIT   UP LIMIT  RUNNING\n"
        "Firefox        70.0 kB/s  50.0 kB/s  snap.firefox.firefox-f99a.scope\n"
        "Snap Updates  125.0 kB/s          -  snapd.service\n"
        "Spotify        16.0 kB/s   8.0 kB/s  no\n"
        "Zoom                   -          -  2 units"
    )


@pytest.mark.parametrize(
    ("changes", "state"),
    [
        ({"paused": True}, "paused (not shaping; still metering)"),
        ({"shaping": None}, "not shaping: setting up tc on wlp0s20f3 failed"),
        ({"shaping": None, "wan": None}, "not shaping: no route to the internet"),
    ],
)
def test_status_states(changes: dict[str, Any], state: str):
    _, out = run(FakeDaemon({"status": {**STATUS, **changes}}), "status")
    assert state in out.splitlines()[0]


def test_status_without_apps():
    _, out = run(FakeDaemon({"status": {**STATUS, "apps": []}}), "status")
    assert out.rstrip().endswith("No apps configured.")


def test_rates():
    code, out = run(FakeDaemon(), "rates")
    assert code == 0
    assert out == (
        "APP           DOWN        UP  DOWN TOTAL  UP TOTAL\n"
        "Firefox  65.4 kB/s  2.5 kB/s     31.0 MB    1.2 MB\n"
        "Zoom       120 B/s     0 B/s      4.1 kB     900 B\n"
    )


def test_rates_watch(monkeypatch: pytest.MonkeyPatch):
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli.time, "sleep", sleep)
    daemon = FakeDaemon()
    code, out = run(daemon, "rates", "-w", "-n", "2")
    assert code == 0
    assert sleeps == [2.0, 2.0, 2.0]
    assert out.count("APP ") == 3 and cli._CLEAR not in out  # not a terminal


def test_json():
    _, out = run(FakeDaemon(), "--json", "rates")
    assert json.loads(out) == USAGE


@pytest.mark.parametrize("cmd", ["pause", "resume", "reload"])
def test_simple_commands(cmd: str):
    daemon = FakeDaemon()
    code, out = run(daemon, cmd)
    assert code == 0 and daemon.calls[0][:2] == (cmd, None)
    assert out.startswith("traffic-copd ")


def test_config_show():
    _, out = run(FakeDaemon(), "config", "show")
    assert out.startswith("### traffic-cop configuration file.")
    assert config.loads(out) == config.load_default()


def test_config_set(tmp_path: Path):
    path = tmp_path / "new.yaml"
    path.write_text("version: 2\napps: {}\n")
    daemon = FakeDaemon()
    code, _ = run(daemon, "config", "set", str(path))
    assert code == 0
    assert daemon.calls[0][:2] == ("set-config", {"yaml": "version: 2\napps: {}\n"})


def test_config_set_stdin(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("version: 2\napps: {}\n"))
    daemon = FakeDaemon()
    assert run(daemon, "config", "set", "-")[0] == 0
    assert daemon.calls[0][1] == {"yaml": "version: 2\napps: {}\n"}


def test_config_set_checks_locally(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    path = tmp_path / "bad.yaml"
    path.write_text("version: 2\napps: {X: {dl: 1}}\n")
    daemon = FakeDaemon()
    assert run(daemon, "config", "set", str(path))[0] == cli.EXIT_FAILED
    assert daemon.calls == []
    assert "unknown key 'dl'" in capsys.readouterr().err


def test_config_set_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    code, _ = run(FakeDaemon(), "config", "set", str(tmp_path / "nope.yaml"))
    assert code == cli.EXIT_FAILED
    assert "nope.yaml" in capsys.readouterr().err


def test_permission_denied(capsys: pytest.CaptureFixture[str]):
    daemon = FakeDaemon()
    daemon.error = protocol.ProtocolError(
        protocol.PERMISSION_DENIED, "'pause' needs root or membership of one of: sudo"
    )
    assert run(daemon, "pause")[0] == cli.EXIT_FAILED
    assert capsys.readouterr().err == (
        "traffic-cop-ctl: 'pause' needs root or membership of one of: sudo (try sudo)\n"
    )


@pytest.mark.parametrize("error", [FileNotFoundError(2, "x"), ConnectionRefusedError()])
def test_daemon_not_running(error: OSError, capsys: pytest.CaptureFixture[str]):
    daemon = FakeDaemon()
    daemon.error = error
    assert run(daemon, "status")[0] == cli.EXIT_UNREACHABLE
    assert "is it running? Start it with: sudo " in capsys.readouterr().err


def test_usage_errors():
    with pytest.raises(SystemExit) as e:
        run(FakeDaemon())
    assert e.value.code == 2


SNAP_ENV = {
    "SNAP": "/snap/traffic-cop/42",
    "SNAP_DATA": "/var/snap/traffic-cop/42",
    "SNAP_COMMON": "/var/snap/traffic-cop/common",
    "SNAP_NAME": "traffic-cop",
}


def test_start_hint_in_the_snap():
    assert cli.start_hint(SNAP_ENV) == "sudo snap start traffic-cop"
    parallel = {**SNAP_ENV, "SNAP_INSTANCE_NAME": "traffic-cop_test"}
    assert cli.start_hint(parallel) == "sudo snap start traffic-cop_test"


def test_start_hint_in_a_venv(tmp_path: Path):
    (tmp_path / "traffic-copd").write_text("#!/bin/sh\n")
    argv0 = str(tmp_path / "traffic-cop-ctl")
    assert cli.start_hint({}, argv0) == f"sudo {tmp_path}/traffic-copd"


def test_start_hint_without_a_daemon_script(tmp_path: Path):
    assert cli.start_hint({}, str(tmp_path / "traffic-cop-ctl")) == "sudo traffic-copd"
