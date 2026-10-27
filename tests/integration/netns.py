"""Run test code inside a throwaway unprivileged user+network namespace.

``unshare -rn`` gives the code root inside a fresh network namespace without
real root, so nftables, tc, ifb and the daemon can all be exercised for real
and vanish when the code exits. ``run`` executes a snippet there (after
``PRELUDE``, which sets up a fake WAN) and returns the JSON it prints last.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

BIN = Path(sys.executable).parent  # the venv's scripts: traffic-copd, -ctl

PRELUDE = '''
import json, os, signal, socket, subprocess, sys, time
from pathlib import Path


def sh(*args):
    subprocess.run(args, check=True, capture_output=True)


def out(*args):
    return subprocess.run(args, capture_output=True, text=True).stdout


def wan():
    """wan0: a veth "uplink" with a default route via 10.0.0.9."""
    sh("ip", "link", "set", "lo", "up")
    sh("ip", "link", "add", "wan0", "type", "veth", "peer", "name", "peer0")
    sh("ip", "link", "set", "wan0", "up")
    sh("ip", "link", "set", "peer0", "up")
    sh("ip", "addr", "add", "10.0.0.1/24", "dev", "wan0")
    sh("ip", "route", "add", "default", "via", "10.0.0.9")
    # A static neighbour, so packets really leave through wan0.
    sh("ip", "neigh", "add", "10.0.0.9", "lladdr", "02:00:00:00:00:09", "dev", "wan0")


def own_cgroup():
    return Path("/proc/self/cgroup").read_text().split("0::", 1)[1].strip().strip("/")


def send(count, size=1000, to=("1.1.1.1", 9), gap=0.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for _ in range(count):
        s.sendto(b"x" * size, to)
        if gap:
            time.sleep(gap)


def result(value):
    print(json.dumps(value))
'''


def available() -> str | None:
    """Why these tests can't run here, or None if they can."""
    if shutil.which("unshare") is None:
        return "unshare isn't installed"
    probe = subprocess.run(
        ["unshare", "-rn", sys.executable, "-c", "import nftables"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        return (
            "no user namespaces or no python3-nftables: " + probe.stderr.strip()[-200:]
        )
    return None


def run(body: str, timeout: float = 60) -> Any:
    """Run ``body`` (after PRELUDE) in a new namespace; return its last JSON."""
    script = PRELUDE + textwrap.dedent(body)
    proc = subprocess.run(
        ["unshare", "-rn", sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        pytest.fail(f"namespace script failed:\n{proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])
