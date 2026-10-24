"""tc and ifb setup that shapes each app's marked traffic.

The same layout is used for upload (on the WAN interface) and download (on
the ifb device that ingress traffic is redirected to)::

    1:   htb root, default 1:2
    └── 1:1    htb, link rate
        ├── 1:2   htb, link rate -> fq_codel     (everything else)
        └── 1:101 htb, link rate -> cake <rate>  (app in mark slot 1)

HTB classes run at ``LINK_RATE``, so HTB only classifies; the CAKE leaf does
the shaping. An ``fw`` filter per app sends packets whose mark has that app's
slot in ``nft.MARK_MASK`` to class ``1:(0x100 + slot)``, e.g. ``1:101``; other
mark bits are ignored. On the ifb device CAKE runs in ``ingress`` mode, which
counts dropped packets as bandwidth already used, so senders back off sooner.

For downloads, an ``ingress`` qdisc on the WAN interface restores the mark
from conntrack (``action connmark``) and redirects every packet to the ifb
device.

Only apps with a limit in a given direction get a class there. The ifb device
is created only if some app has a download limit.

The ``build_*`` functions return argv lists and run nothing, so they can be
tested without root; ``apply`` runs them.

``read_upload_counters`` reads what each app's upload CAKE leaf has actually
sent, for the meter. Unlike the nft counters, which count packets before
they reach the queue, it excludes what CAKE dropped or is still holding.
CAKE's byte counts include the link-layer header (14 bytes on Ethernet and
Wi-Fi), so they run about 1% above the nft counters' IP bytes.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable, Mapping

from trafficcop.common.config import Config, format_rate
from trafficcop.daemon.nft import MARK_MASK, mark_slot

IFB = "ifb-trafficcop"  # our own device, so we never touch an existing ifb0
LINK_RATE = "10gbit"  # HTB classes must never be the bottleneck
DEFAULT_CLASS = 2  # minor number of the class for unmarked traffic
APP_CLASS_BASE = 0x100  # app classes are 1:101, 1:102, ... (minors are hex)


class TcError(RuntimeError):
    """A tc or ip command failed."""


def _classid(minor: int) -> str:
    return f"1:{minor:x}"  # tc reads class minor numbers as hex


def upload_counter_name(mark: int) -> str:
    """Name of an app's shaped-upload counter in ``read_upload_counters``."""
    return f"tc_up_{mark_slot(mark)}"


def read_upload_counters(wan: str) -> dict[str, tuple[int, int]]:
    """Return ``{upload_counter_name: (packets, bytes)}`` for ``wan``'s CAKE leaves.

    Only apps with an upload limit have one. Returns ``{}`` if ``wan`` has no
    Traffic Cop qdiscs or tc fails.
    """
    result = subprocess.run(
        ["tc", "-s", "-j", "qdisc", "show", "dev", wan], capture_output=True, text=True
    )
    if result.returncode != 0:
        return {}
    try:
        qdiscs = json.loads(result.stdout)
    except ValueError:
        return {}
    counters = {}
    for q in qdiscs:
        parent = q.get("parent", "")
        if q.get("kind") != "cake" or not parent.startswith("1:"):
            continue
        try:
            slot = int(parent[2:], 16) - APP_CLASS_BASE
            if slot >= 1:
                counters[f"tc_up_{slot}"] = (int(q["packets"]), int(q["bytes"]))
        except (KeyError, TypeError, ValueError):
            continue
    return counters


def shaper_commands(
    dev: str, leaves: Iterable[tuple[int, int]], ingress: bool = False
) -> list[list[str]]:
    """Commands for the HTB/CAKE tree on ``dev``.

    ``leaves`` are (mark, rate in bits per second) pairs, with marks from
    ``nft.assign_marks``.
    """
    cmds = [
        ["tc", "qdisc", "add", "dev", dev, "root", "handle", "1:",
         "htb", "default", f"{DEFAULT_CLASS:x}"],
        ["tc", "class", "add", "dev", dev, "parent", "1:", "classid", "1:1",
         "htb", "rate", LINK_RATE],
        ["tc", "class", "add", "dev", dev, "parent", "1:1",
         "classid", _classid(DEFAULT_CLASS), "htb", "rate", LINK_RATE],
        ["tc", "qdisc", "add", "dev", dev, "parent", _classid(DEFAULT_CLASS),
         "fq_codel"],
    ]  # fmt: skip
    for mark, rate in leaves:
        classid = _classid(APP_CLASS_BASE + mark_slot(mark))
        cake = ["cake", "bandwidth", format_rate(rate), "besteffort"]
        if ingress:
            cake.append("ingress")
        cmds += [
            ["tc", "class", "add", "dev", dev, "parent", "1:1",
             "classid", classid, "htb", "rate", LINK_RATE],
            ["tc", "qdisc", "add", "dev", dev, "parent", classid, *cake],
            ["tc", "filter", "add", "dev", dev, "parent", "1:", "protocol", "all",
             "prio", "1", "handle", f"{mark:#x}/{MARK_MASK:#x}", "fw",
             "flowid", classid],
        ]  # fmt: skip
    return cmds


def build_setup(config: Config, marks: Mapping[str, int], wan: str) -> list[list[str]]:
    """Commands that shape ``config``'s limited apps on the ``wan`` interface.

    Assumes a clean slate: run ``build_teardown`` first (``apply_setup`` does).
    Returns an empty list if no app has a limit.
    """
    up = [(marks[a.name], a.upload) for a in config.apps if a.upload is not None]
    down = [(marks[a.name], a.download) for a in config.apps if a.download is not None]

    cmds = []
    if up:
        cmds += shaper_commands(wan, up)
    if down:
        cmds += [
            ["ip", "link", "add", IFB, "type", "ifb"],
            ["ip", "link", "set", IFB, "up"],
            ["tc", "qdisc", "add", "dev", wan, "handle", "ffff:", "ingress"],
            ["tc", "filter", "add", "dev", wan, "parent", "ffff:", "protocol", "all",
             "prio", "1", "matchall",
             "action", "connmark",
             "action", "mirred", "egress", "redirect", "dev", IFB],
        ]  # fmt: skip
        cmds += shaper_commands(IFB, down, ingress=True)
    return cmds


def build_teardown(wan: str) -> list[list[str]]:
    """Commands that remove everything ``build_setup`` adds.

    Each may fail harmlessly if that piece isn't there.
    """
    return [
        ["tc", "qdisc", "del", "dev", wan, "root"],
        ["tc", "qdisc", "del", "dev", wan, "ingress"],
        ["ip", "link", "del", IFB],
    ]


def apply(commands: Iterable[list[str]], check: bool = True) -> None:
    """Run commands in order. Needs CAP_NET_ADMIN.

    With ``check``, stop at the first failure and raise TcError; otherwise
    ignore failures.
    """
    for argv in commands:
        result = subprocess.run(argv, text=True, capture_output=True)
        if check and result.returncode != 0:
            raise TcError(f"{' '.join(argv)}: {result.stderr.strip()}")


def apply_setup(config: Config, marks: Mapping[str, int], wan: str) -> None:
    """Replace any existing shaping on ``wan`` with ``config``'s.

    If setup fails partway, everything is torn down again before raising, so
    traffic is never left half-shaped.
    """
    apply(build_teardown(wan), check=False)
    try:
        apply(build_setup(config, marks, wan))
    except TcError:
        apply(build_teardown(wan), check=False)
        raise
