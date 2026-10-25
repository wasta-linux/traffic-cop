"""Real nftables, tc, routing and the daemon, in throwaway namespaces.

No root needed: see netns.py. Run with ``pytest -m integration``.
"""

import pytest

from tests.integration import netns

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(netns.available() is not None, reason=str(netns.available())),
]


def test_nft_marks_and_counts_an_apps_traffic():
    r = netns.run("""
        from trafficcop.common import config
        from trafficcop.daemon import nft
        wan()
        cfg = config.loads("version: 2\\napps:\\n  Me: {match: [{name: x}]}\\n")
        marks = nft.assign_marks(cfg)
        nft.apply(nft.build_ruleset(marks, {"Me": [own_cgroup()]}))
        send(5, to=("1.1.1.1", 9))
        send(3, to=("127.0.0.1", 9))  # loopback: marked, not counted
        before = nft.read_counters()["up_1"]
        nft.apply(nft.build_output_update(marks, {"Me": [own_cgroup()]}))
        after_update = nft.read_counters()["up_1"]
        nft.apply(nft.build_teardown())
        nft.apply(nft.build_teardown())  # twice: must not fail
        result({"before": before, "after_update": after_update,
                "tables": out("nft", "list", "tables").strip()})
    """)
    assert r["before"] == [5, 5 * 1028]  # 1000 payload + UDP + IP headers
    assert r["after_update"] == r["before"]  # rewriting the chain keeps counts
    assert r["tables"] == ""


def test_limited_upload_is_shaped_and_metered_after_the_queue():
    r = netns.run("""
        from trafficcop.common import config
        from trafficcop.daemon import meter, nft, tc
        wan()
        cfg = config.loads(
            "version: 2\\napps:\\n  Me: {upload: 25kbps, match: [{name: x}]}\\n"
        )
        marks = nft.assign_marks(cfg)
        tc.apply_setup(cfg, marks, "wan0")
        nft.apply(nft.build_ruleset(marks, {"Me": [own_cgroup()]}))
        def counters():
            return {**nft.read_counters(), **tc.read_upload_counters("wan0")}
        m = meter.Meter(marks, counters)
        start = time.monotonic()
        send(400, gap=0.01)  # ~100 kB/s for ~4 s
        time.sleep(0.5)
        usage = m.sample()["Me"]
        elapsed = time.monotonic() - start
        result({"sent_by_app": nft.read_counters()["up_1"][1],
                "metered": usage.up_total, "seconds": elapsed})
    """)
    rate = r["metered"] / r["seconds"]
    assert 20_000 < rate < 30_000  # 25 kB/s, give or take
    assert r["sent_by_app"] > 2 * r["metered"]  # CAKE held back the rest


def test_tc_setup_is_repeatable_and_teardown_is_complete():
    r = netns.run("""
        from trafficcop.common import config
        from trafficcop.daemon import nft, tc
        wan()
        cfg = config.load_default()
        marks = nft.assign_marks(cfg)
        tc.apply_setup(cfg, marks, "wan0")
        tc.apply_setup(cfg, marks, "wan0")
        cake = out("tc", "qdisc", "show", "dev", tc.IFB).count("cake")
        ingress = "ingress" in out("tc", "qdisc", "show", "dev", "wan0")
        tc.apply(tc.build_teardown("wan0"), check=False)
        result({"ifb_cakes": cake, "ingress": ingress,
                "after": out("tc", "qdisc", "show", "dev", "wan0").split()[1],
                "ifb_left": out("ip", "link", "show", tc.IFB) != ""})
    """)
    assert r["ifb_cakes"] == 4  # one per app with a download limit
    assert r["ingress"]
    assert r["after"] == "noqueue"
    assert not r["ifb_left"]


def test_wan_detection_follows_routes_and_vpns():
    r = netns.run("""
        from trafficcop.daemon import netdev
        seen = {"offline": netdev.wan_device()}
        wan()
        seen["wired"] = netdev.wan_device()
        # A full-tunnel VPN the wg-quick way: own table, "not fwmark" rule.
        sh("ip", "link", "add", "tun0", "type", "veth", "peer", "name", "tunpeer")
        sh("ip", "link", "set", "tun0", "up")
        sh("ip", "addr", "add", "10.2.0.2/32", "dev", "tun0")
        sh("ip", "route", "add", "default", "dev", "tun0", "table", "51820")
        sh("ip", "rule", "add", "not", "fwmark", "0xca6c", "table", "51820")
        seen["vpn"] = netdev.wan_device()
        # Proton's IPv6 leak sink must never be chosen.
        sh("ip", "rule", "del", "not", "fwmark", "0xca6c", "table", "51820")
        sh("ip", "route", "del", "default")
        sh("ip", "link", "add", "leak0", "type", "dummy")
        sh("ip", "link", "set", "leak0", "up")
        sh("ip", "-6", "addr", "add", "fd00::1/64", "dev", "leak0")
        sh("ip", "-6", "route", "add", "default", "via", "fd00::2", "dev", "leak0")
        seen["dummy_only"] = netdev.wan_device()
        result(seen)
    """)
    assert r == {"offline": None, "wired": "wan0", "vpn": "tun0", "dummy_only": None}


def test_daemon_answers_the_cli_and_cleans_up(tmp_path):
    cfg = tmp_path / "traffic-cop.yaml"
    sock = tmp_path / "s.sock"
    daemon_bin = netns.BIN / "traffic-copd"
    ctl_bin = netns.BIN / "traffic-cop-ctl"
    r = netns.run(f"""
        wan()
        daemon = subprocess.Popen(
            ["{daemon_bin}", "--config", "{cfg}", "--socket", "{sock}"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        for _ in range(100):
            if Path("{sock}").exists():
                break
            time.sleep(0.05)

        def ctl(*args):
            p = subprocess.run(
                ["{ctl_bin}", "--socket", "{sock}", "--json", *args],
                capture_output=True, text=True, env={{**os.environ, "LANGUAGE": "en"}},
            )
            return json.loads(p.stdout) if p.returncode == 0 else p.returncode

        status = ctl("status")
        paused = ctl("pause")
        qdisc_paused = out("tc", "qdisc", "show", "dev", "wan0").split()[1]
        Path("{tmp_path}/new.yaml").write_text(
            "version: 2\\napps:\\n  Only: {{download: 1mbit, match: [{{snap: x}}]}}\\n")
        changed = ctl("config", "set", "{tmp_path}/new.yaml")
        daemon.send_signal(signal.SIGTERM)
        code = daemon.wait(10)
        result({{
            "wan": status["shaping"], "apps": len(status["apps"]),
            "paused": paused["paused"], "qdisc_paused": qdisc_paused,
            "changed": [a["name"] for a in changed["apps"]],
            "exit": code, "socket_left": Path("{sock}").exists(),
            "tables": out("nft", "list", "tables").strip(),
        }})
    """)
    assert r["wan"] == "wan0"
    assert r["apps"] == 4  # the default config, written on first start
    assert r["paused"] is True and r["qdisc_paused"] == "noqueue"
    assert r["changed"] == ["Only"]
    assert r["exit"] == 0 and not r["socket_left"] and r["tables"] == ""
    assert "Only:" in cfg.read_text()  # saved
