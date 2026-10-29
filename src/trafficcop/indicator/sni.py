"""A tray icon with a menu, over D-Bus: StatusNotifierItem + dbusmenu.

StatusNotifierItem (SNI) is the tray protocol of KDE, Cinnamon, Xfce, MATE
and Ubuntu's GNOME (through its AppIndicator extension). An app puts an
object on the session bus describing its icon, and registers it with the
desktop's ``org.kde.StatusNotifierWatcher``; the panel then reads the icon
and draws the menu from a second object speaking ``com.canonical.dbusmenu``.

Specs:
- https://www.freedesktop.org/wiki/Specifications/StatusNotifierItem/
- dbusmenu: the XML in https://github.com/AyatanaIndicators/libdbusmenu
  (libdbusmenu-glib/dbus-menu.xml)

Written with jeepney, which is pure Python and has no GTK or GObject
dependency (AyatanaAppIndicator would bring GTK back; dbus-fast is asyncio
and partly compiled). Everything runs on one thread: ``serve`` answers the
panel's calls for a while and returns, so the caller can poll the daemon
in between.

The names and object paths are the ones libappindicator uses
(``org.kde.StatusNotifierItem-<pid>-<n>``, ``/StatusNotifierItem``,
``/MenuBar``), because snapd's AppArmor rules for tray icons allow those.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from jeepney import (
    DBusAddress,
    HeaderFields,
    MatchRule,
    Message,
    MessageType,
    message_bus,
    new_error,
    new_method_call,
    new_method_return,
    new_signal,
)
from jeepney.io.blocking import DBusConnection, open_dbus_connection

ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
ITEM_IFACE = "org.kde.StatusNotifierItem"
MENU_IFACE = "com.canonical.dbusmenu"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER = DBusAddress("/StatusNotifierWatcher", WATCHER_NAME, WATCHER_NAME)

# Panels pick the size closest to what they draw (often 16-24 px; more on
# high-DPI screens).
ICON_SIZES = (16, 22, 24, 32, 48)


class NoTray(Exception):
    """No StatusNotifierWatcher is running, so there's nowhere to show an icon.

    Plain GNOME (without the AppIndicator extension) has none.
    """


@dataclass(frozen=True)
class MenuItem:
    key: str  # passed to on_click
    label: str = ""
    enabled: bool = True
    separator: bool = False


def circle_pixmap(colour: str, size: int) -> tuple[int, int, bytes]:
    """A filled circle in ``colour`` ("#rrggbb"), as SNI pixmap data.

    The icon is drawn in code rather than named, because an ``IconName``
    must exist in the user's icon theme, and a snap can't install into it.
    SNI pixmaps are ARGB32 in network byte order (A, R, G, B per pixel).
    Edges are anti-aliased by covering each pixel's distance from the rim.
    """
    r, g, b = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    centre = (size - 1) / 2
    radius = size / 2 - 1  # one pixel margin
    data = bytearray()
    for y in range(size):
        for x in range(size):
            distance = math.hypot(x - centre, y - centre)
            alpha = max(0.0, min(1.0, radius - distance + 0.5))
            data += bytes((round(alpha * 255), r, g, b))
    return size, size, bytes(data)


class StatusNotifier:
    """One tray icon. Call ``register``, then ``serve`` in a loop."""

    def __init__(
        self,
        app_id: str,
        title: str,
        on_click: Callable[[str], None],
        connection: DBusConnection | None = None,
    ):
        self.app_id = app_id
        self.title = title
        self.on_click = on_click
        self.conn = connection or open_dbus_connection(bus="SESSION")
        self.bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self.colour = "#808080"
        self.tooltip = ""
        self.items: list[MenuItem] = []
        self.revision = 1  # dbusmenu layout revision; bumped on every change
        self.stopped = False

    # --- Setup ---------------------------------------------------------------

    def register(self) -> None:
        """Take our bus name and announce the icon to the watcher.

        Raises NoTray if no watcher is running. Also asks to hear when the
        watcher (re)starts, e.g. when the GNOME extension is turned off and
        on, so the icon can register again.
        """
        self._call(message_bus.RequestName(self.bus_name))
        rule = MatchRule(
            type="signal",
            sender="org.freedesktop.DBus",
            interface="org.freedesktop.DBus",
            member="NameOwnerChanged",
            path="/org/freedesktop/DBus",
        )
        rule.add_arg_condition(0, WATCHER_NAME)
        self._call(message_bus.AddMatch(rule))
        self._register_with_watcher()

    def _register_with_watcher(self) -> None:
        reply = self._call(
            new_method_call(
                WATCHER, "RegisterStatusNotifierItem", "s", (self.bus_name,)
            ),
            check=False,
        )
        if reply.header.message_type == MessageType.error:
            raise NoTray(str(reply.header.fields.get(HeaderFields.error_name)))

    def close(self) -> None:
        self.conn.close()

    # --- Changing what's shown ----------------------------------------------

    def set_icon(self, colour: str) -> None:
        if colour != self.colour:
            self.colour = colour
            self._emit(ITEM_PATH, ITEM_IFACE, "NewIcon")

    def set_tooltip(self, text: str) -> None:
        if text != self.tooltip:
            self.tooltip = text
            self._emit(ITEM_PATH, ITEM_IFACE, "NewToolTip")

    def set_items(self, items: Sequence[MenuItem]) -> None:
        if list(items) != self.items:
            self.items = list(items)
            self.revision += 1
            self._emit(MENU_PATH, MENU_IFACE, "LayoutUpdated", "ui", (self.revision, 0))

    # --- The event loop ------------------------------------------------------

    def serve(self, seconds: float) -> None:
        """Answer the panel's calls for ``seconds``, or until ``stopped``."""
        deadline = time.monotonic() + seconds
        while not self.stopped:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                msg = self.conn.receive(timeout=remaining)
            except TimeoutError:
                return
            self._handle(msg)

    def _call(self, msg: Message, check: bool = True) -> Message:
        """Send a method call and wait for its reply, answering the panel meanwhile.

        jeepney's own ``send_and_get_reply`` drops other messages while it
        waits; the watcher may call back (to read our properties) before it
        answers our registration, so those calls must be handled here.
        """
        serial = next(self.conn.outgoing_serial)
        self.conn.send(msg, serial=serial)
        while True:
            reply = self.conn.receive(timeout=10)
            if reply.header.fields.get(HeaderFields.reply_serial) == serial:
                if check and reply.header.message_type == MessageType.error:
                    raise RuntimeError(f"D-Bus error: {reply.body}")
                return reply
            self._handle(reply)

    def _emit(
        self,
        path: str,
        iface: str,
        member: str,
        signature: str | None = None,
        body: tuple[Any, ...] = (),
    ) -> None:
        self.conn.send(
            new_signal(DBusAddress(path, interface=iface), member, signature, body)
        )

    def _handle(self, msg: Message) -> None:
        fields = msg.header.fields
        if msg.header.message_type == MessageType.signal:
            # NameOwnerChanged(name, old, new) for the watcher: re-register
            # when a new one appears.
            if fields.get(HeaderFields.member) == "NameOwnerChanged":
                name, _old, new = msg.body
                if name == WATCHER_NAME and new:
                    self._register_with_watcher()
            return
        if msg.header.message_type != MessageType.method_call:
            return
        key = (
            fields.get(HeaderFields.path),
            fields.get(HeaderFields.interface),
            fields.get(HeaderFields.member),
        )
        handler = self._handlers().get(key)
        if handler is None:
            reply = new_error(
                msg,
                "org.freedesktop.DBus.Error.UnknownMethod",
                "s",
                (f"no method {key[2]} on {key[0]} ({key[1]})",),
            )
        else:
            try:
                signature, body = handler(*msg.body)
                reply = new_method_return(msg, signature, body)
            except Exception as e:  # the panel gets an error, we keep running
                reply = new_error(
                    msg, "org.freedesktop.DBus.Error.Failed", "s", (str(e),)
                )
        self.conn.send(reply)

    def _handlers(
        self,
    ) -> dict[tuple[str | None, ...], Callable[..., tuple[str | None, tuple]]]:
        none = (None, ())
        return {
            (ITEM_PATH, PROPS_IFACE, "Get"): self._item_get,
            (ITEM_PATH, PROPS_IFACE, "GetAll"): self._item_get_all,
            (ITEM_PATH, ITEM_IFACE, "Activate"): lambda x, y: none,
            (ITEM_PATH, ITEM_IFACE, "SecondaryActivate"): lambda x, y: none,
            (ITEM_PATH, ITEM_IFACE, "ContextMenu"): lambda x, y: none,
            (ITEM_PATH, ITEM_IFACE, "Scroll"): lambda delta, orientation: none,
            (MENU_PATH, PROPS_IFACE, "Get"): self._menu_get,
            (MENU_PATH, PROPS_IFACE, "GetAll"): self._menu_get_all,
            (MENU_PATH, MENU_IFACE, "GetLayout"): self._get_layout,
            (MENU_PATH, MENU_IFACE, "GetGroupProperties"): self._get_group_properties,
            (MENU_PATH, MENU_IFACE, "GetProperty"): self._get_property,
            (MENU_PATH, MENU_IFACE, "Event"): self._event,
            (MENU_PATH, MENU_IFACE, "EventGroup"): self._event_group,
            (MENU_PATH, MENU_IFACE, "AboutToShow"): lambda id_: ("b", (False,)),
            (MENU_PATH, MENU_IFACE, "AboutToShowGroup"): lambda ids: ("aiai", ([], [])),
        }

    # --- StatusNotifierItem ----------------------------------------------------

    def _item_properties(self) -> dict[str, tuple[str, Any]]:
        """Values are (signature, value): how jeepney writes a D-Bus variant."""
        pixmaps = [circle_pixmap(self.colour, size) for size in ICON_SIZES]
        return {
            "Category": ("s", "SystemServices"),
            "Id": ("s", self.app_id),
            "Title": ("s", self.title),
            "Status": ("s", "Active"),
            "WindowId": ("i", 0),
            "IconName": ("s", ""),
            "IconPixmap": ("a(iiay)", pixmaps),
            "OverlayIconName": ("s", ""),
            "OverlayIconPixmap": ("a(iiay)", []),
            "AttentionIconName": ("s", ""),
            "AttentionIconPixmap": ("a(iiay)", []),
            "AttentionMovieName": ("s", ""),
            "ToolTip": ("(sa(iiay)ss)", ("", [], self.title, self.tooltip)),
            # The menu opens on a left click too (KDE otherwise calls Activate).
            "ItemIsMenu": ("b", True),
            "Menu": ("o", MENU_PATH),
        }

    def _item_get(self, iface: str, name: str) -> tuple[str, tuple]:
        return "v", (self._item_properties()[name],)

    def _item_get_all(self, iface: str) -> tuple[str, tuple]:
        return "a{sv}", (self._item_properties(),)

    # --- dbusmenu ----------------------------------------------------------------
    # Item ids: 0 is the root, then 1, 2, ... in the order of self.items.

    def _menu_properties(self) -> dict[str, tuple[str, Any]]:
        return {
            "Version": ("u", 3),
            "TextDirection": ("s", "ltr"),
            "Status": ("s", "normal"),
            "IconThemePath": ("as", []),
        }

    def _menu_get(self, iface: str, name: str) -> tuple[str, tuple]:
        return "v", (self._menu_properties()[name],)

    def _menu_get_all(self, iface: str) -> tuple[str, tuple]:
        return "a{sv}", (self._menu_properties(),)

    def _props(self, item_id: int) -> dict[str, tuple[str, Any]]:
        if item_id == 0:
            return {"children-display": ("s", "submenu")}
        item = self.items[item_id - 1]
        if item.separator:
            return {"type": ("s", "separator")}
        return {"label": ("s", item.label), "enabled": ("b", item.enabled)}

    def _get_layout(
        self, parent_id: int, depth: int, names: list[str]
    ) -> tuple[str, tuple]:
        def node(item_id: int) -> tuple[int, dict, list]:
            return (item_id, self._props(item_id), [])

        if parent_id == 0:
            children = [("(ia{sv}av)", node(i)) for i in range(1, len(self.items) + 1)]
            layout = (0, self._props(0), children)
        else:
            layout = node(parent_id)
        return "u(ia{sv}av)", (self.revision, layout)

    def _get_group_properties(
        self, ids: list[int], names: list[str]
    ) -> tuple[str, tuple]:
        ids = ids or list(range(len(self.items) + 1))
        valid = [i for i in ids if 0 <= i <= len(self.items)]
        return "a(ia{sv})", ([(i, self._props(i)) for i in valid],)

    def _get_property(self, item_id: int, name: str) -> tuple[str, tuple]:
        return "v", (self._props(item_id)[name],)

    def _event(self, item_id: int, event: str, data: Any, timestamp: int) -> tuple:
        if event == "clicked" and 1 <= item_id <= len(self.items):
            self.on_click(self.items[item_id - 1].key)
        return None, ()

    def _event_group(self, events: list[tuple]) -> tuple[str, tuple]:
        for item_id, event, data, timestamp in events:
            self._event(item_id, event, data, timestamp)
        return "ai", ([],)
