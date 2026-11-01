from pathlib import Path

from trafficcop.common import autostart


def test_on_by_default(tmp_path: Path):
    path = tmp_path / ".config" / "autostart" / "traffic-cop.desktop"
    assert not autostart.enabled(path)  # no file yet
    autostart.set_default(path)
    assert autostart.enabled(path)
    assert "Exec=wasta-traffic-cop.gui --hidden" in path.read_text().splitlines()


def test_off_is_remembered(tmp_path: Path):
    path = tmp_path / "traffic-cop.desktop"
    autostart.set_enabled(path, False)
    assert path.exists() and not autostart.enabled(path)
    assert "Hidden=true" in path.read_text().splitlines()  # snapd skips it
    autostart.set_default(path)  # the default doesn't override the choice
    assert not autostart.enabled(path)
    autostart.set_enabled(path, True)
    assert autostart.enabled(path)
