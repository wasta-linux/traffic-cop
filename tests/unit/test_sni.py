import pytest


class FakeConnection:
    def __init__(self) -> None:
        self.sent: list = []

    def send(self, message, serial=None) -> None:
        self.sent.append(message)

    def close(self) -> None:
        pass


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


class NoWatcher(RefusingBus):
    """A bus where nothing owns the watcher's name (plain GNOME)."""

    def __init__(self) -> None:
        super().__init__("org.freedesktop.DBus.Error.ServiceUnknown")

    def send(self, message, serial=None) -> None:
        from jeepney import HeaderFields, new_method_return

        member = message.header.fields.get(HeaderFields.member)
        if member == "RegisterStatusNotifierItem":
            return super().send(message, serial)
        FakeConnection.send(self, message, serial)
        reply = new_method_return(message, "u", (1,))
        reply.header.fields[HeaderFields.reply_serial] = serial
        self.replies.append(reply)


def test_no_watcher_is_no_tray():
    from trafficcop.indicator import sni

    bus = NoWatcher()
    item = sni.StatusNotifier("id", "Title", lambda key: None, connection=bus)
    with pytest.raises(sni.NoTray):
        item.register()
