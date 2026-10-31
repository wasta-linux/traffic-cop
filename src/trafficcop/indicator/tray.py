"""The tray icon, on its own thread beside the window's Tk loop.

The window and the tray icon are one process (as in most apps with a tray
icon), so that everything runs under the snap's ``gui`` AppArmor label:
GNOME then groups a window shown from the tray with the menu's Traffic Cop,
and a second launch can hand over to the running copy (common/instance.py).

Tk must stay on the main thread, and jeepney's blocking connection isn't
thread-safe, so the tray thread owns the D-Bus connection: only it sends or
receives on it. The two threads talk through queues, as in gui/client.py:

- Tk -> tray: ``show(state)`` queues a ``menu.TrayState``; the thread
  applies the latest one between short ``serve`` calls.
- tray -> Tk: ``events()`` returns the menu clicks (``menu.TOGGLE``,
  ``OPEN``, ``QUIT``) and how registration went (``READY``, ``NO_TRAY``,
  ``NOT_ALLOWED``). The window drains it from its ``after`` loop.

This module doesn't import tkinter, and imports jeepney (through sni.py)
only on the tray thread, so the window still works without jeepney.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from typing import TYPE_CHECKING

from trafficcop.indicator import menu

if TYPE_CHECKING:
    from jeepney.io.blocking import DBusConnection

    from trafficcop.indicator.sni import StatusNotifier

log = logging.getLogger(__name__)

SERVE_SECONDS = 0.25  # how long a new state may wait to be shown

# How registration went. Anything but READY means there's no icon, so the
# window's close button must quit rather than hide.
READY = "ready"
NO_TRAY = "no-tray"  # no tray on this desktop (plain GNOME), or no bus
NOT_ALLOWED = "not-allowed"  # the bus refused (in the snap: unity7)


class Tray:
    def __init__(
        self,
        icon_dir: str = "",
        connection: DBusConnection | None = None,
        start: bool = True,
    ):
        """Start the tray thread (unless ``start`` is False, for tests)."""
        self.icon_dir = icon_dir
        self.item: StatusNotifier | None = None
        self._connection = connection
        self._states: queue.Queue[menu.TrayState] = queue.Queue()
        self._events: queue.Queue[str] = queue.Queue()
        self._last: menu.TrayState | None = None  # last queued, on the Tk side
        self._stopped = False
        self._thread = threading.Thread(target=self.run, name="tray", daemon=True)
        if start:
            self._thread.start()

    # --- Called from the Tk thread ---------------------------------------------

    def show(self, state: menu.TrayState) -> None:
        if state != self._last:
            self._last = state
            self._states.put(state)

    def events(self) -> list[str]:
        out = []
        while True:
            try:
                out.append(self._events.get_nowait())
            except queue.Empty:
                return out

    def stop(self) -> None:
        """Remove the icon: the thread closes its connection, which the panel sees."""
        self._stopped = True
        if self.item is not None:
            self.item.stopped = True
        if self._thread.is_alive():
            self._thread.join(timeout=2 * SERVE_SECONDS)

    # --- The tray thread ----------------------------------------------------------

    def connect(self) -> None:
        """Create the StatusNotifier (and its bus connection) on this thread."""
        from trafficcop.indicator import sni

        self.item = sni.StatusNotifier(
            "wasta-traffic-cop",
            "Traffic Cop",
            on_click=self._events.put,
            icon_dir=self.icon_dir,
            connection=self._connection,
        )

    def pump(self) -> None:
        """Show the latest queued state, if any."""
        assert self.item is not None
        state = None
        while True:
            try:
                state = self._states.get_nowait()
            except queue.Empty:
                break
        if state is not None:
            self.item.set_icon(state.icon)
            self.item.set_tooltip(state.tooltip)
            self.item.set_items(state.items)

    def run(self) -> None:
        try:
            from trafficcop.indicator import sni
        except ImportError:
            log.warning(
                "no tray icon: it needs jeepney (sudo apt install python3-jeepney)"
            )
            self._events.put(NO_TRAY)
            return
        try:
            self.connect()
        except (OSError, KeyError) as e:  # KeyError: no session bus address at all
            log.warning("no tray icon: can't connect to the D-Bus session bus: %s", e)
            self._events.put(NO_TRAY)
            return
        assert self.item is not None
        try:
            self.pump()  # the first icon and menu, before the panel reads them
            self.item.register()
            self._events.put(READY)
            while not self._stopped:
                self.pump()
                self.item.serve(SERVE_SECONDS)
        except sni.NoTray:
            # Plain GNOME has no tray; not an error.
            log.info("no tray on this desktop (no StatusNotifierWatcher)")
            self._events.put(NO_TRAY)
        except sni.NotAllowed as e:
            log.error("the session bus refused the tray icon: %s", e)
            instance = os.environ.get("SNAP_INSTANCE_NAME")
            if instance:
                log.error(
                    "connect the unity7 interface: sudo snap connect %s:unity7",
                    instance,
                )
            self._events.put(NOT_ALLOWED)
        except Exception:
            # Don't let a bug in the tray take the window down with it.
            log.exception("the tray icon failed")
            self._events.put(NO_TRAY)
        finally:
            self.item.close()
