"""Fakes shared by the supervisor and server tests."""

from collections.abc import Mapping
from pathlib import Path

from trafficcop.common import config
from trafficcop.daemon import nft, supervisor, tc

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
