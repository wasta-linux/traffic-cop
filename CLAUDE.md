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

Rebuild it to ship as a **strict-confinement snap**. The snap is the only
package; building a deb is no longer a goal. The work is being done step by
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
  terminal tabs in VTE terminals. On desktops that don't, other apps all land in
  the login `session-N.scope` and can't be told apart, so only snap, flatpak
  and named-service apps can be limited there. Even on GNOME, an app whose
  launcher starts the real program immediately can escape its scope and
  can't be limited (e.g. Zoom's deb); see "Apps that escape their scope".
- **Kernel/userspace:** nftables with `socket cgroupv2` in the output hook
  (kernel ~5.13+, unverified), the CAKE qdisc and ifb, `python3-nftables`.

Tested so far: Ubuntu with GNOME (kernel 7.0, nftables 1.1.6, Python 3.14).
Not yet tested: KDE (believed to create per-app units), Cinnamon/Mint, Xfce,
MATE. Packaging: snap only, so it needs a distro with snapd. Mint blocks
snapd by default, so Mint users would have to enable it first.

## Architecture decisions

**Privilege split.** One root daemon (`traffic-copd`) does everything
privileged: owns the config, detects apps, applies rules, meters traffic.
Clients (`traffic-cop-ctl` CLI now, GTK GUI later) never need root; they talk
to the daemon over a Unix socket in `$SNAP_COMMON`.
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

## Snap interfaces and confinement

- Daemon: `network-control` (ip, tc), `firewall-control` (nftables),
  `system-observe` (/proc), `network-observe` (interface byte counts). None
  auto-connect; all need a store review request. Plus `network-bind`
  (auto-connects): without it seccomp refuses `bind`/`listen` even on the
  daemon's Unix socket. `kernel-module-load` is **not** needed: with `ifb`
  unloaded, the snapped daemon's `ip link add … type ifb` loaded it.
- GUI (tkinter): `x11` (Tk runs through XWayland on Wayland desktops), and
  the daemon's socket. Stage `python3-tk` and `fonts-sil-andika`; `gui/fonts.py`
  registers the snap's Andika with fontconfig before Tk starts.

**Tested under strict confinement** with `tools/snap-permtest/` (a `snap try`
snap on core26, run as root; see its `prepare.sh`), with those four
interfaces connected:

| works | refused |
|---|---|
| ifb link, full tc upload/download trees (HTB, CAKE, masked `fw`, ingress, `connmark`, `mirred`) | listing `/sys/fs/cgroup` (`system-observe` allows only a few cgroup files) |
| nft table with `socket cgroupv2`, masked marks, counters | reading `/proc/<pid>/exe` of another user's process (snapd adds `deny capability sys_ptrace`; explicit denies aren't logged) |
| `/proc/<pid>/cgroup`, `comm`, `cmdline`; `ip route get` | writing `cgroup.procs` (moving a process); no ordinary interface allows it |
| `/sys/class/net/*/statistics` (needs `network-observe`) | |

Consequences: the detector finds units from `/proc/<pid>/cgroup`, never from
sysfs; `exe` matches fall back to `argv[0]` when the executable is
unreadable.

**Snap build notes** (`snap/snapcraft.yaml`, built locally with LXD):
`core26` has no Python, so the interpreter is staged (via `python3-tk`).
The python plugin then finds that staged `python3` first on `PATH` (from the
part's `install/usr/bin`, and from `stage/bin` on rebuilds), which has no
`ensurepip`, and its venv fails. The part sets `PATH: /usr/bin:$PATH` in
`build-environment` and clears the previous venv in `override-build`.
Confined `ip -d link show` reads `/usr/share/iproute2/group` (denied), so
`netdev.link_kind` caches per interface to avoid a denial every poll.

## File layout

```
traffic-cop/
├── pyproject.toml
├── src/trafficcop/
│   ├── __init__.py            # __version__ = "2.0.0.dev0"
│   ├── common/
│   │   ├── config.py          # schema, load/validate, rate parsing
│   │   ├── paths.py           # snap vs development locations; only place that knows
│   │   ├── protocol.py        # API messages (JSON lines), versioning, client call()
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
│   ├── gui/                   # traffic-cop (tkinter/ttk): client, model, window
│   └── legacy/                # old modules, reference only; never imported
├── tests/
│   ├── unit/                  # no root: config, detector (fake cgroup tree), command generation
│   ├── integration/           # real nft/tc/daemon in an unprivileged netns (unshare -rn)
│   └── legacy/                # old tests, ignored by pytest
├── tools/zoom-limit.sh        # working proof of concept
└── snap/                      # snapcraft.yaml; gui/: launcher (gui.desktop), icon.svg
```

## pyproject.toml (already on the branch)

- setuptools ≥ 68 (Ubuntu 24.04), `src/` layout, version from
  `trafficcop.__version__`, license `{file = "LICENSE"}` (the SPDX string form
  would need setuptools 77).
- Dependencies: PyYAML. Extras: `dev`
  (pytest, pytest-cov, ruff).
- The daemon also needs `python3-nftables` (the official libnftables
  bindings), which isn't on PyPI: the snap must stage it (it pulls in
  `libnftables1`). `nft.py` imports it lazily, so the
  unit tests run without it.
- Entry points: `traffic-copd`, `traffic-cop-ctl` (scripts); `traffic-cop`
  (gui-scripts).
- Package data: the default config. Load it with
  `importlib.resources`, never hardcoded `/usr/share` paths.
- pytest marker `integration`; add `--ignore=tests/legacy` to `addopts`.

## Dev environment

Building CLI-first, so there are no GUI dependencies yet:

```bash
sudo apt install python3-nftables python3-tk   # not on PyPI
python3 -m venv --system-site-packages .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest -m "not integration"
sudo .venv/bin/traffic-copd          # sudo resets PATH, so use the full path
                                     # (dev paths: /var/lib/traffic-cop-dev, /run/traffic-cop-dev)
pytest -m integration               # no sudo: runs in `unshare -rn` namespaces
```

Old modules were moved to `src/trafficcop/legacy/` and old tests to
`tests/legacy/` because they import `gi`. They're reference only: excluded
from pytest, ruff and pyright.

## Translations

English and French, via gettext (`common/i18n.py`; domain `traffic-cop`).
Catalogues are package data in `src/trafficcop/locale/<lang>/LC_MESSAGES/`;
both `.po` and compiled `.mo` are committed. After changing user-facing
strings in the CLI or GUI, run `tools/i18n.sh` (needs `python3-babel`),
translate new or fuzzy entries, and run it again. New language:
`tools/i18n.sh add es`. The decimal separator and units (`ko/s`, `Mo`) are
translated strings, so numbers follow the interface language even without
system locale data. The daemon's log and its error messages stay English.
Tests run in English (`tests/conftest.py`); `test_i18n.py` checks each
catalogue is complete and keeps every `{placeholder}`.

## Build order (each step runnable and testable)

Steps 1–7 are done (also `common/paths.py`, `common/protocol.py`,
`common/display.py`, `common/i18n.py`, `daemon/netdev.py`).

1. ✓ `common/config.py`
2. ✓ `daemon/nft.py` + `daemon/tc.py`: reproduce `zoom-limit.sh` from config;
   unit tests for the generated commands, integration tests in a netns.
3. ✓ `daemon/detector.py`
4. ✓ `daemon/meter.py`
5. ✓ `daemon/supervisor.py` + `daemon/server.py`
6. ✓ `cli/`
7. ✓ GUI (tkinter + ttk, chosen over GTK 4 to avoid the ~600 MB GNOME
   runtime snap; costs: X11 via XWayland, plain look, weak screen-reader
   support): 7a read-only window + pause button; 7b editing apps.
8. Packaging: snap `wasta-traffic-cop` (apps: `daemon`, `wasta-traffic-cop`
   = the CLI, `gui`). Built and installed locally: the daemon runs as a
   service under strict confinement and shapes, the CLI reaches its socket as
   a user, ifb loads without `kernel-module-load`. Still to check: the GUI
   from the menu, a real limit, start after reboot; then store registration
   and the interface auto-connect review.

Ideas after that: a "pick a running app" list in the editor (the daemon
would report the app units it sees, with their traffic); a warning for apps
that escaped their scope; a short "recent events" list from the daemon.

## Open questions

- **Global limits and priorities** (from the 1.x format) aren't in config
  v2, which has per-app `download`/`upload` and `match` only; 1.x configs are
  rejected. They'd fit HTB (root rate, class `prio`, `ceil` borrowing), but
  then HTB shapes again rather than only classifying; decide how that would
  interact with CAKE before adding them. Matching picks a whole unit, so a
  narrow 1.x match like Firefox's `name: Socket Process` can't target one
  process inside it.
- **Changing cgroups.** Scopes are created and destroyed constantly, and
  nftables resolves cgroup paths when rules load. For now the supervisor polls
  `Detector.scan()` and rewrites the output chain
  (`nft.build_output_update`) when it changes. Launching an app creates a
  directory in `app.slice`, so inotify on each user's `app.slice` could
  replace most polling (exe/name matches may still need a re-check after the
  new process execs). An nftables map from cgroup ID to mark is another
  option; verify the target nftables version supports it.
- **Apps started from a terminal** share the terminal's scope.
- **Apps that escape their scope.** GNOME creates an app's scope just *after*
  starting it. Children the app starts in that window stay in GNOME Shell's
  own cgroup (`session.slice/org.gnome.Shell@….service`), which is never
  matched. Seen with Zoom's deb (`ZoomLauncher` starts `/opt/zoom/zoom`,
  which carries meeting traffic, and its embedded Chromium creates its own
  `app-org.chromium.Chromium-<pid>.scope`) and Zed (`bin/zed` starts
  `libexec/zed-editor` and exits). Snap and flatpak apps are immune. As root,
  writing the escaped PIDs to the launcher's scope `cgroup.procs` fixes it
  (tested with Zoom), but a strict snap can't. Options: document and
  recommend snap/flatpak versions (current choice); ask the store for a
  privileged interface or classic confinement; have the GUI add per-user
  desktop-entry overrides that launch via `systemd-run --user --scope`
  (needs `personal-files`); or reconsider packaging.
- **Minimum kernel** for `socket cgroupv2` in the output hook (believed to be
  ~5.13+); check on the oldest supported release.
- `SO_PEERCRED` policy: decided for now (root, or a member of `sudo`, `admin`
  or `wheel`, read from the caller's `/proc/<pid>/status`; see `server.py`).
  Revisit if the GUI needs per-user permissions.
