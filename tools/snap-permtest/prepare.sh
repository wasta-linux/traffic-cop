#!/bin/sh
# Build the directory for the strict-confinement permission test snap.
#
#   tools/snap-permtest/prepare.sh            # builds build/snap-permtest/
#   sudo snap try build/snap-permtest
#   sudo trafficcop-permtest.run              # 1st run: no interfaces connected
#   sudo snap connect trafficcop-permtest:system-observe
#   sudo snap connect trafficcop-permtest:network-control
#   sudo snap connect trafficcop-permtest:firewall-control
#   sudo trafficcop-permtest.run              # 2nd run: all connected
#   sudo snap remove trafficcop-permtest
#
# No snapcraft or downloads: core26 provides sh, ip and tc; nft and the two
# libraries core26 lacks are copied from this machine (which must therefore
# have the same glibc as core26, i.e. Ubuntu 26.04).
set -eu

here=$(dirname "$(readlink -f "$0")")
out=${1:-$here/../../build/snap-permtest}

rm -rf "$out"
mkdir -p "$out/meta" "$out/bin" "$out/lib"
cp "$here/snap.yaml" "$out/meta/snap.yaml"
cp "$here/run-tests" "$out/bin/run-tests"
chmod 755 "$out/bin/run-tests"

nft=$(command -v nft || echo /usr/sbin/nft)
cp "$nft" "$out/bin/nft"
for lib in libnftables.so.1 libjansson.so.4; do
    path=$(ldd "$nft" | awk -v l="$lib" '$1 == l {print $3}')
    [ -n "$path" ] || { echo "nft doesn't link $lib here; update this script" >&2; exit 1; }
    cp -L "$path" "$out/lib/$lib"
done

echo "built $(readlink -f "$out")"
