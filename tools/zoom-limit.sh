#!/bin/bash
# Proof of concept: limit Zoom to 100 KB/s down and 100 KB/s up.
#
#   sudo ./zoom-limit.sh start     # Zoom must already be running
#   sudo ./zoom-limit.sh status    # byte counters + CAKE stats
#   sudo ./zoom-limit.sh stop
#
# How it works:
#   nftables (output hook): packets from sockets in Zoom's cgroup get
#     fwmark 0x10, and the mark is saved on the connection (ct mark).
#   Upload:   HTB on the WAN interface sends fwmark 0x10 to class 1:10,
#             whose CAKE leaf shapes to 800 kbit/s.
#   Download: tc ingress restores the mark from conntrack (act_connmark),
#             redirects everything to ifb0, and the same HTB/CAKE layout
#             there shapes marked traffic, with CAKE in "ingress" mode.
# HTB only classifies here (its classes run at link speed). CAKE does the
# shaping, so its ingress-mode drop accounting is what sets the rate.
set -euo pipefail

RATE=800kbit          # 100 KB/s = 800 kbit/s. Careful: in tc, "kbps" means
                      # kiloBYTES/s, so 800kbit and 100kbps are the same.
LINK=1gbit            # anything above the real link speed
MARK=0x10
IFB=ifb0
TABLE=trafficcop_poc

WAN=$(ip route get 1.1.1.1 | awk '{for (i=1; i<NF; i++) if ($i=="dev") {print $(i+1); exit}}')

zoom_cgroup() {
    # Deb/tarball installs run /opt/zoom/zoom; the Zoom snap differs, so adjust.
    local pid
    pid=$(pgrep -o -x zoom || pgrep -o -f '^/opt/zoom/zoom' || true)
    [[ -n $pid ]] || { echo "Zoom isn't running." >&2; exit 1; }
    # cgroup v2 line looks like "0::/user.slice/.../app-gnome-Zoom-1234.scope"
    awk -F'::' '/^0::/ {sub(/^\//, "", $2); print $2}' "/proc/$pid/cgroup"
}

shaper() {   # shaper <dev> [ingress]
    local dev=$1 mode=${2:-}
    tc qdisc replace dev "$dev" root handle 1: htb default 20
    tc class add dev "$dev" parent 1: classid 1:1  htb rate $LINK
    tc class add dev "$dev" parent 1:1 classid 1:10 htb rate $LINK   # Zoom
    tc class add dev "$dev" parent 1:1 classid 1:20 htb rate $LINK   # everything else
    tc qdisc add dev "$dev" parent 1:10 cake bandwidth $RATE besteffort $mode
    tc qdisc add dev "$dev" parent 1:20 fq_codel
    tc filter add dev "$dev" parent 1: protocol all prio 1 handle $MARK fw flowid 1:10
}

start() {
    local cg level
    cg=$(zoom_cgroup)
    level=$(awk -F/ '{print NF}' <<< "$cg")
    echo "WAN device:   $WAN"
    echo "Zoom cgroup:  $cg (level $level)"

    # 1. Classify Zoom's traffic by cgroup and remember it per connection.
    nft -f - <<EOF
table inet $TABLE {
    counter zoom_up   {}
    counter zoom_down {}
    chain output {
        type filter hook output priority mangle; policy accept;
        socket cgroupv2 level $level "$cg" meta mark set $MARK ct mark set meta mark counter name zoom_up
    }
    chain input {
        type filter hook input priority mangle; policy accept;
        ct mark $MARK counter name zoom_down
    }
}
EOF

    # 2. Upload: shape on the WAN interface itself.
    shaper "$WAN"

    # 3. Download: restore mark from conntrack at ingress, send to ifb0, shape there.
    ip link show "$IFB" &>/dev/null || ip link add "$IFB" type ifb
    ip link set "$IFB" up
    tc qdisc replace dev "$WAN" handle ffff: ingress
    tc filter add dev "$WAN" parent ffff: protocol all prio 1 matchall \
        action connmark \
        action mirred egress redirect dev "$IFB"
    shaper "$IFB" ingress

    echo "Zoom limited to $RATE each way. Restart Zoom => run stop/start again."
}

stop() {
    tc qdisc del dev "$WAN" root    2>/dev/null || true
    tc qdisc del dev "$WAN" ingress 2>/dev/null || true
    ip link del "$IFB"              2>/dev/null || true
    nft delete table inet $TABLE    2>/dev/null || true
    echo "Removed."
}

status() {
    nft list counters table inet $TABLE
    echo "--- upload (CAKE on $WAN) ---";  tc -s qdisc show dev "$WAN" parent 1:10
    echo "--- download (CAKE on $IFB) ---"; tc -s qdisc show dev "$IFB" parent 1:10
}

[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }
case "${1:-}" in
    start)  stop >/dev/null; start ;;
    stop)   stop ;;
    status) status ;;
    *)      echo "Usage: $0 start|stop|status" >&2; exit 1 ;;
esac
