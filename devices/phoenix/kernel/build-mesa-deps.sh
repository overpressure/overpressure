#!/usr/bin/env bash
# Build the two libraries Valve's Mesa (deckard-mesa) links that the PICO 4's userspace lacks:
# libdisplay-info.so.1 (Turnip's display code) and libsensors.so.5 (gallium's HUD).
#
#   build-mesa-deps.sh PNP_IDS WORKDIR OUTDIR
#
# PNP_IDS  hwdata's pnp.ids, the vendor table libdisplay-info compiles in (the userspace's
#          hwdata package: usr/share/hwdata/pnp.ids in its build root).
# WORKDIR  sources and build trees (Linux file system).
# OUTDIR   mesa-deps.tar: usr/lib with both libraries, to unpack over the userspace root.
#
# Linux/WSL, cross compiling with aarch64-linux-gnu-gcc; both sources are pinned by hash.
set -euo pipefail

LDI_VERSION=0.1.1
LDI_URL=https://gitlab.freedesktop.org/emersion/libdisplay-info/-/archive/$LDI_VERSION/libdisplay-info-$LDI_VERSION.tar.gz
LDI_SHA256=a5aeef57817916286526292ec816a5338c4d3c0094ce91e584fc82b57070a44f
LMS_TAG=V3-6-0
LMS_URL=https://github.com/lm-sensors/lm-sensors/archive/refs/tags/$LMS_TAG.tar.gz
LMS_SHA256=0591f9fa0339f0d15e75326d0365871c2d4e2ed8aa1ff759b3a55d3734b7d197
CROSS=aarch64-linux-gnu-

[ $# -eq 3 ] || { sed -n '2,11p' "$0" >&2; exit 2; }
pnp=$(realpath "$1") work=$2 out=$3
mkdir -p "$work/dl" "$out"
work=$(realpath "$work") out=$(realpath "$out")
root=$work/root

# fetch NAME URL SHA256: the source tree in $work/NAME, from a checked archive.
fetch() {
	local archive=$work/dl/$1.tar.gz
	[ -f "$archive" ] || curl -fsSL -o "$archive" "$2"
	echo "$3  $archive" | sha256sum -c --quiet
	rm -rf "${work:?}/$1"
	mkdir -p "$work/$1"
	tar -xzf "$archive" -C "$work/$1" --strip-components=1
}

rm -rf "$root"
mkdir -p "$root"

fetch libdisplay-info "$LDI_URL" "$LDI_SHA256"
cat > "$work/cross.ini" <<EOF
[binaries]
c = '${CROSS}gcc'
ar = '${CROSS}ar'
strip = '${CROSS}strip'

[host_machine]
system = 'linux'
cpu_family = 'aarch64'
cpu = 'cortex-a77'
endian = 'little'
EOF
# The fallback path is what the build reads when no hwdata.pc is found.
sed -i "s|files('/usr/share/hwdata/pnp.ids')|files('$pnp')|" "$work/libdisplay-info/meson.build"
meson setup --cross-file "$work/cross.ini" --prefix=/usr --libdir=lib --buildtype=release \
	"$work/libdisplay-info/build" "$work/libdisplay-info"
ninja -C "$work/libdisplay-info/build"
DESTDIR=$root meson install -C "$work/libdisplay-info/build" --no-rebuild

fetch lm-sensors "$LMS_URL" "$LMS_SHA256"
make -C "$work/lm-sensors" CC="${CROSS}gcc" AR="${CROSS}ar" PREFIX=/usr LIBDIR=/usr/lib \
	ETCDIR=/etc BUILD_STATIC_LIB=0 lib/libsensors.so.5.0.0
install -Dm755 "$work/lm-sensors/lib/libsensors.so.5.0.0" -t "$root/usr/lib"
ln -sf libsensors.so.5.0.0 "$root/usr/lib/libsensors.so.5"

${CROSS}strip --strip-unneeded "$root"/usr/lib/lib*.so.*.*
tar -cf "$out/mesa-deps.tar" -C "$root" --owner=0 --group=0 usr/lib
for lib in "$root"/usr/lib/libdisplay-info.so.1 "$root"/usr/lib/libsensors.so.5; do
	echo "$(basename "$lib"): $(${CROSS}readelf -d "$lib" | grep -o 'SONAME.*')"
done
