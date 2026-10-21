#!/bin/bash
# Host test of the pico4-vrdisplay layer in its fake display mode, natively under
# Mesa's software Vulkan driver (lavapipe): Linux or WSL with lavapipe, the Vulkan
# loader and libdrm; headers from VULKAN_INCLUDE (the directory holding vulkan/
# and xf86drm.h, default /usr/include) and DRM_INCLUDE (default
# $VULKAN_INCLUDE/libdrm). docs/session.md has the details.
#
# 1. A short run on the fake display presents and retires frames.
# 2. With PICO4_VRDISPLAY_FAKE_STUCK the fake display stops retiring flips: the
#    layer must count retire timeouts, never treat one as a presented frame,
#    and lose the surface (VK_ERROR_OUT_OF_DATE_KHR) within a bounded time
#    instead of leaving the client waiting.
set -euo pipefail
cd "$(dirname "$0")/.."
inc=${VULKAN_INCLUDE:-/usr/include}
drm=${DRM_INCLUDE:-$inc/libdrm}
icd=${LAVAPIPE_ICD:-/usr/share/vulkan/icd.d/lvp_icd.json}
[ -f "$icd" ] || { echo "SKIP: no lavapipe ICD at $icd"; exit 0; }
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

cc=${CC:-cc}
# System headers first; the given directories only fill in vulkan/ and libdrm.
"$cc" -std=c11 -O1 -Wall -Wextra -Wno-unused-parameter -Werror -fPIC -shared -fvisibility=hidden \
	-idirafter "$inc" -idirafter "$drm" -o "$work/libVkLayer_PICO4_vrdisplay.so" \
	src/vrdisplay/pico4-vrdisplay.c src/vrdisplay/pico4-vrsync.c -l:libdrm.so.2 -lpthread
"$cc" -std=c11 -O1 -Wall -Wextra -Werror -idirafter "$inc" -idirafter "$drm" -o "$work/pico4-vrdisplay-test" \
	src/vrdisplay/pico4-vrdisplay-test.c -l:libvulkan.so.1 -l:libdrm.so.2 -lpthread
sed "s|/usr/lib/libVkLayer_PICO4_vrdisplay.so|$work/libVkLayer_PICO4_vrdisplay.so|" \
	src/vrdisplay/VkLayer_PICO4_vrdisplay.json > "$work/VkLayer_PICO4_vrdisplay.json"

run() { # run NAME [ENV=VALUE...]: the test client on a small fake display, bounded to 30 s
	local name=$1
	shift
	env VK_DRIVER_FILES="$icd" VK_ICD_FILENAMES="$icd" VK_LAYER_PATH="$work" \
		VK_INSTANCE_LAYERS=VK_LAYER_PICO4_vrdisplay PICO4_VRDISPLAY=1 PICO4_VRDISPLAY_FAKE=256x128 \
		PICO4_VRDISPLAY_STATS="$work/$name.stats.json" PICO4_VRDISPLAY_LOGFILE="$work/$name.log" "$@" \
		timeout 30 "$work/pico4-vrdisplay-test" 3 --min-hz 0 > "$work/$name.json" 2> "$work/$name.err" || true
}
field() { sed -n "s/.*\"$2\":\([0-9.-]*\).*/\1/p" "$work/$1"; }
fail() { echo "FAIL: $*"; for f in "$work"/*.json "$work"/*.err "$work"/*.log; do [ -s "$f" ] && { echo "== $f"; tail -n 20 "$f"; }; done; exit 1; }

run normal
grep -q '"status":"pass"' "$work/normal.json" || fail "the fake display did not present"
[ "$(field normal.stats.json retire_timeouts)" = 0 ] || fail "retire timeouts on a working display"
[ "$(field normal.stats.json commits)" -gt 30 ] || fail "too few commits on the fake display"

started=$(date +%s)
run stuck PICO4_VRDISPLAY_FAKE_STUCK=20
elapsed=$(($(date +%s) - started))
[ "$elapsed" -lt 25 ] || fail "the client waited ${elapsed} s on a stuck display"
grep -q '"status":"fail"' "$work/stuck.json" || fail "a stuck display still passed"
grep -q -- '-1000001004' "$work/stuck.json" || fail "the client did not get VK_ERROR_OUT_OF_DATE_KHR"
commits=$(field stuck.stats.json commits)
[ "$commits" -le 21 ] || fail "$commits commits after the display stopped retiring flips at 20"
[ "$(field stuck.stats.json retire_timeouts)" -ge 1 ] || fail "no retire timeout counted"
grep -q "surface lost" "$work/stuck.log" "$work/stuck.err" 2>/dev/null || fail "no 'surface lost' in the layer's log"
echo "pico4-vrdisplay fake display: 2 scenarios passed (stuck display lost after ${elapsed} s)"
