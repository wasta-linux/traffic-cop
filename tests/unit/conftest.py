"""Fixtures shared by the supervisor and server tests; see fakes.py."""

from pathlib import Path

import pytest

from tests.unit.fakes import CFG, SNAPD, ZOOM, FakeProc, FakeSystem
from trafficcop.daemon import supervisor


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
