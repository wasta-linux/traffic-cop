"""Find the network interface that apps' internet traffic leaves through.

This replaces ``tt-wrapper``. The WAN interface is whichever device the
kernel would route a packet to a public address through, from
``ip route get``. That lookup follows policy routing the way apps' packets
do, so with a full-tunnel VPN up it returns the VPN interface (``wg0``,
``tun0``, ...), not the physical one.

That's deliberate, and the opposite of ``tt-wrapper``, which picked the
physical interface. Shaping has to happen where apps' own packets, with
Traffic Cop's marks, are visible. On the physical interface a WireGuard VPN
carries every app inside one encrypted UDP flow, re-marked with its own
fwmark, so apps can't be told apart there.

Dummy interfaces are never the WAN: VPN apps use them as sinks, e.g. Proton
VPN's ``ipv6leakintrf0`` holds the IPv6 default route while connected so
that IPv6 traffic is dropped rather than leaking outside the tunnel.

IPv4 is tried first, then IPv6. ``WanWatcher.poll`` re-checks, so the
supervisor can move the tc setup when the route changes (Wi-Fi to Ethernet,
a VPN going up or down).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trafficcop.daemon.tc import IFB

SYS_NET = Path("/sys/class/net")
# Public addresses to look up routes for; no packets are sent.
PROBES = ("1.1.1.1", "2606:4700:4700::1111")
_NOT_WAN = {"lo", IFB}
_NOT_WAN_KINDS = {"dummy"}


def _ip_json(*args: str) -> Any:
    """Run ``ip -j <args>`` and return its parsed output, or None on failure."""
    try:
        result = subprocess.run(
            ["ip", "-j", *args], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None  # e.g. "Network is unreachable", "Device does not exist"
    try:
        return json.loads(result.stdout)
    except ValueError:
        return None


# Interface kinds already looked up. A link's kind can't change while it
# exists, and `ip -d link show` reads /usr/share/iproute2/group, which the
# snap may not (a harmless but noisy AppArmor denial), so ask once per name.
_kinds: dict[str, str | None] = {}


def link_kind(dev: str) -> str | None:
    """Return an interface's kind (``wireguard``, ``dummy``, ...), if it has one.

    Physical interfaces have no kind. Answers are remembered per interface
    name (see ``forget_link_kinds``); failed lookups aren't.
    """
    if dev in _kinds:
        return _kinds[dev]
    links = _ip_json("-d", "link", "show", "dev", dev)
    if not isinstance(links, list) or not links:
        return None  # gone, or ip failed: ask again next time
    try:
        kind = links[0]["linkinfo"]["info_kind"]
    except (LookupError, TypeError):
        kind = None
    _kinds[dev] = kind if isinstance(kind, str) else None
    return _kinds[dev]


def forget_link_kinds() -> None:
    _kinds.clear()


def route_device(destination: str) -> str | None:
    """Return the device ``ip route get`` picks for ``destination``, if any.

    Returns None if that device can't carry traffic to the internet.
    """
    family = "-6" if ":" in destination else "-4"
    try:
        dev = _ip_json(family, "route", "get", destination)[0]["dev"]
    except (LookupError, TypeError):
        return None
    if not isinstance(dev, str) or dev in _NOT_WAN:
        return None
    return None if link_kind(dev) in _NOT_WAN_KINDS else dev


def wan_device() -> str | None:
    """Return the WAN interface, or None if there's no route to the internet."""
    for destination in PROBES:
        if dev := route_device(destination):
            return dev
    return None


def interface_bytes(dev: str, sys_net: Path = SYS_NET) -> tuple[int, int]:
    """Return ``(received, sent)`` byte counts for a whole interface.

    Raises OSError if the interface doesn't exist.
    """
    stats = sys_net / dev / "statistics"
    return (
        int((stats / "rx_bytes").read_text()),
        int((stats / "tx_bytes").read_text()),
    )


@dataclass
class WanWatcher:
    """Tracks the WAN interface across polls."""

    lookup: Callable[[], str | None] = wan_device
    current: str | None = field(default=None, init=False)

    def poll(self) -> bool:
        """Look up the WAN interface again. Return True if it changed."""
        dev = self.lookup()
        changed = dev != self.current
        self.current = dev
        return changed
