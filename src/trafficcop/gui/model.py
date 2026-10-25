"""What the main window shows, worked out from the daemon's answers.

``build_view`` turns the latest ``status`` and ``usage`` results (or the
error from the last attempt) into plain strings and numbers, so the window
only has to put them on screen. No tkinter here, so it's testable without a
display.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from trafficcop.common.display import (
    human_bytes,
    human_rate,
    rate_of_limit,
    start_hint,
    state_text,
)
from trafficcop.common.i18n import _

# Whether a rate is at its limit, for colouring the bar: "full" means the
# limit is (just about) all in use, so it's holding the app back. While the
# daemon is paused nothing is limited, so every bar is "ok".
Level = Literal["ok", "full"]
FULL = 0.95


@dataclass(frozen=True)
class Direction:
    text: str  # e.g. "45.8 / 70 kB/s", or "1.2 kB/s" when unlimited
    fraction: float | None  # share of the limit in use (0-1); None if unlimited
    total: str  # e.g. "31.0 MB"
    level: Level | None = None  # None if unlimited


def level(fraction: float) -> Level:
    return "full" if fraction >= FULL else "ok"


@dataclass(frozen=True)
class AppRow:
    name: str
    instances: int  # units running; 0 = not running
    down: Direction
    up: Direction

    @property
    def running(self) -> bool:
        return self.instances > 0


@dataclass(frozen=True)
class View:
    connected: bool
    headline: str
    detail: str
    paused: bool | None  # None when unknown (daemon unreachable)
    rows: tuple[AppRow, ...] = ()


def _direction(rate: float, total: int, limit: int | None, paused: bool) -> Direction:
    if limit is None:
        return Direction(human_rate(rate), None, human_bytes(total))
    fraction = min(rate / (limit / 8), 1.0) if limit > 0 else 1.0
    return Direction(
        rate_of_limit(rate, limit),
        fraction,
        human_bytes(total),
        "ok" if paused else level(fraction),
    )


def _row(
    app: Mapping[str, Any], usage: Mapping[str, Any] | None, paused: bool
) -> AppRow:
    u = usage or {"up_rate": 0.0, "down_rate": 0.0, "up_total": 0, "down_total": 0}
    return AppRow(
        name=app["name"],
        instances=len(app["units"]),
        down=_direction(u["down_rate"], u["down_total"], app["download"], paused),
        up=_direction(u["up_rate"], u["up_total"], app["upload"], paused),
    )


def build_view(
    status: Mapping[str, Any] | None,
    usage: Mapping[str, Any] | None,
    error: Exception | None = None,
) -> View:
    """The window's contents. ``error`` is set if the daemon couldn't be reached."""
    if error is not None or status is None:
        return View(
            connected=False,
            headline=_("Traffic Cop isn't running"),
            detail=_("Start it with: {command}").format(command=start_hint()),
            paused=None,
        )
    apps_usage = (usage or {}).get("apps", {})
    state = state_text(status)
    paused = bool(status["paused"])
    return View(
        connected=True,
        headline=state[0].upper() + state[1:],
        detail=f"Traffic Cop {status['version']}",
        paused=paused,
        rows=tuple(
            _row(app, apps_usage.get(app["name"]), paused) for app in status["apps"]
        ),
    )
