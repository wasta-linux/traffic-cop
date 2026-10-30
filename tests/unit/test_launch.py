import subprocess
import sys
from pathlib import Path

import pytest

from trafficcop.common import launch


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """The commands passed to Popen, instead of running them."""
    commands: list[list[str]] = []
    monkeypatch.setattr(
        subprocess, "Popen", lambda command, **kwargs: commands.append(command)
    )
    return commands


def executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)  # shutil.which only finds executable files
    return path


def test_starts_the_program_beside_the_interpreter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, started: list[list[str]]
):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python3"))
    program = executable(tmp_path / "traffic-cop")
    launch.start("traffic-cop", Path("/run/x.sock"))
    assert started == [[str(program), "--socket", "/run/x.sock"]]


def test_falls_back_to_path_without_a_socket(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, started: list[list[str]]
):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python3"))
    on_path = tmp_path / "bin"
    on_path.mkdir()
    program = executable(on_path / "traffic-cop-indicator")
    monkeypatch.setenv("PATH", str(on_path))
    launch.start("traffic-cop-indicator")
    assert started == [[str(program)]]


def test_missing_program_starts_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, started: list[list[str]]
):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python3"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    launch.start("traffic-cop")
    assert started == []
