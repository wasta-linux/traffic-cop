from pathlib import Path

import pytest

from trafficcop.common import paths

SNAP_ENV = {
    "SNAP": "/snap/traffic-cop/42",
    "SNAP_DATA": "/var/snap/traffic-cop/42",
    "SNAP_COMMON": "/var/snap/traffic-cop/common",
}


def test_development():
    assert paths.get({"HOME": "/root"}) == paths.Paths(
        config=Path("/var/lib/traffic-cop-dev/traffic-cop.yaml"),
        socket=Path("/run/traffic-cop-dev/traffic-cop.sock"),
        snap=False,
    )


def test_snap():
    assert paths.get(SNAP_ENV) == paths.Paths(
        config=Path("/var/snap/traffic-cop/42/traffic-cop.yaml"),
        socket=Path("/var/snap/traffic-cop/common/traffic-cop.sock"),
        snap=True,
    )


def test_empty_snap_variable_means_development():
    assert not paths.get({"SNAP": ""}).snap


@pytest.mark.parametrize("missing", ["SNAP_DATA", "SNAP_COMMON"])
def test_snap_without_its_variables(missing: str):
    env = {k: v for k, v in SNAP_ENV.items() if k != missing}
    with pytest.raises(RuntimeError, match=f"\\${missing} isn't set"):
        paths.get(env)


def test_defaults_to_environment(monkeypatch: pytest.MonkeyPatch):
    for name, value in SNAP_ENV.items():
        monkeypatch.setenv(name, value)
    assert paths.get().snap
    monkeypatch.delenv("SNAP")
    assert not paths.get().snap
