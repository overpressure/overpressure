#!/bin/sh
# SPDX-License-Identifier: Unlicense
# Build a headset's system image on the headset itself, from its maintenance system or from its installed system.
# BASE/src holds the build part the PC sends (overpressure's package, the headset's folder in devices/); the build
# scripts run in a build host on the data partition: Holo Core aarch64's rootfs (the one the pinned
# overpressure/packages builds in) with python, made once per Holo snapshot. POSIX sh: a stage 1 has no bash.
#
#   overpressure/build/build-on-headset.sh BASE HEADSET
#
#   BASE/host   the build host            BASE/root   build-userspace.sh's build root
#   BASE/cache  the tree's cache/         BASE/out    the archive, its manifest, the system image
#   BASE/downloads  what the PC downloaded for the build that no cache below keeps
#
# HEADSET is the headset's folder under BASE/src (devices/CODENAME); its scripts/build-on-headset-hook.sh, when it
# has one, defines before_build (run in the build host's namespace before the build, a fan program, say) and
# after_build (its cleanup), and its userspace/build.sh names PREFIX (the image's name: PREFIX-system.img).
# The PC downloads everything first (overpressure.prefetch) and sends it in BASE/src/prefetch, laid out as BASE:
# each file goes into place, where the build finds it and downloads nothing again. What the PC did not send is
# downloaded here, as without it.
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
headset=${2:?specify the headset folder}
src=$base/src host=$base/host
relative=${headset#"$src"/}   # devices/CODENAME, as the build host sees it under /build/src
codename=${relative##*/}
prefix=$(sed -n 's/^PREFIX=//p' "$headset/userspace/build.sh")
[ -n "$prefix" ] || { echo "build-on-headset: $headset/userspace/build.sh names no PREFIX" >&2; exit 1; }
: "${SOURCE_DATE_EPOCH:?the commit time of the tree}" "${OVERPRESSURE_COMMIT:?the commit of the tree}"
log() { printf '>> %s\n' "$*"; }
die() { echo "build-on-headset: $*" >&2; exit 1; }
# shellcheck source=fetch.sh
. "$(dirname "$0")/fetch.sh"

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
# The headset's hooks: before_build and after_build, no-ops without them.
before_build() { :; }
after_build() { :; }
if [ -f "$headset/scripts/build-on-headset-hook.sh" ]; then
	# shellcheck source=/dev/null
	. "$headset/scripts/build-on-headset-hook.sh"
fi
cleanup() {
	after_build
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

# The PC's downloads into place (the paths hold no newlines: the PC's manifest).
if [ -d "$src/prefetch" ]; then
	(cd "$src/prefetch" && find . -type f) > "$base/.prefetch"
	count=0
	while IFS= read -r f; do
		mkdir -p "$base/$(dirname "$f")"
		mv -f "$src/prefetch/$f" "$base/$f"
		count=$((count + 1))
	done < "$base/.prefetch"
	rm -rf "$src/prefetch" "$base/.prefetch"
	log "$count files from the PC's downloads"
fi

# The Holo snapshot the pinned overpressure/packages builds in (its build/packages.sh).
field() { sed -n "s/^ *\"$1\": *\"\([^\"]*\)\".*/\1/p" "$headset/userspace/packages.json"; }
repository=$(field repository) commit=$(field commit)
[ -n "$repository" ] && [ ${#commit} = 40 ] || die "no packages pin in $src"
if [ -s "$base/downloads/packages-$commit.sh" ]; then
	cp "$base/downloads/packages-$commit.sh" "$base/packages.sh"
else
	fetch "https://raw.githubusercontent.com/$repository/$commit/build/packages.sh" "$base/packages.sh" ||
		die "could not fetch $repository's build/packages.sh at $commit"
fi
setup=$(cat "$base/packages.sh")
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
		fetch "$url" "$archive" || die "could not download Holo Core aarch64 $snapshot"
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
	# python from the PC's downloads when they are for this rootfs's repository (userspace/build-host.json).
	server=$(sed -n 's/^Server *= *//p' "$host/etc/pacman.d/mirrorlist" | head -n 1)
	if [ -s "$base/downloads/host/server" ] && [ "$(cat "$base/downloads/host/server")" = "$server" ] &&
		[ -s "$base/downloads/host/sync/core.db" ]; then
		cp "$base"/downloads/host/sync/*.db "$host/var/lib/pacman/sync/"
		cp "$base"/downloads/host/pkg/*.pkg.tar.zst "$host/var/cache/pacman/pkg/"
		log "the build host's python from the PC's downloads"
		fetch_pacman in_host pacman -S --noconfirm --needed python >/dev/null
	else
		fetch_pacman in_host pacman -Sy --noconfirm --needed python >/dev/null
	fi
	cleanup
	echo "$sha" > "$host/.overpressure-host"
fi

# The tree, its cache, the build root and the output as /build in the build host.
ln -sfn /build/cache "$src/cache"
api "$host"
before_build
for d in src cache root out; do
	mkdir -p "$host/build/$d"
	mnt -o bind "$base/$d" "$host/build/$d"
done
# A checkout of overpressure/packages the PC sent along (dev install --on-headset --packages DIR): its
# recipes that differ from the pinned commit's are built here, as in the build container.
packages=""
[ ! -f "$src/packages/headsets/$codename/layers" ] || packages="--packages /build/src/packages"
# The image's package databases the PC downloaded, for a new base root.
[ ! -d "$base/root/downloads/databases" ] || packages="$packages --databases /build/root/downloads/databases"
in_host bash -c "bash /build/src/$relative/scripts/build-userspace.sh /build/root /build/out $packages &&
	bash /build/src/$relative/scripts/build-system-image.sh /build/out/$prefix-userspace.tar.zst /build/out/$prefix-system.img"
