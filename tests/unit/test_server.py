import asyncio
import json
import os
import socket
from pathlib import Path
from typing import Any

import pytest

from tests.unit.fakes import ZOOM, FakeSystem
from trafficcop.common import config, protocol
from trafficcop.daemon import server, supervisor

ADMINS = 27  # gid of the "sudo" group in these tests
USER = server.Peer(pid=1234, uid=1000, gids=frozenset({1000}))
ADMIN = server.Peer(pid=1235, uid=1000, gids=frozenset({1000, ADMINS}))
ROOT = server.Peer(pid=1, uid=0, gids=frozenset({0}))


@pytest.fixture
def api(sup: supervisor.Supervisor, tmp_path: Path) -> server.Server:
    sup.start()
    path = tmp_path / "traffic-cop.yaml"
    config.save(sup.config, path)
    return server.Server(
        sup, tmp_path / "s.sock", path, writer_gids=frozenset({ADMINS})
    )


def ask(api: server.Server, peer: server.Peer, cmd: str, **args: Any) -> dict[str, Any]:
    return api.handle(protocol.encode(protocol.request(cmd, args)), peer)


def result(response: dict[str, Any]) -> Any:
    assert response["ok"], response
    return response["result"]


def error_code(response: dict[str, Any]) -> str:
    assert not response["ok"], response
    return response["error"]["code"]


def test_status(api: server.Server):
    status = result(ask(api, USER, "status"))
    assert status["paused"] is False
    assert status["wan"] == status["shaping"] == "wlp2s0"
    assert status["apps"][0] == {
        "name": "Zoom",
        "download": 800_000,
        "upload": 800_000,
        "units": ["app-gnome-Zoom-1234.scope"],
    }


def test_usage(api: server.Server, system: FakeSystem):
    system.counters = {"down_1": (2, 3000)}
    api.supervisor.sample()
    usage = result(ask(api, USER, "usage"))
    assert usage["apps"]["Zoom"]["down_total"] == 3000
    assert set(usage["apps"]) == {"Zoom", "Snap Updates"}


def test_get_config(api: server.Server):
    cfg = result(ask(api, USER, "get-config"))
    assert config.parse(cfg) == api.supervisor.config


@pytest.mark.parametrize("cmd", sorted(protocol.WRITE_COMMANDS))
def test_writes_need_an_admin(api: server.Server, cmd: str):
    response = ask(api, USER, cmd)
    assert error_code(response) == protocol.PERMISSION_DENIED
    assert "sudo, admin, wheel" in response["error"]["message"]


@pytest.mark.parametrize("peer", [ADMIN, ROOT])
def test_admins_and_root_may_write(api: server.Server, peer: server.Peer):
    assert result(ask(api, peer, "pause"))["paused"] is True
    assert result(ask(api, peer, "resume"))["paused"] is False


def test_set_config(api: server.Server, system: FakeSystem):
    new = {
        "version": 2,
        "apps": {"Zoom": {"download": "1mbit", "match": [{"exe": "/opt/zoom/zoom"}]}},
    }
    status = result(ask(api, ADMIN, "set-config", config=new))
    assert [a["name"] for a in status["apps"]] == ["Zoom"]
    assert status["apps"][0]["download"] == 1_000_000
    assert config.load(api.config_path) == config.parse(new)  # saved
    assert system.calls("nft")[-1] == ("nft", "ruleset")  # applied


def test_set_config_yaml(api: server.Server):
    text = "version: 2\napps:\n  X: {upload: 64kbit, match: [{name: x}]}\n"
    result(ask(api, ADMIN, "set-config", yaml=text))
    assert api.supervisor.config == config.loads(text)


@pytest.mark.parametrize(
    "args",
    [
        {"config": {"version": 2, "apps": {"X": {"match": []}}}},
        {"yaml": "version: 2\napps: {X: {dl: 1}}\n"},
        {"yaml": 5},
        {},
    ],
)
def test_set_config_rejects_invalid(api: server.Server, args: dict[str, Any]):
    before = api.supervisor.config
    assert error_code(ask(api, ADMIN, "set-config", **args)) == protocol.INVALID_CONFIG
    assert api.supervisor.config is before


def test_reload(api: server.Server):
    api.config_path.write_text("version: 2\napps: {}\n")
    assert result(ask(api, ADMIN, "reload"))["apps"] == []
    api.config_path.write_text("garbage: [\n")
    assert error_code(ask(api, ADMIN, "reload")) == protocol.INVALID_CONFIG
    assert api.supervisor.config.apps == ()


def test_apply_failure(api: server.Server, system: FakeSystem):
    system.nft_failures = 3
    response = ask(api, ADMIN, "reload")
    assert error_code(response) == protocol.FAILED
    assert "couldn't load the rules" in response["error"]["message"]


@pytest.mark.parametrize(
    ("line", "code"),
    [
        (b"nope\n", protocol.BAD_REQUEST),
        (b'{"cmd":"status"}\n', protocol.BAD_REQUEST),  # no version
        (b'{"v":2,"cmd":"status"}\n', protocol.BAD_REQUEST),
        (b'{"v":1,"cmd":"status","args":[]}\n', protocol.BAD_REQUEST),
        (b'{"v":1,"cmd":"explode"}\n', protocol.UNKNOWN_COMMAND),
    ],
)
def test_bad_requests(api: server.Server, line: bytes, code: str):
    assert error_code(api.handle(line, USER)) == code


def test_internal_errors_are_reported(api: server.Server, monkeypatch, caplog):
    def broken(args: dict[str, Any]) -> None:
        raise RuntimeError("boom")

    monkeypatch.setitem(api._handlers, "status", broken)  # pyright: ignore[reportPrivateUsage]
    assert error_code(ask(api, USER, "status")) == protocol.FAILED
    assert "error handling a request" in caplog.text


def test_peer_of_reads_real_credentials():
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    with a, b:
        peer = server.peer_of(a)
    assert (peer.pid, peer.uid) == (os.getpid(), os.getuid())
    assert os.getgid() in peer.gids
    assert set(os.getgroups()) <= peer.gids


def test_group_ids():
    assert server.group_ids(["root", "no-such-group-xyz"]) == {0}


def test_over_a_real_socket(api: server.Server, tmp_path: Path):
    """Start the server, then talk to it as traffic-cop-ctl will."""
    api._peer = lambda sock: ADMIN  # pyright: ignore[reportPrivateUsage]

    async def main() -> list[Any]:
        await api.start()
        assert oct(os.stat(api.socket_path).st_mode & 0o777) == "0o666"
        loop = asyncio.get_running_loop()
        try:
            return [
                await loop.run_in_executor(
                    None, protocol.call, cmd, None, api.socket_path
                )
                for cmd in ("status", "pause", "usage")
            ]
        finally:
            await api.close()

    status, paused, usage = asyncio.run(main())
    assert status["shaping"] == "wlp2s0"
    assert paused["paused"] is True
    assert "Zoom" in usage["apps"]
    assert not api.socket_path.exists()


def test_several_requests_per_connection(api: server.Server):
    async def main() -> list[dict[str, Any]]:
        await api.start()
        try:
            reader, writer = await asyncio.open_unix_connection(str(api.socket_path))
            for cmd in ("status", "usage"):
                writer.write(protocol.encode(protocol.request(cmd)))
            writer.write(b"x" * (protocol.MAX_LINE + 10) + b"\n")
            await writer.drain()
            replies = [json.loads(await reader.readline()) for _ in range(3)]
            writer.close()
            return replies
        finally:
            await api.close()

    status, usage, too_long = asyncio.run(main())
    assert status["ok"] and usage["ok"]
    assert too_long["error"]["code"] == protocol.BAD_REQUEST


def test_start_replaces_a_stale_socket(api: server.Server):
    api.socket_path.write_text("left by a crashed daemon")

    async def main() -> None:
        await api.start()
        await api.close()

    asyncio.run(main())
    assert not api.socket_path.exists()


def test_status_units_are_unit_names(api: server.Server):
    assert ZOOM.endswith(result(ask(api, USER, "status"))["apps"][0]["units"][0])
