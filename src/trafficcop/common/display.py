"""Wording and number formatting shared by traffic-cop-ctl and the GUI.

Rates and totals from the daemon are bytes; limits are bits per second (as
in the config). Both are shown in decimal byte units, so a ``70kbps`` limit
and a rate of 70 000 bytes/s both read "70.0 kB/s" ("70,0 ko/s" in French).
Units, wording and the decimal separator are translated; see ``i18n.py``.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from trafficcop.common import paths
from trafficcop.common.i18n import _, number


def _rate_units() -> tuple[tuple[str, float], ...]:
    return ((_("MB/s"), 1e6), (_("kB/s"), 1e3), (_("B/s"), 1.0))


def _byte_units() -> tuple[tuple[str, float], ...]:
    return ((_("GB"), 1e9), (_("MB"), 1e6), (_("kB"), 1e3), (_("B"), 1.0))


def _scaled(value: float, units: tuple[tuple[str, float], ...]) -> str:
    unit, size = next((u, s) for u, s in units if value >= s or s == 1.0)
    return f"{number(value / size, 0 if size == 1.0 else 1)} {unit}"


def human_rate(bytes_per_second: float) -> str:
    """Format a rate in bytes per second, e.g. ``"70.0 kB/s"``."""
    return _scaled(bytes_per_second, _rate_units())


def human_bytes(n: float) -> str:
    return _scaled(n, _byte_units())


def _trim(value: float) -> str:
    """One decimal place, dropped when it's zero: 70.0 -> "70", 62.5 -> "62.5"."""
    text = f"{value:.1f}"
    return number(value, 0) if text.endswith(".0") else number(value)


def rate_of_limit(bytes_per_second: float, limit_bits: int) -> str:
    """A rate against its limit in one unit, e.g. ``"45.8 / 70 kB/s"``.

    The unit is chosen from the limit, so a bar's numbers are directly
    comparable (``"0.9 / 62.5 kB/s"``, not ``"900 B/s of 62.5 kB/s"``), and
    there are no words to translate.
    """
    limit = limit_bits / 8
    unit, size = next((u, s) for u, s in _rate_units() if limit >= s or s == 1.0)
    if size == 1.0:
        return f"{number(bytes_per_second, 0)} / {number(limit, 0)} {unit}"
    return f"{number(bytes_per_second / size)} / {_trim(limit / size)} {unit}"


def human_limit(bits_per_second: int | None) -> str:
    """Format a limit (stored in bits per second) the way rates are shown."""
    return "-" if bits_per_second is None else human_rate(bits_per_second / 8)


def state_text(status: Mapping[str, Any]) -> str:
    """One line describing what the daemon is doing, from a status result."""
    if status["paused"]:
        return _("paused (not shaping; still metering)")
    if status["shaping"]:
        return _("shaping traffic on {interface}").format(interface=status["shaping"])
    if status["wan"]:
        return _(
            "not shaping: setting up tc on {interface} failed (see the log)"
        ).format(interface=status["wan"])
    return _("not shaping: no route to the internet")


def start_hint(env: Mapping[str, str] = os.environ, argv0: str = sys.argv[0]) -> str:
    """How to start the daemon (a command, so not translated)."""
    if paths.get(env).snap:
        name = env.get("SNAP_INSTANCE_NAME") or env.get("SNAP_NAME", "traffic-cop")
        return f"sudo snap start {name}"
    # A development checkout: traffic-copd sits next to this script in the venv.
    daemon = Path(argv0).parent / "traffic-copd"
    return f"sudo {daemon}" if daemon.is_file() else "sudo traffic-copd"
