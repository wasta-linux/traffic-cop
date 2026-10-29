# Traffic Cop 2.x: project notes

Repo: https://github.com/wasta-linux/traffic-cop. The 2.x rewrite is on
`main` (merged from `snap`, PR #1). Current work branch: `indicator`.

## Current state (2026-10-29)

- Daemon, CLI and GUI are done and tested (364 unit tests; integration tests
  in network namespaces).
- The snap `wasta-traffic-cop` is registered (publisher `n8marti`), and
  revision 2 (2.0.0.dev0) is on `latest/edge`.
- The store request for auto-connecting `network-control`,
  `firewall-control` and `system-observe` (see "Store review") has been made;
  waiting on a response.
- **In progress:** the tray indicator (see "Indicator design"). Step 1 done
  unconfined: `indicator/sni.py` + a minimal `__main__.py` register with
  GNOME's watcher, and the menu works (checked with `busctl`). Next: check
  the icon in the panel, then build the snap and find which interface
  (`unity7` / `desktop-legacy`) the confined indicator needs.

## Working with the maintainer

- Code should be easy for non-AI developers (including the maintainer, later)
  to follow: plain names, comments that say *why*, docstrings that record
  decisions and the evidence for them, and how to re-check it.
- Match the surrounding code's style and comment density. Absolute imports
  (`from trafficcop.common import config`). Shared constants live once, in
  `common/` (e.g. `protocol.WRITER_GROUPS`, used by daemon and GUI).
- Before finishing a change: `ruff format`, `ruff check`, `pytest -m "not
  integration"`; update `.po` files for changed user-facing strings (see
  "Translations"); update this file when a decision or the state changes.
- The maintainer usually commits; ask before committing. Commit subjects are
  short and lowercase (e.g. "define the admin group list once, in
  protocol.py"), with a body when the why isn't obvious.
- Claims about external tools should be checked (run it, or read the
  source), not repeated from memory.

## Background

Traffic Cop sets and monitors per-app bandwidth limits (upload and
download). Version 1.x shipped as a deb built from:

- **TrafficToll** (`tt`, from PyPI), which polled psutil for each process's
  connections and wrote a `tc` filter per port, shaping downloads on an ifb.
- **`tt-wrapper`**, a bash script that detected the internet-facing
  interface, run by **`traffic-cop.service`**.
- A **GTK3 GUI** that shelled out to `pkexec` for `nethogs`, `systemctl`,
  `kill` and config resets.
- Config at `/etc/traffic-cop.yaml`.

Its weaknesses: port polling is racy and misses short-lived connections;
nethogs doesn't report UDP reliably, and VoIP apps (Zoom, Skype) use UDP.

## Goal

A rewrite that ships as a **strict-confinement snap**, the only package
(no deb). 1.x configs are rejected; users set their apps up again.

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
  terminal tabs in VTE terminals. On desktops that don't, other apps all land
  in the login `session-N.scope` and can't be told apart, so only snap,
  flatpak and named-service apps can be limited there. Even on GNOME, an app
  whose launcher starts the real program immediately can escape its scope
  and can't be limited (e.g. Zoom's deb); see "Apps that escape their scope".
- **Kernel/userspace:** nftables with `socket cgroupv2` in the output hook
  (kernel ~5.13+, unverified), the CAKE qdisc and ifb, `python3-nftables`.

Tested so far: Ubuntu 26.04 with GNOME (kernel 7.0, nftables 1.1.6, Python
3.14). Not yet tested: KDE (believed to create per-app units), Cinnamon/Mint,
Xfce, MATE. Mint blocks snapd by default, so Mint users would have to enable
it first.

## Architecture decisions

**Privilege split.** One root daemon (`traffic-copd`) does everything
privileged: owns the config, detects apps, applies rules, meters traffic.
Clients (the CLI `traffic-cop-ctl`, the tkinter GUI `traffic-cop`) never
need root; they talk to the daemon over a Unix socket in `$SNAP_COMMON`
(JSON lines; `common/protocol.py`).
The daemon authorizes callers with `SO_PEERCRED`: anyone can read status and
rates; only root or members of `protocol.WRITER_GROUPS` (`sudo`, `admin`,
`wheel`, read from the caller's `/proc/<pid>/status`) can change the config
or pause/resume. The GUI uses the same list to show the editor read-only.
(System D-Bus + polkit was considered; possible later, but needs extra store
review.)

Consequences:
- "Pause/resume" is a daemon command that removes or re-applies the tc
  shaping (metering continues), not starting and stopping a service. The
  daemon remembers it across restarts and reboots (`state.json` beside the
  config, `paths.Paths.state`) and starts paused on a fresh install, when
  there's no state file yet.
- The config is edited through the API, which validates it; no `sudo nano`.
- No `pkexec`, no `systemctl` calls, no nethogs, no TrafficToll dependency.

**Identify apps by cgroup v2, not by port.** systemd desktops put each app in
its own scope, e.g. `app-gnome-Zoom-1234.scope`,
`snap.zoom-client.zoom-client-<uuid>.scope`, `app-flatpak-…scope`.
nftables can match packets on the sending socket's cgroup. **The socket's
cgroup is fixed when the socket is created**: moving a process to another
cgroup later doesn't change its open sockets (see "Apps that escape their
scope").

**Kernel mechanism.** First proven by hand with `tools/zoom-limit.sh`:

1. nftables `inet trafficcop` table, output hook (priority mangle):
   `socket cgroupv2 level <N> "<path>"` sets the app's mark. The path is
   relative to `/sys/fs/cgroup`; the level is its number of components.
   The daemon owns only mark bits `0x3f000000` (bits 24–29, so 63 app slots;
   `config.MAX_APPS`) and sets and tests only those bits, so other software's
   marks (WireGuard `0xca6c`, Tailscale `0xff0000`, Kubernetes
   `0x4000`/`0x8000`) survive. It sets `meta mark` and `ct mark` separately
   with `and`/`or` instead of copying, and uses `ct mark and <mask> ==` for
   download counters. Software that sets or matches the whole 32-bit mark
   (e.g. Mullvad) can still conflict. Rules are nft's **text syntax**, loaded
   through libnftables: JSON input can't express `socket cgroupv2` (its
   parser accepts only the socket keys transparent/mark/wildcard; checked
   against 1.1.6 and upstream 1.1.7-dev; see `nft.py`'s docstring).
2. **Upload:** HTB root on the WAN interface (`default 2`). Classes run at
   link speed (`LINK_RATE`), so HTB only classifies. A `fw` filter
   (`handle <mark>/0x3f000000`) sends traffic to the app's class `1:101`,
   `1:102`, …, whose **CAKE leaf** (`bandwidth <rate> besteffort`) does the
   shaping. The default class `1:2` gets an `fq_codel` leaf.
3. **Download:** an `ingress` qdisc on WAN with a `matchall` filter running
   `action connmark` (restores the mark from conntrack) and then
   `action mirred egress redirect dev ifb-trafficcop`. There, the same
   HTB/CAKE layout, but with CAKE in **`ingress` mode**, which counts dropped
   packets as bandwidth already used and so makes senders back off sooner.
4. **Metering** (`meter.py`), sampled every second:
   - apps without a limit: the nft counters `up_<slot>`/`down_<slot>`;
   - limited uploads: the CAKE leaf's `bytes` (what was actually sent);
   - limited downloads: the CAKE tins' `sent_bytes`, i.e. delivered *plus
     dropped*, which is what the ingress limit is charged.
   Rates are smoothed (EWMA, `SMOOTHING = 3.0` s): shaped traffic is bursty,
   so per-second values swing widely while the average holds. Totals are
   exact. Loopback is excluded from the counts.

**Download-limit rationale.** Shaping incoming traffic is the only lever that
works for every protocol, because TCP, QUIC and WebRTC all slow down when
they see loss or delay. Measured: Firefox at a 30 kB/s limit was charged
26–30 kB/s and received a steady ~21.5 kB/s; the rest was dropped. Set aside:
delaying outgoing ACKs (fragile, useless for QUIC); clamping the TCP receive
window via BPF `sock_ops` (TCP-only, and BPF is hard from a strict snap).
Possible opt-in: `net.ipv4.tcp_ecn=1`, so CAKE can mark instead of drop (TCP
only; Zoom media is mostly UDP).

**WAN detection** (`netdev.py`): `ip -j route get 1.1.1.1` (then the IPv6
probe), which follows policy routing, so a full-tunnel VPN's interface is
chosen; that's where apps' own marked packets are visible. Dummy interfaces
are skipped (Proton VPN's IPv6 leak sink). Re-checked every 5 s.

**Units gotcha.** 100 KB/s = `800kbit`. In `tc`, `kbps` means kilo**bytes**
per second. tc counts header bytes too, so totals may differ slightly from
the ISP's; CAKE's `overhead` options can compensate.

## Snap

`snap/snapcraft.yaml`: name `wasta-traffic-cop` (title "Traffic Cop"),
`core26`, strict, amd64 + arm64. Apps: `daemon` (service), `wasta-traffic-cop`
(the CLI, named like the snap), `gui` (launcher `snap/gui/gui.desktop`).

**Interfaces.**
- Daemon: `network-control` (ip, tc), `firewall-control` (nftables),
  `system-observe` (/proc). None auto-connect; see "Store review". Plus
  `network-bind` (auto-connects): without it seccomp refuses `bind`/`listen`
  even on the daemon's Unix socket. `kernel-module-load` is **not** needed:
  the confined `ip link add … type ifb` loads `ifb`. `network-observe` was
  dropped: nothing reads whole-interface counts.
- CLI: `home` (for `config set FILE`).
- GUI (tkinter): `x11` (Tk runs through XWayland), `desktop`, `home`. Stages
  `python3-tk` and `fonts-sil-andika`; `gui/fonts.py` registers the snap's
  Andika with fontconfig before Tk starts.

**Tested under strict confinement** with `tools/snap-permtest/` (a `snap try`
snap on core26, run as root; see its `prepare.sh`), with those three
interfaces and `network-observe` connected:

| works | refused |
|---|---|
| ifb link, full tc upload/download trees (HTB, CAKE, masked `fw`, ingress, `connmark`, `mirred`) | listing `/sys/fs/cgroup` (`system-observe` allows only a few cgroup files) |
| nft table with `socket cgroupv2`, masked marks, counters | reading `/proc/<pid>/exe` of another user's process (snapd adds `deny capability sys_ptrace`; explicit denies aren't logged) |
| `/proc/<pid>/cgroup`, `comm`, `cmdline`; `ip route get` | writing `cgroup.procs` (moving a process); no ordinary interface allows it |
| `/sys/class/net/*/statistics` (needs `network-observe`) | |

Consequences: the detector finds units from `/proc/<pid>/cgroup`, never from
sysfs; `exe` matches fall back to `argv[0]` when the executable is
unreadable.

**Verified on a real install:** the daemon runs and shapes; the CLI reaches
the socket as a user; the GUI opens from the menu; a real limit holds; it
starts at boot (before the network: it meters only, then shapes once a WAN
appears); a fresh install starts paused.

**Build notes** (built locally with LXD: `snapcraft pack`, then
`sudo snap install --dangerous ./wasta-traffic-cop_*.snap`): `core26` has no
Python, so the interpreter is staged (via `python3-tk`). The python plugin
then finds that staged `python3` first on `PATH` (from the part's
`install/usr/bin`, and from `stage/bin` on rebuilds), which has no
`ensurepip`, and its venv fails. The part sets `PATH: /usr/bin:$PATH` in
`build-environment` and clears the previous venv in `override-build`.
Confined `ip -d link show` reads `/usr/share/iproute2/group` (denied), so
`netdev.link_kind` caches per interface to avoid a denial every poll.

**Store review.** The auto-connect request goes in the Snapcraft forum's
store-requests category, for the `daemon` app only, with a justification per
interface: `network-control` (HTB/CAKE trees, the ifb device and its ingress
redirect, `ip route get`, reading CAKE stats), `firewall-control` (one table
`inet trafficcop`, masked marks only, removed on stop/pause),
`system-observe` (`/proc/<pid>/cgroup`, `comm`, `cmdline`, `exe` of all
users' processes, and callers' `status`). Points to make: clients never get
these interfaces; writes need an admin group; a fresh install starts paused,
so auto-connecting doesn't shape anyone's traffic unexpectedly. A full draft
was written; check with the maintainer whether it has been posted.

**Missing connections** (idea, not built): a strict snap can't connect its
own plugs, and snapd's runtime prompting doesn't cover these interfaces. The
daemon could check `snapctl is-connected <plug>` at startup and report missing
ones in `status`, so the CLI and GUI show the `sudo snap connect …` commands
(plus restarting the daemon) instead of failing vaguely.

## File layout

```
traffic-cop/
├── pyproject.toml
├── README.md, screenshot.png       # user-facing docs (screenshot: cropped GUI, English)
├── src/trafficcop/
│   ├── __init__.py                 # __version__ = "2.0.0.dev0"
│   ├── common/
│   │   ├── config.py               # schema, load/validate/dump, rate parsing
│   │   ├── paths.py                # snap vs development locations; the only place that knows
│   │   ├── protocol.py             # API (JSON lines), commands, WRITER_GROUPS, client call()
│   │   ├── display.py              # rates/sizes/state as text (shared by CLI and GUI)
│   │   ├── i18n.py                 # gettext: _, ngettext, pgettext, number
│   │   └── traffic-cop.yaml.default
│   ├── locale/fr/LC_MESSAGES/      # traffic-cop.po + .mo
│   ├── daemon/                     # traffic-copd
│   │   ├── __main__.py             # startup, signals, teardown on exit
│   │   ├── supervisor.py           # main loop, pause state; the only module coordinating others
│   │   ├── netdev.py               # WAN detection (replaces tt-wrapper)
│   │   ├── detector.py             # /proc/<pid>/cgroup → app units → config apps
│   │   ├── nft.py                  # nftables table/counters (text syntax, python3-nftables)
│   │   ├── tc.py                   # HTB/CAKE/ifb setup; CAKE counters
│   │   ├── meter.py                # counters → smoothed per-app rates and totals
│   │   └── server.py               # Unix socket API + SO_PEERCRED auth
│   ├── cli/__main__.py             # traffic-cop-ctl: status/rates/pause/resume/reload/config
│   ├── gui/                        # traffic-cop (tkinter/ttk)
│   │   ├── client.py               # daemon calls on a background thread + queue
│   │   ├── model.py                # status/usage → view (pure, tested)
│   │   ├── window.py               # main window, Wasta palette, bars
│   │   ├── editor.py, dialog.py    # editing apps; may_change()
│   │   └── fonts.py                # registers the snap's Andika
│   └── indicator/                  # tray indicator: just started (see "Indicator design")
├── tests/
│   ├── unit/                       # no root; fakes.py stands in for System and /proc
│   └── integration/                # real nft/tc in an unprivileged netns (unshare -rn)
├── tools/
│   ├── zoom-limit.sh               # the original proof of concept
│   ├── snap-permtest/              # confinement tests (see "Snap")
│   └── i18n.sh                     # extract/update/compile translations
└── snap/                           # snapcraft.yaml; gui/: gui.desktop, icon.svg
```

## pyproject.toml

- setuptools ≥ 68 (Ubuntu 24.04), `src/` layout, version from
  `trafficcop.__version__`, license `{file = "LICENSE"}` (the SPDX string form
  would need setuptools 77).
- Dependencies: PyYAML. Extras: `dev` (pytest, pytest-cov, ruff).
- The daemon also needs `python3-nftables` (the official libnftables
  bindings), which isn't on PyPI: the snap stages it (it pulls in
  `libnftables1`). `nft.py` imports it lazily, so the unit tests run without
  it.
- Entry points: `traffic-copd`, `traffic-cop-ctl` (scripts); `traffic-cop`
  (gui-scripts).
- Package data: the default config and the `.mo` files. Load them with
  `importlib.resources`, never hardcoded `/usr/share` paths.
- pyright: standard mode, venv `.venv`, `extraPaths` for
  `/usr/lib/python3/dist-packages` (nftables, tkinter).

## Dev environment

```bash
sudo apt install python3-nftables python3-tk   # not on PyPI
python3 -m venv --system-site-packages .venv && . .venv/bin/activate
pip install -e '.[dev]'
ruff format src tests && ruff check src tests
pytest -m "not integration"
pytest -m integration               # no sudo: runs in `unshare -rn` namespaces
sudo .venv/bin/traffic-copd -v      # sudo resets PATH, so use the full path
                                    # (dev paths: /var/lib/traffic-cop-dev, /run/traffic-cop-dev)
.venv/bin/traffic-cop --socket /var/snap/wasta-traffic-cop/common/traffic-cop.sock
                                    # source GUI against the installed snap's daemon
```

## Debugging

When a limit doesn't hold, check what the daemon actually set up. Each app's
slot is its position in the config (1, 2, …; `nft.assign_marks`): its mark
is `slot << 24` (slot 1 = `0x01000000`), its counters are `up_<slot>` and
`down_<slot>`, and its tc class is `1:<0x100 + slot>` in hex (slot 1 =
`1:101`, slot 10 = `1:10a`).

```bash
wasta-traffic-cop status             # paused? which interface is shaped? which units matched?
wasta-traffic-cop --json rates       # the meter's numbers, in bytes per second

# Marking and counting (nftables). Rules list an app's cgroup path only
# while it's running; no rule means the detector found no unit.
sudo nft list table inet trafficcop
sudo nft list counters table inet trafficcop

# Shaping (tc). <wan> is the interface from `status` (or `ip route get 1.1.1.1`).
tc -s qdisc show dev <wan>           # upload: a CAKE leaf per limited app
tc -s class show dev <wan>           # HTB classes 1:101, …; 1:2 is unlimited traffic
tc -s filter show dev <wan> ingress  # downloads: connmark + mirred to the ifb
tc -s qdisc show dev ifb-trafficcop  # download CAKE leaves; "drops" = shaping working

# Which unit is an app in, and which cgroup are its *sockets* in?
cat /proc/$(pgrep -o firefox)/cgroup
ss -tuap --cgroup | grep -i zoom     # must show the app's scope, not org.gnome.Shell@…

# The daemon's log
sudo snap logs -f wasta-traffic-cop.daemon        # or: journalctl -u snap.wasta-traffic-cop.daemon
sudo .venv/bin/traffic-copd -v                    # development: debug output, incl. per-app rates
```

Common causes:
- **No rule for the app:** it isn't running in its own unit (started from a
  terminal, on a desktop without per-app units, or escaped its scope:
  `/proc/<pid>/cgroup` shows `session.slice/org.gnome.Shell@…`), or no
  match key fits it.
- **Rule and unit present, but nothing counted:** the app's sockets were
  opened in another cgroup (e.g. the process was moved into its scope after
  connecting). `ss --cgroup` shows it.
- **Rule present, counters rising, no CAKE leaf** (and `Cannot find device
  "ifb-trafficcop"`): not shaped. Paused, no WAN, or tc failed; `status`
  and the log say which.
- **Counts on the wrong interface:** the WAN changed (VPN up or down) and
  the daemon hasn't caught up yet; it re-checks every 5 seconds.
- **Download below its limit:** expected. CAKE drops to hold the limit, and
  dropped bytes count against it (see "Download-limit rationale").

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

## GUI notes

tkinter + ttk (chosen over GTK 4 to avoid the ~600 MB GNOME runtime snap;
costs: X11 via XWayland, plain look, weak screen-reader support). ttk `alt`
theme with the Wasta palette (`window.py`: SLATE header, BLUE bars, YELLOW
at the limit, pale slate `SLATE_PALE` bars while paused, FIELD_GREY
read-only fields), Andika font. One row per app: download bar above upload
bar, with ↓/↑ arrows and "rate / limit" text. Header button: "Limits on" /
paused. The editor is read-only for non-admins (`editor.may_change`).

## Indicator design (next feature)

A tray icon with a menu: Pause/Resume (whichever applies; disabled for
non-admins), Open, Quit. Planned structure:

- **Separate process** `traffic-cop-indicator`, started at login (snapd's
  `autostart` desktop file), not part of the Tk window: it lives all
  session, and D-Bus and Tk event loops don't mix. Quit closes only the
  indicator, never the daemon.
- `indicator/menu.py`: **pure** status/usage → `Indicator(icon, tooltip,
  items)`; reuse `gui/model.py` and `display` for wording; unit-tested.
  Icons for running / paused / an app at its limit / daemon unreachable.
- `indicator/sni.py` (done): thin D-Bus layer, StatusNotifierItem +
  dbusmenu, with **jeepney** (pure Python, blocking; no GTK/`gi`). One
  thread: `serve(seconds)` answers the panel, then the caller polls. The
  icon is `IconName` + `IconThemePath` pointing at `indicator/icons/`, as
  Chromium and Electron do in snaps: the panel runs outside the snap and
  reads `/snap/<instance>/<revision>/…`, which differs from `$SNAP` for
  parallel installs (`indicator.icon_dir`). A drawn circle in `IconPixmap`
  is the fallback.
- Tray icons: monochrome "-symbolic" SVGs that panels recolour to their
  text colour; bold (on), faint (paused), faint + X (offline), since only
  opacity survives recolouring. Made from the visible layers of
  `snap/gui/icon.svg` (the yield sign; the octagon layers are hidden) by
  `tools/tray-icons.py`, which needs `shapely`. Good enough for now but
  muddled at 22 px; to refine later, probably as a simple hand-drawn
  one-colour version that the script only fades and crosses out.
- `indicator/__main__.py`: poll the daemon (`protocol.call`) every few
  seconds, push changes, handle clicks (`pause`/`resume` via the API; `open`
  runs `$SNAP/bin/traffic-cop`; `quit` exits).
- The GUI becomes **single-instance** (a lock/socket in `$XDG_RUNTIME_DIR`),
  so Open raises an existing window.
- Snap: a new `indicator` app; D-Bus tray access is traditionally the
  `unity7` interface (or `desktop-legacy`); verify which one current snapd
  needs for SNI.
- Shown on KDE, Cinnamon, Xfce, MATE and Ubuntu's GNOME (AppIndicator
  extension); plain GNOME (e.g. Fedora) has no tray, so exit quietly when no
  StatusNotifierWatcher is running.

## Open questions and ideas

- **Apps that escape their scope.** GNOME starts an app, *then* asks systemd
  for its `app-gnome-*.scope`, passing only the launched PID. Children
  started in that gap stay in GNOME Shell's cgroup
  (`session.slice/org.gnome.Shell@….service`), which is never matched. Not a
  systemd bug; a race in GNOME's launch order that only quick launchers hit:
  Zoom's deb (`ZoomLauncher` starts `/opt/zoom/zoom` and exits) and Zed
  (`bin/zed` starts `libexec/zed-editor`). Snap, flatpak and `systemd-run
  --user --scope` create the scope *before* the program starts, so they're
  immune. An `exe`/`cmdline` match doesn't help: matching only picks the
  unit to limit.
  - **Moving processes** (writing PIDs to the scope's `cgroup.procs`) works
    for the user without root, since they own their `user@<uid>.service`
    subtree, but a strict snap can't do it. And it only helps for sockets
    opened afterwards: seen live, a moved Zoom kept its meeting sockets in
    GNOME Shell's cgroup and wasn't counted until it reconnected.
  - **Workaround for users:** launch with `systemd-run --user --scope
    /usr/bin/zoom`, or a copy of the `.desktop` file in
    `~/.local/share/applications/` with that `Exec=`.
  - Options for Traffic Cop: document it and recommend snap/flatpak versions
    (current choice; the README says so); have the GUI write such per-user
    desktop-entry overrides (needs `personal-files`); a warning for escaped
    apps (the daemon could compare a matched exe's cgroup with its sockets').
- **Global limits and priorities** (from 1.x) aren't in config v2, which has
  per-app `download`/`upload` and `match` only. They'd fit HTB (root rate,
  class `prio`, `ceil` borrowing), but then HTB shapes again rather than only
  classifying; decide how that would interact with CAKE first. Matching picks
  a whole unit, so a 1.x match like Firefox's `name: Socket Process` can't
  target one process inside it.
- **Changing cgroups.** Scopes come and go constantly, and nftables resolves
  cgroup paths when rules load. The supervisor polls `Detector.scan()` every
  2 s and rewrites the output chain (`nft.build_output_update`) on change.
  inotify on each user's `app.slice` could replace most polling; an
  nftables map from cgroup ID to mark is another option (check support).
- **Apps started from a terminal** share the terminal's (or tab's) scope.
- **Minimum kernel** for `socket cgroupv2` in the output hook (believed
  ~5.13+); check on the oldest supported release.
- **Other ideas:** a "pick a running app" list in the editor (the daemon
  would report the units it sees, with their traffic); a short "recent
  events" list from the daemon; opt-in ECN.
- **Licence:** confirm GPL-3.0-or-later (snapcraft.yaml) vs. "only".
