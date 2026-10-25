import asyncio
import logging

import pytest

from tests.unit.fakes import CFG, SNAPD, ZOOM, ZOOM2, FakeSystem
from trafficcop.common import config
from trafficcop.daemon import nft, supervisor
from trafficcop.daemon.meter import Usage


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


def test_sample_counts_dropped_downloads_for_limited_apps(sup, system: FakeSystem):
    sup.start()
    system.counters = {"down_1": (5, 2000), "down_2": (1, 100)}
    system.download_counters = {"tc_down_1": (9, 3500)}  # 1500 B dropped
    sup.sample()
    assert sup.usage["Zoom"].down_total == 3500
    assert sup.usage["Snap Updates"].down_total == 100  # no CAKE counter: nft


def test_sample_without_shaping_uses_nft(sup: supervisor.Supervisor, system):
    system.wan = None
    sup.start()
    system.counters = {"up_1": (5, 4000)}
    system.upload_counters = {"tc_up_1": (4, 3000)}  # not read: nothing shaped
    system.download_counters = {"tc_down_1": (4, 3000)}
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


def test_pause_and_resume(sup: supervisor.Supervisor, system: FakeSystem, caplog):
    sup.start()
    system.log.clear()
    sup.pause()
    assert system.log == [("teardown_tc", "wlp2s0")]
    assert sup.paused and sup.shaped is None
    sup.pause()  # already paused: nothing more
    assert len(system.log) == 1

    system.wan = "proton0"  # the WAN can change while paused
    sup.check_wan()
    assert system.calls("setup_tc") == []
    assert sup.wan.current == "proton0"

    sup.resume()
    assert system.calls("setup_tc") == [("setup_tc", "proton0")]
    assert not sup.paused and sup.shaped == "proton0"


def test_paused_still_meters(sup: supervisor.Supervisor, system: FakeSystem):
    sup.start()
    sup.pause()
    system.counters = {"down_1": (3, 900)}
    sup.sample()
    assert sup.usage["Zoom"].down_total == 900


def test_apply_config(sup: supervisor.Supervisor, system: FakeSystem, caplog):
    sup.start()
    system.counters = {"up_1": (5, 4000)}
    sup.sample()
    system.log.clear()
    new = config.loads(
        "version: 2\napps:\n"
        "  Firefox: {download: 1mbit, match: [{snap: firefox}]}\n"
        "  Zoom: {upload: 1mbit, match: [{exe: /opt/zoom/zoom}]}\n"
    )
    with caplog.at_level(logging.INFO):
        sup.apply_config(new)
    assert system.calls("teardown_tc", "nft", "setup_tc") == [
        ("teardown_tc", "wlp2s0"),
        ("nft", "ruleset"),
        ("setup_tc", "wlp2s0"),
    ]
    assert sup.marks == {"Firefox": 0x01000000, "Zoom": 0x02000000}
    assert "Snap Updates: snapd.service ended" in caplog.text

    system.counters = {"up_2": (1, 500)}  # Zoom moved to slot 2; table rebuilt
    sup.sample()
    assert sup.usage["Zoom"].up_total == 4500  # its total carried over
    assert "Snap Updates" not in sup.usage


def test_apply_config_failure_keeps_the_old_config(sup, system: FakeSystem):
    sup.start()
    old_marks = sup.marks
    system.log.clear()
    system.nft_failures = 3
    with pytest.raises(nft.NftError):
        sup.apply_config(config.loads("version: 2\napps: {}\n"))
    assert sup.config is CFG and sup.marks == old_marks
    assert sup.shaped == "wlp2s0"  # shaping restored
