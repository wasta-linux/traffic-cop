from pathlib import Path

import pytest

from trafficcop.indicator import __main__ as indicator

ICONS = Path(indicator.__file__).parent / "icons"
# Where Python finds the package inside the snap: through "current", not
# the revision in $SNAP (seen in a parallel-installed test snap).
IN_SNAP = Path(
    "/snap/wasta-traffic-cop/current/lib/python3.14/site-packages/trafficcop/indicator"
)


@pytest.fixture
def no_snap(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in ("SNAP", "SNAP_NAME", "SNAP_INSTANCE_NAME", "SNAP_REVISION"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def in_snap(no_snap: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    no_snap.setattr(indicator.resources, "files", lambda package: IN_SNAP)
    no_snap.setenv("SNAP", "/snap/wasta-traffic-cop/x2")
    no_snap.setenv("SNAP_NAME", "wasta-traffic-cop")
    no_snap.setenv("SNAP_REVISION", "x2")
    return no_snap


def test_icons_exist():
    for name, _colour in (indicator.ON, indicator.PAUSED, indicator.OFFLINE):
        assert (ICONS / f"{name}.svg").is_file()


def test_icon_dir_unconfined(no_snap: pytest.MonkeyPatch):
    assert indicator.icon_dir() == str(ICONS)


def test_icon_dir_in_the_snap(in_snap: pytest.MonkeyPatch):
    in_snap.setenv("SNAP_INSTANCE_NAME", "wasta-traffic-cop")
    assert indicator.icon_dir() == str(IN_SNAP / "icons")


def test_icon_dir_in_a_parallel_instance(in_snap: pytest.MonkeyPatch):
    # On the host, /snap/wasta-traffic-cop is the other (store) install.
    in_snap.setenv("SNAP_INSTANCE_NAME", "wasta-traffic-cop_test")
    assert indicator.icon_dir() == (
        "/snap/wasta-traffic-cop_test/current/lib/python3.14/site-packages"
        "/trafficcop/indicator/icons"
    )
