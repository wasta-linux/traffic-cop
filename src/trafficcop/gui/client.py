"""Talk to the daemon from the GUI without freezing it.

A daemon call blocks until it answers (or times out), and Tk isn't
thread-safe, so requests run one at a time on a background thread and
their replies come back through a queue. The window calls ``dispatch``
from its own event loop (every ~100 ms, via ``after``), which runs each
request's callback on the GUI thread.

This module doesn't import tkinter, so it can be tested without a display.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trafficcop.common import paths, protocol


@dataclass(frozen=True)
class Reply:
    cmd: str
    result: Any = None
    error: Exception | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def unreachable(self) -> bool:
        """The daemon isn't running (or its socket can't be opened)."""
        return isinstance(self.error, OSError)

    @property
    def permission_denied(self) -> bool:
        return (
            isinstance(self.error, protocol.ProtocolError)
            and self.error.code == protocol.PERMISSION_DENIED
        )

    @property
    def message(self) -> str:
        if isinstance(self.error, protocol.ProtocolError):
            return self.error.message
        return str(self.error) if self.error else ""


Callback = Callable[[Reply], None]
_STOP = object()


class Client:
    def __init__(
        self,
        socket_path: Path | None = None,
        call: Callable[..., Any] = protocol.call,
    ):
        self.socket_path = socket_path or paths.get().socket
        self._call = call
        self._requests: queue.Queue[Any] = queue.Queue()
        self._replies: queue.Queue[tuple[Callback, Reply]] = queue.Queue()
        self._pending: set[str] = set()  # commands sent but not yet dispatched
        self._thread = threading.Thread(
            target=self._work, name="daemon-client", daemon=True
        )
        self._thread.start()

    def request(
        self,
        cmd: str,
        on_reply: Callback,
        args: Mapping[str, Any] | None = None,
        once: bool = False,
    ) -> bool:
        """Queue a request; ``on_reply`` runs on the GUI thread in ``dispatch``.

        With ``once``, the request is skipped (returning False) while the same
        command is still pending, so polling never piles up behind a slow daemon.
        """
        if once and cmd in self._pending:
            return False
        self._pending.add(cmd)
        self._requests.put((cmd, args, on_reply))
        return True

    def dispatch(self) -> int:
        """Run the callbacks of replies that have arrived. Call on the GUI thread."""
        count = 0
        while True:
            try:
                on_reply, reply = self._replies.get_nowait()
            except queue.Empty:
                return count
            self._pending.discard(reply.cmd)
            on_reply(reply)
            count += 1

    def close(self, timeout: float = 1.0) -> None:
        self._requests.put(_STOP)
        self._thread.join(timeout)

    def _work(self) -> None:
        while (item := self._requests.get()) is not _STOP:
            cmd, args, on_reply = item
            try:
                reply = Reply(cmd, result=self._call(cmd, args, self.socket_path))
            except (protocol.ProtocolError, OSError) as e:
                reply = Reply(cmd, error=e)
            except Exception as e:  # never let the worker thread die
                reply = Reply(
                    cmd, error=protocol.ProtocolError(protocol.FAILED, str(e))
                )
            self._replies.put((on_reply, reply))
