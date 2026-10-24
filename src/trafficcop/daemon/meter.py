"""Turn the nftables counters into per-app traffic rates and totals.

Each configured app has an upload and a download counter in the nft table
(see ``nft.py``). The supervisor calls ``Meter.sample`` about once a second;
each call reads all the counters at once and returns, per app, the rate over
the interval since the previous sample and the total since the meter
started. This replaces nethogs, and includes UDP.

All values are in **bytes** (rates in bytes per second), unlike the config,
which stores limits in bits per second. Byte counts include IP headers, so
they can differ slightly from what the ISP reports.

Counters start from zero whenever ``nft.build_ruleset`` replaces the table
(on startup and config reloads). Create the ``Meter`` right after the table
is first loaded, and call ``Meter.restarted`` right after each rebuild, so
that each sample measures from the right baseline. As a fallback, a counter
that goes down is assumed to have restarted.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from trafficcop.daemon import nft

Counters = Mapping[str, tuple[int, int]]  # name -> (packets, bytes)


@dataclass(frozen=True)
class Usage:
    up_rate: float = 0.0  # bytes per second over the last interval
    down_rate: float = 0.0
    up_total: int = 0  # bytes since the meter started
    down_total: int = 0


class Meter:
    def __init__(
        self,
        marks: Mapping[str, int],
        read: Callable[[], Counters] = nft.read_counters,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._read = read
        self._clock = clock
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

    def sample(self) -> dict[str, Usage]:
        """Read the counters and return each configured app's usage.

        Raises ``nft.NftError`` if the table can't be read.
        """
        counters = self._read()
        now = self._clock()
        elapsed = now - self._last_time
        self._last_time = now

        usage = {}
        for name, mark in self._marks.items():
            up, down = (self._delta(c, counters) for c in nft.counter_names(mark))
            up_total, down_total = self._totals[name]
            self._totals[name] = (up_total + up, down_total + down)
            usage[name] = Usage(
                up / elapsed if elapsed > 0 else 0.0,
                down / elapsed if elapsed > 0 else 0.0,
                *self._totals[name],
            )
        return usage

    def _delta(self, counter: str, counters: Counters) -> int:
        current = counters.get(counter, (0, 0))[1]
        previous = self._last.get(counter, 0)
        self._last[counter] = current
        # A counter that went down was reset; count what it has since.
        return current - previous if current >= previous else current
