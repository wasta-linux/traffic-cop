"""The tray thread's parts, run on the test's thread with a fake bus."""

import threading

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
    t.show(menu.tray_state({}, may_change=True))  # run() waits for a first state
    t.run()
    assert t.events() == [tray.NOT_ALLOWED]


def test_no_tray():
    t = tray.Tray(connection=NoWatcher(), start=False)
    t.show(menu.tray_state({}, may_change=True))
    t.run()
    assert t.events() == [tray.NO_TRAY]


def test_registers_only_with_the_first_state():
    """The panel must never see the placeholder icon (no name, a grey circle)."""
    seen = []

    class Watching(NoWatcher):
        def send(self, message, serial=None) -> None:
            from jeepney import HeaderFields

            member = message.header.fields.get(HeaderFields.member)
            if member in ("RequestName", "RegisterStatusNotifierItem"):
                seen.append(t.item.icon if t.item else None)
            super().send(message, serial)

    t = tray.Tray(connection=Watching(), start=False)
    # The state arrives after the thread has started, as from the window.
    late = threading.Timer(
        0.1, t.show, (menu.tray_state({"paused": True}, may_change=True),)
    )
    late.start()
    t.run()
    assert seen == [menu.PAUSED, menu.PAUSED]


def test_stopped_before_the_first_state():
    t = tray.Tray(connection=NoWatcher(), start=False)
    t.stop()
    t.run()  # returns without registering
    assert t.events() == []
