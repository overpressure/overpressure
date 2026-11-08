#!/bin/bash
# SPDX-License-Identifier: Unlicense
# The installed system's read-only image for super (docs/install.md), from
# the userspace archive scripts/build-userspace.sh writes. As root in the build container (ownership
# and file capabilities): the window's "System image for super" step, or python -m overpressure
# container run -- bash devices/phoenix/scripts/build-system-image.sh ...:
#
#   scripts/build-system-image.sh out/userspace/pico4-userspace.tar.zst out/userspace/pico4-system.img
#
# ext4 without a journal (it is never written on the headset) and without the features the stock
# kernel, under the maintenance stage 1, cannot mount. /data is the data partition's mount point; the image names the
# archive it came from in /usr/share/pico4. IMAGE.zst beside it is what dev install sends, IMAGE.zst.sha256 the hash
# of the image it holds.
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
# The same archive gives the same image: the archive's time (its /usr, the build's SOURCE_DATE_EPOCH)
# for every time the file system records, and its hash for the UUID and the directory hash seed.
epoch=$(stat -c %Y "$tree/usr")
uuid() { sed -E 's/^(.{8})(.{4})(.{4})(.{4})(.{12}).*/\1-\2-\3-\4-\5/' <<< "$1"; }
install -d -m 0755 "$tree/data" "$tree/usr/share/pico4"
echo "$sha" > "$tree/usr/share/pico4/userspace-archive.sha256"
echo "$sha" > "$tree/usr/share/pico4/system-image"
touch -h -d "@$epoch" "$tree" "$tree/data" "$tree/usr/share/pico4" "$tree/usr/share/pico4/userspace-archive.sha256" \
	"$tree/usr/share/pico4/system-image"

# The content plus a margin, rounded to MiB: free space on a read-only file system is unused.
used_mib=$(( $(du -sk "$tree" | cut -f1) / 1024 ))
size_mib=$(( used_mib * 110 / 100 + 128 ))
[ "$size_mib" -le "$SUPER_MIB" ] || { echo "the system ($size_mib MiB) does not fit super" >&2; exit 1; }
rm -f "$image"
E2FSPROGS_FAKE_TIME=$epoch mkfs.ext4 -q -F -L pico4-system -m 0 -O ^has_journal,^orphan_file,^metadata_csum_seed \
	-U "$(uuid "$sha")" -E lazy_itable_init=0,root_owner=0:0,hash_seed="$(uuid "${sha:32}")" -d "$tree" "$image" "${size_mib}M"
# mke2fs takes each file's change and access times from the tree, where the extraction set them to now.
if find "$tree" -mindepth 1 -name '*"*' -print -quit | grep -q .; then
	echo "a path in the archive holds a double quote, which debugfs cannot name" >&2
	exit 1
fi
find "$tree" -mindepth 1 -printf '%P\n' |
	awk -v t="@$epoch" '{ printf "sif \"/%s\" ctime %s\nsif \"/%s\" atime %s\n", $0, t, $0, t }' > "$work/times"
errors=$(E2FSPROGS_FAKE_TIME=$epoch debugfs -w -f "$work/times" "$image" 2>&1 >/dev/null | grep -v '^debugfs [0-9]' || true)
[ -z "$errors" ] || { echo "debugfs: $errors" | head -n 5 >&2; exit 1; }
e2fsck -fn "$image" > /dev/null
image_sha=$(sha256sum "$image" | cut -d' ' -f1)
# The install streams the image compressed over the USB link (dev install): about half the bytes.
# Beside it the uncompressed image's hash, so that a stale copy is never sent for a newer image.
rm -f "$image.zst" "$image.zst.sha256"
zstd -q -T0 -3 "$image" -o "$image.zst"
echo "$image_sha" > "$image.zst.sha256"
echo "$image: ${size_mib} MiB, $image_sha, from archive $sha; $(( $(stat -c %s "$image.zst") >> 20 )) MiB compressed"
