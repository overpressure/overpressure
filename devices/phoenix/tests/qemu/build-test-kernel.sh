#!/bin/bash
# SPDX-License-Identifier: Unlicense
# Build a mainline arm64 kernel for the QEMU smoke test, in the build container, which has its
# tools (curl, tar, xz, make, gcc, flex, bison, bc, libssl-dev, libelf-dev, gcc-aarch64-linux-gnu):
#
#   python -m overpressure container run -- bash devices/phoenix/tests/qemu/build-test-kernel.sh [kernel-version]
# Output: cache/qemu/Image (inside the project directory)
set -euo pipefail

VERSION=${1:-6.18.56}
HERE=$(cd "$(dirname "$0")" && pwd)
PROJECT=$(cd "$HERE/../../../.." && pwd)  # the repository: cache/, out/
FRAGMENTS=("$HERE/kernel.fragment")
IMAGE=Image
# Build on a Linux file system (the container's /var/tmp volume): a bind mount from Windows or macOS is slow.
WORK=${PICO4_BUILD_DIR:-/var/tmp/pico4-qemu-kernel}/linux-$VERSION
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
