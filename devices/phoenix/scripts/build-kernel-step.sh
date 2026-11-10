#!/usr/bin/env bash
# SPDX-License-Identifier: Unlicense
# Valve's kernel for the PICO 4, from a clean checkout: the window's and the guide's kernel step.
#
#   build-kernel-step.sh STOCK_RECOVERY|ITS_FOLDER STOCK_FOLDER [WORKDIR]
#
# Fetches Valve's public source package (checked against the hash build-kernel.sh pins) into cache/,
# builds in WORKDIR on a Linux file system, and leaves the results in out/kernel.
set -euo pipefail
PACKAGE=linux-72-deckard-7.2.0+g509b71a1bdcd-1.src.tar.gz
URL=https://steamdeck-packages.steamos.cloud/archlinux-deckard-hotfixes/source/linux-72-deckard-7.2.0%2Bg509b71a1bdcd-1.src.tar.gz

[ $# -ge 2 ] || { sed -n '3,9p' "$0" >&2; exit 2; }
recovery=$1 stock=$2 work=${3:-/var/tmp/pico4-kernel-build}
[ -f "$stock/vendor.img" ] || { echo "no $stock/vendor.img: run the Stock firmware and assets step first" >&2; exit 1; }
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
if [ -d "$recovery" ]; then
	set -- "$recovery"/*.recovery.img
	[ $# -eq 1 ] && [ -f "$1" ] || { echo "$recovery holds $# stock recovery images, name one (the Dev image step makes it)" >&2; exit 1; }
	recovery=$1
fi
[ -f "$recovery" ] || { echo "no stock recovery image at $recovery: run the Dev image step first" >&2; exit 1; }
mkdir -p "$repo/cache/kernel"
if [ ! -f "$repo/cache/kernel/$PACKAGE" ]; then
	echo "downloading $PACKAGE (2.5 GB)" >&2
	curl -fL --retry 3 -o "$repo/cache/kernel/$PACKAGE.part" "$URL"
	mv "$repo/cache/kernel/$PACKAGE.part" "$repo/cache/kernel/$PACKAGE"
fi
bash "$here/../kernel/build-kernel.sh" "$repo/cache/kernel/$PACKAGE" "$recovery" "$stock/vendor.img" "$work" "$work/out"
mkdir -p "$repo/out/kernel"
for f in Image.gz sm8250-pico4-phoenix.dtb version config; do
	install -m 0644 "$work/out/$f" "$repo/out/kernel/$f"
done
echo "out/kernel: Image.gz, sm8250-pico4-phoenix.dtb, version, config" >&2
