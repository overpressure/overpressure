#!/bin/sh
# Usage: scripts/preflight.sh [rootfs.tar] [adb-serial]
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
TAR=${1:-$(ls -t "$ROOT"/out/pico4-poc-*-rootfs.tar 2>/dev/null | head -n 1)}
[ -n "$TAR" ] || { echo "no rootfs tar in out/ - run: python3 build.py build" >&2; exit 1; }
if [ -n "${2:-}" ]; then
    exec python3 "$ROOT/hardware_test.py" preflight --rootfs "$TAR" --serial "$2"
fi
exec python3 "$ROOT/hardware_test.py" preflight --rootfs "$TAR"
