"""The tray thread's parts, run on the test's thread with a fake bus."""

from tests.unit.test_sni import FakeConnection, NoWatcher, RefusingBus, layout_ids
from trafficcop.indicator import menu, tray


def connected() -> tray.Tray:
    t = tray.Tray(connection=FakeConnection(), start=False)
    t.connect()
    return t


def test_pump_shows_the_latest_state():
    t = connected()
    t.show(menu.tray_state(None, may_change=True))
    t.show(menu.tray_state({"paused": True}, may_change=True))
    t.pump()
    assert t.item is not None
    assert t.item.icon == menu.PAUSED
    assert t.item.tooltip == "Limits off"
    assert "Turn limits on" in layout_ids(t.item)


def test_same_state_is_queued_once():
    t = connected()
    state = menu.tray_state({}, may_change=True)
    t.show(state)
    t.show(state)
    assert t._states.qsize() == 1


def test_clicks_come_back_as_events():
    t = connected()
    t.show(menu.tray_state({}, may_change=True))
    t.pump()
    assert t.item is not None
    t.item._event(layout_ids(t.item)["Open Traffic Cop"], "clicked", ("s", ""), 0)
    assert t.events() == [menu.OPEN]
    assert t.events() == []  # drained


def test_refused_by_apparmor():
    t = tray.Tray(
        connection=RefusingBus("org.freedesktop.DBus.Error.AccessDenied"), start=False
    )
    t.run()
    assert t.events() == [tray.NOT_ALLOWED]


def test_no_tray():
    t = tray.Tray(connection=NoWatcher(), start=False)
    t.run()
    assert t.events() == [tray.NO_TRAY]
