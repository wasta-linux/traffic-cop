"""The API between traffic-copd and its clients (traffic-cop-ctl, the GUI).

Clients connect to the daemon's Unix socket (``paths.get().socket``) and
exchange **one JSON object per line**, in UTF-8. A connection can carry any
number of requests; each gets exactly one response, in order::

    -> {"v": 1, "cmd": "usage"}
    <- {"ok": true, "result": {"apps": {"Zoom": {"up_rate": 812.5, ...}}}}

    -> {"v": 1, "cmd": "pause"}
    <- {"ok": false, "error": {"code": "permission-denied", "message": "..."}}

``v`` is ``VERSION``; the daemon rejects requests with a different one, so
an old client gets a clear error after the daemon is upgraded. ``args`` is an
optional object.

Commands (``READ_COMMANDS`` anyone may use; ``WRITE_COMMANDS`` need root or
an admin group, see ``server.py``):

    status       daemon state: version, paused, WAN interface, apps and the
                 units each is limiting
    usage        per-app rates (bytes/s) and totals (bytes) since start
    get-config   the config, as a version 2 dict
    set-config   args {"config": {...}} or {"yaml": "..."}: validate, save
                 and apply a new config
    reload       re-read the config file and apply it
    pause        stop shaping (traffic is still metered)
    resume       shape again

Rates and totals are bytes; limits in configs are tc rate strings, e.g.
``"800kbit"``, as in the config file.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any

VERSION = 1
MAX_LINE = 1 << 20  # bytes; longer requests or responses are refused

READ_COMMANDS = frozenset({"status", "usage", "get-config"})
WRITE_COMMANDS = frozenset({"set-config", "reload", "pause", "resume"})
COMMANDS = READ_COMMANDS | WRITE_COMMANDS

# Error codes
BAD_REQUEST = "bad-request"  # not JSON, wrong version, wrong types
UNKNOWN_COMMAND = "unknown-command"
PERMISSION_DENIED = "permission-denied"
INVALID_CONFIG = "invalid-config"
FAILED = "failed"  # the daemon couldn't carry it out


class ProtocolError(Exception):
    """An error response from the daemon, or a malformed message."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def encode(message: Mapping[str, Any]) -> bytes:
    return json.dumps(message, separators=(",", ":")).encode() + b"\n"


def decode(line: bytes) -> dict[str, Any]:
    """Parse one line into a JSON object; raise ProtocolError if it isn't one."""
    try:
        message = json.loads(line)
    except (UnicodeDecodeError, ValueError) as e:
        raise ProtocolError(BAD_REQUEST, f"not valid JSON: {e}") from None
    if not isinstance(message, dict):
        raise ProtocolError(BAD_REQUEST, "expected a JSON object")
    return message


def request(cmd: str, args: Mapping[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"v": VERSION, "cmd": cmd}
    if args:
        message["args"] = dict(args)
    return message


def ok(result: Any = None) -> dict[str, Any]:
    return {"ok": True, "result": result}


def error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


def call(
    cmd: str,
    args: Mapping[str, Any] | None = None,
    path: str | Path | None = None,
    timeout: float = 10.0,
) -> Any:
    """Send one request to the daemon and return its result.

    Raises ProtocolError for an error response, and OSError if the daemon
    can't be reached (e.g. FileNotFoundError or ConnectionRefusedError when
    it isn't running).
    """
    if path is None:
        from trafficcop.common import paths

        path = paths.get().socket
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(str(path))
        s.sendall(encode(request(cmd, args)))
        with s.makefile("rb") as f:
            line = f.readline(MAX_LINE + 1)
    if not line.endswith(b"\n"):
        raise ProtocolError(FAILED, "the daemon closed the connection")
    response = decode(line)
    if response.get("ok") is True:
        return response.get("result")
    err = response.get("error")
    if isinstance(err, dict):
        raise ProtocolError(str(err.get("code", FAILED)), str(err.get("message", "")))
    raise ProtocolError(FAILED, "malformed response from the daemon")
