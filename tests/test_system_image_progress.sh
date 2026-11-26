#!/bin/bash
# SPDX-License-Identifier: Unlicense
# Run on a Linux host as root: bash tests/test_system_image_progress.sh
# Use real ext4 and compression tools, but no mounts, chroot, or network.
set -euo pipefail
cd "$(dirname "$0")/.."
build=$PWD/overpressure/build
[[ $(id -u) == 0 ]] || { echo "SKIP system image metadata fixture: run as root"; exit 0; }
for tool in zstd mkfs.ext4 debugfs e2fsck flock python3; do
	command -v "$tool" >/dev/null || { echo "SKIP system image metadata fixture: missing $tool"; exit 0; }
done
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
trap 'echo "fixture failed at line $LINENO (umask ${mask:-setup}, cache ${cache:-setup})" >&2' ERR
mkdir -p "$work/bin" "$work/tree/usr/lib/systemd" "$work/tree/usr/share" "$work/holo/etc/pacman.d"
install -m 0755 /dev/null "$work/tree/usr/lib/systemd/systemd"
tar --mtime=@1700000000 --mode='u+rwX,go+rX,go-w' --zstd -cf "$work/userspace.tar.zst" -C "$work/tree" usr
printf 'Server = https://example.invalid/fixture\n' > "$work/holo/etc/pacman.d/mirrorlist"
tar --zstd -cf "$work/holo.zst" -C "$work/holo" etc
holo_sha=$(sha256sum "$work/holo.zst" | cut -d' ' -f1)
pin=$(printf '%040d' 1)

cat > "$work/bin/chroot" <<'EOF'
#!/bin/bash
set -eu
base=${1%/host}
shift
args=("$@")
if [[ ${args[-3]} == bash && ${args[-2]} == -c ]]; then
	# Host e2fsprogs may live in /usr/sbin, unlike the target's tools.
	[[ ${args[2]} == PATH=* ]]
	args[2]="PATH=$PATH"
	args[-1]=${args[-1]//\/build\//$base/}
	exec "${args[@]}"
fi
[[ ${args[-5]} == pacman ]]
echo pacman >> "$base/host-setup"
EOF
printf '#!/bin/sh\nexit 0\n' > "$work/bin/mount"
cp "$work/bin/mount" "$work/bin/umount"
printf '#!/bin/sh\necho "unexpected network or namespace call" >&2\nexit 1\n' > "$work/bin/curl"
cp "$work/bin/curl" "$work/bin/unshare"
chmod +x "$work/bin/"*

for mask in 022 027; do
	base=$work/mask-$mask
	src=$base/src
	headset=$src/devices/fixture
	mkdir -p "$headset/"{userspace,scripts} "$base/"{downloads,root/packages,out}
	ln -s "$build" "$src/build"
	printf 'PREFIX=fixture\nSUPER_MIB=256\n' > "$headset/userspace/build.sh"
	printf '{\n "repository": "fixture/fixture",\n "commit": "%s"\n}\n' "$pin" > "$headset/userspace/packages.json"
	printf 'HOLO_SNAPSHOT=fixture\nHOLO_SHA256=%s\nHOLO_URL=https://example.invalid/fixture\n' "$holo_sha" \
		> "$base/downloads/packages-$pin.sh"
	cp "$work/holo.zst" "$base/root/packages/holo-fixture.rootfs.zst"
	cp "$work/userspace.tar.zst" "$base/userspace.tar.zst"
	cat > "$headset/scripts/build-userspace.sh" <<'EOF'
set -eu
umask > "$2/child.umask"
cp "${1%/root}/userspace.tar.zst" "$2/fixture-userspace.tar.zst"
EOF
	cat > "$headset/scripts/build-system-image.sh" <<'EOF'
set -eu
headset=$(cd "$(dirname "$0")/.." && pwd)
exec bash "$headset/../../build/build-system-image.sh" "$headset" "$@"
EOF
	for cache in baseline cold stale warm; do
		progress=1
		[[ $cache != baseline ]] || progress=0
		image=$base/out/fixture-system.img
		if [[ $cache == cold ]]; then
			rm -rf "$base/host"
			rm "$image.zst" "$image.zst.sha256" "$image.json"
		fi
		if [[ $cache == stale ]]; then
			printf 'stale\n' > "$base/host/.overpressure-host"
			touch "$base/host/stale"
			for suffix in .zst .zst.sha256 .json; do
				printf 'stale\n' > "$image$suffix"
				chmod 0600 "$image$suffix"
			done
		fi
		(
			umask "$mask"
			unset ALL_PROXY
			PATH="$work/bin:$PATH" OVERPRESSURE_BUILD_NAMESPACE=1 OVERPRESSURE_BUILD_PROGRESS="$progress" \
				SOURCE_DATE_EPOCH=1700000000 OVERPRESSURE_COMMIT=fixture \
				sh "$build/build-on-headset.sh" "$base" "$headset" 3>"$base/events" >"$base/log"
		)
		[[ $(cat "$base/out/child.umask") == "0$mask" ]]
		[[ $(cat "$base/host/.overpressure-host") == "$holo_sha" && ! -e $base/host/stale ]]
		[[ ! -e $image && ! -e $base/out/fixture-userspace.tar.zst ]]
		# A new sidecar honors the child umask. A pre-existing JSON is truncated, retaining its mode.
		mode=$(printf '%o' "$(( 8#666 & ~8#$mask ))")
		json_mode=$mode
		[[ $cache != stale && $cache != warm ]] || json_mode=600
		[[ $(stat -c %a "$image.zst.sha256") == "$mode" ]]
		[[ $(stat -c %a "$image.json") == "$json_mode" ]]
		python3 - "$image" "$base/userspace.tar.zst" "$work/unpacked.img" "$mode" <<'PY'
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

image, archive, unpacked = map(Path, sys.argv[1:4])
mode = sys.argv[4]
subprocess.run(["zstd", "-qdf", "--sparse", f"{image}.zst", "-o", unpacked], check=True)
with unpacked.open("rb") as stream:
    prefix = stream.read(1048576)
    sha = hashlib.sha256(prefix)
    for chunk in iter(lambda: stream.read(1048576), b""):
        sha.update(chunk)
archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
assert Path(f"{image}.zst.sha256").read_text().strip() == sha.hexdigest()
assert json.loads(Path(f"{image}.json").read_text()) == {
    "image": image.name,
    "bytes": 192 * 1048576,
    "sha256": sha.hexdigest(),
    "prefix_bytes": 1048576,
    "prefix_sha256": hashlib.sha256(prefix).hexdigest(),
    "archive_sha256": archive_sha,
}
assert unpacked.stat().st_size == 192 * 1048576
for marker in ("userspace-archive.sha256", "system-image"):
    path = f"/usr/share/fixture/{marker}"
    info = subprocess.check_output(["debugfs", "-R", f"stat {path}", unpacked], stderr=subprocess.PIPE, text=True)
    assert re.search(rf"\bMode:\s+0{mode}\b", info), f"incorrect mode for {marker}"
    content = subprocess.check_output(["debugfs", "-R", f"cat {path}", unpacked], stderr=subprocess.PIPE, text=True)
    assert content == f"{archive_sha}\n", f"incorrect content for {marker}"
PY
		if [[ $progress == 0 ]]; then
			[[ ! -s $base/events ]]
		else
			if [[ $cache != warm ]]; then
				grep -qx 'holo skipped' "$base/events"
				grep -qx 'holo-verify done' "$base/events"
			else
				grep -qx 'holo-cache skipped' "$base/events"
				! grep -q '^holo-verify ' "$base/events" || exit 1
			fi
			for phase in filesystem fsck image-hash compress; do grep -qx "$phase done" "$base/events"; done
		fi
	done
	[[ $(cat "$base/host-setup") == $'pacman\npacman\npacman' ]]
	echo "PASS: umask $mask, cold/stale/warm host cache, final metadata parity"
done
