import threading
import time
from pathlib import Path
from typing import Any

from trafficcop.common import protocol
from trafficcop.gui.client import Client, Reply


def wait_for(client: Client, count: int = 1, timeout: float = 2.0) -> int:
    """Dispatch until ``count`` callbacks have run."""
    done = 0
    end = time.monotonic() + timeout
    while done < count and time.monotonic() < end:
        done += client.dispatch()
        time.sleep(0.005)
    return done


def test_request_and_dispatch():
    calls: list[tuple[str, Any, Path]] = []
    threads: list[str] = []

    def call(cmd: str, args: Any, path: Path) -> Any:
        calls.append((cmd, args, path))
        threads.append(threading.current_thread().name)
        return {"cmd": cmd}

    client = Client(Path("/tmp/t.sock"), call)
    replies: list[Reply] = []
    client.request("status", replies.append)
    client.request("set-config", replies.append, {"yaml": "x"})
    assert wait_for(client, 2) == 2
    client.close()

    assert [r.result for r in replies] == [{"cmd": "status"}, {"cmd": "set-config"}]
    assert calls[1] == ("set-config", {"yaml": "x"}, Path("/tmp/t.sock"))
    assert threads == ["daemon-client", "daemon-client"]  # not the GUI thread


def test_callbacks_only_run_in_dispatch():
    client = Client(Path("/x"), lambda *a: 1)
    ran: list[Reply] = []
    client.request("status", ran.append)
    time.sleep(0.05)
    assert ran == []  # the worker never calls back directly
    wait_for(client)
    client.close()
    assert len(ran) == 1


def test_once_skips_while_pending():
    gate = threading.Event()

    def call(cmd: str, args: Any, path: Path) -> Any:
        gate.wait(2)
        return cmd

    client = Client(Path("/x"), call)
    replies: list[Reply] = []
    assert client.request("usage", replies.append, once=True)
    assert not client.request("usage", replies.append, once=True)
    assert client.request("status", replies.append, once=True)  # other commands go
    gate.set()
    wait_for(client, 2)
    assert client.request("usage", replies.append, once=True)  # no longer pending
    wait_for(client)
    client.close()
    assert [r.result for r in replies] == ["usage", "status", "usage"]


def test_errors():
    errors: dict[str, Exception] = {
        "status": FileNotFoundError(2, "No such file"),
        "pause": protocol.ProtocolError(protocol.PERMISSION_DENIED, "needs root"),
        "usage": RuntimeError("boom"),
    }

    def call(cmd: str, args: Any, path: Path) -> Any:
        raise errors[cmd]

    client = Client(Path("/x"), call)
    replies: dict[str, Reply] = {}
    for cmd in errors:
        client.request(cmd, lambda r: replies.__setitem__(r.cmd, r))
    wait_for(client, 3)
    client.close()

    assert replies["status"].unreachable and not replies["status"].ok
    assert replies["pause"].permission_denied
    assert replies["pause"].message == "needs root"
    assert replies["usage"].message == "boom"  # the worker survived
    assert not replies["usage"].unreachable


def test_close_stops_the_worker():
    client = Client(Path("/x"), lambda *a: None)
    client.close()
    assert not client._thread.is_alive()  # pyright: ignore[reportPrivateUsage]
