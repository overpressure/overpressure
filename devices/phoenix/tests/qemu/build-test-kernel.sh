#!/bin/bash
# Build a mainline arm64 kernel for the QEMU smoke test (Linux / WSL only).
#
# Needs: curl, tar, xz, make, gcc, flex, bison, bc, libssl-dev, libelf-dev and
# an aarch64 cross compiler (Debian/Ubuntu: gcc-aarch64-linux-gnu).
#
# Usage: tests/qemu/build-test-kernel.sh [kernel-version]
# Output: cache/qemu/Image (inside the project directory)
#
# A 4.x version also applies kernel-4.19.fragment and writes cache/qemu/Image-4.19:
#   tests/qemu/build-test-kernel.sh 4.19.325   # for tests/qemu/run-userspace.sh
set -euo pipefail

VERSION=${1:-6.18.56}
HERE=$(cd "$(dirname "$0")" && pwd)
PROJECT=$(cd "$HERE/../../../.." && pwd)  # the repository: cache/, out/
FRAGMENTS=("$HERE/kernel.fragment")
IMAGE=Image
case "$VERSION" in
4.*) FRAGMENTS+=("$HERE/kernel-4.19.fragment"); IMAGE=Image-4.19 ;;
esac
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
# Old kernels predate GCC 10's -fno-common default (scripts/dtc yylloc).
MAKE=(make -s ARCH=arm64 CROSS_COMPILE="$CROSS" HOSTCFLAGS=-fcommon)
"${MAKE[@]}" defconfig
./scripts/kconfig/merge_config.sh -m .config "${FRAGMENTS[@]}" >/dev/null
"${MAKE[@]}" olddefconfig

# merge_config only warns; fail loudly if a required option did not stick.
missing=0
for fragment in "${FRAGMENTS[@]}"; do
    while read -r line; do
        case "$line" in
        CONFIG_*=*) grep -qx "$line" .config || { echo "!! $line did not apply"; missing=1; } ;;
        "# CONFIG_"*" is not set")
            opt=${line#\# }; opt=${opt%% *}
            ! grep -q "^$opt=" .config || { echo "!! $opt is still enabled"; missing=1; } ;;
        esac
    done < "$fragment"
done
[ "$missing" = 0 ] || exit 1

echo ">> building Image with $(nproc) jobs"
"${MAKE[@]}" -j"$(nproc)" Image
cp arch/arm64/boot/Image "$OUT/$IMAGE"
cp .config "$OUT/$IMAGE.config"
echo ">> done: $OUT/$IMAGE ($("${MAKE[@]}" kernelrelease))"
