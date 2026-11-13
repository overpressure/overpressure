#!/bin/sh
# SPDX-License-Identifier: Unlicense
# Build the system image on the headset itself (docs/install.md#built-on-the-headset), from the
# maintenance stage 1 (pico4-install build) or from the installed system. BASE/src holds this
# repository's build part (the PC sends it); the build scripts run in a build host on the data
# partition: Holo Core aarch64's rootfs (the one the pinned overpressure/packages builds in) with
# python, made once per Holo snapshot. POSIX sh: stage 1 has no bash.
#
#   build-on-headset.sh BASE
#
#   BASE/host   the build host            BASE/root   build-userspace.sh's build root
#   BASE/cache  the tree's cache/         BASE/out    the archive, its manifest, the system image
#
# Downloads go through ALL_PROXY when it is set (the PC's connection: a SOCKS proxy from ssh -R,
# names resolved there), else over the headset's own network. SOURCE_DATE_EPOCH and
# OVERPRESSURE_COMMIT name the tree, which has no Git.
set -eu
# The build in a mount namespace of its own, nothing propagated: on the installed system / is shared,
# and the lazy unmount of a recursive bind of /sys took the system's own cgroup and configfs mounts
# with it (every unit after it failed, the reboot never happened). Its mounts go with the namespace.
if [ -z "${OVERPRESSURE_BUILD_NAMESPACE:-}" ]; then
	OVERPRESSURE_BUILD_NAMESPACE=1 exec unshare --mount --propagation private sh "$0" "$@"
fi
base=${1:?specify the build folder}
src=$base/src host=$base/host
: "${SOURCE_DATE_EPOCH:?the commit time of the tree}" "${OVERPRESSURE_COMMIT:?the commit of the tree}"
log() { printf '>> %s\n' "$*"; }
die() { echo "build-on-headset: $*" >&2; exit 1; }

# One build at a time: the build roots are shared.
exec 9> "$base/.lock"
flock -n 9 || die "another build runs in $base"

# mnt ARGS...: mount, remembered for the cleanup (innermost first).
mounts="" last=""
mnt() {
	mount "$@"
	eval "last=\${$#}"
	mounts="$last $mounts"
}
fan=""
cleanup() {
	if [ -n "$fan" ]; then
		kill "$fan" 2>/dev/null || true
		wait "$fan" 2>/dev/null || true
	fi
	fan=""
	for m in $mounts; do umount -l "$m" 2>/dev/null || true; done
	mounts=""
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP
# unmount_below DIR: what a stopped build left mounted under DIR.
unmount_below() {
	awk -v d="$1/" 'index($2 "/", d) == 1 {print $2}' /proc/mounts | sort -r | while read -r m; do
		umount -l "$m" 2>/dev/null || true
	done
}
api() {
	mkdir -p "$1/proc" "$1/sys" "$1/dev" "$1/run" "$1/tmp"
	mnt -t proc proc "$1/proc"
	mnt -o rbind /sys "$1/sys"
	mnt -o rbind /dev "$1/dev"
	mnt -t tmpfs tmpfs "$1/run"
	mnt -t tmpfs tmpfs "$1/tmp"
}
in_host() {
	chroot "$host" /usr/bin/env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/bin LANG=C.UTF-8 HOME=/root \
		SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH" OVERPRESSURE_COMMIT="$OVERPRESSURE_COMMIT" \
		${ALL_PROXY:+ALL_PROXY="$ALL_PROXY"} "$@"
}

# The Holo snapshot the pinned overpressure/packages builds in (its build/packages.sh).
field() { sed -n "s/^ *\"$1\": *\"\([^\"]*\)\".*/\1/p" "$src/devices/phoenix/userspace/packages.json"; }
repository=$(field repository) commit=$(field commit)
[ -n "$repository" ] && [ ${#commit} = 40 ] || die "no packages pin in $src"
setup=$(curl -fsSL --retry 3 "https://raw.githubusercontent.com/$repository/$commit/build/packages.sh") ||
	die "could not fetch $repository's build/packages.sh at $commit"
pin() { printf '%s\n' "$setup" | sed -n "s/^$1=//p"; }
snapshot=$(pin HOLO_SNAPSHOT) sha=$(pin HOLO_SHA256)
url=$(pin HOLO_URL | sed "s/\$HOLO_SNAPSHOT/$snapshot/")
[ -n "$snapshot" ] && [ ${#sha} = 64 ] && [ -n "$url" ] || die "no Holo pin in $repository's build/packages.sh"

mkdir -p "$base/cache" "$base/root/packages" "$base/out"
unmount_below "$host"
# Where packages.sh looks for the archive: the build environment unpacks the same one.
archive=$base/root/packages/holo-$snapshot.rootfs.zst
if [ "$(cat "$host/.overpressure-host" 2>/dev/null)" != "$sha" ]; then
	if [ "$(sha256sum "$archive" 2>/dev/null | cut -d' ' -f1)" != "$sha" ]; then
		log "downloading Holo Core aarch64 $snapshot"
		curl -fsSL --retry 3 -o "$archive.part" "$url"
		mv "$archive.part" "$archive"
	fi
	[ "$(sha256sum "$archive" | cut -d' ' -f1)" = "$sha" ] || die "$archive is not the pinned Holo rootfs"
	log "making the build host: Holo Core aarch64 $snapshot with python"
	rm -rf "$host"
	mkdir -p "$host"
	zstd -qdc "$archive" | tar -x --numeric-owner -C "$host"
	# Names resolve where the proxy is; the build environments copy this file.
	rm -f "$host/etc/resolv.conf"
	: > "$host/etc/resolv.conf"
	api "$host"
	in_host pacman -Sy --noconfirm --needed python >/dev/null
	cleanup
	echo "$sha" > "$host/.overpressure-host"
fi

# The tree, its cache, the build root and the output as /build in the build host.
ln -sfn /build/cache "$src/cache"
api "$host"
# The fan, with the installed system's program and policy (pico4-fan, PICO OS's table), where nothing
# drives it: the maintenance stage 1 runs no fan service, and the build's compression took the headset
# to 88 °C without it. The installed system's service drives it there. Stopped, it keeps its duty.
if [ -e /sys/class/tty/ttyHS1 ] && ! pgrep -f "pico4-fan run" > /dev/null; then
	curl -fsSL --retry 3 -o "$host/run/pico4-fan-run" \
		"https://raw.githubusercontent.com/$repository/$commit/phoenix/pico4-power/pico4-fan"
	mkdir -p "$host/run/pico4-fan"
	# A plain command, so that $! is the program itself (chroot and env exec it): it holds this
	# namespace's mounts, the data partition among them, until it ends.
	chroot "$host" /usr/bin/env -i PATH=/usr/bin python3 /run/pico4-fan-run run > "$base/fan.log" 2>&1 &
	fan=$!
	log "the fan runs with PICO OS's policy (pico4-fan, log in $base/fan.log)"
fi
for d in src cache root out; do
	mkdir -p "$host/build/$d"
	mnt -o bind "$base/$d" "$host/build/$d"
done
in_host bash -c 'bash /build/src/devices/phoenix/scripts/build-userspace.sh /build/root /build/out &&
	bash /build/src/devices/phoenix/scripts/build-system-image.sh /build/out/pico4-userspace.tar.zst /build/out/pico4-system.img'
