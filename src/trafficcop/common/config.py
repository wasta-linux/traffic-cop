"""Traffic Cop configuration: schema, loading, validation and rate parsing.

Config format, version 2::

    version: 2
    apps:
      "Zoom":                     # display name, used in status and logs
        download: 70kbps          # optional; omit for no limit
        upload: 35kbps            # optional; omit for no limit
        match:                    # required; the app matches if ANY entry does
          - exe: /opt/zoom/zoom   # an entry matches if ALL its keys do
          - snap: zoom-client

An app without limits is still detected and metered, just not shaped.

Matching selects a systemd unit, not a single process: every process in a
matched unit shares the app's limits. Units are the apps users launch (in
each user's ``app.slice``), plus system services named with ``service``.
Match keys:

    exe      exact executable path of a process in the unit
    name     regex on a process name
    cmdline  regex on a process's full command line (joined with spaces)
    snap     snap name (unit ``snap.<name>.*``)
    flatpak  flatpak app ID (unit ``app-flatpak-<id>-*``)
    scope    regex on the unit name, e.g. ``app-gnome-Zoom-\\d+\\.scope``
    service  a system service, e.g. ``snapd.service`` (".service" optional);
             can't be combined with snap, flatpak or scope

Regexes must match the whole value (``re.fullmatch``).

Rates use tc units, which are case-sensitive here: ``kbit`` is kilobits per
second but ``kbps`` is kilo*bytes* per second. A unit is required. Parsed
rates are stored as integer bits per second.

Version 1 files (the TrafficToll format, with a ``processes`` key) are
migrated when loaded. Global limits, minimums and priorities have no
equivalent yet, so they are dropped with a warning.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from yaml.constructor import ConstructorError

log = logging.getLogger(__name__)

VERSION = 2
DEFAULT_CONFIG_NAME = "traffic-cop.yaml.default"
MAX_APPS = 63  # one per slot in the packet-mark bits the daemon reserves (nft.py)

MATCH_KEYS = ("exe", "name", "cmdline", "snap", "flatpak", "scope", "service")
_APP_UNIT_KEYS = ("snap", "flatpak", "scope")  # only for units in app.slice
_REGEX_KEYS = frozenset({"name", "cmdline", "scope"})
_APP_KEYS = ("download", "upload", "match")
_TOP_KEYS = ("version", "apps")

_V1_GLOBAL_KEYS = (
    "download",
    "upload",
    "download-minimum",
    "upload-minimum",
    "download-priority",
    "upload-priority",
)
_V1_APP_KEYS = ("download", "upload", "download-priority", "upload-priority", "match")
_V1_MATCH_KEYS = ("exe", "name", "cmdline")


class ConfigError(ValueError):
    """The config could not be read, parsed or validated."""


# --- Rates --------------------------------------------------------------------

_PREFIXES = {
    "": 1,
    "k": 10**3,
    "m": 10**6,
    "g": 10**9,
    "t": 10**12,
    "ki": 2**10,
    "mi": 2**20,
    "gi": 2**30,
    "ti": 2**40,
}
# unit -> bits per second
_UNITS = {p + "bit": m for p, m in _PREFIXES.items()} | {
    p + "bps": m * 8 for p, m in _PREFIXES.items()
}
_RATE_RE = re.compile(r"(\d+(?:\.\d*)?|\.\d+)\s*([A-Za-z]+)")


def parse_rate(value: Any) -> int:
    """Parse a tc-style rate such as ``"800kbit"`` into bits per second."""
    if not isinstance(value, str):
        raise ConfigError(f"rate {value!r} needs a unit, e.g. '800kbit' or '100kbps'")
    m = _RATE_RE.fullmatch(value.strip())
    if not m:
        raise ConfigError(
            f"invalid rate {value!r}: expected a number and a unit, "
            "e.g. '800kbit' or '100kbps'"
        )
    number, unit = m.groups()
    if unit not in _UNITS:
        hint = ""
        if unit.lower() in _UNITS:
            hint = (
                f"; units are lowercase, and '{unit.lower()}' means "
                + ("bytes" if unit.lower().endswith("bps") else "bits")
                + " per second"
            )
        raise ConfigError(
            f"unknown unit {unit!r} in rate {value!r}{hint} "
            "(use bit, kbit, mbit, gbit, bps, kbps, mbps, kibps, ...)"
        )
    bits = round(float(number) * _UNITS[unit])
    if bits <= 0:
        raise ConfigError(f"rate {value!r} must be greater than zero")
    return bits


def format_rate(bits: int) -> str:
    """Format bits per second as the shortest exact tc rate, e.g. ``"800kbit"``.

    The result is valid both in the config file and on a tc command line.
    """
    for unit, mult in (("gbit", 10**9), ("mbit", 10**6), ("kbit", 10**3)):
        if bits % mult == 0:
            return f"{bits // mult}{unit}"
    return f"{bits}bit"


# --- Model --------------------------------------------------------------------


@dataclass(frozen=True)
class Match:
    """One match entry. Every field that is set must match."""

    exe: str | None = None
    name: re.Pattern[str] | None = None
    cmdline: re.Pattern[str] | None = None
    snap: str | None = None
    flatpak: str | None = None
    scope: re.Pattern[str] | None = None
    service: str | None = None  # full unit name, e.g. "snapd.service"

    def to_dict(self) -> dict[str, str]:
        out = {}
        for key in MATCH_KEYS:
            value = getattr(self, key)
            if value is not None:
                out[key] = value.pattern if key in _REGEX_KEYS else value
        return out


@dataclass(frozen=True)
class App:
    name: str
    match: tuple[Match, ...]
    download: int | None = None  # bits per second; None = unlimited
    upload: int | None = None  # bits per second; None = unlimited

    @property
    def limited(self) -> bool:
        return self.download is not None or self.upload is not None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.download is not None:
            out["download"] = format_rate(self.download)
        if self.upload is not None:
            out["upload"] = format_rate(self.upload)
        out["match"] = [m.to_dict() for m in self.match]
        return out


@dataclass(frozen=True)
class Config:
    apps: tuple[App, ...] = ()

    def app(self, name: str) -> App | None:
        return next((a for a in self.apps if a.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        """Return a v2 config dict, suitable for the API or for writing YAML."""
        return {"version": VERSION, "apps": {a.name: a.to_dict() for a in self.apps}}


# --- Validation ---------------------------------------------------------------


def _check_keys(data: dict[Any, Any], allowed: tuple[str, ...], where: str) -> None:
    for key in data:
        if key not in allowed:
            raise ConfigError(
                f"{where}: unknown key {key!r} (expected one of: "
                + ", ".join(allowed)
                + ")"
            )


def _rate(data: dict[Any, Any], key: str, where: str) -> int | None:
    if data.get(key) is None:
        return None
    try:
        return parse_rate(data[key])
    except ConfigError as e:
        raise ConfigError(f"{where}.{key}: {e}") from None


def _parse_match(data: Any, where: str) -> Match:
    if not isinstance(data, dict) or not data:
        raise ConfigError(
            f"{where}: expected a mapping such as '- exe: /opt/zoom/zoom'"
        )
    _check_keys(data, MATCH_KEYS, where)
    fields: dict[str, Any] = {}
    for key, value in data.items():
        if not isinstance(value, str) or not value:
            raise ConfigError(f"{where}.{key}: expected a non-empty string")
        if key in _REGEX_KEYS:
            try:
                fields[key] = re.compile(value)
            except re.error as e:
                raise ConfigError(
                    f"{where}.{key}: invalid regex {value!r}: {e}"
                ) from None
        elif key == "exe" and not value.startswith("/"):
            raise ConfigError(f"{where}.exe: expected an absolute path, got {value!r}")
        elif key == "service":
            if "/" in value:
                raise ConfigError(
                    f"{where}.service: expected a unit name, got {value!r}"
                )
            fields[key] = value if value.endswith(".service") else value + ".service"
        else:
            fields[key] = value
    if "service" in fields and any(k in fields for k in _APP_UNIT_KEYS):
        raise ConfigError(
            f"{where}: 'service' can't be combined with " + ", ".join(_APP_UNIT_KEYS)
        )
    return Match(**fields)


def _parse_app(name: Any, data: Any) -> App:
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"apps: app name {name!r} must be a non-empty string")
    where = f"apps.{name}"
    if not isinstance(data, dict):
        raise ConfigError(f"{where}: expected a mapping with 'match' and limits")
    _check_keys(data, _APP_KEYS, where)
    match = data.get("match")
    if not isinstance(match, list) or not match:
        raise ConfigError(f"{where}.match: expected a non-empty list of match entries")
    return App(
        name=name,
        match=tuple(
            _parse_match(m, f"{where}.match[{i}]") for i, m in enumerate(match)
        ),
        download=_rate(data, "download", where),
        upload=_rate(data, "upload", where),
    )


def parse(data: Any) -> Config:
    """Validate a config dict (from YAML or the API) and build a Config.

    Version 1 dicts are migrated first.
    """
    if data is None:
        # Most likely a truncated file. Refuse it rather than silently lift
        # every limit; 'apps: {}' is the explicit way to configure nothing.
        raise ConfigError("config is empty")
    if not isinstance(data, dict):
        raise ConfigError("config: expected a mapping at the top level")
    if "version" not in data:
        data = migrate_v1(data)
    _check_keys(data, _TOP_KEYS, "config")
    if data["version"] != VERSION:
        raise ConfigError(
            f"config: unsupported version {data['version']!r} (expected {VERSION})"
        )
    apps = data.get("apps") or {}
    if not isinstance(apps, dict):
        raise ConfigError("apps: expected a mapping of app names to settings")
    if len(apps) > MAX_APPS:
        raise ConfigError(f"apps: at most {MAX_APPS} apps are supported")
    return Config(apps=tuple(_parse_app(n, a) for n, a in apps.items()))


def migrate_v1(data: dict[Any, Any]) -> dict[str, Any]:
    """Convert a version 1 (TrafficToll) config dict to version 2.

    Settings without a v2 equivalent are dropped with a logged warning.
    Matching is not validated here beyond what is needed to convert it;
    ``parse`` validates the result.
    """
    _check_keys(data, (*_V1_GLOBAL_KEYS, "processes"), "config")
    dropped = [k for k in _V1_GLOBAL_KEYS if k in data]
    if dropped:
        log.warning(
            "config v1: ignoring global settings not supported in v2: %s",
            ", ".join(dropped),
        )

    apps = {}
    for name, proc in (data.get("processes") or {}).items():
        where = f"processes.{name}"
        if not isinstance(proc, dict):
            raise ConfigError(f"{where}: expected a mapping with 'match' and limits")
        _check_keys(proc, _V1_APP_KEYS, where)
        if "download-priority" in proc or "upload-priority" in proc:
            log.warning("config v1: %s: ignoring priorities (unsupported in v2)", where)

        # In v1 every predicate in the list had to match (AND). In v2 that is
        # a single entry with several keys.
        match = proc.get("match")
        if not isinstance(match, list) or not match:
            raise ConfigError(f"{where}.match: expected a non-empty list of predicates")
        merged: dict[str, Any] = {}
        for i, pred in enumerate(match):
            if not isinstance(pred, dict):
                raise ConfigError(f"{where}.match[{i}]: expected a mapping")
            _check_keys(pred, _V1_MATCH_KEYS, f"{where}.match[{i}]")
            for key, value in pred.items():
                if key in merged:
                    raise ConfigError(f"{where}.match: {key!r} given more than once")
                merged[key] = value

        app: dict[str, Any] = {"match": [merged]}
        for key in ("download", "upload"):
            if proc.get(key) is not None:
                app[key] = proc[key]
        apps[name] = app

    return {"version": VERSION, "apps": apps}


# --- Loading ------------------------------------------------------------------


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate keys instead of keeping the last one."""

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise ConstructorError(
                    None, None, f"duplicate key {key!r}", key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def loads(text: str, source: str = "<string>") -> Config:
    """Parse and validate config YAML text."""
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as e:
        raise ConfigError(f"{source}: invalid YAML: {e}") from None
    try:
        return parse(data)
    except ConfigError as e:
        raise ConfigError(f"{source}: {e}") from None


def load(path: str | Path) -> Config:
    """Read, parse and validate a config file."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"{path}: cannot read config: {e.strerror}") from None
    return loads(text, source=str(path))


def default_text() -> str:
    """Return the text of the packaged default config, comments included."""
    return (
        resources.files("trafficcop.common")
        .joinpath(DEFAULT_CONFIG_NAME)
        .read_text(encoding="utf-8")
    )


def load_default() -> Config:
    return loads(default_text(), source=DEFAULT_CONFIG_NAME)
