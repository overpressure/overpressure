#!/bin/bash
# SPDX-License-Identifier: Unlicense
# Build the SteamOS-compatible ARM64 userspace for the PICO 4 (docs/userspace.md).
#
# Usage (as root in the build container: the window's "Userspace archive" step, or
# python -m overpressure container run -- bash devices/phoenix/scripts/build-userspace.sh ...):
#   scripts/build-userspace.sh BUILD_ROOT OUTPUT_DIR [OPTIONS]
#
# Each of these comes from the stock firmware (PICO's public OTA) through python -m overpressure
# assets, an assets/KIND-VERSION folder (docs/development.md#stock-firmware). The headset's own factory data is
# not in the image: it links to persist, mounted read-only at boot (pico4-persist.service).
#   --gpu-firmware DIR  a650_* GPU firmware (python -m overpressure assets,
#                       docs/graphics.md). Without it the userspace boots, but Vulkan cannot start.
#   --sensor-data DIR   sensor DSP firmware and config, lens and controller data, the RGB camera's
#                       register table (python -m overpressure assets, docs/xr.md). Without it the
#                       head IMU stack stays inactive and the RGB camera dark.
#   --wlan DIR          Wi-Fi firmware and board data (python -m overpressure assets,
#                       docs/userspace.md). Without it Wi-Fi stays off.
#   --audio DIR         ADSP firmware and the audio process's files
#                       (python -m overpressure assets, docs/userspace.md). Without it there is no
#                       sound card.
#   --stock-slam DIR    PICO OS's head tracker, its Compute DSP firmware and bionic root
#                       (python -m overpressure assets, docs/xr.md). Without it head tracking
#                       falls back to 3DoF unless PICO4_SLAM_BACKEND=basalt.
#   --packages DIR      build a checkout of overpressure/packages instead of taking the
#                       pinned release's packages (trying a change to a package)
#   --build-packages    build the pinned commit's recipes here instead of taking its release's
#                       packages
#   --refresh           download the current package databases and reinstall the base
#                       root; without it every build installs the same package versions.
#                       Builds the project's packages here against them.
#   --update-lock       write OUTPUT_DIR/packages.lock instead of checking the pinned one
#
# The image's definition (pacman.conf, packages.txt, packages.lock) and the project's packages
# are overpressure/packages' (headsets/phoenix and its layers), at the commit
# userspace/packages.json pins; the build fetches it into cache/packages. Each step is skipped
# when its inputs did not change:
# 1. Build environment and project packages: overpressure/packages' build/packages.sh, in
#    Valve/Collabora's Holo Core aarch64 preview rootfs (sha256-pinned), entered with chroot.
#    The project's packages are the pinned release's (built on arm64 in that repository's CI),
#    downloaded into cache/packages-release/TAG and checked against its SHA256SUMS, whose hash
#    the pin holds. With --packages, --build-packages or --refresh they are built here instead:
#    on other hosts than arm64 the programs run under QEMU and the host's clang and lld compile
#    natively; a package is rebuilt when its recipe folder changes. Each layer is a local pacman
#    repository, overpressure-LAYER.
# 2. Base root: packages.txt installed with pacman from the project's layers, the Steam Frame
#    hotfix repository and the Holo Core preview (pacman.conf). Reinstalled when the list or
#    pacman.conf changes, and with --refresh.
# 3. Image root: an overlay on the base root with the current project packages and the
#    PICO 4 adaptation layer (userspace/rootfs, shared stage-1 scripts, users, units),
#    checked natively for unresolved ELF dependencies (overpressure/elfdeps.py), written as:
#      OUTPUT_DIR/pico4-userspace.tar.zst        root filesystem for scripts/build-system-image.sh
#      OUTPUT_DIR/pico4-userspace.manifest.json  sources, hashes, package list
#
# Requires curl, tar, zstd, sha256sum, python3, chroot and overlayfs; on x86-64 an
# aarch64 binfmt handler with the F (fix-binary) flag and LLVM 20's clang and lld: the build
# container has them all and registers the handler when the engine has none. The build reads a snapshot of the tree taken at its start, so the tree can be
# edited meanwhile. Nothing touches the headset. The output contains Valve packages:
# keep it private, do not redistribute.
set -euo pipefail
source_dir=$(cd "$(dirname "$0")/.." && pwd)   # devices/phoenix
repo=$(cd "$source_dir/../.." && pwd)            # overpressure (its package, cache/)
build_root=${1:?specify an isolated Linux-filesystem build directory}
output=${2:?specify an output directory}
shift 2
firmware="" sensor_data="" wlan="" audio="" stock_slam="" packages_dir="" build_packages=0 refresh=0
while [[ $# -gt 0 ]]; do
    case "$1" in
    --gpu-firmware) firmware=$(realpath "${2:?}"); shift 2 ;;
    --sensor-data) sensor_data=$(realpath "${2:?}"); shift 2 ;;
    --wlan) wlan=$(realpath "${2:?}"); shift 2 ;;
    --audio) audio=$(realpath "${2:?}"); shift 2 ;;
    --stock-slam) stock_slam=$(realpath "${2:?}"); shift 2 ;;
    --packages) packages_dir=$(realpath "${2:?}"); shift 2 ;;
    --build-packages) build_packages=1; shift ;;
    --refresh) refresh=1; shift ;;
    --update-lock) update_lock=1; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
    esac
done
[[ $EUID == 0 && "$build_root" = /* && "$build_root" != / ]] || { echo 'Root and an absolute isolated build directory are required' >&2; exit 2; }
# The pinned release's packages, unless they are to be built here.
packages_release=1
[[ $build_packages == 0 && $refresh == 0 && -z $packages_dir ]] || packages_release=0
host_arch=$(uname -m)
if [[ $host_arch != aarch64 ]]; then
    # The handler by its ELF magic (aarch64 executables), whatever it is named: qemu-aarch64 from
    # Debian's qemu-user-static, aarch64 from Docker Desktop or tonistiigi/binfmt.
    aarch64_binfmt=""
    for f in /proc/sys/fs/binfmt_misc/*; do
        grep -qs '^magic 7f454c460201010000000000000000000200b700' "$f" && grep -qs '^flags: .*F' "$f" && aarch64_binfmt=$f
    done
    [[ -n $aarch64_binfmt ]] || { echo 'An aarch64 binfmt handler with the F flag is required' >&2; exit 2; }
fi
mkdir -p "$build_root" "$output"
build_root=$(realpath "$build_root")
output=$(realpath "$output")
[[ "$build_root" != / ]] || { echo 'The build root must not resolve to /' >&2; exit 2; }

# The build environment is overpressure/packages' (build/packages.sh), kept in the build root.
B=$build_root/packages/environment
T=$B/target    # the image root, an overlay on $B/base
log() { printf '>> %s\n' "$*"; }
phase() { printf '>> [%4ss] %s\n' "$SECONDS" "$*"; }
# step WORDS: the next of the build's steps begins, a progress line for the installer (overpressure.progress):
# the environment with the project packages, the base root, the image root, its project packages,
# the ELF check, the adaptation layer and the archive. A step with nothing to do passes at once.
step_total=8
step_done=0
step() { printf 'progress %d/%d: %s\n' "$step_done" "$step_total" "$*"; step_done=$((step_done + 1)); }
# assets_modes DIR...: what came from an assets folder as 0644 files and 0755 directories,
# whatever modes the checkout showed (a Windows checkout through the container's bind mount shows
# 0777 or 0755); links stay as they are. What must run gets 0755 explicitly afterwards.
assets_modes() {
    find "$@" -type d -exec chmod 0755 {} +
    find "$@" -type f -exec chmod 0644 {} +
}
mounted=()
mnt() { mount "$@"; mounted+=("${!#}"); }
cleanup() { for ((i=${#mounted[@]}-1;i>=0;i--)); do umount -l "${mounted[$i]}" 2>/dev/null || true; done; mounted=(); }
trap cleanup EXIT
# unmount_below DIR [inner]: detach every mount at or below DIR (left over from an
# interrupted build) so that removing DIR can never reach the host's /dev or /sys;
# "inner" keeps DIR itself mounted.
unmount_below() {
    local p
    while read -r p; do umount -l "$p"; done < <(awk -v d="$1" -v i="${2:-}" \
        '(i == "" && $2 == d) || index($2, d "/") == 1 {print $2}' /proc/mounts | sort -r)
    ! awk -v d="$1" -v i="${2:-}" '(i == "" && $2 == d) || index($2, d "/") == 1 {found=1} END {exit !found}' /proc/mounts ||
        { echo "mounts remain below $1" >&2; exit 1; }
}
# mount_api DIR: the kernel file systems a chroot into DIR needs. /dev goes in recursively: rootless
# Podman binds each device over a placeholder file (root's, mode 0700), and a plain bind shows the
# placeholders, which only root can open. A slave, so that the devpts below stays the chroot's.
mount_api() {
    mnt --bind /proc "$1/proc"
    mnt --bind /sys "$1/sys"
    mnt --rbind /dev "$1/dev"
    mount --make-rslave "$1/dev"
    mnt -t devpts -o newinstance,ptmxmode=0666 devpts "$1/dev/pts"
    mnt -t tmpfs tmpfs "$1/run"
}

# -- 0. a snapshot of the inputs ------------------------------------------------------
# overpressure/packages at the pinned commit, fetched once into cache/packages (git checks that the
# files are the commit's), or the checkout --packages names.
read -r packages_repository packages_tag packages_commit packages_sums < <(python3 -c \
    'import json,sys; d=json.load(open(sys.argv[1])); print(d["repository"], d["tag"], d["commit"], d["sha256sums"])' \
    "$source_dir/userspace/packages.json")
if [[ -z $packages_dir ]]; then
    packages_dir=$repo/cache/packages/$packages_commit
    if [[ ! -f $packages_dir/.overpressure-commit ]]; then
        log "fetching $packages_repository $packages_tag ($packages_commit)"
        rm -rf "$packages_dir.new"
        git init -q "$packages_dir.new"
        git -C "$packages_dir.new" fetch -q --depth 1 "https://github.com/$packages_repository.git" "$packages_commit"
        git -C "$packages_dir.new" -c advice.detachedHead=false checkout -q FETCH_HEAD
        [[ $(git -C "$packages_dir.new" rev-parse HEAD) == "$packages_commit" ]]
        rm -rf "$packages_dir.new/.git" "$packages_dir"
        echo "$packages_commit" > "$packages_dir.new/.overpressure-commit"
        mv "$packages_dir.new" "$packages_dir"
    fi
    packages_source="$packages_repository $packages_tag $packages_commit"
else
    packages_source="local $(git -c safe.directory='*' -C "$packages_dir" describe --always --dirty --abbrev=12 2>/dev/null || echo checkout)"
fi
[[ -f $packages_dir/headsets/phoenix/layers && -f $packages_dir/build/packages.sh ]] ||
    { echo "$packages_dir is no checkout of overpressure/packages" >&2; exit 2; }
project=$build_root/project
rm -rf "$project.new"
mkdir -p "$project.new/initramfs/usr/libexec/pico4" "$project.new/prebuilt/aarch64" "$project.new/packages"
cp -r "$repo/overpressure" "$source_dir/src" "$source_dir/userspace" "$project.new/"
(cd "$packages_dir" && tar --exclude=.git --exclude=./out -cf - .) | tar -xf - -C "$project.new/packages"
for f in lib.sh recovery-policy.sh recovery-watch; do
    cp "$source_dir/initramfs/usr/libexec/pico4/$f" "$project.new/initramfs/usr/libexec/pico4/"
done
cp "$source_dir/prebuilt/aarch64/pico4-reboot" "$source_dir/prebuilt/aarch64/pico4-splash" \
    "$project.new/prebuilt/aarch64/"
find "$project.new" -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$project"
mv "$project.new" "$project"
# What the image says it is, from the tree the snapshot was taken of: the release's version and the
# Git commit. The commit's time is every time the build records (SOURCE_DATE_EPOCH: package build
# dates and file times, install dates, caches, the archive), so that two clean builds of a commit give
# the same archive (docs/userspace.md#reproducible-builds).
version=$(sed -n 's/^version = "\(.*\)"/\1/p' "$repo/pyproject.toml")
# A worktree's repository is at OVERPRESSURE_GIT_DIR in the build container (overpressure/linux.py).
git_where=(-C "$repo")
[ -z "${OVERPRESSURE_GIT_DIR:-}" ] || git_where=(--git-dir="$OVERPRESSURE_GIT_DIR" --work-tree="$repo")
commit=$(git -c safe.directory='*' "${git_where[@]}" describe --always --dirty --abbrev=12 2>/dev/null || echo unknown)
if [[ -z ${SOURCE_DATE_EPOCH:-} ]]; then
    SOURCE_DATE_EPOCH=$(git -c safe.directory='*' "${git_where[@]}" log -1 --format=%ct 2>/dev/null || date +%s)
fi
export SOURCE_DATE_EPOCH

# -- 1. build environment and project packages --------------------------------------------
# The image's definition and its layers, from the snapshot of overpressure/packages.
definition=$project/packages/headsets/phoenix
mapfile -t layers < <(sed -e 's/#.*//' -e '/^[[:space:]]*$/d' "$definition/layers")
repos=$build_root/packages/repositories
step "the build environment and the project packages"
if [[ $packages_release == 1 ]]; then
    # The release's packages, each checked against its SHA256SUMS, kept in cache/packages-release/TAG;
    # each layer gets the packages its database lists. The environment is packages.sh's, without its tools.
    release=$repo/cache/packages-release/$packages_tag
    url=https://github.com/$packages_repository/releases/download/$packages_tag
    mkdir -p "$release"
    [[ -s $release/SHA256SUMS ]] || curl -fsSL --retry 3 -o "$release/SHA256SUMS" "$url/SHA256SUMS"
    printf '%s  %s\n' "$packages_sums" "$release/SHA256SUMS" | sha256sum --check --quiet ||
        { echo "the release's SHA256SUMS is not the pinned one" >&2; exit 1; }
    while read -r sum name; do
        [[ $name == *.pkg.tar.zst || $name == overpressure-*.db ]] || continue
        if ! printf '%s  %s\n' "$sum" "$release/$name" | sha256sum --check --quiet --status 2>/dev/null; then
            log "downloading $name"
            curl -fsSL --retry 3 -o "$release/$name.part" "$url/$name"
            mv "$release/$name.part" "$release/$name"
        fi
        printf '%s  %s\n' "$sum" "$release/$name" | sha256sum --check --quiet
    done < "$release/SHA256SUMS"
    rm -rf "$repos"
    for layer in "${layers[@]}"; do
        install -d "$repos/$layer"
        while read -r name; do
            cp "$release/$name" "$repos/$layer/"
        done < <(tar -xzOf "$release/overpressure-$layer.db" --wildcards '*/desc' | awk '/^%FILENAME%$/ {getline; print}')
    done
    bash "$project/packages/build/packages.sh" --databases phoenix "$build_root/packages" "$repos"
    packages_source="$packages_source release"
else
    OVERPRESSURE_REFRESH=$([[ $refresh == 1 ]] && echo 1) bash "$project/packages/build/packages.sh" phoenix \
        "$build_root/packages" "$repos"
fi
in_b() { chroot "$B" /usr/bin/env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/bin LANG=C.UTF-8 HOME=/root \
    SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH" "$@"; }
mkdir -p "$B/etc/pico4" "$B/var/cache/pico4-pkgs"
cp "$definition/pacman.conf" "$B/etc/pico4/pacman.conf"
# Each layer's repository where pacman.conf's file:// servers name it.
rm -rf "$B/var/cache/overpressure"
for layer in "${layers[@]}"; do
    install -d "$B/var/cache/overpressure/$layer"
    cp "$repos/$layer"/* "$B/var/cache/overpressure/$layer/"
done
cp -L /etc/resolv.conf "$B/etc/resolv.conf"
mount_api "$B"
# The host's clang and lld, for PICO OS's tracker (--stock-slam).
llvm=""
if [[ $host_arch == x86_64 ]]; then
    for v in 21 20 19; do
        if [[ -x /usr/lib/llvm-$v/bin/clang && -x /usr/lib/llvm-$v/bin/ld.lld ]]; then llvm=/usr/lib/llvm-$v; break; fi
    done
fi
# project_packages: every project package file, as /var/cache/overpressure/LAYER/FILE.
project_packages() {
    (cd "$B" && find var/cache/overpressure -name '*.pkg.tar.zst' ! -name '*-debug-*' | sort | sed 's|^|/|')
}

# -- 2. base root ------------------------------------------------------------------------
step "the base root"
mapfile -t packages < <(sed -e 's/#.*//' -e 's/[[:space:]]*$//' -e '/^$/d' -e '/^!/d' "$definition/packages.txt")
# "!name": dependencies on name count as satisfied, name is not installed.
mapfile -t assumed < <(sed -n 's/^!\([^[:space:]#]*\).*/--assume-installed=\1=0/p' "$definition/packages.txt")
base=$B/base
# pacman copies the project repositories' packages into its cache. A package rebuilt from
# changed sources keeps its version, so a copy there would no longer match the repository
# (a corrupt package for pacman, the wrong file for the manifest): only the repositories have them.
while read -r file; do rm -f "$B/var/cache/pico4-pkgs/${file##*/}"; done < <(project_packages)
steamos_user=userspace/rootfs/usr/lib/sysusers.d/pico4-steamos.conf
base_key=$(cat "$definition/packages.txt" "$definition/pacman.conf" "$project/$steamos_user" |
    sha256sum | cut -d' ' -f1)
if [[ $refresh == 1 || ! -d "$base" || "$(cat "$build_root/base.key" 2>/dev/null)" != "$base_key" ]]; then
    phase "installing packages.txt into a new base root"
    rm -f "$build_root/base.key"
    # Without --refresh the new base root keeps the package databases of the last one.
    dbs=$build_root/base-databases
    if [[ $refresh == 0 && -d "$base/var/lib/pacman/sync" ]]; then
        rm -rf "$dbs"
        cp -a "$base/var/lib/pacman/sync" "$dbs"
    fi
    unmount_below "$T"
    unmount_below "$base"
    rm -rf "$base"
    mkdir -p "$base"/{dev,proc,sys,run,tmp,etc,var/lib/pacman/sync,var/cache/pacman/pkg}
    chmod 1777 "$base/tmp"
    sync=y
    if [[ $refresh == 0 && -d "$dbs" ]]; then
        cp -a "$dbs"/. "$base/var/lib/pacman/sync/"
        for layer in "${layers[@]}"; do
            cp "$B/var/cache/overpressure/$layer/overpressure-$layer.db" "$base/var/lib/pacman/sync/"
        done
        sync=""
    fi
    mount_api "$base"
    # The session user exists before pacman's hooks run: steamos-customizations' tmpfiles
    # entries name it, and the sysusers hook runs before the tmpfiles hook.
    install -D -m 0644 "$project/$steamos_user" "$base/${steamos_user#userspace/rootfs/}"
    in_b pacman --config /etc/pico4/pacman.conf -r /base --cachedir /var/cache/pico4-pkgs \
        "-S$sync" --noconfirm --needed "${assumed[@]}" "${packages[@]}"
    unmount_below "$base" inner
    systemd_version=$(chroot "$base" /usr/bin/pacman -Q systemd | cut -d' ' -f2)
    [[ "$systemd_version" == 257.* ]] || { echo "systemd $systemd_version was selected; the image stays on the Frame's 257.x" >&2; exit 1; }
    # The project packages in the base root, by package file hash.
    project_packages | (cd "$B" && sed 's|^/||' | xargs -r sha256sum) > "$build_root/base.project"
    echo "$base_key" > "$build_root/base.key"
fi

# -- 3. image root -----------------------------------------------------------------------
step "assembling the image root"
phase "assembling the image root"
unmount_below "$T"
rm -rf "$T" "$build_root/overlay"
mkdir -p "$T" "$build_root/overlay/upper" "$build_root/overlay/work"
mnt -t overlay overlay -o "lowerdir=$base,upperdir=$build_root/overlay/upper,workdir=$build_root/overlay/work" "$T"
mount_api "$T"
in_t() { chroot "$T" /usr/bin/env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/bin LANG=C.UTF-8 HOME=/root \
    SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH" "$@"; }
for layer in "${layers[@]}"; do
    cp "$B/var/cache/overpressure/$layer/overpressure-$layer.db" "$T/var/lib/pacman/sync/"
done
# This build's project packages where they differ from the base root's.
changed=()
while read -r sum file; do
    grep -qxF "$sum  $file" "$build_root/base.project" || changed+=("/$file")
done < <(project_packages | (cd "$B" && sed 's|^/||' | xargs -r sha256sum))
step "the image's project packages"
if [[ ${#changed[@]} -gt 0 ]]; then
    phase "installing ${#changed[@]} changed project packages"
    in_b pacman --config /etc/pico4/pacman.conf -r /target --cachedir /var/cache/pico4-pkgs -U --noconfirm "${changed[@]}"
fi
# adb (android-platform-tools, with which Lepton installs Android games) was built against
# protobuf 25; the base has 33. The Frame repository's protobuf 25 libraries go where adb's
# own libraries are (its RUNPATH), not over the base's; only its libraries are wanted, so its
# dependencies are not resolved (the ELF check below covers what the libraries need).
in_b pacman --config /etc/pico4/pacman.conf -r /target --cachedir /var/cache/pico4-pkgs -Sw -dd --noconfirm \
    deckard-arch-hotfixes-release-0.3/protobuf >/dev/null
protobuf=$(cd "$B/var/cache/pico4-pkgs" && ls protobuf-25.*-aarch64.pkg.tar.zst | sort -V | tail -n 1)
in_b bsdtar -xf "/var/cache/pico4-pkgs/$protobuf" -C /target/usr/lib/aarch64/android \
    --strip-components 2 'usr/lib/libprotobuf.so.25*'
# Same repositories on the headset, but an upgrade must never pull systemd 258+.
sed -e '/^\[options\]/a IgnorePkg = systemd systemd-libs systemd-sysvcompat systemd-resolvconf' \
    -e '/^\[overpressure-/,/^$/d' "$definition/pacman.conf" > "$T/etc/pacman.conf"

# Packages from the Frame repository and our own were built against other library
# versions than the preview base: their programs and public libraries must resolve, and so must
# the packages that depend on them, which load the Frame's libraries in place of the preview's.
# A program's every symbol must be defined in what it loads: the library check alone does not see
# a single function that a newer version of a library has (the Frame's Xwayland and libei).
step "checking ELF dependencies"
phase "checking ELF dependencies of Frame and project packages"
mapfile -t foreign < <(in_b pacman --config /etc/pico4/pacman.conf -r /target -Sl deckard-arch-hotfixes-release-0.3 \
    "${layers[@]/#/overpressure-}" |
    awk '/\[installed\]$/ {print $2}')
# Optional programs whose libraries are deliberately not installed. SteamVR's desktop
# tools need Qt5 or GLEW; its launcher puts libopenvr_api and libsteam_api on
# LD_LIBRARY_PATH for the rest. (Python regular expression.)
optional='^(systemd /usr/lib/systemd/systemd-journal-(gatewayd|remote)|libinput /usr/lib/libinput/libinput-debug-gui'
optional+='|v4l-utils /usr/bin/qv4l2|v4l-utils /usr/bin/qvidcap'
# Valve's Mesa's freedreno debugging tools.
optional+='|deckard-mesa-linux-aarch64 /usr/bin/(cffdump|crashdec|fdperf|qrisc-asm|qrisc-disasm|rddecompiler)'
optional+='|deckard-steamvr-rel /opt/steamvr/bin/linuxarm64/(vrmonitor|overlay_viewer|hellovr_sdl)):'
optional+='|^deckard-steamvr-rel /opt/steamvr/[^:]*:\s*(\./)?lib(openvr_api|steam_api)\.so '
# Bionic programs and libraries, run in Lepton's Android: SteamVR's Android client, Valve's
# Android Mesa and Vulkan layers.
optional+='|^deckard-steamvr-rel /opt/steamvr/bin/androidarm64/'
optional+='|^deckard-(mesa|vulkan-layers)-android-aarch64 '
# The preview's optional parts that depend on a Frame package: glibc's memusagestat (libgd),
# lm_sensors' sensord (librrd), OpenCV's HDF5 and VTK modules.
optional+='|^(glibc /usr/bin/memusagestat|lm_sensors /usr/bin/sensord|opencv /usr/lib/libopencv_(hdf|viz)\.so[.0-9]*):'
PYTHONPATH=$project python3 -m overpressure.elfdeps "$T" "${foreign[@]}" --dependents --ignore "$optional" >&2 ||
    { echo "unresolved ELF dependencies (above)" >&2; exit 1; }
log "${#foreign[@]} Frame/project packages and their dependents: all ELF dependencies and symbols resolve"

step "applying the PICO 4 adaptation layer"
phase "applying the PICO 4 adaptation layer"
# overlay FILE DEST: root-owned; scripts executable, everything else 0644
# (modes from a Windows checkout are meaningless, as in tooling/image.py).
overlay() {
    local mode=0644
    [[ "$(head -c 2 "$1")" == '#!' ]] && mode=0755
    install -D -o 0 -g 0 -m "$mode" "$1" "$2"
}
while IFS= read -r -d '' f; do
    overlay "$f" "$T/${f#"$project/userspace/rootfs/"}"
done < <(find "$project/userspace/rootfs" -type f -print0)
# Shared with stage 1, at the same paths: one recovery policy for both stages.
for f in lib.sh recovery-policy.sh recovery-watch; do
    overlay "$project/initramfs/usr/libexec/pico4/$f" "$T/usr/libexec/pico4/$f"
done
for f in pico4-reboot pico4-splash; do
    install -o 0 -g 0 -m 0755 "$project/prebuilt/aarch64/$f" "$T/usr/bin/$f"
done
# SteamVR reads its own remapping for pico_controller, whatever the profile names: the Frame
# controller's layout (session/frame-controller-remapping.json) joins Valve's layouts there.
python3 - "$T/opt/steamvr/resources/input/default_pico_controller_remapping.json" \
    "$project/userspace/session/frame-controller-remapping.json" <<'EOF'
import json, sys
path, ours = sys.argv[1], json.load(open(sys.argv[2]))
remapping = json.load(open(path))
remapping["layouts"] = [l for l in remapping["layouts"]
                        if l.get("from_controller_type") != ours["from_controller_type"]] + [ours]
with open(path, "w") as f:
    json.dump(remapping, f, indent=2)
EOF
firmware_json="[]"
if [[ -n "$firmware" ]]; then
    mkdir -p "$T/usr/lib/firmware"
    for f in a650_gmu.bin a650_sqe.fw a650_zap.mdt a650_zap.b00 a650_zap.b01 a650_zap.b02; do
        [[ -s "$firmware/$f" ]] || { echo "missing GPU firmware $firmware/$f" >&2; exit 1; }
        install -m 0644 "$firmware/$f" "$T/usr/lib/firmware/$f"
    done
    # A mainline kernel's msm (Valve's) loads them again after a GPU recovery: at its paths, the
    # zap shader where the device tree names it.
    mkdir -p "$T/usr/lib/firmware/qcom/sm8250/pico/phoenix"
    ln -sfn ../a650_sqe.fw "$T/usr/lib/firmware/qcom/a650_sqe.fw"
    ln -sfn ../a650_gmu.bin "$T/usr/lib/firmware/qcom/a650_gmu.bin"
    for f in a650_zap.mdt a650_zap.b00 a650_zap.b01 a650_zap.b02; do
        ln -sfn "../../../../$f" "$T/usr/lib/firmware/qcom/sm8250/pico/phoenix/$f"
    done
    # The video codec (Venus) for Steam Link's decoder (docs/session.md#steam-link).
    [[ -s "$firmware/venus.mdt" ]] || { echo "missing video codec firmware $firmware/venus.mdt" >&2; exit 1; }
    install -m 0644 "$firmware"/venus.* "$T/usr/lib/firmware/"
    firmware_json=$(cd "$T/usr/lib/firmware" && sha256sum a650_* venus.* | python3 -c 'import json,sys; print(json.dumps([dict(zip(("sha256","file"),l.split())) for l in sys.stdin]))')
fi
# Wi-Fi: the QCA6390's firmware and the headset's board data, under ath11k's names, where the
# kernel's firmware loader looks. PICO OS's driver picks bdwlanq.elf on this headset: ath11k's board.bin.
wlan_json="[]"
if [[ -n "$wlan" ]]; then
    mkdir -p "$T/usr/lib/firmware/qca6390" "$T/usr/lib/firmware/ath11k/QCA6390/hw2.0"
    for f in amss20.bin m3.bin bdwlanq.elf; do
        [[ -s "$wlan/firmware/qca6390/$f" ]] || { echo "missing $wlan/firmware/qca6390/$f (see python -m overpressure assets)" >&2; exit 1; }
        install -m 0644 "$wlan/firmware/qca6390/$f" "$T/usr/lib/firmware/qca6390/"
    done
    ln -sfn ../../../qca6390/amss20.bin "$T/usr/lib/firmware/ath11k/QCA6390/hw2.0/amss.bin"
    ln -sfn ../../../qca6390/m3.bin "$T/usr/lib/firmware/ath11k/QCA6390/hw2.0/m3.bin"
    ln -sfn ../../../qca6390/bdwlanq.elf "$T/usr/lib/firmware/ath11k/QCA6390/hw2.0/board.bin"
    # The same chip's Bluetooth firmware and NV data, under the names hci_qca asks for.
    mkdir -p "$T/usr/lib/firmware/qca"
    for f in htbtfw20.tlv htnv20.bin; do
        [[ -s "$wlan/firmware/qca/$f" ]] || { echo "missing $wlan/firmware/qca/$f (see python -m overpressure assets)" >&2; exit 1; }
        install -m 0644 "$wlan/firmware/qca/$f" "$T/usr/lib/firmware/qca/"
    done
    chown -R 0:0 "$T/usr/lib/firmware/qca6390" "$T/usr/lib/firmware/ath11k" "$T/usr/lib/firmware/qca"
    wlan_json=$(cd "$T" && find usr/lib/firmware/qca6390 usr/lib/firmware/qca -type f -print0 |
        sort -z | xargs -0 sha256sum |
        python3 -c 'import json,sys; print(json.dumps([dict(zip(("sha256","file"),l.split())) for l in sys.stdin]))')
fi
# Audio: the ADSP's image and protection-domain maps where the kernel and pd-mapper look, the tree
# hexagonrpcd serves to the audio process (pico4-audiopd.service) and the calibration: the q6
# calibration driver loads PICO OS's database's blocks from one file that pico4-audiocal builds here.
audio_json="[]"
if [[ -n "$audio" ]]; then
    for f in firmware/adsp.mdt firmware/adspr.jsn \
        firmware/adspua.jsn audiopd/acdb/adsp_avs_config.acdb calibration/MTP_base_Wsa8810_Global_cal.acdb; do
        [[ -s "$audio/$f" ]] || { echo "missing $audio/$f (see python -m overpressure assets)" >&2; exit 1; }
    done
    mkdir -p "$T/usr/lib/firmware" "$T/usr/lib/pico4-audio" "$T/usr/share/qcom/pico4-audio"
    install -m 0644 "$audio"/firmware/adsp* "$T/usr/lib/firmware/"
    install -D -m 0644 -t "$T/usr/lib/pico4-audio/acdb" "$audio"/calibration/*.acdb
    cp -r --no-preserve=ownership "$audio/audiopd/." "$T/usr/share/qcom/pico4-audio/"
    python3 "$project/userspace/rootfs/usr/bin/pico4-audiocal" export --acdb "$audio/calibration" \
        "$T/usr/lib/firmware/qcom/sm8250/pico4/audio-calibration.bin"
    chown -R 0:0 "$T/usr/lib/pico4-audio" "$T/usr/share/qcom/pico4-audio" "$T/usr/lib/firmware/qcom/sm8250/pico4"
    assets_modes "$T/usr/share/qcom/pico4-audio"
    audio_json=$(cd "$T" && find usr/lib/firmware/adsp* usr/lib/firmware/qcom/sm8250/pico4/audio-calibration.bin usr/lib/pico4-audio \
        usr/share/qcom/pico4-audio -type f -print0 |
        sort -z | xargs -0 sha256sum |
        python3 -c 'import json,sys; print(json.dumps([dict(zip(("sha256","file"),l.split())) for l in sys.stdin]))')
fi
# Sensor DSP: firmware for the kernel's PIL loader, protection-domain maps for
# pd-mapper, and the tree hexagonrpcd serves to the sensors process (-R).
sensor_json="[]"
if [[ -n "$sensor_data" ]]; then
    [[ -s "$sensor_data/firmware/slpi.mdt" && -d "$sensor_data/sensors/config" ]] ||
        { echo "incomplete sensor data in $sensor_data (see python -m overpressure assets)" >&2; exit 1; }
    [[ -s "$sensor_data/camera/imx471-mode.bin" ]] ||
        { echo "no RGB camera table in $sensor_data: extract it again (python -m overpressure assets sensor-data)" >&2; exit 1; }
    mkdir -p "$T/usr/lib/firmware"
    install -m 0644 "$sensor_data"/firmware/slpi* "$T/usr/lib/firmware/"
    # The RGB camera's register table, which the kernel's IMX471 driver loads at its first stream.
    install -D -m 0644 "$sensor_data/camera/imx471-mode.bin" "$T/usr/lib/firmware/qcom/sm8250/pico4/imx471-mode.bin"
    qcom=$T/usr/share/qcom/pico4
    mkdir -p "$qcom"
    cp -r --no-preserve=ownership "$sensor_data/sensors" "$qcom/"
    # Lens configuration and controller data from the stock firmware.
    [[ ! -d "$sensor_data/calibration" ]] || cp -r --no-preserve=ownership "$sensor_data/calibration" "$qcom/"
    # The headset's own: the sensor registry with its factory calibration and the camera, IMU and
    # lens calibration, in persist where PICO OS keeps them (docs/userspace.md#the-headsets-own-data).
    mkdir -p "$qcom/calibration"
    ln -sfn /mnt/vendor/persist/sensors/registry/registry "$qcom/sensors/registry"
    ln -sfn /mnt/vendor/persist/sensors/registry/sns_reg_version "$qcom/sensors/sns_reg_version"
    ln -sfn /mnt/vendor/persist/pvr "$qcom/calibration/pvr"
    chown -R 0:0 "$qcom"
    assets_modes "$qcom/sensors" "$qcom/calibration"
    # The controllers' render models, where SteamVR finds the Monado driver's resources ({monado}name).
    models=$T/usr/share/steamvr-monado/resources/rendermodels
    if [[ -d "$sensor_data/rendermodels" ]]; then
        mkdir -p "$models"
        cp -r --no-preserve=ownership "$sensor_data"/rendermodels/* "$models/"
        chown -R 0:0 "$models"
        assets_modes "$models"
    fi
    rendermodels=()
    [[ ! -d $models ]] || rendermodels=(usr/share/steamvr-monado/resources/rendermodels)
    sensor_json=$(cd "$T" && find usr/lib/firmware/slpi* usr/lib/firmware/qcom/sm8250/pico4/imx471-mode.bin \
        usr/share/qcom/pico4 "${rendermodels[@]}" \
        -type f -print0 | sort -z | xargs -0 sha256sum |
        python3 -c 'import json,sys; print(json.dumps([dict(zip(("sha256","file"),l.split())) for l in sys.stdin]))')
fi

# PICO OS's head tracker (docs/xr.md#head-tracking): the Compute DSP's firmware for the PIL
# loader and pd-mapper, the tracker's bionic root, and this project's pico4-slamd with the
# root's stand-ins, built for bionic with the host's clang against the root's own libraries.
stock_json="[]"
if [[ -n "$stock_slam" ]]; then
    [[ -s "$stock_slam/firmware/cdsp.mdt" &&
       -s "$stock_slam/root/system/lib64/libSlam.so" && -s "$stock_slam/root/system/lib64/libphotoid.so" ]] ||
        { echo "incomplete tracker data in $stock_slam (see python -m overpressure assets)" >&2; exit 1; }
    [[ -n $llvm ]] || { echo "--stock-slam needs the host's clang and lld (LLVM 20, in the build container)" >&2; exit 1; }
    mkdir -p "$T/usr/lib/firmware"
    install -m 0644 "$stock_slam"/firmware/cdsp* "$T/usr/lib/firmware/"
    island=$T/usr/lib/pico4-stock-slam
    rm -rf "$island"
    mkdir -p "$island"
    cp -r --no-preserve=ownership "$stock_slam/root/." "$island/"
    bash "$project/src/stockslam/build.sh" "$island" "$island/pico4" "$project/packages/phoenix/pico4-monado" "$llvm"
    mv "$island/pico4/libandroid.so" "$island/system/lib64/libandroid.so"
    # Mount points: the root is read-only on the headset.
    mkdir -p "$island"/{dev,proc,sys,data,tmp,mnt/vendor/persist/pvr,mnt/vendor/pvr}
    chown -R 0:0 "$island"
    find "$island" -type d -exec chmod 0755 {} +
    find "$island" -type f -exec chmod 0644 {} +
    chmod 0755 "$island/system/bin/bootstrap/linker64" "$island/vendor/bin/cdsprpcd" "$island/pico4/pico4-slamd"
    stock_json=$(cd "$T" && find usr/lib/firmware/cdsp* usr/lib/pico4-stock-slam -type f -print0 | sort -z |
        xargs -0 sha256sum |
        python3 -c 'import json,sys; print(json.dumps([dict(zip(("sha256","file"),l.split())) for l in sys.stdin]))')
fi

in_t systemd-sysusers
# The Frame's session user (steamos-customizations-deckard refers to it), from
# pico4-steamos.conf. sysusers creates no home: it comes from /etc/skel, as with useradd -m.
user=steamos
in_t id "$user" >/dev/null 2>&1 || in_t useradd -m -u 1000 -U -s /bin/bash "$user"
if [[ ! -d "$T/home/$user" ]]; then
    in_t cp -a /etc/skel "/home/$user"
    in_t chmod 0700 "/home/$user"   # HOME_MODE in login.defs
    in_t chown -R "$user:$user" "/home/$user"
fi
in_t usermod -a -G wheel,video,render,input,audio "$user"
# Subordinate IDs for rootless podman (Lepton's Android runs in it), as useradd gives a user
# from login.defs; sysusers created this one without them. The Frame's filesystem package has no
# subuid and subgid files, Holo's empty ones.
for f in subuid subgid; do [[ -e "$T/etc/$f" ]] || install -m 0644 /dev/null "$T/etc/$f"; done
grep -q "^$user:" "$T/etc/subuid" 2>/dev/null || in_t usermod --add-subuids 100000-165535 --add-subgids 100000-165535 "$user"
# No password logs in, here or through su: SSH takes keys, the session logs in by itself.
in_t usermod -p '*' root
in_t usermod -p '*' "$user"
echo pico4-steamos > "$T/etc/hostname"
echo LANG=C.UTF-8 > "$T/etc/locale.conf"
rm -f "$T/etc/machine-id"   # written from the headset serial during the handoff
# SteamOS keeps writable state on /home and bind-mounts it over /var/log,
# /var/tmp, /root, ... (steamos-offload.target, before tmpfiles runs). On the
# Frame the sources persist; here they are created in the image.
in_t systemd-tmpfiles --create steamos-offload.conf
# PICO OS's mount point of its persist partition (pico4-persist.service, read-only).
mkdir -p "$T/mnt/vendor/persist"
in_t systemctl enable sshd.service systemd-networkd.service NetworkManager.service \
    pico4-recovery-watch.service pico4-storage-ro.service \
    pico4-userspace-logdisk.timer pico4-userspace-netdiag.service pico4-sensors.target pico4-imu-ready.service pico4-kthread-priorities.service \
    pico4-monado.socket pico4-session.service pico4-fan.service \
    pico4-usb-internet.service systemd-timesyncd.service pico4-audio.target \
    pico4-splash.service pico4-powerkey.service pico4-battery-guard.service pico4-persist.service pico4-zram-swap.service \
    pico4-venus-probe.service steam-video-codec-setup.service pico4-bt-firmware.service pico4-bt-address.service \
    bluetooth.service pico4-wlan-probe.service pico4-wlan-address.service pico4-panel-probe.service pico4-clock.service \
    pico4-usb-gadget.service pico4-boot-ok.service
# Units that would wait for console input, erase the previous boot's evidence from pstore,
# or manage boot partitions the system never touches.
# steamos-dump-info reads the Frame's EEPROM and EFI boot configuration
# (steamos-bootconf), which do not exist here, and then fails. The Frame's
# USB gadget units would take the controller from pico4-usb-gadget's link. The kernel
# console's getty (the generator adds it for console=ttyMSM0): no one logs in on the UART.
in_t systemctl mask systemd-firstboot.service systemd-pstore.service \
    systemd-boot-update.service systemd-repart.service steamos-dump-info.service \
    usb-gadget.target usb-ncm-gadget@.service usb-ncm-dnsmasq@.service serial-getty@ttyMSM0.service
# zram-generator's swap unit has no PICO OS priority; pico4-zram-swap.service runs its setup from
# the Frame's configuration instead.
mkdir -p "$T/etc/systemd/system-generators"
ln -sfn /dev/null "$T/etc/systemd/system-generators/zram-generator"
# Valve's VR session (docs/session.md): no display manager (pico4-session.service
# starts the session), and none of the Frame-only system units: the controller
# radio programmer (a firmware OTA for the Frame's nRF, never to run here),
# the tracking DSP service, kernel-thread priorities (its script stops at its first
# entry here; pico4-kthread-priorities takes its values), v4l2loopback and the debug port forwards. The Frame's codec links
# (steam-video-codec-setup) stay: the PICO 4's vrdevice_path answers them with Venus.
in_t systemctl mask sddm.service dsp_service.service steamvr-program-ble.service \
    steamvr-set-kernel-thread-priorities.service steamvr-v4l2loopback.service \
    steamvr-web-debug-portforward.service steam-web-debug-portforward.service
# The session's user units for Frame hardware (the headset-view loopback camera; the
# proximity microphone mute, which drives the Frame's codec's mixer: pico4-micmute does it) and
# the sched_ext CPU scheduler (scx_lavd needs an option Valve's kernel configuration leaves out;
# steamos-manager offers no scheduler interface here, and stage 1 already sets schedutil).
in_t systemctl --global mask steamvr-v4l2cam.service steamvr-proxmicmute.service steamos-scheduler.service
# SteamVR's log forwarder (steamvr-logs) imports inotify_simple, which the Frame's
# package installs for Python 3.12 only. The module is plain Python: the image's
# Python shares it.
python=$(readlink "$T/usr/bin/python3")
if [[ ! -f "$T/usr/lib/python3.12/site-packages/inotify_simple.py" || ! -d "$T/usr/lib/$python/site-packages" ]]; then
    echo "inotify_simple or $python's site-packages is missing" >&2
    exit 1
fi
ln -sfn ../../python3.12/site-packages/inotify_simple.py "$T/usr/lib/$python/site-packages/inotify_simple.py"
# gamescope's session starts MangoHud's mangoapp, the Steam client's
# performance overlay (pico4-mangohud).
if ! grep -q -- '--mangoapp' "$T/usr/lib/steamos/gamescope-session" || [[ ! -x "$T/usr/bin/mangoapp" ]]; then
    echo "gamescope-session does not start mangoapp, or mangoapp is missing" >&2
    exit 1
fi
# SteamVR's headset driver is Monado's plugin (driver_monado, pico4-monado);
# the Frame's drivers stay off. steamvr_first_init.sh keeps these files.
install -d -o 1000 -g 1000 -m 0755 "$T/home/$user/.config/openvr" "$T/home/$user/.local/share/Steam/config" \
    "$T/home/$user/.local/share/Steam/logs" "$T/home/$user/.steam"
install -o 1000 -g 1000 -m 0644 "$project/userspace/session/openvrpaths.vrpath" "$T/home/$user/.config/openvr/"
install -o 1000 -g 1000 -m 0644 "$project/userspace/session/steamvr.vrsettings" "$T/home/$user/.local/share/Steam/config/"
# The image's copy: steamvr-settings puts its keys into the user's file at each SteamVR start.
install -D -m 0644 "$project/userspace/session/steamvr.vrsettings" "$T/usr/share/pico4/steamvr.vrsettings"
# The Steam client's RUNSTEAM.sh makes these links; SteamVR finds its logs and
# the Steam folder through them.
ln -sfn "/home/$user/.local/share/Steam" "$T/home/$user/.steam/steam"
ln -sfn "/home/$user/.local/share/Steam" "$T/home/$user/.steam/root"
chown -h 1000:1000 "$T/home/$user/.steam/steam" "$T/home/$user/.steam/root"
# install -d made the parent directories root's: the session user owns its home.
chown -R 1000:1000 "$T/home/$user"
# The compositor's launcher raises its priority (as SteamVR's setup does on
# desktops); the archive keeps file capabilities.
in_t setcap cap_sys_nice=eip /opt/steamvr/bin/linuxarm64/vrcompositor-launcher
# Steam Link's client takes the PICO 4's controllers for the Frame's through pico4-vrlink's
# compatibility layer (docs/session.md#steam-link), preloaded by its launcher only.
vrlink_run=$T/opt/steamvr/tools/vrlink/bin/linuxarm64/run_vrlink.sh
grep -qx '\./vrlink "\$@" &' "$vrlink_run" ||
    { echo "$vrlink_run does not start vrlink as expected; check pico4-vrlink's preload" >&2; exit 1; }
sed -i 's|^\./vrlink "\$@" &$|LD_PRELOAD=/usr/lib/libpico4-vrlink-compat.so${LD_PRELOAD:+:$LD_PRELOAD} ./vrlink "$@" \&|' "$vrlink_run"
mkdir -p "$T/etc/systemd/system-generators"
ln -sfn /dev/null "$T/etc/systemd/system-generators/systemd-gpt-auto-generator"
in_t systemctl set-default graphical.target
in_t ldconfig
# ldconfig's aux-cache holds the libraries' inode numbers and change times; it only speeds up the
# next ldconfig run.
rm -f "$T/var/cache/ldconfig/aux-cache"

# -- manifest and archive --------------------------------------------------------------
step "writing archive and manifest"
phase "writing archive and manifest"
in_t pacman -Q > "$build_root/packages.txt"
# The package files of exactly the installed versions (the caches keep older ones too): the project's,
# from its repositories, and the upstream ones, which the lock pins (pacman's cache holds copies of the
# project's too).
while read -r name version; do
    for layer in "${layers[@]}"; do
        for f in "$B/var/cache/overpressure/$layer/$name-$version"-{aarch64,any}.pkg.tar.zst; do
            [[ ! -f "$f" ]] || sha256sum "$f" | sed 's|  .*/|  ./|'
        done
    done
done < "$build_root/packages.txt" | sort -u -k2 > "$build_root/project-hashes.txt"
while read -r name version; do
    for f in "$B/var/cache/pico4-pkgs/$name-$version"-{aarch64,any}.pkg.tar.zst; do
        [[ ! -f "$f" ]] || sha256sum "$f" | sed 's|  .*/|  ./|'
    done
done < "$build_root/packages.txt" | sort -u -k2 |
    awk 'NR == FNR {project[$2] = 1; next} !($2 in project)' "$build_root/project-hashes.txt" - \
    > "$build_root/upstream-hashes.txt"
sort -u -k2 "$build_root/upstream-hashes.txt" "$build_root/project-hashes.txt" > "$build_root/package-hashes.txt"
# The project repositories' databases are the build's own (repo-add records the time it ran), and the
# headset's pacman.conf has none of them: the image keeps only the upstream databases.
rm -f "$T"/var/lib/pacman/sync/overpressure-*.db
(cd "$T/var/lib/pacman/sync" && sha256sum ./*.db) > "$build_root/db-hashes.txt"
# Every upstream package is the tested one, or the build stops: Valve's repositories publish no
# signatures, and the Frame's hotfix repository moves on (docs/userspace.md#package-lock).
python3 "$source_dir/scripts/check-package-lock.py" "$build_root/upstream-hashes.txt" "$definition/packages.lock" \
    ${update_lock:+--write "$output/packages.lock"}
install -d -m 0755 "$T/usr/share/pico4"
printf 'version=%s\ncommit=%s\n' "$version" "$commit" > "$T/usr/share/pico4/version"
unmount_below "$T" inner
rm -rf "$T/var/cache/pacman/pkg"/* "$T/var/log/pacman.log"
# pacman records when it installed each package and has no SOURCE_DATE_EPOCH.
sed -i "/^%INSTALLDATE%\$/{n;s/.*/$SOURCE_DATE_EPOCH/}" "$T"/var/lib/pacman/local/*/desc
archive=$output/pico4-userspace.tar.zst
# No time after SOURCE_DATE_EPOCH, and no access or change times.
tar --numeric-owner --xattrs --xattrs-include=security.capability --sort=name \
    --mtime="@$SOURCE_DATE_EPOCH" --clamp-mtime --pax-option=exthdr.name=%d/PaxHeaders/%f,delete=atime,delete=ctime \
    -C "$T" -cf - . |
    zstd -q -T0 -12 -o "$archive.part" -f
mv "$archive.part" "$archive"
installed_bytes=$(du -sb --apparent-size "$T" | cut -f1)
cleanup
python3 - "$archive" "$build_root" "$packages_source" "$installed_bytes" "$firmware_json" "$sensor_json" \
    "$wlan_json" "$audio_json" "$stock_json" "$version" "$commit" > "$output/pico4-userspace.manifest.json" <<'EOF'
import hashlib, json, os, sys, datetime
archive, root, packages, installed, firmware, sensors, wlan, audio, stock, version, commit = sys.argv[1:12]
def lines(name):
    with open(os.path.join(root, name)) as f:
        return [l.split() for l in f if l.strip()]
h = hashlib.sha256()
with open(archive, "rb") as f:
    for block in iter(lambda: f.read(1 << 20), b""):
        h.update(block)
print(json.dumps({
    "schema": 1,
    "version": version,
    "commit": commit,
    "source_date": datetime.datetime.fromtimestamp(int(os.environ["SOURCE_DATE_EPOCH"]),
                                                   datetime.timezone.utc).isoformat(timespec="seconds"),
    "archive": {"file": os.path.basename(archive), "sha256": h.hexdigest(),
                "bytes": os.path.getsize(archive), "installed_bytes": int(installed)},
    "project_packages": dict(zip(("repository", "tag", "commit", "from"), packages.split() + ["built here"]))
        if not packages.startswith("local ") else {"local": packages.split(" ", 1)[1]},
    "repository_databases": {n.lstrip("./"): s for s, n in lines("db-hashes.txt")},
    "packages": {n: v for n, v in lines("packages.txt")},
    "package_files": {n.lstrip("./"): s for s, n in lines("package-hashes.txt")},
    "gpu_firmware": json.loads(firmware),
    "sensor_data": json.loads(sensors),
    "wlan": json.loads(wlan),
    "audio": json.loads(audio),
    "stock_slam": json.loads(stock),
}, indent=1, sort_keys=True))
EOF
step "done"
phase "done: $archive ($(du -h "$archive" | cut -f1), installed $((installed_bytes >> 20)) MiB)"
