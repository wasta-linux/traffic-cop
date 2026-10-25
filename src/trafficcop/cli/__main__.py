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
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

from trafficcop import __version__
from trafficcop.common import config, paths, protocol
from trafficcop.common.display import (
    human_bytes,
    human_limit,
    human_rate,
    start_hint,
    state_text,
)
from trafficcop.common.i18n import _, ngettext

Call = Callable[[str, Mapping[str, Any] | None], Any]

EXIT_FAILED = 1
EXIT_UNREACHABLE = 3
_CLEAR = "\x1b[H\x1b[2J"


# --- Formatting -------------------------------------------------------------------


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
    state = state_text(status)
    lines = [
        f"traffic-copd {status['version']}: {state}",
        _("config: {path}").format(path=status["config"]),
        "",
    ]
    apps = status["apps"]
    if not apps:
        return "\n".join([*lines, _("No apps configured.")])
    rows = [[_("APP"), _("DOWN LIMIT"), _("UP LIMIT"), _("RUNNING")]]
    for app in apps:
        units = app["units"]
        running = (
            _("no")
            if not units
            else units[0]
            if len(units) == 1
            else ngettext("{n} unit", "{n} units", len(units)).format(n=len(units))
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
        return _("No apps configured.")
    rows = [[_("APP"), _("DOWN"), _("UP"), _("DOWN TOTAL"), _("UP TOTAL")]]
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="traffic-cop-ctl",
        description=_("Control and monitor the Traffic Cop daemon."),
    )
    parser.add_argument("--json", action="store_true", help=_("print raw JSON"))
    parser.add_argument(
        "--socket",
        type=Path,
        help=_("daemon socket (default: {path})").format(path=paths.get().socket),
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    sub.add_parser("status", help=_("daemon state and each app's limits")).set_defaults(
        run=cmd_status
    )
    rates = sub.add_parser("rates", help=_("current rates and totals"))
    rates.add_argument("-w", "--watch", action="store_true", help=_("keep updating"))
    rates.add_argument(
        "-n", "--interval", type=float, default=1.0, help=_("seconds between updates")
    )
    rates.set_defaults(run=cmd_rates)
    for name, text in (
        ("pause", _("stop shaping (metering continues)")),
        ("resume", _("start shaping again")),
        ("reload", _("re-read the config file")),
    ):
        sub.add_parser(name, help=text).set_defaults(run=cmd_simple(name))

    cfg = sub.add_parser("config", help=_("show or replace the config"))
    cfg_sub = cfg.add_subparsers(dest="action", required=True, metavar="ACTION")
    cfg_sub.add_parser("show", help=_("print the config as YAML")).set_defaults(
        run=cmd_config_show
    )
    cfg_set = cfg_sub.add_parser(
        "set", help=_("validate, apply and save a config file")
    )
    cfg_set.add_argument("file", help=_('YAML config file, or "-" for stdin'))
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
        if e.code == protocol.PERMISSION_DENIED:
            message = _(
                "'{command}' needs root or an administrator account (try sudo)"
            ).format(command=args.command)
        else:
            message = e.message  # from the daemon, in English
        print(f"traffic-cop-ctl: {message}", file=sys.stderr)
        return EXIT_FAILED
    except config.ConfigError as e:
        print(f"traffic-cop-ctl: {e}", file=sys.stderr)
        return EXIT_FAILED
    except (FileNotFoundError, ConnectionRefusedError) as e:
        if getattr(e, "filename", None) not in (None, str(socket_path)):
            print(f"traffic-cop-ctl: {e}", file=sys.stderr)  # e.g. config set FILE
            return EXIT_FAILED
        print(
            "traffic-cop-ctl: "
            + _(
                "can't reach the daemon at {path}; is it running?"
                " Start it with: {command}"
            ).format(path=socket_path, command=start_hint()),
            file=sys.stderr,
        )
        return EXIT_UNREACHABLE
    except OSError as e:
        print(f"traffic-cop-ctl: {e}", file=sys.stderr)
        return EXIT_FAILED
    return 0


if __name__ == "__main__":
    sys.exit(main())
