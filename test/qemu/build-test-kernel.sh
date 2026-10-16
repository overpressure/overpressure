#!/bin/bash
# Build a mainline arm64 kernel for the QEMU smoke test (Linux / WSL only).
#
# Needs: curl, tar, xz, make, gcc, flex, bison, bc, libssl-dev, libelf-dev and
# an aarch64 cross compiler (Debian/Ubuntu: gcc-aarch64-linux-gnu).
#
# Usage: test/qemu/build-test-kernel.sh [kernel-version]
# Output: cache/qemu/Image (inside the project directory)
set -euo pipefail

VERSION=${1:-6.18.56}
HERE=$(cd "$(dirname "$0")" && pwd)
PROJECT=$(cd "$HERE/../.." && pwd)
# Build on a native Linux filesystem: compiling under /mnt/c (WSL) is very slow.
WORK=${PICO4_BUILD_DIR:-$HOME/.cache/pico4-poc}/linux-$VERSION
OUT=$PROJECT/cache/qemu
CROSS=${CROSS_COMPILE:-aarch64-linux-gnu-}

mkdir -p "$(dirname "$WORK")" "$OUT"
if [ ! -f "$WORK/Makefile" ]; then
    major=${VERSION%%.*}
    url="https://cdn.kernel.org/pub/linux/kernel/v${major}.x/linux-${VERSION}.tar.xz"
    echo ">> downloading $url"
    curl -fL --retry 3 "$url" | tar -xJ -C "$(dirname "$WORK")"
fi

cd "$WORK"
make -s ARCH=arm64 CROSS_COMPILE="$CROSS" defconfig
./scripts/kconfig/merge_config.sh -m .config "$HERE/kernel.fragment" >/dev/null
make -s ARCH=arm64 CROSS_COMPILE="$CROSS" olddefconfig

# merge_config only warns; fail loudly if a required option did not stick.
missing=0
while read -r line; do
    case "$line" in CONFIG_*=*) ;; *) continue ;; esac
    grep -qx "$line" .config || { echo "!! $line did not apply"; missing=1; }
done < "$HERE/kernel.fragment"
[ "$missing" = 0 ] || exit 1

echo ">> building Image with $(nproc) jobs"
make -s ARCH=arm64 CROSS_COMPILE="$CROSS" -j"$(nproc)" Image
cp arch/arm64/boot/Image "$OUT/Image"
cp .config "$OUT/Image.config"
echo ">> done: $OUT/Image ($(make -s kernelrelease))"
