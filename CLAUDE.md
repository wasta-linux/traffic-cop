# Traffic Cop redesign: handoff notes

Repo: https://github.com/wasta-linux/traffic-cop (work happens on a local redesign branch)

## Background

Traffic Cop lets users set and monitor per-app bandwidth limits (upload and
download). Today it ships as a deb and is built from:

- **TrafficToll** (`tt`, from PyPI), which polls psutil for each process's
  connections and writes a `tc` filter per port; it runs `modprobe ifb` and
  shapes downloads on an ifb device.
- **`tt-wrapper`**, a bash script that detects the internet-facing interface,
  run by **`traffic-cop.service`** (systemd).
- A **GTK3 GUI** that shells out to `pkexec` for `nethogs`, `systemctl`,
  `kill` and config resets, and opens the config with
  `gnome-text-editor admin:///etc/traffic-cop.yaml`.
- Config at `/etc/traffic-cop.yaml`, default at
  `/usr/share/traffic-cop/traffic-cop.yaml.default`.

Known weaknesses: port polling is racy and misses short-lived connections;
nethogs doesn't report UDP reliably, and many VoIP apps (Zoom, Skype) use UDP.

## Goal

Rebuild it so it can ship as a **strict-confinement snap**, while still being
able to build a deb from the same source. The work is being done step by
step, starting with a daemon and CLI; the GUI comes last.

## Supported systems

Desktop-agnostic by design: no code for any particular desktop. What it
relies on:

- **systemd and cgroup v2** (unified hierarchy; Ubuntu 21.10+), with a
  per-user systemd manager. The detector only looks at units in each user's
  `user@<uid>.service/app.slice`, plus system services named with the
  `service:` match key (e.g. `snapd.service` for snap updates).
- **Per-app units.** Snap and flatpak apps get their own unit on any such
  desktop (snapd and flatpak create them). Other apps do only if the desktop
  launches them that way: GNOME (`app-gnome-*.scope`) does, and so do
  terminal tabs in VTE terminals. On desktops that don't, deb apps all land in
  the login `session-N.scope` and can't be told apart, so only snap, flatpak
  and named-service apps can be limited there.
- **Kernel/userspace:** nftables with `socket cgroupv2` in the output hook
  (kernel ~5.13+, unverified), the CAKE qdisc and ifb, `python3-nftables`.

Tested so far: Ubuntu with GNOME (kernel 7.0, nftables 1.1.6, Python 3.14).
Not yet tested: KDE (believed to create per-app units), Cinnamon/Mint, Xfce,
MATE. Packaging: the snap targets Ubuntu and other snapd distros; the deb
covers distros without snapd, such as Mint.

## Architecture decisions

**Privilege split.** One root daemon (`traffic-copd`) does everything
privileged: owns the config, detects apps, applies rules, meters traffic.
Clients (`traffic-cop-ctl` CLI now, GTK GUI later) never need root; they talk
to the daemon over a Unix socket in `$SNAP_COMMON` (or `/run` for the deb).
The daemon authorizes callers with `SO_PEERCRED`: anyone can read status and
rates; only `sudo`/`admin` group members can change config or pause/resume.
(System D-Bus + polkit was considered; it's possible later but needs extra
snap store review.)

Consequences:
- "Pause/resume" is a daemon command that removes or re-applies rules, not
  starting and stopping a systemd service.
- The config is edited through the API, which validates it; no `sudo nano`.
- No `pkexec`, no `systemctl` calls, no nethogs, no TrafficToll dependency.

**Identify apps by cgroup v2, not by port.** systemd desktops put each app in
its own scope, e.g. `app-gnome-Zoom-1234.scope`,
`snap.zoom-client.zoom-client-<uuid>.scope`, `app-flatpak-…scope`.
nftables can match packets on the sending socket's cgroup.

**Kernel mechanism.** This has been proven by hand with
`tools/zoom-limit.sh`, which the user confirmed works:

1. nftables `inet` table, output hook (priority mangle):
   `socket cgroupv2 level <N> "<path>" meta mark set <mark> ct mark set meta mark`
   The path is relative to `/sys/fs/cgroup`; the level is its number of
   components.
   **The daemon differs here:** it owns only mark bits `0x3f000000` (bits
   24–29, so 63 app slots; `config.MAX_APPS`), and sets and tests only those
   bits, so other software's marks (WireGuard `0xca6c`, Tailscale `0xff0000`,
   Kubernetes `0x4000`/`0x8000`) survive. It sets `meta mark` and `ct mark`
   separately with `and`/`or` instead of copying, uses `ct mark and <mask> ==`
   for download counters, and `handle <mark>/<mask> fw` filters to app classes
   `1:101`, `1:102`, …; the default class is `1:2`, not `1:20`. Software that
   sets or matches the whole 32-bit mark (e.g. Mullvad) can still conflict.
2. **Upload:** HTB root on the WAN interface (`default 20`). Classes run at
   link speed, so HTB only classifies. A `fw` filter on the mark sends traffic
   to the app's class, whose **CAKE leaf** (`bandwidth <rate> besteffort`)
   does the shaping. The default class gets an `fq_codel` leaf.
3. **Download:** an `ingress` qdisc on WAN with a `matchall` filter running
   `action connmark` (restores the mark from conntrack) and then
   `action mirred egress redirect dev ifb0`. On `ifb0`, the same HTB/CAKE
   layout, but with CAKE in **`ingress` mode**, which counts dropped packets
   as bandwidth already used and so makes senders back off sooner.
4. **Metering:** named nftables counters (output hook for upload; input hook
   matching `ct mark` for download), or `tc -s` class/qdisc stats. Sampling
   them every second gives rates and replaces nethogs; UDP is included.

**Download-limit rationale.** Shaping incoming traffic is the only lever that
works for every protocol, because TCP, QUIC and WebRTC all slow down when
they see loss or delay. Alternatives considered and set aside: delaying
outgoing ACKs (fragile, useless for QUIC); clamping the TCP receive window via
BPF `sock_ops` (precise but TCP-only, per connection, and BPF is hard to use
from a strict snap; possible later addition). Optional improvement: set
`net.ipv4.tcp_ecn=1` so client connections request ECN, letting CAKE mark
instead of drop (TCP only; Zoom media is mostly UDP).

**Units gotcha.** 100 KB/s = `800kbit`. In `tc`, `kbps` means kilo**bytes**
per second. tc counts header bytes too, so totals may differ slightly from the
ISP's; CAKE's `overhead` options can compensate.

## Planned snap interfaces

- Daemon: `network-control` (tc, ip link), `firewall-control` (nftables),
  `system-observe` (cgroup tree, /proc). All need a store review request.
- `kernel-module-load` is probably **not** needed: `ip link add ifb0 type ifb`
  should autoload the module. Verify this under strict confinement.
- GUI: `network` at most.

## File layout

```
traffic-cop/
├── pyproject.toml
├── src/trafficcop/
│   ├── __init__.py            # __version__ = "2.0.0.dev0"
│   ├── common/
│   │   ├── config.py          # schema, load/validate, rate parsing
│   │   ├── paths.py           # snap vs deb locations; only place that knows
│   │   ├── protocol.py        # API messages, socket path, versioning
│   │   └── traffic-cop.yaml.default   # package data (or put in daemon/)
│   ├── daemon/                # traffic-copd
│   │   ├── __main__.py        # startup, signals, teardown on exit
│   │   ├── supervisor.py      # main loop; the only module coordinating others
│   │   ├── netdev.py          # WAN detection + route changes (replaces tt-wrapper)
│   │   ├── detector.py        # app.slice units + named services → config apps
│   │   ├── nft.py             # nftables table/counters, via python3-nftables (text syntax;
│   │   │                      #   JSON input can't express `socket cgroupv2 level`)
│   │   ├── tc.py              # HTB/CAKE/ifb setup
│   │   ├── meter.py           # poll counters → per-app rates
│   │   └── server.py          # Unix socket API + SO_PEERCRED auth
│   ├── cli/__main__.py        # traffic-cop-ctl: status/rates/pause/resume/reload
│   ├── gui/                   # traffic-cop (later): app, window, client, ui/*.glade
│   └── legacy/                # old modules, reference only; never imported
├── data/                      # desktop file, icon, systemd unit (for the deb)
├── tests/
│   ├── unit/                  # no root: config, detector (fake cgroup tree), command generation
│   ├── integration/           # root: real nft/tc in a network namespace (veth pair)
│   ├── data/                  # old good/bad config samples, still useful
│   └── legacy/                # old tests, ignored by pytest
├── tools/zoom-limit.sh        # working proof of concept
├── snap/                      # snapcraft.yaml, hooks/install
└── debian/
```

## pyproject.toml (already on the branch)

- setuptools ≥ 68 (Ubuntu 24.04), `src/` layout, version from
  `trafficcop.__version__`, license `{file = "LICENSE"}` (the SPDX string form
  would need setuptools 77).
- Dependencies: PyYAML, psutil. Extras: `gui` (PyGObject), `dev`
  (pytest, pytest-cov, ruff).
- The daemon also needs `python3-nftables` (the official libnftables
  bindings), which isn't on PyPI: it's a deb dependency, and the snap must
  stage it (it pulls in `libnftables1`). `nft.py` imports it lazily, so the
  unit tests run without it.
- Entry points: `traffic-copd`, `traffic-cop-ctl` (scripts); `traffic-cop`
  (gui-scripts).
- Package data: `gui/ui/*.glade`, the default config. Load these with
  `importlib.resources`, never hardcoded `/usr/share` paths.
- pytest marker `integration`; add `--ignore=tests/legacy` to `addopts`.
- Ubuntu 22.04's setuptools (59) can't read `[project]`. Either keep a
  minimal `setup.py` shim for 22.04 debs or drop 22.04 and use
  `pybuild-plugin-pyproject`. **Undecided.**

## Dev environment

Building CLI-first, so there are no GUI dependencies yet:

```bash
sudo apt install python3-nftables   # not on PyPI
python3 -m venv --system-site-packages .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest -m "not integration"
sudo .venv/bin/traffic-copd          # sudo resets PATH, so use the full path
sudo .venv/bin/pytest -m integration
```

Old modules were moved to `src/trafficcop/legacy/` and old tests to
`tests/legacy/` because they import `gi`. Each entry point currently has a
stub `main()`.

## Build order (each step runnable and testable)

1. `common/config.py`:
2. `daemon/nft.py` + `daemon/tc.py`: reproduce `zoom-limit.sh` from config;
   unit-test the generated commands, integration-test in a netns.
3. `daemon/detector.py`
4. `daemon/meter.py`
5. `daemon/supervisor.py` + `daemon/server.py`
6. `cli/`
7. Port the GUI onto `gui/client.py`
8. Packaging: snap and deb

## Open questions

- **Config schema v2.** The old (TrafficToll) format has global `download`/
  `upload` limits, `*-minimum`, `*-priority`, and per-process entries matched
  by `cmdline` (regex), `exe` (exact path) or `name` (regex). Decide what to
  keep. Notes:
  - Matching now picks a **cgroup scope**, so every process in that scope is
    limited together. Narrow matches such as Firefox's `name: Socket Process`
    can't target just one process inside a scope.
  - **Global limits and priorities** fit HTB (root rate, class `prio`,
    `ceil` borrowing), but then HTB is shaping again, not just classifying.
    Decide how that interacts with CAKE doing the per-app shaping.
- **Changing cgroups.** Scopes are created and destroyed constantly, and
  nftables resolves cgroup paths when rules load. For now the supervisor polls
  `Detector.scan()` and rewrites the output chain
  (`nft.build_output_update`) when it changes. Launching an app creates a
  directory in `app.slice`, so inotify on each user's `app.slice` could
  replace most polling (exe/name matches may still need a re-check after the
  new process execs). An nftables map from cgroup ID to mark is another
  option; verify the target nftables version supports it.
- **Apps started from a terminal** share the terminal's scope.
- **Minimum kernel** for `socket cgroupv2` in the output hook (believed to be
  ~5.13+); check on the oldest supported release.
- Where the default config lives: `common/` vs `daemon/`.
- Exact `SO_PEERCRED` policy (which groups may write).
