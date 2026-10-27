import pytest

pytest.importorskip("tkinter")

from trafficcop.gui import fonts  # noqa: E402


def test_register_outside_the_snap():
    assert fonts.register({}) == []


def test_register_with_no_bundled_fonts(tmp_path):
    assert fonts.register({"SNAP": str(tmp_path)}) == []


def test_register_bundled_fonts(tmp_path):
    import shutil
    from pathlib import Path

    system = sorted(Path("/usr/share/fonts/truetype/andika").glob("Andika-*.ttf"))
    if not system:
        pytest.skip("Andika isn't installed here")
    target = tmp_path / fonts.FONT_DIR
    target.mkdir(parents=True)
    for f in system:
        shutil.copy(f, target)
    added = fonts.register({"SNAP": str(tmp_path)})
    assert [f.name for f in added] == [f.name for f in system]
