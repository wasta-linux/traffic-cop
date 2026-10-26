"""nftables rules that mark and count each app's traffic.

Everything lives in one table, ``inet trafficcop``::

    table inet trafficcop {
        counter up_1 {}             # one up/down pair per configured app
        counter down_1 {}
        chain output {              # tag packets from the app's cgroups
            type filter hook output priority mangle; policy accept;
            socket cgroupv2 level 5 "user.slice/.../app-gnome-Zoom-1234.scope"
                meta mark set meta mark and 0xc0ffffff or 0x01000000
                ct mark set ct mark and 0xc0ffffff or 0x01000000
                oifname != "lo" counter name "up_1"
        }
        chain input {               # count replies on the app's connections
            type filter hook input priority mangle; policy accept;
            iifname != "lo" ct mark and 0x3f000000 == 0x01000000
                counter name "down_1"
        }
    }

Loopback traffic is marked (harmlessly) but not counted: it never reaches
the network, and each packet would be counted both ways. Traffic to the
local network is counted, though tc only shapes the WAN interface.

Traffic Cop owns only the mark bits in ``MARK_MASK`` (bits 24-29). Each app
gets a slot from 1 to 63 there, and 0 means "not ours". Rules set and test
only those bits, so marks from other software (WireGuard's ``0xca6c``,
Tailscale's bits in ``0xff0000``, Kubernetes' ``0x4000``/``0x8000``)
survive. Software that sets or matches the whole 32-bit value can still
conflict: it either overwrites our bits or stops matching once we add them.

The mark on the packet routes upload traffic to the app's tc class; the mark
on the connection (ct mark) is restored at ingress for downloads (see
``tc.py``). The two are set separately rather than copied, because nft can't
merge one mark into the other under a mask without newer kernels. The
counters are read by the meter.

Rules are generated as nft's text syntax (what `nft list ruleset` prints and
the nft man page documents) and loaded in-process through libnftables, ...

JSON input can't express these rules: its parser accepts only the socket
keys "transparent", "mark" and "wildcard" (json_parse_socket_expr in
nftables' src/parser_json.c, checked against 1.1.6 and upstream 1.1.7-dev,
October 2026), and JSON output drops the cgroupv2 level. To re-check on a
newer nftables: load a `socket cgroupv2` rule as text, `nft -j list` it, and
load that JSON back with `nft -j -f`. JSON output is fine for counters.

nftables resolves a cgroup path to an ID when the rule is loaded, so the
path must exist then, and the rule stops matching once the scope is gone.
When scopes come and go, ``build_output_update`` rewrites just the output
chain, which keeps the counters.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from trafficcop.common.config import Config

if TYPE_CHECKING:
    import nftables

TABLE = "trafficcop"
FAMILY = "inet"
MARK_MASK = 0x3F000000  # the only mark bits Traffic Cop sets or tests
MARK_SHIFT = 24
MAX_SLOT = MARK_MASK >> MARK_SHIFT  # 63 apps
_KEEP = ~MARK_MASK & 0xFFFFFFFF  # everyone else's bits


class NftError(RuntimeError):
    """libnftables rejected a command."""


_ctx: nftables.Nftables | None = None


def assign_marks(config: Config) -> dict[str, int]:
    """Give each configured app a firewall mark, in config order.

    The mark has the app's slot (1, 2, ...) in the ``MARK_MASK`` bits and
    nothing else.
    """
    if len(config.apps) > MAX_SLOT:
        raise ValueError(f"at most {MAX_SLOT} apps fit in the mark bits")
    return {app.name: (i + 1) << MARK_SHIFT for i, app in enumerate(config.apps)}


def mark_slot(mark: int) -> int:
    """Return the app slot (1 to ``MAX_SLOT``) encoded in one of our marks."""
    slot = mark >> MARK_SHIFT
    if mark & ~MARK_MASK or not 1 <= slot <= MAX_SLOT:
        raise ValueError(f"{mark:#x} is not a Traffic Cop mark")
    return slot


def counter_names(mark: int) -> tuple[str, str]:
    """Return the (upload, download) counter names for an app's mark."""
    slot = mark_slot(mark)
    return f"up_{slot}", f"down_{slot}"


def cgroup_level(path: str) -> int:
    """Depth of a cgroup path relative to /sys/fs/cgroup, as nft's ``level``."""
    return len(_clean_path(path).split("/"))


def _clean_path(path: str) -> str:
    """Normalize a cgroup path to the form nft expects, with no leading slash."""
    clean = path.strip("/")
    if not clean:
        raise ValueError("cgroup path is empty (the root cgroup can't be matched)")
    if '"' in clean or "\n" in clean:
        # nft strings have no escape for these, and systemd never produces them.
        raise ValueError(f"cgroup path can't contain quotes or newlines: {path!r}")
    return clean


def _output_rules(
    marks: Mapping[str, int], cgroups: Mapping[str, Iterable[str]]
) -> list[str]:
    rules = []
    for name, paths in cgroups.items():
        if name not in marks:
            raise ValueError(f"cgroups given for unknown app {name!r}")
        mark = marks[name]
        up, _ = counter_names(mark)
        for path in sorted(set(paths)):
            clean = _clean_path(path)
            rules.append(
                f'socket cgroupv2 level {cgroup_level(clean)} "{clean}" '
                f"meta mark set meta mark and {_KEEP:#010x} or {mark:#010x} "
                f"ct mark set ct mark and {_KEEP:#010x} or {mark:#010x} "
                f'oifname != "lo" counter name "{up}"'
            )
    return rules


def build_ruleset(
    marks: Mapping[str, int], cgroups: Mapping[str, Iterable[str]]
) -> str:
    """Return an nft script that (re)creates the whole table.

    ``marks`` comes from ``assign_marks``. ``cgroups`` maps app names to the
    cgroup paths currently matched to them; apps that aren't running can be
    left out, and still get counters. The script replaces any existing table
    in one transaction, which resets the counters.
    """
    lines = [
        # Creating the table first makes the delete succeed if it's missing.
        f"table {FAMILY} {TABLE}",
        f"delete table {FAMILY} {TABLE}",
        f"table {FAMILY} {TABLE} {{",
    ]
    for mark in marks.values():
        for counter in counter_names(mark):
            lines.append(f"    counter {counter} {{}}")
    lines += [
        "    chain output {",
        "        type filter hook output priority mangle; policy accept;",
        *(f"        {rule}" for rule in _output_rules(marks, cgroups)),
        "    }",
        "    chain input {",
        "        type filter hook input priority mangle; policy accept;",
    ]
    for mark in marks.values():
        _, down = counter_names(mark)
        lines.append(
            f'        iifname != "lo" ct mark and {MARK_MASK:#010x} == {mark:#010x}'
            f' counter name "{down}"'
        )
    lines += ["    }", "}"]
    return "\n".join(lines) + "\n"


def build_output_update(
    marks: Mapping[str, int], cgroups: Mapping[str, Iterable[str]]
) -> str:
    """Return an nft script that replaces only the output chain's rules.

    Use this when the set of matched cgroups changes but the config doesn't:
    the counters (and the input chain) are left alone.
    """
    lines = [f"flush chain {FAMILY} {TABLE} output"]
    lines += [
        f"add rule {FAMILY} {TABLE} output {rule}"
        for rule in _output_rules(marks, cgroups)
    ]
    return "\n".join(lines) + "\n"


def build_teardown() -> str:
    """Return an nft script that removes the table, whether or not it exists."""
    return f"table {FAMILY} {TABLE}\ndelete table {FAMILY} {TABLE}\n"


def _nft() -> nftables.Nftables:
    """Return the shared libnftables context, creating it on first use.

    The import is deferred so that the rule builders above (and their tests)
    work without the system package installed.
    """
    global _ctx
    if _ctx is None:
        import nftables

        _ctx = nftables.Nftables()
    return _ctx


def _run(script: str, json_output: bool = False) -> str:
    nft = _nft()
    old = nft.set_json_output(json_output)
    try:
        rc, output, error = nft.cmd(script)
    finally:
        nft.set_json_output(old)
    if rc != 0:
        raise NftError(error.strip() or f"libnftables returned {rc}")
    return output


def apply(script: str) -> None:
    """Load an nft script atomically. Needs CAP_NET_ADMIN."""
    _run(script)


def read_counters() -> dict[str, tuple[int, int]]:
    """Return ``{counter name: (packets, bytes)}`` for every counter in the table.

    Raises NftError if the table doesn't exist.
    """
    output = _run(f"list counters table {FAMILY} {TABLE}", json_output=True)
    counters = {}
    for item in json.loads(output)["nftables"]:
        if counter := item.get("counter"):
            counters[counter["name"]] = (counter["packets"], counter["bytes"])
    return counters
