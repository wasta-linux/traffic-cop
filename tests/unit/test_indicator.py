from pathlib import Path

import pytest

from trafficcop.indicator import __main__ as indicator

ICONS = Path(indicator.__file__).parent / "icons"


@pytest.fixture
def no_snap(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in ("SNAP", "SNAP_INSTANCE_NAME", "SNAP_REVISION"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_icons_exist():
    for name, _colour in (indicator.ON, indicator.PAUSED, indicator.OFFLINE):
        assert (ICONS / f"{name}.svg").is_file()


def test_icon_dir_unconfined(no_snap: pytest.MonkeyPatch):
    assert indicator.icon_dir() == str(ICONS)


def test_icon_dir_in_a_parallel_instance(no_snap: pytest.MonkeyPatch):
    # Pretend the package lives in the snap: everything above icons/ is $SNAP.
    snap = str(ICONS.parent)
    no_snap.setenv("SNAP", snap)
    no_snap.setenv("SNAP_INSTANCE_NAME", "wasta-traffic-cop_test")
    no_snap.setenv("SNAP_REVISION", "x1")
    assert indicator.icon_dir() == "/snap/wasta-traffic-cop_test/x1/icons"
