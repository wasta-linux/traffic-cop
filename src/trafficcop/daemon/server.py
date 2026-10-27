"""The daemon's Unix socket API (see ``protocol.py`` for the messages).

Anyone may connect: the socket is mode 0666. Each request is authorized
from the caller's credentials, which the kernel attaches to the connection
(``SO_PEERCRED``), so they can't be forged:

* **Read** commands (status, usage, get-config): everyone.
* **Write** commands (set-config, reload, pause, resume): root, or a member
  of one of ``WRITER_GROUPS`` (the groups Ubuntu, Debian and Fedora use for
  administrators).

Group membership is read from the caller's ``/proc/<pid>/status``, which
lists the groups the process actually holds and is readable in the snap
with ``system-observe``; group names are resolved once, at startup.

Requests are handled on the event loop that runs the supervisor, so they
never race with its periodic checks.
"""

from __future__ import annotations

import asyncio
import grp
import logging
import os
import socket
import struct
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trafficcop import __version__
from trafficcop.common import config, protocol
from trafficcop.common.protocol import ProtocolError
from trafficcop.daemon import nft
from trafficcop.daemon.meter import Usage
from trafficcop.daemon.supervisor import Supervisor

log = logging.getLogger(__name__)

WRITER_GROUPS = ("sudo", "admin", "wheel")
PROC = Path("/proc")


@dataclass(frozen=True)
class Peer:
    pid: int
    uid: int
    gids: frozenset[int]  # effective gid plus supplementary groups


def peer_of(sock: socket.socket, proc: Path = PROC) -> Peer:
    """The credentials of the process at the other end of a Unix socket."""
    creds = sock.getsockopt(
        socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
    )
    pid, uid, gid = struct.unpack("3i", creds)
    gids = {gid}
    try:
        for line in (proc / str(pid) / "status").read_text().splitlines():
            if line.startswith("Groups:"):
                gids.update(int(g) for g in line.split()[1:])
    except (OSError, ValueError):
        pass  # the client exited; fall back to its primary group
    return Peer(pid, uid, frozenset(gids))


def group_ids(names: Iterable[str]) -> frozenset[int]:
    gids = set()
    for name in names:
        try:
            gids.add(grp.getgrnam(name).gr_gid)
        except KeyError:
            pass  # not every distro has every group
    return frozenset(gids)


class Server:
    def __init__(
        self,
        supervisor: Supervisor,
        socket_path: Path,
        config_path: Path,
        writer_gids: frozenset[int] | None = None,
        peer: Callable[[socket.socket], Peer] = peer_of,
    ):
        self.supervisor = supervisor
        self.socket_path = socket_path
        self.config_path = config_path
        self.writer_gids = (
            group_ids(WRITER_GROUPS) if writer_gids is None else writer_gids
        )
        self._peer = peer
        self._server: asyncio.Server | None = None
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "status": self._status,
            "usage": self._usage,
            "get-config": self._get_config,
            "set-config": self._set_config,
            "reload": self._reload,
            "pause": self._pause,
            "resume": self._resume,
        }

    # --- Lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self.socket_path.unlink(missing_ok=True)  # left by a crashed daemon
        self._server = await asyncio.start_unix_server(
            self._connection, path=str(self.socket_path), limit=protocol.MAX_LINE
        )
        os.chmod(self.socket_path, 0o666)  # anyone may connect; see the docstring
        log.info("listening on %s", self.socket_path)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self.socket_path.unlink(missing_ok=True)

    # --- Connections -------------------------------------------------------------

    async def _connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            peer = self._peer(writer.get_extra_info("socket"))
            while True:
                try:
                    line = await reader.readline()
                except ValueError:  # longer than MAX_LINE
                    await self._send(
                        writer, protocol.error(protocol.BAD_REQUEST, "request too long")
                    )
                    break
                if not line:
                    break
                await self._send(writer, self.handle(line, peer))
        except (ConnectionError, OSError):
            pass  # the client went away
        finally:
            writer.close()

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, response: dict[str, Any]) -> None:
        writer.write(protocol.encode(response))
        await writer.drain()

    def handle(self, line: bytes, peer: Peer) -> dict[str, Any]:
        """Answer one request line from ``peer``."""
        try:
            message = protocol.decode(line)
            if message.get("v") != protocol.VERSION:
                raise ProtocolError(
                    protocol.BAD_REQUEST,
                    f"protocol version {message.get('v')!r} isn't supported"
                    f" (this daemon speaks {protocol.VERSION})",
                )
            cmd = message.get("cmd")
            args = message.get("args", {})
            if not isinstance(args, dict):
                raise ProtocolError(protocol.BAD_REQUEST, "'args' must be an object")
            if cmd not in self._handlers:
                raise ProtocolError(
                    protocol.UNKNOWN_COMMAND, f"unknown command {cmd!r}"
                )
            if cmd in protocol.WRITE_COMMANDS and not self.may_write(peer):
                raise ProtocolError(
                    protocol.PERMISSION_DENIED,
                    f"'{cmd}' needs root or membership of one of: "
                    + ", ".join(WRITER_GROUPS),
                )
            if cmd in protocol.WRITE_COMMANDS:
                log.info("%s requested by uid %d (pid %d)", cmd, peer.uid, peer.pid)
            return protocol.ok(self._handlers[cmd](args))
        except ProtocolError as e:
            return protocol.error(e.code, e.message)
        except Exception:
            log.exception("error handling a request")
            return protocol.error(
                protocol.FAILED, "internal error; see the daemon's log"
            )

    def may_write(self, peer: Peer) -> bool:
        return peer.uid == 0 or bool(peer.gids & self.writer_gids)

    # --- Commands ---------------------------------------------------------------

    def _status(self, args: dict[str, Any]) -> dict[str, Any]:
        sup = self.supervisor
        return {
            "version": __version__,
            "paused": sup.paused,
            "wan": sup.wan.current,
            "shaping": sup.shaped,
            "config": str(self.config_path),
            "apps": [
                {
                    "name": app.name,
                    "download": app.download,  # bits/s, or None
                    "upload": app.upload,
                    "units": sorted(
                        u.rsplit("/", 1)[-1]
                        for u in sup.detector.current.get(app.name, ())
                    ),
                }
                for app in sup.config.apps
            ],
        }

    def _usage(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "apps": {
                name: {
                    "up_rate": u.up_rate,
                    "down_rate": u.down_rate,
                    "up_total": u.up_total,
                    "down_total": u.down_total,
                }
                for name, u in (
                    (a.name, self.supervisor.usage.get(a.name, Usage()))
                    for a in self.supervisor.config.apps
                )
            }
        }

    def _get_config(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.supervisor.config.to_dict()

    def _set_config(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            if "yaml" in args:
                if not isinstance(args["yaml"], str):
                    raise config.ConfigError("'yaml' must be a string")
                new = config.loads(args["yaml"], source="request")
            elif "config" in args:
                new = config.parse(args["config"])
            else:
                raise config.ConfigError("expected 'config' or 'yaml' in args")
        except config.ConfigError as e:
            raise ProtocolError(protocol.INVALID_CONFIG, str(e)) from None
        self._apply(new)
        try:
            config.save(new, self.config_path)
        except config.ConfigError as e:
            raise ProtocolError(
                protocol.FAILED, f"applied, but not saved: {e}"
            ) from None
        return self._status({})

    def _reload(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            new = config.load(self.config_path)
        except config.ConfigError as e:
            raise ProtocolError(protocol.INVALID_CONFIG, str(e)) from None
        self._apply(new)
        return self._status({})

    def _apply(self, new: config.Config) -> None:
        try:
            self.supervisor.apply_config(new)
        except nft.NftError as e:
            raise ProtocolError(
                protocol.FAILED, f"couldn't load the rules: {e}"
            ) from None

    def _pause(self, args: dict[str, Any]) -> dict[str, Any]:
        self.supervisor.pause()
        return self._status({})

    def _resume(self, args: dict[str, Any]) -> dict[str, Any]:
        self.supervisor.resume()
        return self._status({})


async def serve(server: Server, run: Callable[[], Awaitable[None]]) -> None:
    """Listen while ``run`` runs, then close the socket."""
    await server.start()
    try:
        await run()
    finally:
        await server.close()
