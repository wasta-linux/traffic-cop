from pathlib import Path

import pytest

from trafficcop.indicator import menu

ICONS = Path(menu.__file__).parent / "icons"
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
    no_snap.setattr(menu.resources, "files", lambda package: IN_SNAP)
    no_snap.setenv("SNAP", "/snap/wasta-traffic-cop/x2")
    no_snap.setenv("SNAP_NAME", "wasta-traffic-cop")
    no_snap.setenv("SNAP_REVISION", "x2")
    return no_snap


def test_icons_exist():
    for icon in (menu.ON, menu.PAUSED, menu.OFFLINE):
        assert (ICONS / f"{icon.name}.svg").is_file()


def test_icon_dir_unconfined(no_snap: pytest.MonkeyPatch):
    assert menu.icon_dir() == str(ICONS)


def test_icon_dir_in_the_snap(in_snap: pytest.MonkeyPatch):
    in_snap.setenv("SNAP_INSTANCE_NAME", "wasta-traffic-cop")
    assert menu.icon_dir() == str(IN_SNAP / "icons")


def test_icon_dir_in_a_parallel_instance(in_snap: pytest.MonkeyPatch):
    # On the host, /snap/wasta-traffic-cop is the other (store) install.
    in_snap.setenv("SNAP_INSTANCE_NAME", "wasta-traffic-cop_test")
    assert menu.icon_dir() == (
        "/snap/wasta-traffic-cop_test/current/lib/python3.14/site-packages"
        "/trafficcop/indicator/icons"
    )


def items(state: menu.TrayState) -> dict[str, menu.MenuItem]:
    return {item.key: item for item in state.items}


def test_running():
    state = menu.tray_state({"paused": False}, may_change=True)
    assert (state.icon, state.tooltip) == (menu.ON, "Limits on")
    toggle = items(state)[menu.TOGGLE]
    assert toggle.label == "Turn limits off"
    assert toggle.enabled and toggle.visible
    assert [i.key for i in state.items] == [
        menu.TOGGLE,
        menu.OPEN,
        "separator",
        menu.QUIT,
    ]


def test_paused():
    state = menu.tray_state({"paused": True}, may_change=True)
    assert (state.icon, state.tooltip) == (menu.PAUSED, "Limits off")
    assert items(state)[menu.TOGGLE].label == "Turn limits on"


def test_daemon_unreachable():
    state = menu.tray_state(None, may_change=True)
    assert (state.icon, state.tooltip) == (menu.OFFLINE, "Traffic Cop isn't running")
    # Hidden, not removed, so the other items keep their places.
    assert not items(state)[menu.TOGGLE].visible
    assert items(state)[menu.OPEN].visible


def test_not_an_admin():
    state = menu.tray_state({"paused": False}, may_change=False)
    toggle = items(state)[menu.TOGGLE]
    assert toggle.visible and not toggle.enabled


def test_states_compare_equal():
    # Tray.show skips a state equal to the last one.
    assert menu.tray_state({}, True) == menu.tray_state({}, True)
