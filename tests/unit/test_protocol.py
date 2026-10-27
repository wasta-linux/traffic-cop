import json
import socket
import threading
from pathlib import Path

import pytest

from trafficcop.common import protocol
from trafficcop.common.protocol import ProtocolError


def test_encode_decode():
    line = protocol.encode(protocol.request("usage"))
    assert line == b'{"v":1,"cmd":"usage"}\n'
    assert protocol.decode(line) == {"v": 1, "cmd": "usage"}


def test_request_args():
    assert protocol.request("set-config", {"yaml": "x"}) == {
        "v": 1,
        "cmd": "set-config",
        "args": {"yaml": "x"},
    }


@pytest.mark.parametrize("line", [b"not json\n", b"[1, 2]\n", b"\xff\n"])
def test_decode_rejects(line: bytes):
    with pytest.raises(ProtocolError) as e:
        protocol.decode(line)
    assert e.value.code == protocol.BAD_REQUEST


def test_commands_are_read_or_write():
    assert not protocol.READ_COMMANDS & protocol.WRITE_COMMANDS
    assert protocol.COMMANDS == {
        "status", "usage", "get-config", "set-config", "reload", "pause", "resume"
    }  # fmt: skip


def serve_once(path: Path, response: bytes) -> list[bytes]:
    """Answer one connection on ``path`` with ``response``; return what came in."""
    received: list[bytes] = []
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.listen()

    def run() -> None:
        conn, _ = srv.accept()
        with conn, conn.makefile("rb") as f:
            received.append(f.readline())
            conn.sendall(response)
        srv.close()

    threading.Thread(target=run, daemon=True).start()
    return received


def test_call(tmp_path: Path):
    path = tmp_path / "s.sock"
    received = serve_once(path, b'{"ok":true,"result":{"paused":false}}\n')
    assert protocol.call("status", path=path) == {"paused": False}
    assert json.loads(received[0]) == {"v": 1, "cmd": "status"}


def test_call_error_response(tmp_path: Path):
    path = tmp_path / "s.sock"
    serve_once(
        path,
        b'{"ok":false,"error":{"code":"permission-denied","message":"nope"}}\n',
    )
    with pytest.raises(ProtocolError) as e:
        protocol.call("pause", path=path)
    assert (e.value.code, e.value.message) == ("permission-denied", "nope")


@pytest.mark.parametrize("response", [b"", b'{"ok":false}\n', b'{"ok":true'])
def test_call_bad_responses(tmp_path: Path, response: bytes):
    path = tmp_path / "s.sock"
    serve_once(path, response)
    with pytest.raises(ProtocolError):
        protocol.call("status", path=path)


def test_call_without_daemon(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        protocol.call("status", path=tmp_path / "missing.sock")
