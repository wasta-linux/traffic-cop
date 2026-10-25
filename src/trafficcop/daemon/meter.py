"""Turn the traffic counters into per-app rates and totals.

Each configured app has an upload and a download counter in the nft table
(see ``nft.py``). The supervisor calls ``Meter.sample`` about once a second;
each call reads all the counters at once and returns, per app, its rates
and its totals since the meter started. This replaces nethogs, and includes
UDP.

Rates are smoothed: an exponential moving average with a time constant of
``SMOOTHING`` seconds. Shaped traffic is bursty (connections ramp up, CAKE
drops, senders back off), so per-second rates swing widely even while the
average holds at the limit. Totals are exact.

For apps with a limit, the CAKE leaves count what the app costs the
connection better than the nft counters do (see ``tc.py``): for uploads,
what was actually sent; for downloads, everything that arrived, including
what CAKE dropped to hold the app to its limit. When the counters ``read``
returns include an app's CAKE counter (``tc.upload_counter_name`` /
``tc.download_counter_name``), that's used instead of the nft one.

All values are in **bytes** (rates in bytes per second), unlike the config,
which stores limits in bits per second. Byte counts include IP headers, so
they can differ slightly from what the ISP reports.

Counters start from zero whenever ``nft.build_ruleset`` replaces the table
(on startup and config reloads). Create the ``Meter`` right after the table
is first loaded, and call ``Meter.restarted`` right after each rebuild, so
that each sample measures from the right baseline. Likewise, call
``Meter.forget`` with the CAKE counter names after rebuilding the tc setup.
As a fallback, a counter that goes down is assumed to have restarted.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from trafficcop.daemon import nft, tc

Counters = Mapping[str, tuple[int, int]]  # name -> (packets, bytes)
SMOOTHING = 3.0  # seconds; the rates' time constant


@dataclass(frozen=True)
class Usage:
    up_rate: float = 0.0  # bytes per second, smoothed
    down_rate: float = 0.0
    up_total: int = 0  # bytes since the meter started
    down_total: int = 0


class Meter:
    def __init__(
        self,
        marks: Mapping[str, int],
        read: Callable[[], Counters] = nft.read_counters,
        clock: Callable[[], float] = time.monotonic,
        smoothing: float = SMOOTHING,
    ):
        """``smoothing`` is the rates' time constant in seconds; 0 turns it off."""
        self._read = read
        self._clock = clock
        self._smoothing = smoothing
        self._rates: dict[str, tuple[float, float]] = {}  # app -> (up, down)
        self._marks: dict[str, int] = {}
        self._last: dict[str, int] = {}  # counter name -> bytes at last sample
        self._last_time = 0.0
        self._totals: dict[str, tuple[int, int]] = {}  # app -> (up, down)
        self.restarted(marks)

    def restarted(self, marks: Mapping[str, int]) -> None:
        """Note that the nft table was just (re)built with ``marks``.

        Counters are taken to be zero now. Totals are kept for apps that are
        still configured.
        """
        self._marks = dict(marks)
        self._last = {}
        self._last_time = self._clock()
        self._totals = {name: self._totals.get(name, (0, 0)) for name in marks}
        self._rates = {n: r for n, r in self._rates.items() if n in marks}

    def forget(self, counters: Iterable[str]) -> None:
        """Take these counters to be zero now (e.g. tc was just rebuilt)."""
        for name in counters:
            self._last.pop(name, None)

    def sample(self) -> dict[str, Usage]:
        """Read the counters and return each configured app's usage.

        Raises ``nft.NftError`` if the table can't be read.
        """
        counters = self._read()
        now = self._clock()
        elapsed = now - self._last_time
        self._last_time = now

        # Keep a baseline for every counter, used or not, so that switching an
        # app between its nft and CAKE upload counters doesn't count twice.
        deltas = {name: self._delta(name, counters) for name in counters}

        usage = {}
        for name, mark in self._marks.items():
            nft_up, nft_down = nft.counter_names(mark)
            shaped_up = tc.upload_counter_name(mark)
            shaped_down = tc.download_counter_name(mark)
            up = deltas[shaped_up] if shaped_up in counters else deltas.get(nft_up, 0)
            if shaped_down in counters:
                down = deltas[shaped_down]
            else:
                down = deltas.get(nft_down, 0)
            up_total, down_total = self._totals[name]
            self._totals[name] = (up_total + up, down_total + down)
            if elapsed > 0:
                self._rates[name] = self._smooth(
                    name, (up / elapsed, down / elapsed), elapsed
                )
            rates = self._rates.get(name, (0.0, 0.0))
            usage[name] = Usage(*rates, *self._totals[name])
        return usage

    def _smooth(
        self, name: str, now: tuple[float, float], elapsed: float
    ) -> tuple[float, float]:
        before = self._rates.get(name)
        if before is None or self._smoothing <= 0:
            return now  # nothing to smooth against yet
        weight = 1 - math.exp(-elapsed / self._smoothing)
        up = before[0] + weight * (now[0] - before[0])
        down = before[1] + weight * (now[1] - before[1])
        return (up, down)

    def _delta(self, counter: str, counters: Counters) -> int:
        current = counters.get(counter, (0, 0))[1]
        previous = self._last.get(counter, 0)
        self._last[counter] = current
        # A counter that went down was reset; count what it has since.
        return current - previous if current >= previous else current
