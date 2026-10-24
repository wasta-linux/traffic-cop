"""Find the cgroups that belong to each configured app.

Only two kinds of systemd unit are ever considered:

* **App units:** the units in each logged-in user's ``app.slice``, e.g.
  ``user.slice/user-1000.slice/user@1000.service/app.slice/
  snap.firefox.firefox-<uuid>.scope``. Desktops, snapd and flatpak start each
  launched app in its own unit there (terminal tabs get one too).
* **System services** named by a ``service`` match key, e.g.
  ``system.slice/snapd.service``.

Everything else (the desktop shell and session services, login session
scopes, kernel threads, unrelated system services) is out of reach, so a
broad match can't throttle the whole desktop.

What gets limited is a whole unit: every process in it shares the app's
limits, including processes in nested cgroups (nftables matches ancestors
via ``socket cgroupv2 level``). The result maps app names to unit cgroup
paths, ready for ``nft.build_ruleset`` or ``nft.build_output_update``.

Matching rules (see ``config.py`` for the format):

* An app matches a unit if any of its match entries does.
* An entry matches if all of its keys do. ``snap``, ``flatpak`` and ``scope``
  test an app unit's name, and ``service`` a system service's; ``exe``,
  ``name`` and ``cmdline`` must all hold for one process inside the unit.
* A unit claimed by an earlier app in the config isn't given to a later one.

Desktops that don't start apps in their own units leave them all in the
login session's scope, which is never matched; only snap and flatpak apps
are detected there.

This module only reads; the daemon polls ``Detector.scan`` and applies the
changes itself.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from trafficcop.common.config import App, Config, Match

log = logging.getLogger(__name__)

CGROUP_ROOT = Path("/sys/fs/cgroup")
PROC = Path("/proc")
APP_SLICES = "user.slice/user-*.slice/user@*.service/app.slice"
SYSTEM_SLICE = "system.slice"

_UNIT_SUFFIXES = (".scope", ".service")
_COMM_LEN = 15  # the kernel truncates /proc/<pid>/comm to this many chars


@dataclass(frozen=True)
class Process:
    pid: int
    name: str
    exe: str | None = None  # None if unreadable
    cmdline: tuple[str, ...] = ()


@dataclass(frozen=True)
class Unit:
    path: str  # cgroup path relative to the cgroup root, no leading slash
    system: bool  # a system service rather than an app unit
    processes: tuple[Process, ...] = ()

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class Skipped:
    """A unit an app matched but wasn't given."""

    app: str
    cgroup: str
    reason: str


@dataclass(frozen=True)
class Matches:
    cgroups: Mapping[str, frozenset[str]]  # app name -> unit cgroup paths
    skipped: frozenset[Skipped] = frozenset()


# --- Reading the system -------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="surrogateescape")


def _name(comm: str, cmdline: tuple[str, ...]) -> str:
    # comm is cut to 15 characters; recover the full name from argv[0] when
    # it's the same program, as psutil does.
    if len(comm) == _COMM_LEN and cmdline:
        base = os.path.basename(cmdline[0])
        if base.startswith(comm):
            return base
    return comm


def read_process(pid: int, proc: Path = PROC) -> Process | None:
    """Read one process, or return None if it has exited."""
    base = proc / str(pid)
    try:
        comm = _read(base / "comm").rstrip("\n")
        raw = _read(base / "cmdline")
    except OSError:
        return None
    cmdline = tuple(raw.split("\0")[:-1]) if raw.endswith("\0") else (raw,)
    if cmdline == ("",):
        cmdline = ()
    try:
        exe: str | None = os.readlink(base / "exe")
    except OSError:
        exe = None
    else:
        # A program upgraded while running shows its old, deleted file.
        exe = exe.removesuffix(" (deleted)")
    return Process(pid, _name(comm, cmdline), exe, cmdline)


def _subdirs(path: Path) -> list[Path]:
    try:
        return [p for p in path.iterdir() if p.is_dir()]
    except OSError:
        return []  # removed while we were looking


def _units_under(slice_dir: Path) -> Iterator[Path]:
    """Yield the unit directories in a slice, descending into nested slices."""
    for d in _subdirs(slice_dir):
        if d.name.endswith(_UNIT_SUFFIXES):
            yield d
        elif d.name.endswith(".slice"):
            yield from _units_under(d)


def _pids(unit_dir: Path) -> list[int]:
    """PIDs in a unit's cgroup and any cgroups nested inside it."""
    pids = []
    try:
        pids += [int(line) for line in _read(unit_dir / "cgroup.procs").split()]
    except OSError:
        pass
    for d in _subdirs(unit_dir):
        pids += _pids(d)
    return pids


def read_units(
    services: Iterable[str] = (),
    cgroup_root: Path = CGROUP_ROOT,
    proc: Path = PROC,
) -> list[Unit]:
    """Read every app unit, plus the named system services that are running."""
    found = [(d, False) for s in cgroup_root.glob(APP_SLICES) for d in _units_under(s)]
    wanted = set(services)
    if wanted:
        found += [
            (d, True)
            for d in _units_under(cgroup_root / SYSTEM_SLICE)
            if d.name in wanted
        ]
    units = []
    for d, system in found:
        procs = (read_process(pid, proc) for pid in _pids(d))
        units.append(
            Unit(
                str(d.relative_to(cgroup_root)),
                system,
                tuple(p for p in procs if p is not None),
            )
        )
    return units


# --- Matching -----------------------------------------------------------------


def _systemd_escape(text: str) -> str:
    # Enough of systemd's unit-name escaping for app IDs: "-" is reserved.
    return text.replace("-", "\\x2d")


def _unit_matches(entry: Match, unit: Unit) -> bool:
    if unit.system:
        return entry.service == unit.name
    if entry.service is not None:
        return False
    if entry.snap is not None and not unit.name.startswith(f"snap.{entry.snap}."):
        return False
    if entry.flatpak is not None and not unit.name.startswith(
        f"app-flatpak-{_systemd_escape(entry.flatpak)}-"
    ):
        return False
    return entry.scope is None or entry.scope.fullmatch(unit.name) is not None


def _process_matches(entry: Match, p: Process) -> bool:
    if entry.exe is not None and p.exe != entry.exe:
        return False
    if entry.name is not None and not entry.name.fullmatch(p.name):
        return False
    return entry.cmdline is None or bool(entry.cmdline.fullmatch(" ".join(p.cmdline)))


def _entry_matches(entry: Match, unit: Unit) -> bool:
    if not _unit_matches(entry, unit):
        return False
    if entry.exe is None and entry.name is None and entry.cmdline is None:
        return True
    return any(_process_matches(entry, p) for p in unit.processes)


def app_matches(app: App, unit: Unit) -> bool:
    return any(_entry_matches(entry, unit) for entry in app.match)


def match(config: Config, units: Iterable[Unit]) -> Matches:
    """Assign units to the config's apps."""
    owner: dict[str, str] = {}  # unit cgroup -> app name
    found: dict[str, set[str]] = {}
    skipped: set[Skipped] = set()
    for unit in sorted(units, key=lambda u: u.path):
        for app in config.apps:
            if not app_matches(app, unit):
                continue
            if unit.path in owner:
                reason = f"already matched by {owner[unit.path]}"
                skipped.add(Skipped(app.name, unit.path, reason))
            else:
                owner[unit.path] = app.name
                found.setdefault(app.name, set()).add(unit.path)
    return Matches(
        cgroups={name: frozenset(cgs) for name, cgs in found.items()},
        skipped=frozenset(skipped),
    )


# --- Polling ------------------------------------------------------------------


@dataclass
class Detector:
    """Re-reads the units on demand and reports when the matches change."""

    config: Config
    cgroup_root: Path = CGROUP_ROOT
    proc: Path = PROC
    current: Mapping[str, frozenset[str]] = field(default_factory=dict, init=False)
    _reported: set[Skipped] = field(default_factory=set, init=False)

    def scan(self) -> bool:
        """Re-read units and update ``current``. Return True if it changed.

        Each skipped unit is logged once, the first time it's seen.
        """
        services = {m.service for a in self.config.apps for m in a.match if m.service}
        units = read_units(services, self.cgroup_root, self.proc)
        result = match(self.config, units)
        for s in sorted(result.skipped - self._reported, key=str):
            log.warning("%s: not limiting %s: %s", s.app, s.cgroup, s.reason)
        self._reported = set(result.skipped)
        changed = result.cgroups != self.current
        self.current = result.cgroups
        return changed
