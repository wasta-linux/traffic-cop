import asyncio
import logging
from collections.abc import Mapping
from pathlib import Path

import pytest

from trafficcop.common import config
from trafficcop.daemon import nft, supervisor, tc
from trafficcop.daemon.meter import Usage

APPS = "user.slice/user-1000.slice/user@1000.service/app.slice"
ZOOM = f"{APPS}/app-gnome-Zoom-1234.scope"
ZOOM2 = f"{APPS}/app-gnome-Zoom-5678.scope"
SNAPD = "system.slice/snapd.service"

CFG = config.loads(
    """
version: 2
apps:
  Zoom:
    download: 100kbps
    upload: 100kbps
    match: [{exe: /opt/zoom/zoom}]
  Snap Updates:
    download: 1mbit
    match: [{service: snapd}]
"""
)


class FakeSystem(supervisor.System):
    """Records what the supervisor asks for; set the attributes to steer it."""

    def __init__(self):
        self.wan: str | None = "wlp2s0"
        self.counters: dict[str, tuple[int, int]] = {}
        self.upload_counters: dict[str, tuple[int, int]] = {}
        self.nft_failures = 0  # fail this many apply_nft calls first
        self.tc_error: str | None = None
        self.log: list[tuple[str, ...]] = []

    def apply_nft(self, script: str) -> None:
        if self.nft_failures:
            self.nft_failures -= 1
            raise nft.NftError("Error: cgroupv2 path fails: No such file or directory")
        kind = "ruleset" if script.startswith("table") else "update"
        self.log.append(("nft", kind, script))

    def read_counters(self) -> dict[str, tuple[int, int]]:
        return self.counters

    def read_upload_counters(self, wan: str) -> dict[str, tuple[int, int]]:
        return self.upload_counters

    def setup_tc(self, config: config.Config, marks: Mapping[str, int], wan: str):
        if self.tc_error:
            raise tc.TcError(self.tc_error)
        self.log.append(("setup_tc", wan))

    def teardown_tc(self, wan: str) -> None:
        self.log.append(("teardown_tc", wan))

    def teardown_nft(self) -> None:
        self.log.append(("teardown_nft",))

    def wan_device(self) -> str | None:
        return self.wan

    def calls(self, *kinds: str) -> list[tuple[str, ...]]:
        return [c[:2] if c[0] == "nft" else c for c in self.log if c[0] in kinds]


class FakeProc:
    def __init__(self, root: Path):
        self.path = root / "proc"
        self.path.mkdir()

    def add(self, pid: int, cgroup: str, name: str, exe: str) -> None:
        d = self.path / str(pid)
        d.mkdir()
        (d / "cgroup").write_text(f"0::/{cgroup}\n")
        (d / "comm").write_text(name + "\n")
        (d / "cmdline").write_text(exe + "\0")

    def remove(self, pid: int) -> None:
        for f in (self.path / str(pid)).iterdir():
            f.unlink()
        (self.path / str(pid)).rmdir()


@pytest.fixture
def proc(tmp_path: Path) -> FakeProc:
    p = FakeProc(tmp_path)
    p.add(100, ZOOM, "zoom", "/opt/zoom/zoom")
    p.add(200, SNAPD, "snapd", "/usr/lib/snapd/snapd")
    return p


@pytest.fixture
def system() -> FakeSystem:
    return FakeSystem()


@pytest.fixture
def sup(system: FakeSystem, proc: FakeProc) -> supervisor.Supervisor:
    return supervisor.Supervisor(CFG, system, proc.path)


def test_start(sup: supervisor.Supervisor, system: FakeSystem, caplog):
    with caplog.at_level(logging.INFO):
        sup.start()
    assert system.calls("teardown_nft", "teardown_tc", "nft", "setup_tc") == [
        ("teardown_nft",),  # leftovers from a previous run
        ("teardown_tc", "wlp2s0"),
        ("setup_tc", "wlp2s0"),  # before nft, so nothing passes unshaped
        ("nft", "ruleset"),
    ]
    ruleset = system.log[3][2]
    assert f'"{ZOOM}"' in ruleset and f'"{SNAPD}"' in ruleset
    assert sup.shaped == "wlp2s0"
    assert "Zoom: limiting app-gnome-Zoom-1234.scope" in caplog.text
    assert "shaping traffic on wlp2s0" in caplog.text


def test_start_offline(sup: supervisor.Supervisor, system: FakeSystem, caplog):
    system.wan = None
    sup.start()
    assert system.calls("setup_tc", "teardown_tc") == []
    assert ("nft", "ruleset") in system.calls("nft")
    assert "metering only" in caplog.text


def test_start_retries_a_vanished_unit(sup: supervisor.Supervisor, system: FakeSystem):
    system.nft_failures = 2
    sup.start()
    assert system.calls("nft") == [("nft", "ruleset")]


def test_start_gives_up(sup: supervisor.Supervisor, system: FakeSystem):
    system.nft_failures = 3
    with pytest.raises(nft.NftError):
        sup.start()
    assert system.log[-2:] == [("teardown_tc", "wlp2s0"), ("teardown_nft",)]
    assert sup.shaped is None


def test_check_apps(sup: supervisor.Supervisor, system: FakeSystem, proc, caplog):
    sup.start()
    system.log.clear()
    sup.check_apps()
    assert system.log == []  # nothing changed

    proc.add(300, ZOOM2, "zoom", "/opt/zoom/zoom")
    proc.remove(100)
    with caplog.at_level(logging.INFO):
        sup.check_apps()
    assert system.calls("nft") == [("nft", "update")]
    update = system.log[0][2]
    assert f'"{ZOOM2}"' in update and f'"{ZOOM}"' not in update
    assert "Zoom: limiting app-gnome-Zoom-5678.scope" in caplog.text
    assert "Zoom: app-gnome-Zoom-1234.scope ended" in caplog.text


def test_check_apps_retries_later(sup: supervisor.Supervisor, system, proc, caplog):
    sup.start()
    system.log.clear()
    proc.add(300, ZOOM2, "zoom", "/opt/zoom/zoom")
    system.nft_failures = 3
    sup.check_apps()
    assert system.log == []
    assert "couldn't update the rules" in caplog.text

    sup.check_apps()  # the change is reported again and now succeeds
    assert system.calls("nft") == [("nft", "update")]


def test_check_wan(sup: supervisor.Supervisor, system: FakeSystem, caplog):
    sup.start()
    system.log.clear()
    sup.check_wan()
    assert system.log == []

    system.wan = "proton0"  # VPN connected
    with caplog.at_level(logging.INFO):
        sup.check_wan()
    assert system.log == [("teardown_tc", "wlp2s0"), ("setup_tc", "proton0")]
    assert "WAN interface: wlp2s0 -> proton0" in caplog.text

    system.log.clear()
    system.wan = None  # offline
    sup.check_wan()
    assert system.log == [("teardown_tc", "proton0")]
    assert sup.shaped is None


def test_check_wan_retries_failed_shaping(sup, system: FakeSystem, caplog):
    system.tc_error = "tc qdisc add dev wlp2s0 ...: Error: Exclusivity flag on"
    sup.start()
    assert sup.shaped is None
    sup.check_wan()
    assert caplog.text.count("couldn't shape traffic") == 1  # not repeated

    system.tc_error = None
    sup.check_wan()
    assert sup.shaped == "wlp2s0"
    assert system.calls("setup_tc") == [("setup_tc", "wlp2s0")]


def test_sample(sup: supervisor.Supervisor, system: FakeSystem):
    sup.start()
    system.counters = {"up_1": (5, 4000), "down_1": (3, 1000)}
    sup.sample()
    assert sup.usage["Zoom"].up_total == 4000
    assert sup.usage["Snap Updates"] == Usage()


def test_sample_uses_cake_for_limited_uploads(sup: supervisor.Supervisor, system):
    sup.start()
    system.counters = {"up_1": (5, 4000), "up_2": (2, 300)}
    system.upload_counters = {"tc_up_1": (4, 3000)}  # CAKE dropped the rest
    sup.sample()
    assert sup.usage["Zoom"].up_total == 3000
    assert sup.usage["Snap Updates"].up_total == 300  # no upload limit: nft


def test_sample_without_shaping_uses_nft(sup: supervisor.Supervisor, system):
    system.wan = None
    sup.start()
    system.counters = {"up_1": (5, 4000)}
    system.upload_counters = {"tc_up_1": (4, 3000)}  # not read: nothing shaped
    sup.sample()
    assert sup.usage["Zoom"].up_total == 4000


def test_sample_error_keeps_last_usage(sup: supervisor.Supervisor, system, caplog):
    sup.start()
    system.counters = {"up_1": (5, 4000)}
    sup.sample()

    def broken():
        raise nft.NftError("Error: No such file or directory")

    sup.meter._read = broken  # pyright: ignore[reportPrivateUsage]
    sup.sample()
    assert sup.usage["Zoom"].up_total == 4000
    assert "couldn't read the traffic counters" in caplog.text


def test_stop(sup: supervisor.Supervisor, system: FakeSystem):
    sup.start()
    system.log.clear()
    sup.stop()
    assert system.log == [("teardown_tc", "wlp2s0"), ("teardown_nft",)]


def test_run(sup: supervisor.Supervisor, system: FakeSystem, monkeypatch):
    monkeypatch.setattr(supervisor, "DETECT_INTERVAL", 0.01)
    monkeypatch.setattr(supervisor, "METER_INTERVAL", 0.01)
    calls = {"check_apps": 0, "sample": 0}

    def counting(name: str):
        original = getattr(sup, name)

        def wrapper() -> None:
            calls[name] += 1
            original()

        wrapper.__name__ = name
        return wrapper

    sup.check_apps = counting("check_apps")  # type: ignore[method-assign]
    sup.sample = counting("sample")  # type: ignore[method-assign]

    async def main() -> None:
        stop = asyncio.Event()
        asyncio.get_running_loop().call_later(0.1, stop.set)
        await supervisor.run(sup, stop)

    asyncio.run(main())
    assert calls["check_apps"] > 2 and calls["sample"] > 2
    assert system.log[-1] == ("teardown_nft",)  # cleaned up at the end


def test_run_survives_unexpected_errors(sup, system: FakeSystem, monkeypatch, caplog):
    monkeypatch.setattr(supervisor, "DETECT_INTERVAL", 0.01)

    def broken() -> None:
        raise RuntimeError("boom")

    broken.__name__ = "check_apps"
    sup.check_apps = broken  # type: ignore[method-assign]

    async def main() -> None:
        stop = asyncio.Event()
        asyncio.get_running_loop().call_later(0.05, stop.set)
        await supervisor.run(sup, stop)

    asyncio.run(main())
    assert "unexpected error in check_apps" in caplog.text
    assert system.log[-1] == ("teardown_nft",)
