import json
import subprocess

import pytest

from trafficcop.daemon import netdev


class FakeIp:
    """Stands in for subprocess.run(["ip", ...]).

    ``answers`` maps a route destination to a route list or an exit code.
    ``kinds`` maps a device to its link kind; other devices are physical.
    """

    def __init__(self, answers: dict[str, object], kinds: dict[str, str]):
        self.answers = answers
        self.kinds = kinds
        self.calls: list[list[str]] = []

    def __call__(
        self, argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if argv[2:5] == ["-d", "link", "show"]:
            link: dict[str, object] = {"ifname": argv[-1]}
            if argv[-1] in self.kinds:
                link["linkinfo"] = {"info_kind": self.kinds[argv[-1]]}
            return subprocess.CompletedProcess(argv, 0, json.dumps([link]), "")
        answer = self.answers.get(argv[-1], 2)
        if isinstance(answer, int):
            return subprocess.CompletedProcess(argv, answer, "", "RTNETLINK answers: x")
        out = answer if isinstance(answer, str) else json.dumps(answer)
        return subprocess.CompletedProcess(argv, 0, out, "")


def route(dev: str) -> list[dict[str, object]]:
    return [{"dst": "1.1.1.1", "gateway": "192.168.1.1", "dev": dev, "flags": []}]


@pytest.fixture(autouse=True)
def fresh_kinds():
    netdev.forget_link_kinds()
    yield
    netdev.forget_link_kinds()


@pytest.fixture
def fake_ip(monkeypatch: pytest.MonkeyPatch):
    def install(
        answers: dict[str, object], kinds: dict[str, str] | None = None
    ) -> FakeIp:
        fake = FakeIp(answers, kinds or {})
        monkeypatch.setattr(netdev.subprocess, "run", fake)
        return fake

    return install


def test_route_device(fake_ip):
    fake = fake_ip({"1.1.1.1": route("wlp2s0")})
    assert netdev.route_device("1.1.1.1") == "wlp2s0"
    assert fake.calls == [
        ["ip", "-j", "-4", "route", "get", "1.1.1.1"],
        ["ip", "-j", "-d", "link", "show", "dev", "wlp2s0"],
    ]


def test_route_device_skips_dummies(fake_ip):
    fake_ip({"1.1.1.1": route("ipv6leakintrf0")}, {"ipv6leakintrf0": "dummy"})
    assert netdev.route_device("1.1.1.1") is None


@pytest.mark.parametrize(
    ("kinds", "kind"),
    [({"proton0": "wireguard"}, "wireguard"), ({}, None)],
)
def test_link_kind(fake_ip, kinds: dict[str, str], kind: str | None):
    fake_ip({}, kinds)
    assert netdev.link_kind("proton0") == kind


@pytest.mark.parametrize("output", ["[]", "not json", '[{"linkinfo": 5}]'])
def test_link_kind_unreadable(monkeypatch: pytest.MonkeyPatch, output: str):
    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(netdev.subprocess, "run", run)
    assert netdev.link_kind("x") is None


def test_route_device_ipv6(fake_ip):
    fake = fake_ip({"2606:4700:4700::1111": route("eth0")})
    assert netdev.route_device("2606:4700:4700::1111") == "eth0"
    assert fake.calls[0][2] == "-6"


@pytest.mark.parametrize(
    "answer",
    [
        2,  # Network is unreachable
        "not json",
        "[]",
        [{"dst": "1.1.1.1"}],
        [{"dev": 5}],
        route("lo"),
        route("ifb-trafficcop"),
    ],
)
def test_route_device_none(fake_ip, answer: object):
    fake_ip({"1.1.1.1": answer})
    assert netdev.route_device("1.1.1.1") is None


def test_route_device_without_ip(monkeypatch: pytest.MonkeyPatch):
    def missing(argv: list[str], **kwargs: object):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(netdev.subprocess, "run", missing)
    assert netdev.route_device("1.1.1.1") is None


def test_wan_device_prefers_ipv4(fake_ip):
    fake_ip({"1.1.1.1": route("wg0"), "2606:4700:4700::1111": route("eth0")})
    assert netdev.wan_device() == "wg0"


def test_wan_device_falls_back_to_ipv6(fake_ip):
    fake_ip({"2606:4700:4700::1111": route("eth0")})
    assert netdev.wan_device() == "eth0"


def test_wan_device_with_proton_vpn(fake_ip):
    # Proton VPN while connected: IPv4 via WireGuard, IPv6 into a leak sink.
    fake_ip(
        {"1.1.1.1": route("proton0"), "2606:4700:4700::1111": route("ipv6leakintrf0")},
        {"proton0": "wireguard", "ipv6leakintrf0": "dummy"},
    )
    assert netdev.wan_device() == "proton0"


def test_wan_device_never_falls_back_to_a_dummy(fake_ip):
    fake_ip(
        {"2606:4700:4700::1111": route("ipv6leakintrf0")}, {"ipv6leakintrf0": "dummy"}
    )
    assert netdev.wan_device() is None


def test_wan_device_skips_dummy_ipv4(fake_ip):
    fake_ip(
        {"1.1.1.1": route("sink0"), "2606:4700:4700::1111": route("eth0")},
        {"sink0": "dummy"},
    )
    assert netdev.wan_device() == "eth0"


def test_wan_device_offline(fake_ip):
    fake_ip({})
    assert netdev.wan_device() is None


def test_watcher(fake_ip):
    fake = fake_ip({"1.1.1.1": route("wlp2s0")})
    w = netdev.WanWatcher()
    assert w.poll() is True
    assert w.current == "wlp2s0"
    assert w.poll() is False
    fake.answers["1.1.1.1"] = route("wg0")  # VPN connected
    assert w.poll() is True
    assert w.current == "wg0"
    fake.answers.clear()  # offline
    assert w.poll() is True
    assert w.current is None


def test_link_kind_is_looked_up_once(fake_ip):
    fake = fake_ip({"1.1.1.1": route("proton0")}, {"proton0": "wireguard"})
    for _ in range(3):
        assert netdev.wan_device() == "proton0"
    link_calls = [c for c in fake.calls if "link" in c]
    assert len(link_calls) == 1  # route lookups repeat; the kind doesn't


def test_failed_link_lookups_are_retried(monkeypatch: pytest.MonkeyPatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "Device does not exist")

    monkeypatch.setattr(netdev.subprocess, "run", run)
    assert netdev.link_kind("tun0") is None
    assert netdev.link_kind("tun0") is None
    assert len(calls) == 2
