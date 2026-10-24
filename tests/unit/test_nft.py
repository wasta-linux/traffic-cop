import json
import sys
import types

import pytest

from trafficcop.common import config
from trafficcop.daemon import nft

CFG = config.loads(
    """
version: 2
apps:
  Zoom:
    download: 100kbps
    upload: 100kbps
    match: [{exe: /opt/zoom/zoom}]
  Spotify:
    download: 128kbit
    match: [{snap: spotify}]
"""
)
ZOOM_CG = (
    "user.slice/user-1000.slice/user@1000.service/app.slice/app-gnome-Zoom-1234.scope"
)


ZOOM = 0x01000000
SPOTIFY = 0x02000000
SET_ZOOM = (
    "meta mark set meta mark and 0xc0ffffff or 0x01000000"
    " ct mark set ct mark and 0xc0ffffff or 0x01000000"
)
SET_SPOTIFY = (
    "meta mark set meta mark and 0xc0ffffff or 0x02000000"
    " ct mark set ct mark and 0xc0ffffff or 0x02000000"
)


def test_assign_marks():
    assert nft.assign_marks(CFG) == {"Zoom": ZOOM, "Spotify": SPOTIFY}


def test_assign_marks_fills_all_slots():
    cfg = config.parse(
        {
            "version": 2,
            "apps": {f"a{i}": {"match": [{"name": "x"}]} for i in range(63)},
        }
    )
    marks = nft.assign_marks(cfg)
    assert max(marks.values()) == nft.MARK_MASK
    assert all(m & ~nft.MARK_MASK == 0 for m in marks.values())


def test_mark_mask_fits_config_limit():
    assert nft.MAX_SLOT == config.MAX_APPS


@pytest.mark.parametrize(("mark", "slot"), [(ZOOM, 1), (0x3F000000, 63)])
def test_mark_slot(mark, slot):
    assert nft.mark_slot(mark) == slot


@pytest.mark.parametrize("mark", [0, 0x10, 0x01000010, 0x40000000])
def test_mark_slot_rejects_foreign_marks(mark):
    with pytest.raises(ValueError, match="not a Traffic Cop mark"):
        nft.mark_slot(mark)


def test_counter_names():
    assert nft.counter_names(ZOOM) == ("up_1", "down_1")
    assert nft.counter_names(0x3F000000) == ("up_63", "down_63")


@pytest.mark.parametrize(
    ("path", "level"),
    [
        ("user.slice", 1),
        ("/user.slice/user-1000.slice", 2),
        (ZOOM_CG, 5),
        (ZOOM_CG + "/", 5),
    ],
)
def test_cgroup_level(path, level):
    assert nft.cgroup_level(path) == level


@pytest.mark.parametrize("path", ["", "/", 'a/"b"', "a\nb"])
def test_bad_cgroup_paths(path):
    with pytest.raises(ValueError):
        nft.cgroup_level(path)


def test_ruleset_matches_proof_of_concept():
    marks = nft.assign_marks(CFG)
    script = nft.build_ruleset(marks, {"Zoom": ["/" + ZOOM_CG]})
    assert script == (
        "table inet trafficcop\n"
        "delete table inet trafficcop\n"
        "table inet trafficcop {\n"
        "    counter up_1 {}\n"
        "    counter down_1 {}\n"
        "    counter up_2 {}\n"
        "    counter down_2 {}\n"
        "    chain output {\n"
        "        type filter hook output priority mangle; policy accept;\n"
        f'        socket cgroupv2 level 5 "{ZOOM_CG}" {SET_ZOOM}'
        ' counter name "up_1"\n'
        "    }\n"
        "    chain input {\n"
        "        type filter hook input priority mangle; policy accept;\n"
        '        ct mark and 0x3f000000 == 0x01000000 counter name "down_1"\n'
        '        ct mark and 0x3f000000 == 0x02000000 counter name "down_2"\n'
        "    }\n"
        "}\n"
    )


def test_ruleset_several_scopes_per_app():
    marks = nft.assign_marks(CFG)
    script = nft.build_ruleset(
        marks,
        {
            "Spotify": [
                "system.slice/b.scope",
                "system.slice/a.scope",
                "system.slice/a.scope",
            ]
        },
    )
    rules = [line.strip() for line in script.splitlines() if "cgroupv2" in line]
    assert rules == [
        f'socket cgroupv2 level 2 "system.slice/a.scope" {SET_SPOTIFY}'
        ' counter name "up_2"',
        f'socket cgroupv2 level 2 "system.slice/b.scope" {SET_SPOTIFY}'
        ' counter name "up_2"',
    ]


def test_ruleset_without_running_apps():
    script = nft.build_ruleset(nft.assign_marks(CFG), {})
    assert "cgroupv2" not in script
    assert "counter up_1 {}" in script


def test_ruleset_rejects_unknown_app():
    with pytest.raises(ValueError, match="unknown app 'Skype'"):
        nft.build_ruleset(nft.assign_marks(CFG), {"Skype": ["a.scope"]})


def test_output_update():
    script = nft.build_output_update(nft.assign_marks(CFG), {"Zoom": [ZOOM_CG]})
    assert script == (
        "flush chain inet trafficcop output\n"
        f'add rule inet trafficcop output socket cgroupv2 level 5 "{ZOOM_CG}"'
        f' {SET_ZOOM} counter name "up_1"\n'
    )


def test_teardown():
    assert nft.build_teardown() == (
        "table inet trafficcop\ndelete table inet trafficcop\n"
    )


class FakeNftables:
    """Stands in for nftables.Nftables (python3-nftables)."""

    def __init__(self):
        self.json_output = False
        self.calls = []
        self.result = (0, "", "")

    def set_json_output(self, value):
        old, self.json_output = self.json_output, value
        return old

    def cmd(self, script):
        self.calls.append((script, self.json_output))
        return self.result


@pytest.fixture
def fake_nft(monkeypatch):
    fake = FakeNftables()
    module = types.ModuleType("nftables")
    monkeypatch.setattr(module, "Nftables", lambda: fake, raising=False)
    monkeypatch.setitem(sys.modules, "nftables", module)
    monkeypatch.setattr(nft, "_ctx", None)
    return fake


def test_apply(fake_nft):
    nft.apply("flush ruleset\n")
    assert fake_nft.calls == [("flush ruleset\n", False)]


def test_apply_raises_on_error(fake_nft):
    fake_nft.result = (1, "", "Error: syntax error\n")
    with pytest.raises(nft.NftError, match="syntax error"):
        nft.apply("bogus\n")


def test_read_counters(fake_nft):
    fake_nft.result = (
        0,
        json.dumps(
            {
                "nftables": [
                    {"metainfo": {"json_schema_version": 1}},
                    {"counter": {"name": "up_1", "packets": 3, "bytes": 188}},
                    {"counter": {"name": "down_1", "packets": 0, "bytes": 0}},
                ]
            }
        ),
        "",
    )
    assert nft.read_counters() == {"up_1": (3, 188), "down_1": (0, 0)}
    assert fake_nft.calls == [("list counters table inet trafficcop", True)]
    assert fake_nft.json_output is False  # restored afterwards


def test_read_counters_restores_output_mode_on_error(fake_nft):
    fake_nft.result = (1, "", "Error: No such file or directory\n")
    with pytest.raises(nft.NftError, match="No such file"):
        nft.read_counters()
    assert fake_nft.json_output is False
