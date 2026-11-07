#!/usr/bin/env bash
# SPDX-License-Identifier: Unlicense
# Build Valve's Frame kernel (linux-valve) for the PICO 4: Image.gz and the PICO 4's device tree.
#
#   build-kernel.sh SOURCE_PACKAGE STOCK_RECOVERY STOCK_VENDOR WORKDIR OUTDIR
#
# SOURCE_PACKAGE  linux-72-deckard-7.2.0+g509b71a1bdcd-1.src.tar.gz, Valve's GPL source package
#                 (steamdeck-packages.steamos.cloud/archlinux-deckard-hotfixes/source/), checked
#                 against its pinned hash; it carries the linux-valve git repository.
# STOCK_RECOVERY  the stock recovery image of the headset's firmware (python -m overpressure
#                 stock): its recovery_dtbo gives the labels the ABL's board overlay targets.
# STOCK_VENDOR    the stock vendor image of the headset's firmware (vendor.img of python -m
#                 overpressure stock): its camera module gives the RGB camera's register table.
# WORKDIR         build tree (Linux file system; a few GB).
# OUTDIR          Image.gz, sm8250-pico4-phoenix.dtb, config, version.
#
# The PICO 4's kernel patches are in patches/, applied in name order; its own sources are in
# src/, at their paths in the tree.
#
# Runs in the build container (python -m overpressure container run), cross compiling with
# aarch64-linux-gnu-gcc; overpressure's ext4 reader (scripts/stock-lib.sh) reads the vendor
# image. Incremental: a second run rebuilds only what changed.
set -euo pipefail

PACKAGE_SHA256=871f6abbc167a360368b0d2b19f1e0b2aed25d835f388873d3698b0b3a170252
COMMIT=509b71a1bdcdc1055eec7426a5e25633b07db12d
DTB=sm8250-pico4-phoenix
CAMERA_MODULE=/lib64/camera/com.qti.sensormodule.sunny_imx471.bin

[ $# -eq 5 ] || { sed -n '3,18p' "$0" >&2; exit 2; }
package=$(realpath "$1") recovery=$(realpath "$2") vendor=$(realpath "$3") work=$4 out=$5
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../../../scripts/stock-lib.sh"  # overpressure's
mkdir -p "$work" "$out"
work=$(realpath "$work") out=$(realpath "$out")

repo=$work/linux-valve.git
src=$work/linux
if [ ! -d "$repo" ]; then
	echo "checking $package" >&2
	echo "$PACKAGE_SHA256  $package" | sha256sum -c --quiet
	tar -xzf "$package" -C "$work" linux-72-deckard/archlinux-linux-deckard
	mv "$work/linux-72-deckard/archlinux-linux-deckard" "$repo"
	rmdir "$work/linux-72-deckard"
fi
if [ "$(git -C "$src" rev-parse HEAD 2>/dev/null)" != "$COMMIT" ]; then
	rm -rf "$src"
	git --git-dir="$repo" worktree prune
	git --git-dir="$repo" worktree add --detach "$src" "$COMMIT"
fi
# The PICO 4's patches (patches/*.patch, in order) on the pinned commit; a changed set starts
# from the commit again, which touches only the files that differ.
cat /dev/null "$here"/patches/*.patch > "$work/patches.wanted"
if ! cmp -s "$work/patches.wanted" "$work/patches.applied" 2>/dev/null; then
	git -C "$src" reset -q --hard "$COMMIT"
	# Files a patch added are untracked: without this its next application finds them there.
	git -C "$src" clean -q -fd
	for patch in "$here"/patches/*.patch; do
		[ -e "$patch" ] && git -C "$src" apply "$patch"
	done
	cp "$work/patches.wanted" "$work/patches.applied"
fi

# A file into the tree only when it changed, so an unchanged one is not rebuilt.
update() {
	cmp -s "$1" "$2" 2>/dev/null || install -m 644 "$1" "$2"
}
# The PICO 4's own sources (src/, at their paths in the tree).
(cd "$here/src" && find . -type f ! -name LICENSE.md) | while IFS= read -r file; do
	update "$here/src/$file" "$src/$file"
done

# The RGB camera's register table, generated from PICO OS's camera module.
dump "$vendor" "$CAMERA_MODULE" "$work/imx471-module.bin"
python3 "$here/imx471_mode.py" "$work/imx471-module.bin" "$work/pico4-imx471-mode.h"
update "$work/pico4-imx471-mode.h" "$src/drivers/media/i2c/pico4-imx471-mode.h"

# The PICO 4's tree beside mainline's SM8250 boards, with the sink for the ABL's overlay and
# the panels' command sequences, both generated from the stock recovery image.
dts=$src/arch/arm64/boot/dts/qcom
update "$here/$DTB.dts" "$dts/$DTB.dts"
python3 "$here/dtbo_sink.py" "$recovery" "$work/$DTB-dtbo-sink.dtsi"
update "$work/$DTB-dtbo-sink.dtsi" "$dts/$DTB-dtbo-sink.dtsi"
python3 "$here/panel_sequence.py" "$recovery" "$work/$DTB-panel.dtsi"
update "$work/$DTB-panel.dtsi" "$dts/$DTB-panel.dtsi"
grep -q "$DTB.dtb" "$dts/Makefile" || echo "dtb-\$(CONFIG_ARCH_QCOM) += $DTB.dtb" >> "$dts/Makefile"

# The version string (uname -v) names the inputs, not the build: instead of a local build counter
# and the build's time, a hash of everything this script puts into the tree (the patches, the own
# sources, the tree with its generated parts, the configuration fragment) and the pinned commit's
# date. The same inputs give the same kernel on any host.
build_id=$({
	cat "$work/patches.wanted" "$here/pico4.config" "$here/$DTB.dts" "$work/pico4-imx471-mode.h" \
		"$work/$DTB-dtbo-sink.dtsi" "$work/$DTB-panel.dtsi"
	(cd "$here/src" && find . -type f ! -name LICENSE.md | LC_ALL=C sort | xargs -r sha256sum)
} | sha256sum | cut -c1-12)
stamp=$(TZ=UTC git -C "$src" show -s --date='format-local:%a %b %e %H:%M:%S UTC %Y' --format=%cd "$COMMIT")
make=(make -C "$src" O="$work/build" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j"$(nproc)"
	KBUILD_BUILD_USER=overpressure KBUILD_BUILD_HOST=pico4 LOCALVERSION=
	KBUILD_BUILD_VERSION="$build_id" KBUILD_BUILD_TIMESTAMP="$stamp"
	# pahole 1.25 encodes BTF in parallel with make's -j (scripts/Makefile.btf's JOBS), and the
	# threads order the type names differently every time; one thread gives the same BTF.
	JOBS=1
	# The debug information names the work folder, and the build ID hashes it: without the prefix
	# the same inputs in another folder give another kernel.
	KCFLAGS="-ffile-prefix-map=$work/=" KAFLAGS="-ffile-prefix-map=$work/=")
config=$work/build/.config
mkdir -p "$work/build"
# The configuration starts from Valve's each time, so a changed fragment never stacks.
cat "$src/arch/arm64/configs/gabe_defconfig" "$here/pico4.config" > "$work/config.wanted"
if ! cmp -s "$work/config.wanted" "$work/config.applied" 2>/dev/null; then
	cp "$src/arch/arm64/configs/gabe_defconfig" "$config"
	"$src/scripts/kconfig/merge_config.sh" -m -O "$work/build" "$config" "$here/pico4.config" >/dev/null
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
done < "$here/pico4.config"

"${make[@]}" Image.gz "qcom/$DTB.dtb"
install -m 644 "$work/build/arch/arm64/boot/Image.gz" "$out/Image.gz"
install -m 644 "$work/build/arch/arm64/boot/dts/qcom/$DTB.dtb" "$out/$DTB.dtb"
install -m 644 "$config" "$out/config"
"${make[@]}" -s kernelrelease > "$out/version"
echo "$(cat "$out/version"): $(stat -c %s "$out/Image.gz") bytes Image.gz, $(stat -c %s "$work/build/arch/arm64/boot/Image") bytes Image" >&2
