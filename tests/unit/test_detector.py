import logging
import os
from pathlib import Path

import pytest

from trafficcop.common import config
from trafficcop.daemon import detector
from trafficcop.daemon.detector import Process, Skipped, Unit

USER = "user.slice/user-1000.slice"
APPS = f"{USER}/user@1000.service/app.slice"
FIREFOX = f"{APPS}/snap.firefox.firefox-0f1e2d3c-aaaa-bbbb-cccc-123456789abc.scope"
ZOOM = f"{APPS}/app-gnome-Zoom-1234.scope"
KEEPASS = f"{APPS}/app-flatpak-org.keepassxc.KeePassXC-2678985583.scope"
TERMINAL = f"{APPS}/vte-spawn-11111111-2222-3333-4444-555555555555.scope"
SESSION_MANAGER = (
    f"{APPS}/app-gnome\\x2dsession\\x2dmanager.slice/gnome-session@x.service"
)
SNAPD = "system.slice/snapd.service"


def cfg(text: str) -> config.Config:
    return config.loads("version: 2\napps:\n" + text)


# --- Reading the system -------------------------------------------------------


class FakeSystem:
    """A fake /sys/fs/cgroup and /proc under a temporary directory."""

    def __init__(self, root: Path):
        self.cgroup = root / "cgroup"
        self.proc = root / "proc"
        self.cgroup.mkdir()
        self.proc.mkdir()

    def process(
        self,
        cgroup: str,
        pid: int,
        comm: str,
        cmdline: list[str],
        exe: str | None = None,
    ) -> None:
        d = self.cgroup / cgroup
        d.mkdir(parents=True, exist_ok=True)
        procs = d / "cgroup.procs"
        procs.write_text((procs.read_text() if procs.exists() else "") + f"{pid}\n")
        p = self.proc / str(pid)
        p.mkdir()
        (p / "comm").write_text(comm + "\n")
        (p / "cmdline").write_text("".join(arg + "\0" for arg in cmdline))
        if exe is not None:
            os.symlink(exe, p / "exe")  # dangling is fine: only readlink is used

    def remove_process(self, cgroup: str, pid: int) -> None:
        procs = self.cgroup / cgroup / "cgroup.procs"
        procs.write_text(procs.read_text().replace(f"{pid}\n", ""))
        for f in (self.proc / str(pid)).iterdir():
            f.unlink()
        (self.proc / str(pid)).rmdir()

    def units(self, services: set[str] | None = None) -> dict[str, Unit]:
        units = detector.read_units(services or set(), self.cgroup, self.proc)
        return {u.path: u for u in units}


@pytest.fixture
def system(tmp_path: Path) -> FakeSystem:
    s = FakeSystem(tmp_path)
    s.process(ZOOM, 100, "zoom", ["/opt/zoom/zoom", "--x"], "/opt/zoom/zoom")
    s.process(ZOOM + "/helper", 101, "ZoomWebviewHost", [], "/opt/zoom/ZoomWebview")
    s.process(
        FIREFOX,
        200,
        "Isolated Web Co",  # comm is truncated to 15 chars...
        ["/snap/firefox/123/usr/lib/firefox/firefox", "-contentproc"],
        "/snap/firefox/123/usr/lib/firefox/firefox (deleted)",
    )
    s.process(SESSION_MANAGER, 300, "gnome-session-b", ["/usr/libexec/x"], "/x")
    s.process(SNAPD, 400, "snapd", ["/usr/lib/snapd/snapd"], "/usr/lib/snapd/snapd")
    s.process("system.slice/system-getty.slice/getty@tty1.service", 401, "agetty", [])
    # Not units of interest: never read.
    s.process(f"{USER}/session-2.scope", 500, "skype", ["skype"], "/usr/bin/skype")
    s.process(f"{USER}/user@1000.service/init.scope", 501, "systemd", [])
    s.process(f"{USER}/user@1000.service/session.slice/org.gnome.Shell@x.service",
              502, "gnome-shell", ["/usr/bin/gnome-shell"])  # fmt: skip
    s.process("init.scope", 1, "systemd", ["/sbin/init"], "/sbin/init")
    (s.cgroup / APPS / "app-empty-9.scope").mkdir()  # no cgroup.procs: tolerated
    return s


def test_read_units_finds_app_units_only(system: FakeSystem):
    assert set(system.units()) == {
        ZOOM,
        FIREFOX,
        SESSION_MANAGER,
        f"{APPS}/app-empty-9.scope",
    }


def test_read_units_includes_nested_cgroups(system: FakeSystem):
    zoom = system.units()[ZOOM]
    assert [p.pid for p in zoom.processes] == [100, 101]
    assert zoom.processes[0] == Process(
        100, "zoom", "/opt/zoom/zoom", ("/opt/zoom/zoom", "--x")
    )
    assert not zoom.system


def test_read_units_named_services(system: FakeSystem):
    units = system.units({"snapd.service", "getty@tty1.service", "absent.service"})
    assert units[SNAPD].system
    assert units[SNAPD].processes[0].name == "snapd"
    assert "system.slice/system-getty.slice/getty@tty1.service" in units
    assert len(units) == 6


def test_read_units_skips_exited_processes(system: FakeSystem):
    (system.cgroup / ZOOM / "cgroup.procs").write_text("100\n999\n")
    assert [p.pid for p in system.units()[ZOOM].processes] == [100, 101]


def test_read_process_strips_deleted_suffix(system: FakeSystem):
    p = detector.read_process(200, system.proc)
    assert p is not None
    assert p.exe == "/snap/firefox/123/usr/lib/firefox/firefox"


def test_read_process_keeps_truncated_comm_unless_argv0_matches(system: FakeSystem):
    p = detector.read_process(200, system.proc)
    assert p is not None
    assert p.name == "Isolated Web Co"  # argv[0] is "firefox", a different name


def test_read_process_expands_truncated_comm(system: FakeSystem):
    cmd = ["/usr/libexec/gnome-software-service", "--gapplication-service"]
    system.process(f"{APPS}/x.service", 7, "gnome-software-", cmd)
    p = detector.read_process(7, system.proc)
    assert p is not None
    assert p.name == "gnome-software-service"


def test_read_process_without_exe(system: FakeSystem):
    p = detector.read_process(101, system.proc)
    assert p is not None
    assert p.exe == "/opt/zoom/ZoomWebview"
    p = detector.read_process(401, system.proc)
    assert p is not None
    assert p.exe is None


def test_read_units_on_this_system():
    own = Path("/proc/self/cgroup").read_text().split("0::/", 1)[1].strip()
    units = detector.read_units()
    assert all("/app.slice/" in u.path for u in units)
    if "/app.slice/" not in own:
        pytest.skip("not running inside a user's app.slice")
    mine = [u for u in units if own.startswith(u.path)]
    assert len(mine) == 1
    assert os.getpid() in {p.pid for p in mine[0].processes}


# --- Matching -----------------------------------------------------------------


def unit(path: str, *procs: Process, system: bool = False) -> Unit:
    return Unit(path, system, procs)


ZOOM_UNIT = unit(
    ZOOM,
    Process(10, "zoom", "/opt/zoom/zoom", ("/opt/zoom/zoom",)),
    Process(11, "ZoomWebviewHost", "/opt/zoom/ZoomWebviewHost"),
)
FIREFOX_UNIT = unit(FIREFOX, Process(20, "firefox", "/snap/firefox/1/firefox"))
KEEPASS_UNIT = unit(KEEPASS, Process(30, "keepassxc", "/app/bin/keepassxc"))
TERMINAL_UNIT = unit(
    TERMINAL,
    Process(40, "curl", "/usr/bin/curl", ("curl", "https://example.com")),
    Process(41, "bash", "/usr/bin/bash", ("bash",)),
)
SNAPD_UNIT = unit(SNAPD, Process(50, "snapd", "/usr/lib/snapd/snapd"), system=True)
ALL = [ZOOM_UNIT, FIREFOX_UNIT, KEEPASS_UNIT, TERMINAL_UNIT, SNAPD_UNIT]


@pytest.mark.parametrize(
    ("entry", "units"),
    [
        ("{snap: firefox}", {FIREFOX}),
        ("{snap: fire}", set()),  # whole snap name only
        ("{flatpak: org.keepassxc.KeePassXC}", {KEEPASS}),
        ("{exe: /opt/zoom/zoom}", {ZOOM}),
        ("{exe: /opt/zoom/ZoomWebviewHost}", {ZOOM}),
        ("{name: zoom}", {ZOOM}),
        ("{name: zo}", set()),  # regexes match the whole name
        ("{name: '.*zoom.*'}", {ZOOM}),
        ("{cmdline: 'curl https://.*'}", {TERMINAL}),
        ("{scope: 'app-gnome-Zoom-[0-9]+\\.scope'}", {ZOOM}),
        ("{name: curl, exe: /usr/bin/curl}", {TERMINAL}),
        ("{name: bash, exe: /usr/bin/curl}", set()),  # keys must fit one process
        ("{snap: firefox, name: firefox}", {FIREFOX}),
        ("{snap: firefox, name: zoom}", set()),
        ("{service: snapd.service}", {SNAPD}),
        ("{service: snapd}", {SNAPD}),
        ("{service: snapd, name: snapd}", {SNAPD}),
        ("{service: snapd, name: other}", set()),
        ("{name: snapd}", set()),  # system services only via 'service'
        ("{scope: 'snapd\\.service'}", set()),
        ("{service: zoom}", set()),  # 'service' never matches app units
    ],
)
def test_match_entry(entry: str, units: set[str]):
    result = detector.match(cfg(f"  X: {{match: [{entry}]}}\n"), ALL)
    assert result.cgroups.get("X", frozenset()) == units


def test_match_any_entry():
    result = detector.match(
        cfg("  X: {match: [{snap: firefox}, {exe: /opt/zoom/zoom}]}\n"), ALL
    )
    assert result.cgroups == {"X": {FIREFOX, ZOOM}}


def test_flatpak_ids_are_escaped():
    path = f"{APPS}/app-flatpak-org.example.My\\x2dApp-42.scope"
    result = detector.match(
        cfg("  X: {match: [{flatpak: org.example.My-App}]}\n"), [unit(path)]
    )
    assert result.cgroups == {"X": {path}}


def test_first_app_wins():
    result = detector.match(
        cfg(
            "  Zoom: {match: [{exe: /opt/zoom/zoom}]}\n"
            "  Everything: {match: [{name: '.*'}]}\n"
        ),
        ALL,
    )
    assert result.cgroups["Zoom"] == {ZOOM}
    assert result.cgroups["Everything"] == {FIREFOX, KEEPASS, TERMINAL}
    assert result.skipped == {Skipped("Everything", ZOOM, "already matched by Zoom")}


# --- Detector -----------------------------------------------------------------


def test_detector_reports_changes(system: FakeSystem, caplog: pytest.LogCaptureFixture):
    d = detector.Detector(
        cfg(
            "  Zoom: {match: [{exe: /opt/zoom/zoom}]}\n"
            "  Snap Updates: {match: [{service: snapd}]}\n"
            "  Zoom again: {match: [{name: zoom}]}\n"
        ),
        system.cgroup,
        system.proc,
    )
    with caplog.at_level(logging.WARNING):
        assert d.scan() is True
        assert d.scan() is False
    assert d.current == {"Zoom": {ZOOM}, "Snap Updates": {SNAPD}}
    assert caplog.text.count("not limiting") == 1
    assert f"Zoom again: not limiting {ZOOM}: already matched by Zoom" in caplog.text

    system.remove_process(ZOOM, 100)
    assert d.scan() is True
    assert d.current == {"Snap Updates": {SNAPD}}
