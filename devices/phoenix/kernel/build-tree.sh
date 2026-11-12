#!/usr/bin/env bash
# SPDX-License-Identifier: Unlicense
# Build a checkout of overpressure/linux's PICO 4 branch (phoenix/7.2 or one of its tags): Image.gz,
# the device tree and a record of both.
#
#   build-tree.sh TREE WORKDIR OUTDIR
#
# TREE     a clean checkout of overpressure/linux at a phoenix/ commit: Valve's kernel with the
#          layers, the device tree and arch/arm64/configs/pico4.config, which is merged over
#          Valve's gabe_defconfig.
# WORKDIR  the build's output tree (O=) and state (Linux file system; a few GB).
# OUTDIR   Image.gz, sm8250-pico4-phoenix.dtb, config, version and build.json.
#
# The RGB camera's register table is PICO's data and not part of the tree. While the driver
# compiles it in (TREE/drivers/media/i2c/pico4-imx471-mode.h, from the headset's vendor image by
# imx471_mode.py), a tree without it gets an empty table, which leaves the camera dark;
# build.json's camera_table says which: "none" (the driver needs none), "empty" or the table's hash.
#
# uname -v names the tree's commit (#<12 hex digits>) and carries the commit's time, not the
# build's: the same commit and camera table give the same kernel on any host.
#
# Runs in the build container (python -m overpressure container run), cross compiling with
# aarch64-linux-gnu-gcc. Incremental: a second run rebuilds only what changed.
set -euo pipefail

DTB=sm8250-pico4-phoenix

[ $# -eq 3 ] || { sed -n '3,20p' "$0" >&2; exit 2; }
tree=$(realpath "$1")
mkdir -p "$2" "$3"
work=$(realpath "$2") out=$(realpath "$3")
here=$(cd "$(dirname "$0")" && pwd)
fragment=$tree/arch/arm64/configs/pico4.config
table=$tree/drivers/media/i2c/pico4-imx471-mode.h
[ -f "$fragment" ] || { echo "$tree is no PICO 4 tree: it has no arch/arm64/configs/pico4.config" >&2; exit 1; }
camera=none
if grep -q 'pico4-imx471-mode\.h' "$tree/drivers/media/i2c/pico4-imx471.c"; then
	if [ ! -f "$table" ]; then
		echo "no camera table in the tree: an empty one, the RGB camera stays dark" >&2
		printf '%s\n' '/* No camera module: an empty table, the RGB camera stays dark. */' \
			'static const struct imx471_reg imx471_mode[] = {' '};' > "$table"
	fi
	camera=$(sha256sum "$table" | cut -d' ' -f1)
	grep -q '^static const struct imx471_reg imx471_mode\[\] = {$' "$table" &&
		[ "$(grep -c . "$table")" -le 3 ] && camera=empty
fi
# uname names the commit, so the tracked files must be the commit's (the table is untracked).
[ -z "$(git -C "$tree" status --porcelain --untracked-files=no)" ] ||
	{ echo "$tree has changes: its kernel would name a commit it is not" >&2; exit 1; }
commit=$(git -C "$tree" rev-parse HEAD)
stamp=$(TZ=UTC git -C "$tree" show -s --date='format-local:%a %b %e %H:%M:%S UTC %Y' --format=%cd HEAD)

make=(make -C "$tree" O="$work/build" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j"$(nproc)"
	KBUILD_BUILD_USER=overpressure KBUILD_BUILD_HOST=pico4 LOCALVERSION=
	KBUILD_BUILD_VERSION="${commit:0:12}" KBUILD_BUILD_TIMESTAMP="$stamp"
	# pahole 1.25 encodes BTF in parallel with make's -j (scripts/Makefile.btf's JOBS), and the
	# threads order the type names differently every time; one thread gives the same BTF.
	JOBS=1
	# The debug information names the tree's and the work folder's paths, and the build ID hashes
	# it: without the prefixes the same commit in another folder gives another kernel.
	KCFLAGS="-ffile-prefix-map=$tree/= -ffile-prefix-map=$work/="
	KAFLAGS="-ffile-prefix-map=$tree/= -ffile-prefix-map=$work/=")
config=$work/build/.config
mkdir -p "$work/build"
# The configuration starts from Valve's each time, so a changed fragment never stacks.
cat "$tree/arch/arm64/configs/gabe_defconfig" "$fragment" > "$work/config.wanted"
if ! cmp -s "$work/config.wanted" "$work/config.applied" 2>/dev/null; then
	cp "$tree/arch/arm64/configs/gabe_defconfig" "$config"
	"$tree/scripts/kconfig/merge_config.sh" -m -O "$work/build" "$config" "$fragment" >/dev/null
	"${make[@]}" olddefconfig >/dev/null
	cp "$work/config.wanted" "$work/config.applied"
fi
# Every option of the fragment must have survived olddefconfig (a missing dependency drops it),
# and every one it turns off must be off (another option may select it).
while IFS= read -r line; do
	case $line in
	CONFIG_*=*) grep -qxF "$line" "$config" || { echo "not applied: $line" >&2; exit 1; } ;;
	"# CONFIG_"*" is not set")
		name=${line#"# "}
		name=${name%" is not set"}
		! grep -q "^$name=" "$config" || { echo "not turned off: $name" >&2; exit 1; }
		;;
	esac
done < "$fragment"

"${make[@]}" Image.gz "qcom/$DTB.dtb"
install -m 644 "$work/build/arch/arm64/boot/Image.gz" "$out/Image.gz"
install -m 644 "$work/build/arch/arm64/boot/dts/qcom/$DTB.dtb" "$out/$DTB.dtb"
install -m 644 "$config" "$out/config"
"${make[@]}" -s kernelrelease > "$out/version"

sum() { sha256sum "$out/$1" | cut -d' ' -f1; }
# The tooling's own commit; a worktree's repository is at OVERPRESSURE_GIT_DIR in the build
# container (overpressure/linux.py).
repo=$(cd "$here/../../.." && pwd)
git_where=(-C "$repo")
[ -z "${OVERPRESSURE_GIT_DIR:-}" ] || git_where=(--git-dir="$OVERPRESSURE_GIT_DIR" --work-tree="$repo")
tooling=$(git -c safe.directory='*' "${git_where[@]}" describe --always --dirty --abbrev=40 2>/dev/null || echo unknown)
# The banner the kernel prints and /proc/version shows: release, compiler, commit and time.
banner=$(LC_ALL=C grep -a -m1 -o 'Linux version [[:print:]]*' "$work/build/arch/arm64/boot/Image")
cat > "$out/build.json" <<EOF
{
  "kernel": "$(cat "$out/version")",
  "commit": "$commit",
  "banner": "$banner",
  "camera_table": "$camera",
  "tooling": "$tooling",
  "files": {
    "Image.gz": "$(sum Image.gz)",
    "$DTB.dtb": "$(sum "$DTB.dtb")",
    "config": "$(sum config)"
  }
}
EOF
echo "$(cat "$out/version") #${commit:0:12}: $(stat -c %s "$out/Image.gz") bytes Image.gz, Image.gz $(sum Image.gz)" >&2
