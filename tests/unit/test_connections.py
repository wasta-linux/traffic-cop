import subprocess
from pathlib import Path
from typing import Any

import pytest

from trafficcop.common import connections

SNAP_ENV = {
    "SNAP": "/snap/wasta-traffic-cop/2",
    "SNAP_DATA": "/var/snap/wasta-traffic-cop/2",
    "SNAP_COMMON": "/var/snap/wasta-traffic-cop/common",
    "SNAP_NAME": "wasta-traffic-cop",
}


def snapctl(stdout: str = "", returncode: int = 0):
    """A fake subprocess.run that answers like ``snapctl is-connected --list``."""
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert kwargs["timeout"] > 0
        return subprocess.CompletedProcess(argv, returncode, stdout, "")

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_all_connected():
    run = snapctl(
        "desktop\nfirewall-control\nhome\nnetwork-bind\nnetwork-control\n"
        "system-observe\nunity7\nx11\n"
    )
    assert connections.missing(SNAP_ENV, run) == ()
    assert run.calls == [["snapctl", "is-connected", "--list"]]  # type: ignore[attr-defined]


def test_some_missing():
    run = snapctl("desktop\nnetwork-bind\nnetwork-control\nx11\n")
    assert connections.missing(SNAP_ENV, run) == ("firewall-control", "system-observe")


def test_unknown_when_snapctl_fails():
    assert connections.missing(SNAP_ENV, snapctl(returncode=1)) is None

    def not_found(argv: list[str], **kwargs: Any) -> Any:
        raise FileNotFoundError(argv[0])

    def too_slow(argv: list[str], **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    assert connections.missing(SNAP_ENV, not_found) is None
    assert connections.missing(SNAP_ENV, too_slow) is None


def test_unknown_outside_the_snap():
    run = snapctl()
    assert connections.missing({}, run) is None
    assert run.calls == []  # type: ignore[attr-defined]


def test_fix_command_for_one_plug():
    assert connections.fix_command("wasta-traffic-cop_test", ["system-observe"]) == (
        "sudo snap connect wasta-traffic-cop_test:system-observe"
        " && sudo snap restart wasta-traffic-cop_test.daemon"
    )


def test_fix_command_for_all():
    assert connections.fix_command("wasta-traffic-cop", connections.DAEMON_PLUGS) == (
        "sudo snap connect wasta-traffic-cop:network-control"
        " && sudo snap connect wasta-traffic-cop:firewall-control"
        " && sudo snap connect wasta-traffic-cop:system-observe"
        " && sudo snap restart wasta-traffic-cop.daemon"
    )


@pytest.mark.parametrize("plug", connections.DAEMON_PLUGS)
def test_docs_links(plug: str):
    assert connections.DOCS[plug] == f"https://snapcraft.io/docs/{plug}-interface"


def test_daemon_plugs_match_the_snap():
    """Every daemon plug that doesn't connect on its own, and only those."""
    import yaml

    snapcraft = Path(__file__).parents[2] / "snap" / "snapcraft.yaml"
    snap = yaml.safe_load(snapcraft.read_text())
    assert isinstance(snap, dict)
    plugs = set(snap["apps"]["daemon"]["plugs"]) - {"network-bind"}
    assert plugs == set(connections.DAEMON_PLUGS)
