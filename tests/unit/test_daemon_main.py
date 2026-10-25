from pathlib import Path

import pytest

from trafficcop.common import config
from trafficcop.daemon import __main__ as daemon


def test_load_or_create_writes_the_default(tmp_path: Path):
    path = tmp_path / "new" / "traffic-cop.yaml"
    cfg = daemon.load_or_create(path)
    assert path.read_text() == config.default_text()
    assert cfg == config.load_default()


def test_load_or_create_keeps_an_existing_config(tmp_path: Path):
    path = tmp_path / "traffic-cop.yaml"
    path.write_text("version: 2\napps: {}\n")
    assert daemon.load_or_create(path).apps == ()


def test_main_needs_root(monkeypatch: pytest.MonkeyPatch, caplog):
    monkeypatch.setattr(daemon.os, "geteuid", lambda: 1000)
    assert daemon.main([]) == 2
    assert "must run as root" in caplog.text


def test_main_rejects_an_invalid_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
):
    path = tmp_path / "traffic-cop.yaml"
    path.write_text("version: 2\napps:\n  X: {match: []}\n")
    monkeypatch.setattr(daemon.os, "geteuid", lambda: 0)
    assert daemon.main(["--config", str(path)]) == 1
    assert "apps.X.match" in caplog.text


def test_main_runs_the_supervisor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "traffic-cop.yaml"
    monkeypatch.setattr(daemon.os, "geteuid", lambda: 0)
    ran: list[config.Config] = []

    async def fake_run(cfg: config.Config, *paths: Path):
        ran.append(cfg)

    monkeypatch.setattr(daemon, "_run", fake_run)
    assert daemon.main(["--config", str(path)]) == 0
    assert ran == [config.load_default()]
