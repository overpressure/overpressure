# shellcheck shell=bash
# SPDX-License-Identifier: Unlicense
# The reproducible core of a headset's kernel build: a checkout of overpressure/linux at one of its commits built
# into Image.gz, the headset's device tree, its config, version and build.json. Sourced by a headset's
# kernel/build-tree.sh (which overpressure/linux's CI and the headset's kernel step run), after it set:
#
#   tree work out   the checkout, the build's output tree and state (O=, Linux file system), the output folder
#   DTB             the device tree (qcom/DTB.dtb), FRAGMENT the config fragment merged over Valve's gabe_defconfig
#   KERNEL_HOST     the build's host name in uname -v; KERNEL_RECORD build.json's lines of the headset's own ("  "key":
#                   "value",\n" each, after banner; empty for none)
#
# uname -v names the tree's commit (#<12 hex digits>) and carries the commit's time, not the build's. The same
# commit gives the same device tree, config and version with the amd64 build image on any host; Image.gz differs
# in each build by the module-signing key Valve's configuration generates and builds in. Cross compiles with
# aarch64-linux-gnu-gcc in the build container. Incremental: a second run rebuilds only what changed. With
# CCACHE_DIR set, the compiler runs through ccache (the build image's), which keeps compiled objects in that folder
# for the next build, also from another checkout. ccache gives the objects the compiler would.
# shellcheck disable=SC2154  # tree, work, out and the headset's values: its kernel/build-tree.sh sets them first
fragment=$tree/arch/arm64/configs/$FRAGMENT
[ -f "$fragment" ] || { echo "$tree has no arch/arm64/configs/$FRAGMENT" >&2; exit 1; }
# uname names the commit, so the tracked files must be the commit's (a headset's prepared files are untracked).
[ -z "$(git -C "$tree" status --porcelain --untracked-files=no)" ] ||
	{ echo "$tree has changes: its kernel would name a commit it is not" >&2; exit 1; }
commit=$(git -C "$tree" rev-parse HEAD)
stamp=$(TZ=UTC git -C "$tree" show -s --date='format-local:%a %b %e %H:%M:%S UTC %Y' --format=%cd HEAD)

make=(make -C "$tree" O="$work/build" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j"$(nproc)"
	KBUILD_BUILD_USER=overpressure KBUILD_BUILD_HOST="$KERNEL_HOST" LOCALVERSION=
	KBUILD_BUILD_VERSION="${commit:0:12}" KBUILD_BUILD_TIMESTAMP="$stamp"
	# pahole 1.25 encodes BTF in parallel with make's -j (scripts/Makefile.btf's JOBS), and the
	# threads order the type names differently every time; one thread gives the same BTF.
	JOBS=1
	# The debug information names the tree's and the work folder's paths, and the build ID hashes
	# it: without the prefixes the same commit in another folder gives another kernel.
	KCFLAGS="-ffile-prefix-map=$tree/= -ffile-prefix-map=$work/="
	KAFLAGS="-ffile-prefix-map=$tree/= -ffile-prefix-map=$work/=")
if [ -n "${CCACHE_DIR:-}" ]; then
	if command -v ccache >/dev/null; then
		make+=(CC="ccache aarch64-linux-gnu-gcc")
		# A compiler of the same name but other contents is another compiler; 3 GB hold a kernel's objects.
		export CCACHE_COMPILERCHECK=content CCACHE_MAXSIZE=${CCACHE_MAXSIZE:-3G}
		mkdir -p "$CCACHE_DIR"
		ccache --zero-stats >/dev/null
	else
		echo "CCACHE_DIR is set, but this build image has no ccache: building without it" >&2
		unset CCACHE_DIR
	fi
fi
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
[ -z "${CCACHE_DIR:-}" ] || ccache --show-stats | grep -iE 'hits|misses|cache size' >&2 || true

sum() { sha256sum "$out/$1" | cut -d' ' -f1; }
# The tooling's own commit; a worktree's repository is at OVERPRESSURE_GIT_DIR in the build
# container (overpressure/linux.py).
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)   # overpressure
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
$KERNEL_RECORD  "tooling": "$tooling",
  "image": "${OVERPRESSURE_IMAGE:-unknown}",
  "files": {
    "Image.gz": "$(sum Image.gz)",
    "$DTB.dtb": "$(sum "$DTB.dtb")",
    "config": "$(sum config)"
  }
}
EOF
echo "$(cat "$out/version") #${commit:0:12}: $(stat -c %s "$out/Image.gz") bytes Image.gz, Image.gz $(sum Image.gz)" >&2
