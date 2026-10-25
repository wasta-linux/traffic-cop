"""Editing apps in the config: the logic behind the app editor dialog.

The dialog works on an ``AppDraft``, plain strings as typed, and turns it
into a config entry with ``AppDraft.to_entry``, which raises ``EditError``
(with a translated message) for anything the user must fix. ``with_app`` and
``without_app`` then produce the whole new config dict to send with
``set-config``; the daemon validates it again.

Limits are entered in the same units the window shows them in (kB/s or MB/s,
translated as ko/s and Mo/s), with either decimal separator, and stored as
tc rates in kilo/megabytes per second (``"70kbps"``); a blank limit means
none. Match rules are one key and value each; a rule from the config file
with several keys (all must match) is kept as it is, but can only be removed
in the dialog, not edited.
"""

from __future__ import annotations

import grp
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from trafficcop.common import config
from trafficcop.common.i18n import _, pgettext

UNITS = ("kB/s", "MB/s")  # shown translated; stored as kbps/mbps
_TC_UNITS = {"kB/s": ("kbps", 1e3), "MB/s": ("mbps", 1e6)}
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d*)?|[.,]\d+")


def rule_keys() -> tuple[tuple[str, str], ...]:
    """Match keys the dialog offers, with their labels, most useful first."""
    return (
        ("snap", _("Snap package")),
        ("flatpak", _("Flatpak app ID")),
        ("exe", _("Program (full path)")),
        ("name", _("Process name (pattern)")),
        ("cmdline", _("Command line (pattern)")),
        ("scope", _("systemd scope (pattern)")),
        ("service", _("System service")),
    )


class EditError(ValueError):
    """Something in the draft the user has to fix (message is translated)."""


@dataclass
class Limit:
    number: str = ""  # as typed; blank = no limit
    unit: str = "kB/s"  # one of UNITS

    @classmethod
    def from_bits(cls, bits_per_second: int | None) -> Limit:
        if bits_per_second is None:
            return cls()
        bytes_per_second = bits_per_second / 8
        unit = "MB/s" if bytes_per_second >= 1e6 else "kB/s"
        value = bytes_per_second / _TC_UNITS[unit][1]
        text = f"{value:.3f}".rstrip("0").rstrip(".")
        return cls(text.replace(".", pgettext("decimal separator", ".")), unit)

    def to_rate(self, label: str) -> str | None:
        """The tc rate string for the config, or None for no limit."""
        text = self.number.strip()
        if not text:
            return None
        if not _NUMBER_RE.fullmatch(text) or float(text.replace(",", ".")) <= 0:
            raise EditError(
                _("{field}: enter a number greater than 0, or leave it blank").format(
                    field=label
                )
            )
        tc_unit = _TC_UNITS[self.unit][0]
        return f"{text.replace(',', '.')}{tc_unit}"


@dataclass
class AppDraft:
    name: str = ""
    download: Limit = field(default_factory=Limit)
    upload: Limit = field(default_factory=Limit)
    rules: list[tuple[str, str]] = field(default_factory=list)  # (key, value)
    fixed_rules: list[dict[str, str]] = field(default_factory=list)  # multi-key

    @classmethod
    def from_entry(cls, name: str, entry: Mapping[str, Any]) -> AppDraft:
        """A draft from an app's entry in a config dict (``to_dict`` form)."""
        cfg = config.parse({"version": config.VERSION, "apps": {name: entry}})
        app = cfg.apps[0]
        rules, fixed = [], []
        for m in entry["match"]:
            if len(m) == 1:
                ((key, value),) = m.items()
                rules.append((key, str(value)))
            else:
                fixed.append(dict(m))
        return cls(
            name=name,
            download=Limit.from_bits(app.download),
            upload=Limit.from_bits(app.upload),
            rules=rules,
            fixed_rules=fixed,
        )

    def to_entry(self) -> tuple[str, dict[str, Any]]:
        """``(name, entry)`` for the config. Raises EditError."""
        name = self.name.strip()
        if not name:
            raise EditError(_("Give the app a name."))
        entry: dict[str, Any] = {}
        for key, limit, label in (
            ("download", self.download, _("Download limit")),
            ("upload", self.upload, _("Upload limit")),
        ):
            rate = limit.to_rate(label)
            if rate is not None:
                entry[key] = rate
        match: list[dict[str, str]] = [
            {key: value.strip()} for key, value in self.rules if value.strip()
        ]
        match += self.fixed_rules
        if not match:
            raise EditError(_("Add at least one rule to recognise the app by."))
        entry["match"] = match
        try:  # catch what the daemon would reject, e.g. a bad pattern
            config.parse({"version": config.VERSION, "apps": {name: entry}})
        except config.ConfigError as e:
            raise EditError(str(e)) from None
        return name, entry


def with_app(
    cfg: Mapping[str, Any], old_name: str | None, name: str, entry: Mapping[str, Any]
) -> dict[str, Any]:
    """``cfg`` with ``old_name`` replaced by (or, if None, ``name`` added as)
    ``entry``, keeping the apps' order. Raises EditError on a name clash."""
    apps: dict[str, Any] = dict(cfg.get("apps") or {})
    if name != old_name and name in apps:
        raise EditError(_("There's already an app called {name}.").format(name=name))
    if old_name is None:
        apps[name] = dict(entry)
    else:
        apps = {
            (name if n == old_name else n): (dict(entry) if n == old_name else e)
            for n, e in apps.items()
        }
    return {"version": config.VERSION, "apps": apps}


def without_app(cfg: Mapping[str, Any], name: str) -> dict[str, Any]:
    apps = {n: e for n, e in (cfg.get("apps") or {}).items() if n != name}
    return {"version": config.VERSION, "apps": apps}


ADMIN_GROUPS = ("sudo", "admin", "wheel")  # as in daemon/server.py


def may_change(groups: Sequence[int] | None = None, uid: int | None = None) -> bool:
    """Whether this user may change the config (the daemon checks it again).

    Used only to show the editor read-only for other users, rather than let
    them fill it in and be refused.
    """
    uid = os.getuid() if uid is None else uid
    if uid == 0:
        return True
    gids = set(os.getgroups() if groups is None else groups)
    for name in ADMIN_GROUPS:
        try:
            if grp.getgrnam(name).gr_gid in gids:
                return True
        except KeyError:
            pass
    return False
