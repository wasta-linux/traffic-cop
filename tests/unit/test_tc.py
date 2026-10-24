import pytest

from trafficcop.common import config
from trafficcop.daemon import nft, tc

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
  Watch only:
    match: [{snap: firefox}]
"""
)
MARKS = nft.assign_marks(CFG)


def cmd(line):
    return line.split()


def test_shaper_matches_proof_of_concept():
    # zoom-limit.sh's shaper(), with the default class at 1:2, app classes at
    # 1:101 and up, and the fw filter masked to Traffic Cop's mark bits.
    assert tc.shaper_commands("eth0", [(0x01000000, 800_000)]) == [
        cmd("tc qdisc add dev eth0 root handle 1: htb default 2"),
        cmd("tc class add dev eth0 parent 1: classid 1:1 htb rate 10gbit"),
        cmd("tc class add dev eth0 parent 1:1 classid 1:2 htb rate 10gbit"),
        cmd("tc qdisc add dev eth0 parent 1:2 fq_codel"),
        cmd("tc class add dev eth0 parent 1:1 classid 1:101 htb rate 10gbit"),
        cmd("tc qdisc add dev eth0 parent 1:101 cake bandwidth 800kbit besteffort"),
        cmd(
            "tc filter add dev eth0 parent 1: protocol all prio 1"
            " handle 0x1000000/0x3f000000 fw flowid 1:101"
        ),
    ]


def test_shaper_ingress_mode():
    cmds = tc.shaper_commands("ifb-trafficcop", [(0x01000000, 800_000)], ingress=True)
    assert (
        cmd(
            "tc qdisc add dev ifb-trafficcop parent 1:101"
            " cake bandwidth 800kbit besteffort ingress"
        )
        in cmds
    )


def test_shaper_class_ids_are_hex():
    cmds = tc.shaper_commands("eth0", [(0x3F000000, 1_000_000)])  # slot 63
    assert cmd("tc class add dev eth0 parent 1:1 classid 1:13f htb rate 10gbit") in cmds
    fw = "tc filter add dev eth0 parent 1: protocol all prio 1"
    assert cmd(fw + " handle 0x3f000000/0x3f000000 fw flowid 1:13f") in cmds


def test_shaper_rejects_foreign_marks():
    with pytest.raises(ValueError, match="not a Traffic Cop mark"):
        tc.shaper_commands("eth0", [(0x10, 1000)])


def test_setup():
    cmds = tc.build_setup(CFG, MARKS, "wlp2s0")
    up = tc.shaper_commands("wlp2s0", [(0x01000000, 800_000)])
    down = tc.shaper_commands(
        tc.IFB, [(0x01000000, 800_000), (0x02000000, 128_000)], ingress=True
    )
    assert cmds == [
        *up,
        cmd("ip link add ifb-trafficcop type ifb"),
        cmd("ip link set ifb-trafficcop up"),
        cmd("tc qdisc add dev wlp2s0 handle ffff: ingress"),
        cmd(
            "tc filter add dev wlp2s0 parent ffff: protocol all prio 1 matchall"
            " action connmark action mirred egress redirect dev ifb-trafficcop"
        ),
        *down,
    ]


def test_setup_upload_only():
    cfg = config.loads("version: 2\napps:\n  X: {upload: 1mbit, match: [{name: x}]}\n")
    cmds = tc.build_setup(cfg, nft.assign_marks(cfg), "eth0")
    assert cmds == tc.shaper_commands("eth0", [(0x01000000, 1_000_000)])


def test_setup_without_limits():
    cfg = config.loads("version: 2\napps:\n  X: {match: [{name: x}]}\n")
    assert tc.build_setup(cfg, nft.assign_marks(cfg), "eth0") == []


def test_teardown():
    assert tc.build_teardown("eth0") == [
        cmd("tc qdisc del dev eth0 root"),
        cmd("tc qdisc del dev eth0 ingress"),
        cmd("ip link del ifb-trafficcop"),
    ]


class FakeRun:
    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        failed = self.fail_on is not None and argv[:3] == self.fail_on
        return type(
            "R", (), {"returncode": 2 if failed else 0, "stderr": "Error: nope\n"}
        )()


def test_apply_setup(monkeypatch):
    run = FakeRun()
    monkeypatch.setattr(tc.subprocess, "run", run)
    tc.apply_setup(CFG, MARKS, "eth0")
    assert run.calls == tc.build_teardown("eth0") + tc.build_setup(CFG, MARKS, "eth0")


def test_apply_setup_tears_down_on_failure(monkeypatch):
    run = FakeRun(fail_on=["ip", "link", "add"])
    monkeypatch.setattr(tc.subprocess, "run", run)
    with pytest.raises(tc.TcError, match="ip link add ifb-trafficcop type ifb: Error"):
        tc.apply_setup(CFG, MARKS, "eth0")
    assert run.calls[-3:] == tc.build_teardown("eth0")


QDISCS = [
    {"kind": "htb", "handle": "1:", "root": True, "bytes": 9000, "packets": 9},
    {"kind": "fq_codel", "handle": "8013:", "parent": "1:2", "bytes": 1, "packets": 1},
    {"kind": "cake", "handle": "8014:", "parent": "1:101", "bytes": 1042, "packets": 1},
    {"kind": "cake", "handle": "8015:", "parent": "1:13f", "bytes": 50, "packets": 2},
    {"kind": "cake", "handle": "8016:", "parent": "1:2", "bytes": 7, "packets": 7},
    {"kind": "cake", "handle": "8017:", "parent": "2:101", "bytes": 7, "packets": 7},
    {"kind": "cake", "handle": "8018:", "parent": "1:102"},  # incomplete stats
    {"kind": "ingress", "handle": "ffff:", "parent": "ffff:fff1"},
]


def test_upload_counter_name():
    assert tc.upload_counter_name(0x01000000) == "tc_up_1"
    assert tc.upload_counter_name(0x3F000000) == "tc_up_63"


def test_read_upload_counters(monkeypatch):
    import json
    import subprocess

    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(QDISCS), "")

    monkeypatch.setattr(tc.subprocess, "run", run)
    assert tc.read_upload_counters("wlp2s0") == {
        "tc_up_1": (1, 1042),
        "tc_up_63": (2, 50),
    }
    assert calls == [["tc", "-s", "-j", "qdisc", "show", "dev", "wlp2s0"]]


@pytest.mark.parametrize(("code", "out"), [(1, ""), (0, "not json")])
def test_read_upload_counters_failures(monkeypatch, code, out):
    import subprocess

    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, code, out, "Cannot find device")

    monkeypatch.setattr(tc.subprocess, "run", run)
    assert tc.read_upload_counters("eth9") == {}
