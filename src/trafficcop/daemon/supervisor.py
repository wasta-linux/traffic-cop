"""The daemon's main loop: the only module that coordinates the others.

``Supervisor.start`` clears anything a previous run left behind, loads the
nft table for the config's apps and shapes the WAN interface. After that,
``run`` calls three periodic checks until asked to stop:

* ``check_apps`` (every ``DETECT_INTERVAL``): re-scan for the apps' units and
  rewrite the nft output chain when they change. The counters survive.
* ``check_wan`` (every ``WAN_INTERVAL``): look up the WAN interface again and
  move the tc setup when it changes, or retry it if it failed.
* ``sample`` (every ``METER_INTERVAL``): read the counters into ``usage``.

``Supervisor.stop`` removes everything again. A failure in one check is
logged and doesn't stop the others; the next run of that check tries again.

With no route to the internet the nft table is still loaded, so traffic is
metered; shaping starts when a WAN interface appears.

Everything privileged goes through ``System``, so tests can replace it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from pathlib import Path

from trafficcop.common.config import Config
from trafficcop.daemon import nft, tc
from trafficcop.daemon.detector import PROC, Detector
from trafficcop.daemon.meter import Counters, Meter, Usage
from trafficcop.daemon.netdev import WanWatcher, wan_device

log = logging.getLogger(__name__)

DETECT_INTERVAL = 2.0
WAN_INTERVAL = 5.0
METER_INTERVAL = 1.0
_NFT_ATTEMPTS = 3  # a unit can vanish between scanning and loading its rule


class System:
    """The privileged operations the supervisor needs (root; tests fake it)."""

    def apply_nft(self, script: str) -> None:
        nft.apply(script)

    def read_counters(self) -> Counters:
        return nft.read_counters()

    def setup_tc(self, config: Config, marks: Mapping[str, int], wan: str) -> None:
        tc.apply_setup(config, marks, wan)

    def teardown_tc(self, wan: str) -> None:
        tc.apply(tc.build_teardown(wan), check=False)

    def teardown_nft(self) -> None:
        try:
            nft.apply(nft.build_teardown())
        except nft.NftError as e:
            log.warning("couldn't remove the nft table: %s", e)

    def wan_device(self) -> str | None:
        return wan_device()


def _unit_name(cgroup: str) -> str:
    return cgroup.rsplit("/", 1)[-1]


class Supervisor:
    def __init__(self, config: Config, system: System | None = None, proc: Path = PROC):
        self.config = config
        self.system = system or System()
        self.marks = nft.assign_marks(config)
        self.detector = Detector(config, proc)
        self.wan = WanWatcher(self.system.wan_device)
        self.meter = Meter(self.marks, self.system.read_counters)
        self.usage: dict[str, Usage] = {}
        self.shaped: str | None = None  # interface the tc setup is on
        self._tc_error: str | None = None

    # --- Lifecycle ---------------------------------------------------------------

    def start(self) -> None:
        """Clear leftovers, load the nft table and shape the WAN interface.

        Raises ``nft.NftError`` if the table can't be loaded.
        """
        self.system.teardown_nft()
        self.wan.poll()
        if self.wan.current:
            self.system.teardown_tc(self.wan.current)
        self._load_table()
        self.meter.restarted(self.marks)
        for app, cgroups in sorted(self.detector.current.items()):
            for cgroup in sorted(cgroups):
                log.info("%s: limiting %s", app, _unit_name(cgroup))
        self._shape()

    def stop(self) -> None:
        if self.shaped:
            self.system.teardown_tc(self.shaped)
            self.shaped = None
        self.system.teardown_nft()
        log.info("removed all rules")

    # --- Periodic checks -----------------------------------------------------------

    def check_apps(self) -> None:
        """Re-scan for the apps' units and update the nft rules if they changed."""
        before = self.detector.current
        if not self.detector.scan():
            return
        for attempt in range(1, _NFT_ATTEMPTS + 1):
            try:
                self.system.apply_nft(
                    nft.build_output_update(self.marks, self.detector.current)
                )
                break
            except nft.NftError as e:
                if attempt == _NFT_ATTEMPTS:
                    log.error("couldn't update the rules for new or ended apps: %s", e)
                    # Report a change again next time, so the update is retried.
                    self.detector.current = before
                    return
                self.detector.scan()
        self._log_changes(before, self.detector.current)

    def check_wan(self) -> None:
        """Follow the WAN interface, and retry shaping if it failed before."""
        before = self.wan.current
        if self.wan.poll():
            log.info(
                "WAN interface: %s -> %s", before or "none", self.wan.current or "none"
            )
        if self.wan.current == self.shaped and self._tc_error is None:
            return
        if self.shaped:
            self.system.teardown_tc(self.shaped)
            self.shaped = None
        self._shape()

    def sample(self) -> None:
        try:
            self.usage = self.meter.sample()
        except nft.NftError as e:
            log.error("couldn't read the traffic counters: %s", e)
            return
        for app, u in self.usage.items():
            if u.up_rate or u.down_rate:
                log.debug(
                    "%s: up %.1f kB/s, down %.1f kB/s",
                    app,
                    u.up_rate / 1000,
                    u.down_rate / 1000,
                )

    # --- Helpers -------------------------------------------------------------------

    def _load_table(self) -> None:
        for attempt in range(1, _NFT_ATTEMPTS + 1):
            self.detector.scan()
            try:
                self.system.apply_nft(
                    nft.build_ruleset(self.marks, self.detector.current)
                )
                return
            except nft.NftError:
                if attempt == _NFT_ATTEMPTS:
                    raise

    def _shape(self) -> None:
        wan = self.wan.current
        if wan is None:
            self._tc_error = None
            log.warning("no route to the internet: metering only until there is one")
            return
        try:
            self.system.setup_tc(self.config, self.marks, wan)
        except tc.TcError as e:
            if str(e) != self._tc_error:  # don't repeat the same error every poll
                log.error("couldn't shape traffic on %s: %s", wan, e)
            self._tc_error = str(e)
            return
        self.shaped = wan
        self._tc_error = None
        log.info("shaping traffic on %s", wan)

    @staticmethod
    def _log_changes(
        before: Mapping[str, frozenset[str]], after: Mapping[str, frozenset[str]]
    ) -> None:
        for app in sorted(set(before) | set(after)):
            old, new = before.get(app, frozenset()), after.get(app, frozenset())
            for cgroup in sorted(new - old):
                log.info("%s: limiting %s", app, _unit_name(cgroup))
            for cgroup in sorted(old - new):
                log.info("%s: %s ended", app, _unit_name(cgroup))


# --- Running ---------------------------------------------------------------------


async def _every(
    interval: float, check: Callable[[], None], stop: asyncio.Event
) -> None:
    while not stop.is_set():
        try:
            check()
        except Exception:
            log.exception("unexpected error in %s", check.__name__)
        try:
            await asyncio.wait_for(stop.wait(), interval)
        except asyncio.TimeoutError:  # an alias of TimeoutError from 3.11
            pass


async def run(supervisor: Supervisor, stop: asyncio.Event) -> None:
    """Start, run the periodic checks until ``stop`` is set, then clean up."""
    supervisor.start()
    try:
        await asyncio.gather(
            _every(DETECT_INTERVAL, supervisor.check_apps, stop),
            _every(WAN_INTERVAL, supervisor.check_wan, stop),
            _every(METER_INTERVAL, supervisor.sample, stop),
        )
    finally:
        supervisor.stop()
