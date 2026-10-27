#!/bin/bash
# Every asset the userspace build takes, in one go: the stock firmware from PICO's public OTA
# (python -m overpressure stock), then each extraction script into assets/KIND-VERSION
# (docs/development.md#stock-firmware). Linux or WSL, as root, from anywhere:
#
#   devices/phoenix/scripts/extract-assets.sh 5.13.7-global-seko [STOCK_DIR]
#
# STOCK_DIR defaults to /var/tmp/pico4-stock/FIRMWARE (about 11 GB, on the Linux disk); the OTA
# members already there are not fetched again. VERSION is the firmware's number (5.13.7). Each
# kind is extracted into a fresh folder that replaces the old one only when its script succeeded.
set -euo pipefail
firmware=${1:?specify the firmware entry, e.g. 5.13.7-global-seko}
stock=${2:-/var/tmp/pico4-stock/$firmware}
version=${firmware%%-*}
project=$(cd "$(dirname "$0")/.." && pwd)   # devices/phoenix
repo=$(cd "$project/../.." && pwd)
[[ $EUID == 0 ]] || { echo 'run as root (debugfs and the image mounts)' >&2; exit 2; }
cd "$repo"
python3 -m overpressure --device phoenix stock --firmware "$firmware" "$stock"
for kind in graphics-firmware sensor-data wlan audio stock-slam; do
    out=assets/$kind-$version
    echo ">> $out"
    rm -rf "$out.new"
    "$project/scripts/extract-$kind.sh" "$stock" "$out.new"
    rm -rf "$out"
    mv "$out.new" "$out"
done
echo ">> assets for $firmware: $(ls -d assets/*-"$version" | tr '\n' ' ')"
