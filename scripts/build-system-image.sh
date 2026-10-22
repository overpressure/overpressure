#!/bin/bash
# The installed system's read-only image for super (docs/userspace.md#installed-system), from
# the userspace archive scripts/build-userspace.sh writes. Linux or WSL, as root (ownership and
# file capabilities):
#
#   scripts/build-system-image.sh out/userspace/pico4-userspace.tar.zst out/userspace/pico4-system.img
#
# ext4 without a journal (it is never written on the headset) and without the features the
# 4.19 kernel cannot mount. /data is the data partition's mount point; the image names the
# archive it came from in /usr/share/pico4-poc.
set -euo pipefail

SUPER_MIB=8192 # the super partition
archive=${1:?archive}
image=${2:?image}
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
[ -s "$archive" ] || { echo "no archive $archive" >&2; exit 1; }

work=$(mktemp -d /var/tmp/pico4-system.XXXXXX)
trap 'rm -rf "$work"' EXIT
tree=$work/tree
mkdir -p "$tree"
tar --zstd -x --numeric-owner --xattrs --xattrs-include=security.capability --no-acls -C "$tree" -f "$archive"
[ -x "$tree/usr/lib/systemd/systemd" ] || { echo "the archive has no systemd" >&2; exit 1; }

sha=$(sha256sum "$archive" | cut -d' ' -f1)
install -d -m 0755 "$tree/data" "$tree/usr/share/pico4-poc"
echo "$sha" > "$tree/usr/share/pico4-poc/userspace-archive.sha256"
echo "$sha" > "$tree/usr/share/pico4-poc/system-image"

# The content plus a margin, rounded to MiB: free space on a read-only file system is unused.
used_mib=$(( $(du -sk "$tree" | cut -f1) / 1024 ))
size_mib=$(( used_mib * 110 / 100 + 128 ))
[ "$size_mib" -le "$SUPER_MIB" ] || { echo "the system ($size_mib MiB) does not fit super" >&2; exit 1; }
rm -f "$image"
mkfs.ext4 -q -F -L pico4-system -m 0 -O ^has_journal,^orphan_file,^metadata_csum_seed \
	-E lazy_itable_init=0,root_owner=0:0 -d "$tree" "$image" "${size_mib}M"
e2fsck -fn "$image" > /dev/null
echo "$image: ${size_mib} MiB, $(sha256sum "$image" | cut -d' ' -f1), from archive $sha"
