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


class FakeConnection:
    def __init__(self) -> None:
        self.sent: list = []

    def send(self, message, serial=None) -> None:
        self.sent.append(message)


def menu(clicks: list[str] | None = None):
    from trafficcop.indicator import sni

    on_click = (clicks if clicks is not None else []).append
    return sni, sni.StatusNotifier("id", "Title", on_click, connection=FakeConnection())


def layout_ids(item) -> dict[str, int]:
    """Label (or "separator") -> id, from the layout the panel would read."""
    _sig, (_revision, (_root, _props, children)) = item._get_layout(0, -1, [])
    out = {}
    for _sig, (item_id, props, _children) in children:
        name = props["label"][1] if "label" in props else "separator"
        out[name] = item_id
    return out


def test_menu_ids_stay_put_when_items_come_and_go():
    sni, item = menu()
    full = [
        sni.MenuItem("toggle", "Turn limits off"),
        sni.MenuItem("open", "Open"),
        sni.MenuItem("sep", separator=True),
        sni.MenuItem("quit", "Quit"),
    ]
    item.set_items(full)
    before = layout_ids(item)
    item.set_items(full[1:])  # an item removed: the others keep their ids
    assert layout_ids(item) == {
        k: v for k, v in before.items() if k != "Turn limits off"
    }
    item.set_items(full)
    assert layout_ids(item) == before


def test_hidden_item_and_clicks():
    clicks: list[str] = []
    sni, item = menu(clicks)
    item.set_items(
        [
            sni.MenuItem("toggle", "Turn limits on", visible=False),
            sni.MenuItem("quit", "Quit"),
        ]
    )
    ids = layout_ids(item)
    assert item._props(ids["Turn limits on"])["visible"] == ("b", False)
    item._event(ids["Quit"], "clicked", ("s", ""), 0)
    item._event(99, "clicked", ("s", ""), 0)  # unknown ids are ignored
    assert clicks == ["quit"]


class RefusingBus(FakeConnection):
    """Answers every call with an error, like dbus-daemon under AppArmor."""

    def __init__(self, error_name: str) -> None:
        import itertools

        super().__init__()
        self.error_name = error_name
        self.outgoing_serial = itertools.count(1)
        self.replies: list = []

    def send(self, message, serial=None) -> None:
        from jeepney import HeaderFields, new_error

        super().send(message, serial)
        reply = new_error(message, self.error_name, "s", (DENIAL,))
        reply.header.fields[HeaderFields.reply_serial] = serial
        self.replies.append(reply)

    def receive(self, timeout=None):
        return self.replies.pop(0)


# The text seen in the snap with unity7 disconnected.
DENIAL = (
    "An AppArmor policy prevents this sender from sending this message to "
    'this recipient; type="method_call", member="RequestName"'
)


def test_refused_by_apparmor_is_not_allowed():
    from trafficcop.indicator import sni

    bus = RefusingBus("org.freedesktop.DBus.Error.AccessDenied")
    item = sni.StatusNotifier("id", "Title", lambda key: None, connection=bus)
    with pytest.raises(sni.NotAllowed, match="AppArmor policy"):
        item.register()


def test_no_watcher_is_no_tray():
    from trafficcop.indicator import sni

    class NoWatcher(RefusingBus):
        def send(self, message, serial=None) -> None:
            from jeepney import HeaderFields, new_method_return

            member = message.header.fields.get(HeaderFields.member)
            if member == "RegisterStatusNotifierItem":
                return super().send(message, serial)
            FakeConnection.send(self, message, serial)
            reply = new_method_return(message, "u", (1,))
            reply.header.fields[HeaderFields.reply_serial] = serial
            self.replies.append(reply)

    bus = NoWatcher("org.freedesktop.DBus.Error.ServiceUnknown")
    item = sni.StatusNotifier("id", "Title", lambda key: None, connection=bus)
    with pytest.raises(sni.NoTray):
        item.register()
