#!/bin/bash
# SPDX-License-Identifier: Unlicense
# A headset's read-only system image for its super partition, from the userspace archive build-userspace.sh writes.
# As root in the build container (ownership and file capabilities), or in the headset's build host; a headset's
# scripts/build-system-image.sh calls it with its folder:
#
#   overpressure/build/build-system-image.sh HEADSET ARCHIVE IMAGE [--keep]
#
# ext4 with a journal (steamos-readonly disable makes it writable until the next upgrade) and without the features
# an older stock kernel cannot mount. /data is the data partition's mount point; the image names the archive it came
# from in /usr/share/PREFIX. HEADSET/userspace/build.sh gives PREFIX (the label PREFIX-system) and SUPER_MIB, the
# size of its super partition. What is kept is what an install sends: IMAGE.zst, the image compressed,
# IMAGE.zst.sha256 the hash of the image it holds and IMAGE.json the image's size, hash and its first MiB's hash.
# The image itself and the archive go, unless --keep: the install needs neither, and an upgrade builds both again.
set -euo pipefail
here=$(dirname "$0")
# shellcheck source=progress.sh
source "$here/progress.sh"

source_dir=$(cd "${1:?specify the headset folder}" && pwd)
archive=${2:?archive}
image=${3:?image}
keep=0
[ "${4:-}" = --keep ] && keep=1
PREFIX="" SUPER_MIB=""
# shellcheck source=/dev/null
source "$source_dir/userspace/build.sh"
[[ -n $PREFIX && -n $SUPER_MIB ]] || { echo "$source_dir/userspace/build.sh names no PREFIX or SUPER_MIB" >&2; exit 2; }
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
[ -s "$archive" ] || { echo "no archive $archive" >&2; exit 1; }

build_event filesystem running
work=$(mktemp -d "/var/tmp/$PREFIX-system.XXXXXX")
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
share=$tree/usr/share/$PREFIX
install -d -m 0755 "$tree/data" "$share"
echo "$sha" > "$share/userspace-archive.sha256"
echo "$sha" > "$share/system-image"
touch -h -d "@$epoch" "$tree" "$tree/data" "$share" "$share/userspace-archive.sha256" "$share/system-image"

# The content plus a margin and the journal, rounded to MiB: free space on a read-only file system is unused,
# and steamos-readonly disable grows it to the whole partition.
JOURNAL_MIB=64
used_mib=$(( $(du -sk "$tree" | cut -f1) / 1024 ))
size_mib=$(( used_mib * 110 / 100 + 128 + JOURNAL_MIB ))
[ "$size_mib" -le "$SUPER_MIB" ] || { echo "the system ($size_mib MiB) does not fit super" >&2; exit 1; }
rm -f "$image"
E2FSPROGS_FAKE_TIME=$epoch mkfs.ext4 -q -F -L "$PREFIX-system" -m 0 -O ^orphan_file,^metadata_csum_seed \
	-J size=$JOURNAL_MIB -U "$(uuid "$sha")" -E lazy_itable_init=0,lazy_journal_init=0,root_owner=0:0,hash_seed="$(uuid "${sha:32}")" \
	-d "$tree" "$image" "${size_mib}M"
# mke2fs takes each file's change and access times from the tree, where the extraction set them to now.
if find "$tree" -mindepth 1 -name '*"*' -print -quit | grep -q .; then
	echo "a path in the archive holds a double quote, which debugfs cannot name" >&2
	exit 1
fi
find "$tree" -mindepth 1 -printf '%P\n' |
	awk -v t="@$epoch" '{ printf "sif \"/%s\" ctime %s\nsif \"/%s\" atime %s\n", $0, t, $0, t }' > "$work/times"
errors=$(E2FSPROGS_FAKE_TIME=$epoch debugfs -w -f "$work/times" "$image" 2>&1 >/dev/null | grep -v '^debugfs [0-9]' || true)
[ -z "$errors" ] || { echo "debugfs: $errors" | head -n 5 >&2; exit 1; }
build_event filesystem 'done'
build_event fsck running
if [[ ${OVERPRESSURE_BUILD_PROGRESS:-0} == 1 ]]; then
	# -C reports work within each pass. Count only passes the checker reports
	# complete; this is a work fraction, not an elapsed-time estimate.
	LC_ALL=C e2fsck -fn -C 4 "$image" 4>&1 >/dev/null | awk '
		$1 ~ /^[1-5]$/ && $2 ~ /^[0-9]+$/ && $3 ~ /^[1-9][0-9]*$/ && $2 == $3 && $1 > seen {
			seen=$1
			printf "fsck running %d 100 percent local-check-progress\n", $1 * 20
			fflush()
		}' >&3
else
	e2fsck -fn "$image" > /dev/null
fi
build_event fsck 'done'
build_event image-hash running
image_sha=$(build_meter_input image-hash "$image" sha256sum "$image" | cut -d' ' -f1)
build_event image-hash 'done'
# The install streams the image compressed over the USB link (dev install): about half the bytes.
# Beside it the uncompressed image's hash, so that a stale copy is never sent for a newer image.
rm -f "$image.zst" "$image.zst.sha256"
build_event compress running
build_meter_input compress "$image" zstd -q -T0 -3 "$image" -o "$image.zst"
echo "$image_sha" > "$image.zst.sha256"
prefix_sha=$(head -c 1048576 "$image" | sha256sum | cut -d' ' -f1)
printf '{"image": "%s", "bytes": %s, "sha256": "%s", "prefix_bytes": 1048576, "prefix_sha256": "%s", "archive_sha256": "%s"}\n' \
	"$(basename "$image")" "$(stat -c %s "$image")" "$image_sha" "$prefix_sha" "$sha" > "$image.json"
echo "$image: ${size_mib} MiB, $image_sha, from archive $sha; $(( $(stat -c %s "$image.zst") >> 20 )) MiB compressed"
if [ "$keep" = 0 ]; then
	rm -f "$image" "$archive"
	echo "kept $image.zst and its record; the image and the archive are removed (--keep keeps them)"
fi
build_event compress 'done'
