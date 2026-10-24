#!/bin/bash
# Build pico4-slamd and the tracker root's stand-ins (docs/xr.md#head-tracking) for bionic
# (aarch64-linux-android29) with a host clang, against PICO OS's own bionic libraries, which
# scripts/extract-stock-slam.sh puts into ROOT. No NDK: own entry point (crt.c) and
# declarations (bionic.h). Runs the image-processing test natively first.
#
#   src/stockslam/build.sh ROOT OUTPUT_DIR [LLVM_DIR]
#
# OUTPUT_DIR receives pico4-slamd, libpico4-slam-stubs.so and libandroid.so.
set -euo pipefail
src=$(cd "$(dirname "$0")" && pwd)
root=${1:?specify the tracker root (system/lib64, system/lib64/bootstrap)}
out=${2:?specify an output directory}
llvm=${3:-}
clang=${llvm:+$llvm/bin/}clang
[[ -x "$(command -v "$clang")" ]] || { echo "no clang ($clang)" >&2; exit 2; }
[[ -s "$root/system/lib64/bootstrap/libc.so" && -s "$root/system/lib64/libSlam.so" ]] ||
    { echo "$root is not a tracker root (scripts/extract-stock-slam.sh)" >&2; exit 1; }
mkdir -p "$out"

# The image processing, natively on the build host.
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
"$clang" -std=c11 -O2 -Wall -Wextra -Werror -o "$work/test_isp" "$src/test_isp.c"
"$work/test_isp"

cc=("$clang" --target=aarch64-linux-android29 -fuse-ld=lld -O2 -fPIC -fno-stack-protector -nostdlib -ffreestanding
    -Wall -Wextra -Werror -I"$src/../monado")
libs=(-L"$root/system/lib64/bootstrap" -L"$root/system/lib64")
"${cc[@]}" -shared -Wl,-soname,libpico4-slam-stubs.so -o "$out/libpico4-slam-stubs.so" "$src/stubs.c" "${libs[@]}" -lc
"${cc[@]}" -shared -Wl,-soname,libandroid.so -o "$out/libandroid.so" "$src/android.c"
"${cc[@]}" -fPIE -pie -Wl,--dynamic-linker=/system/bin/bootstrap/linker64 -Wl,-z,max-page-size=4096 \
    -o "$out/pico4-slamd" "$src/crt.c" "$src/pico4-slamd.c" "${libs[@]}" -lc -ldl
