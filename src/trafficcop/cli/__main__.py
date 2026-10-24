"""traffic-cop-ctl: control and monitor traffic-copd from the command line.

    traffic-cop-ctl status            daemon state and each app's limits
    traffic-cop-ctl rates [-w]        current rates and totals (-w: keep updating)
    traffic-cop-ctl pause | resume    stop or restart shaping (metering continues)
    traffic-cop-ctl reload            re-read the config file
    traffic-cop-ctl config show       print the config as YAML
    traffic-cop-ctl config set FILE   validate, apply and save a config ("-": stdin)

Reading needs no privileges; changing anything needs root or membership of
an admin group (sudo, admin or wheel). ``--json`` prints the daemon's raw
answer instead, for scripts.

Exit codes: 0 success, 1 the daemon refused or failed, 2 bad usage,
3 the daemon couldn't be reached.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

from trafficcop import __version__
from trafficcop.common import config, paths, protocol

Call = Callable[[str, Mapping[str, Any] | None], Any]

EXIT_FAILED = 1
EXIT_UNREACHABLE = 3
_CLEAR = "\x1b[H\x1b[2J"


# --- Formatting -------------------------------------------------------------------


def human_rate(bytes_per_second: float) -> str:
    """Format a rate in bytes per second, e.g. ``"70.0 kB/s"``."""
    for unit, size in (("MB/s", 1e6), ("kB/s", 1e3)):
        if bytes_per_second >= size:
            return f"{bytes_per_second / size:.1f} {unit}"
    return f"{bytes_per_second:.0f} B/s"


def human_bytes(n: float) -> str:
    for unit, size in (("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n:.0f} B"


def human_limit(bits_per_second: int | None) -> str:
    """Format a limit (stored in bits per second) the way rates are shown."""
    return "-" if bits_per_second is None else human_rate(bits_per_second / 8)


def table(rows: Sequence[Sequence[str]], right: Sequence[int] = ()) -> str:
    """Align rows into columns; columns in ``right`` are right-aligned."""
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines = []
    for row in rows:
        cells = [
            cell.rjust(w) if i in right else cell.ljust(w)
            for i, (cell, w) in enumerate(zip(row, widths, strict=True))
        ]
        lines.append("  ".join(cells).rstrip())
    return "\n".join(lines)


def format_status(status: Mapping[str, Any]) -> str:
    if status["paused"]:
        state = "paused (not shaping; still metering)"
    elif status["shaping"]:
        state = f"shaping traffic on {status['shaping']}"
    elif status["wan"]:
        state = f"not shaping: setting up tc on {status['wan']} failed (see the log)"
    else:
        state = "not shaping: no route to the internet"
    lines = [
        f"traffic-copd {status['version']}: {state}",
        f"config: {status['config']}",
        "",
    ]
    apps = status["apps"]
    if not apps:
        return "\n".join([*lines, "No apps configured."])
    rows = [["APP", "DOWN LIMIT", "UP LIMIT", "RUNNING"]]
    for app in apps:
        units = app["units"]
        running = (
            "no"
            if not units
            else units[0]
            if len(units) == 1
            else f"{len(units)} units"
        )
        rows.append(
            [
                app["name"],
                human_limit(app["download"]),
                human_limit(app["upload"]),
                running,
            ]
        )
    return "\n".join([*lines, table(rows, right=(1, 2))])


def format_rates(usage: Mapping[str, Any]) -> str:
    apps = usage["apps"]
    if not apps:
        return "No apps configured."
    rows = [["APP", "DOWN", "UP", "DOWN TOTAL", "UP TOTAL"]]
    for name, u in apps.items():
        rows.append(
            [
                name,
                human_rate(u["down_rate"]),
                human_rate(u["up_rate"]),
                human_bytes(u["down_total"]),
                human_bytes(u["up_total"]),
            ]
        )
    return table(rows, right=(1, 2, 3, 4))


# --- Commands -----------------------------------------------------------------------


def _print(
    result: Any, args: argparse.Namespace, text: Callable[[Any], str], out: TextIO
):
    print(json.dumps(result, indent=2) if args.json else text(result), file=out)


def cmd_status(call: Call, args: argparse.Namespace, out: TextIO) -> None:
    _print(call("status", None), args, format_status, out)


def cmd_rates(call: Call, args: argparse.Namespace, out: TextIO) -> None:
    if not args.watch:
        _print(call("usage", None), args, format_rates, out)
        return
    clear = out.isatty() and not args.json
    try:
        while True:
            usage = call("usage", None)
            if clear:
                out.write(_CLEAR)
            _print(usage, args, format_rates, out)
            if not clear:
                print(file=out)
            out.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


def cmd_simple(name: str) -> Callable[[Call, argparse.Namespace, TextIO], None]:
    def run(call: Call, args: argparse.Namespace, out: TextIO) -> None:
        _print(call(name, None), args, format_status, out)

    return run


def cmd_config_show(call: Call, args: argparse.Namespace, out: TextIO) -> None:
    cfg = call("get-config", None)
    if args.json:
        print(json.dumps(cfg, indent=2), file=out)
    else:
        out.write(config.dumps(config.parse(cfg)))


def cmd_config_set(call: Call, args: argparse.Namespace, out: TextIO) -> None:
    if args.file == "-":
        text = sys.stdin.read()
    else:
        text = Path(args.file).read_text(encoding="utf-8")
    config.loads(text, source=args.file)  # check locally first, for a clearer error
    _print(call("set-config", {"yaml": text}), args, format_status, out)


# --- Main ----------------------------------------------------------------------------


def start_hint(env: Mapping[str, str] = os.environ, argv0: str = sys.argv[0]) -> str:
    """How to start the daemon, for the "can't reach it" error."""
    if paths.get(env).snap:
        name = env.get("SNAP_INSTANCE_NAME") or env.get("SNAP_NAME", "traffic-cop")
        return f"sudo snap start {name}"
    # A development checkout: traffic-copd sits next to this script in the venv.
    daemon = Path(argv0).parent / "traffic-copd"
    return f"sudo {daemon}" if daemon.is_file() else "sudo traffic-copd"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="traffic-cop-ctl",
        description="Control and monitor the Traffic Cop daemon.",
    )
    parser.add_argument("--json", action="store_true", help="print raw JSON")
    parser.add_argument(
        "--socket", type=Path, help=f"daemon socket (default: {paths.get().socket})"
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    sub.add_parser("status", help="daemon state and each app's limits").set_defaults(
        run=cmd_status
    )
    rates = sub.add_parser("rates", help="current rates and totals")
    rates.add_argument("-w", "--watch", action="store_true", help="keep updating")
    rates.add_argument(
        "-n", "--interval", type=float, default=1.0, help="seconds between updates"
    )
    rates.set_defaults(run=cmd_rates)
    for name, text in (
        ("pause", "stop shaping (metering continues)"),
        ("resume", "start shaping again"),
        ("reload", "re-read the config file"),
    ):
        sub.add_parser(name, help=text).set_defaults(run=cmd_simple(name))

    cfg = sub.add_parser("config", help="show or replace the config")
    cfg_sub = cfg.add_subparsers(dest="action", required=True, metavar="ACTION")
    cfg_sub.add_parser("show", help="print the config as YAML").set_defaults(
        run=cmd_config_show
    )
    cfg_set = cfg_sub.add_parser("set", help="validate, apply and save a config file")
    cfg_set.add_argument("file", help='YAML config file, or "-" for stdin')
    cfg_set.set_defaults(run=cmd_config_set)
    return parser


def main(
    argv: Sequence[str] | None = None,
    call: Callable[..., Any] = protocol.call,
    out: TextIO | None = None,
) -> int:
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    socket_path = args.socket or paths.get().socket

    def call_daemon(cmd: str, cmd_args: Mapping[str, Any] | None) -> Any:
        return call(cmd, cmd_args, socket_path)

    try:
        args.run(call_daemon, args, out)
    except protocol.ProtocolError as e:
        hint = ""
        if e.code == protocol.PERMISSION_DENIED:
            hint = " (try sudo)"
        print(f"traffic-cop-ctl: {e.message}{hint}", file=sys.stderr)
        return EXIT_FAILED
    except config.ConfigError as e:
        print(f"traffic-cop-ctl: {e}", file=sys.stderr)
        return EXIT_FAILED
    except (FileNotFoundError, ConnectionRefusedError) as e:
        if getattr(e, "filename", None) not in (None, str(socket_path)):
            print(f"traffic-cop-ctl: {e}", file=sys.stderr)  # e.g. config set FILE
            return EXIT_FAILED
        print(
            f"traffic-cop-ctl: can't reach the daemon at {socket_path};"
            f" is it running? Start it with: {start_hint()}",
            file=sys.stderr,
        )
        return EXIT_UNREACHABLE
    except OSError as e:
        print(f"traffic-cop-ctl: {e}", file=sys.stderr)
        return EXIT_FAILED
    return 0


if __name__ == "__main__":
    sys.exit(main())
