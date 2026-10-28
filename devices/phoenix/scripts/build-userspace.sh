#!/bin/bash
# Build the SteamOS-compatible ARM64 userspace for the PICO 4 (docs/userspace.md).
#
# Usage (Linux or WSL, as root):
#   scripts/build-userspace.sh BUILD_ROOT OUTPUT_DIR [OPTIONS]
#
# Each of these comes from the stock firmware (python -m overpressure stock: PICO's public OTA) through
# an extraction script (docs/development.md#stock-firmware). The headset's own factory data is
# not in the image: it links to persist, mounted read-only at boot (pico4-persist.service).
#   --gpu-firmware DIR  a650_* GPU firmware (scripts/extract-graphics-firmware.sh,
#                       docs/graphics.md). Without it the userspace boots, but Vulkan cannot start.
#   --sensor-data DIR   sensor DSP firmware and config, lens and controller data
#                       (scripts/extract-sensor-data.sh, docs/xr.md). Without it the head IMU
#                       stack stays inactive.
#   --wlan DIR          Wi-Fi driver module, firmware and configuration (scripts/extract-wlan.sh,
#                       docs/userspace.md). Without it Wi-Fi stays off.
#   --audio DIR         audio modules, ADSP firmware and the audio process's files
#                       (scripts/extract-audio.sh, docs/userspace.md). Without it there is no
#                       sound card.
#   --stock-slam DIR    PICO OS's head tracker, its Compute DSP firmware and bionic root
#                       (scripts/extract-stock-slam.sh, docs/xr.md). Without it head tracking
#                       falls back to 3DoF unless PICO4_SLAM_BACKEND=basalt.
#   --refresh           download the current package databases and reinstall the base
#                       root; without it every build installs the same package versions
#   --clean             rebuild the project packages from scratch
#   --emulated-cc       compile with the build environment's own GCC under QEMU
#
# Each step is skipped when its inputs did not change:
# 1. Build environment: Valve/Collabora's Holo Core aarch64 preview rootfs (sha256-pinned),
#    entered with chroot; on other hosts than arm64 its programs run under QEMU.
# 2. Project packages (userspace/pkgbuilds) with makepkg. A package is rebuilt when its
#    recipe changes; when only the project's own sources (src/) changed, it is rebuilt
#    incrementally in its last build tree (makepkg -e). On x86-64 hosts the host's clang
#    and lld compile natively inside the build environment, which is their sysroot.
# 3. Base root: userspace/packages.txt installed with pacman from the Steam Frame hotfix
#    repository and the Holo Core preview (userspace/pacman.conf). Reinstalled when the
#    list or pacman.conf changes, and with --refresh.
# 4. Image root: an overlay on the base root with the current project packages and the
#    PICO 4 adaptation layer (userspace/rootfs, shared stage-1 scripts, users, units),
#    checked natively for unresolved ELF dependencies (overpressure/elfdeps.py), written as:
#      OUTPUT_DIR/pico4-userspace.tar.zst        root filesystem for the handoff
#      OUTPUT_DIR/pico4-userspace.manifest.json  sources, hashes, package list
#
# Requires curl, tar, zstd, sha256sum, python3, chroot and overlayfs; on x86-64 an
# aarch64 binfmt handler with the F (fix-binary) flag and LLVM (apt install clang-20
# lld-20). The build reads a snapshot of the tree taken at its start, so the tree can be
# edited meanwhile. Nothing touches the headset. The output contains Valve packages:
# keep it private, do not redistribute.
set -euo pipefail
source_dir=$(cd "$(dirname "$0")/.." && pwd)   # devices/phoenix
repo=$(cd "$source_dir/../.." && pwd)            # overpressure (its package, cache/)
build_root=${1:?specify an isolated Linux-filesystem build directory}
output=${2:?specify an output directory}
shift 2
firmware="" sensor_data="" wlan="" audio="" stock_slam="" refresh=0 clean=0 emulated_cc=0
while [[ $# -gt 0 ]]; do
    case "$1" in
    --gpu-firmware) firmware=$(realpath "${2:?}"); shift 2 ;;
    --sensor-data) sensor_data=$(realpath "${2:?}"); shift 2 ;;
    --wlan) wlan=$(realpath "${2:?}"); shift 2 ;;
    --audio) audio=$(realpath "${2:?}"); shift 2 ;;
    --stock-slam) stock_slam=$(realpath "${2:?}"); shift 2 ;;
    --refresh) refresh=1; shift ;;
    --clean) clean=1; shift ;;
    --emulated-cc) emulated_cc=1; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
    esac
done
[[ $EUID == 0 && "$build_root" = /* && "$build_root" != / ]] || { echo 'Root and an absolute isolated build directory are required' >&2; exit 2; }
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

holo_snapshot=mash-20251118.3
holo_url=https://holo-packages.steamos.cloud/holo-core-aarch64-preview/$holo_snapshot/system.rootfs.zst
holo_sha256=7e3fb88454e1ac633b7488abb72d3ca0cc7d2578a38146fdd7d58b50fcbd60bf
holo_archive=$repo/cache/holo/$holo_snapshot-system.rootfs.zst
mesa_archive=$repo/cache/mesa-26.1.6.tar.xz
B=$build_root/bootstrap
T=$B/target    # the image root, an overlay on $B/base
log() { printf '>> %s\n' "$*"; }
phase() { printf '>> [%4ss] %s\n' "$SECONDS" "$*"; }

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
# mount_api DIR: the kernel file systems a chroot into DIR needs.
mount_api() {
    mnt --bind /proc "$1/proc"
    mnt --bind /sys "$1/sys"
    mnt --bind /dev "$1/dev"
    mnt -t devpts -o newinstance,ptmxmode=0666 devpts "$1/dev/pts"
    mnt -t tmpfs tmpfs "$1/run"
}

# -- 0. a snapshot of the inputs ------------------------------------------------------
project=$build_root/project
rm -rf "$project.new"
mkdir -p "$project.new/initramfs/usr/libexec/pico4-poc" "$project.new/prebuilt/aarch64"
cp -r "$repo/overpressure" "$source_dir/src" "$source_dir/userspace" "$project.new/"
for f in lib.sh recovery-policy.sh recovery-watch; do
    cp "$source_dir/initramfs/usr/libexec/pico4-poc/$f" "$project.new/initramfs/usr/libexec/pico4-poc/"
done
cp "$source_dir/prebuilt/aarch64/pico4-reboot" "$source_dir/prebuilt/aarch64/pico4-splash" \
    "$project.new/prebuilt/aarch64/"
find "$project.new" -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$project"
mv "$project.new" "$project"

# -- 1. build environment ----------------------------------------------------------------
mkdir -p "$repo/cache/holo"
if [[ ! -s "$holo_archive" ]]; then
    log "downloading $holo_url"
    curl --fail --location --output "$holo_archive.part" "$holo_url"
    mv "$holo_archive.part" "$holo_archive"
fi
printf '%s  %s\n' "$holo_sha256" "$holo_archive" | sha256sum --check --quiet
if [[ ! -f "$B/.pico4-bootstrap" ]]; then
    phase "unpacking Holo Core aarch64 $holo_snapshot"
    unmount_below "$B"
    rm -rf "$B"
    mkdir -p "$B"
    zstd -qdc "$holo_archive" | tar -x --numeric-owner -C "$B"
    touch "$B/.pico4-bootstrap"
fi
unmount_below "$B"
cp -L /etc/resolv.conf "$B/etc/resolv.conf"
mkdir -p "$B"/{proc,sys,dev,run,tmp} "$B/etc/pico4" "$B/var/cache/pico4-repo" "$B/var/cache/pico4-pkgs" "$B/build"
cp "$project/userspace/pacman.conf" "$B/etc/pico4/pacman.conf"
mount_api "$B"
in_b() { chroot "$B" /usr/bin/env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/bin LANG=C.UTF-8 HOME=/root "$@"; }

# Package databases are downloaded by the first build and with --refresh only, so that
# repeated builds install the same versions.
sync=""
[[ $refresh == 1 || -z "$(ls "$B"/var/lib/pacman/sync/*.db 2>/dev/null)" ]] && sync=y
in_b pacman "-S$sync" --noconfirm --needed base-devel git meson ninja cmake eigen openxr python-mako python-yaml \
    python-packaging glslang libdrm vulkan-icd-loader vulkan-headers spirv-tools zlib zstd expat libelf wayland \
    wayland-protocols libx11 libxcb libxshmfence libxrandr xcb-util-keysyms opencv onetbb fmt boost ccache \
    glib2-devel at-spi2-core pango cairo gdk-pixbuf2 libxcursor libxinerama libxi libxcomposite libxdamage \
    shared-mime-info gtk-update-icon-cache hicolor-icon-theme glfw dbus libxkbcommon libnl pcsclite
in_b id builder >/dev/null 2>&1 || in_b useradd -m builder
builder=$(in_b id -u builder):$(in_b id -g builder)
# Emulated compilers: ccache (in the builder's home, kept between builds) saves repeats.
sed -i -E 's/^(BUILDENV=\(.*)!ccache/\1ccache/' "$B/etc/makepkg.conf"

# Native compilers: the kernel runs the host's x86-64 clang and lld directly inside the
# build environment, where they compile for aarch64 with the environment as sysroot (its
# glibc, GCC runtime and libraries). The target comes from the program names.
llvm=""
if [[ $host_arch == x86_64 && $emulated_cc == 0 ]]; then
    for v in 21 20 19; do
        if [[ -x /usr/lib/llvm-$v/bin/clang && -x /usr/lib/llvm-$v/bin/ld.lld ]]; then llvm=/usr/lib/llvm-$v; break; fi
    done
    [[ -n $llvm ]] || log "WARNING: no host clang and lld (apt install clang-20 lld-20): compiling under QEMU, about ten times slower"
fi
compiler="GCC under QEMU"
ccenv=""
if [[ -n $llvm ]]; then
    multiarch=/usr/lib/x86_64-linux-gnu
    for d in "$llvm" "$multiarch"; do
        mkdir -p "$B$d"
        mnt --bind "$d" "$B$d"
        mount -o remount,bind,ro "$B$d"
    done
    # The x86-64 dynamic loader where x86-64 programs look for it (/lib64 is usr/lib here).
    ln -sfn "$multiarch/ld-linux-x86-64.so.2" "$B/usr/lib/ld-linux-x86-64.so.2"
    mkdir -p "$B/usr/local/bin"
    ln -sfn "$llvm/bin/clang" "$B/usr/local/bin/aarch64-unknown-linux-gnu-clang"
    ln -sfn "$llvm/bin/clang++" "$B/usr/local/bin/aarch64-unknown-linux-gnu-clang++"
    compiler=$("$llvm/bin/clang" --version | head -n 1)
    ccenv="PICO4_CC=1"
fi
# lld links; no LTO, whose bitcode objects would need LLVM's archiver in every build system.
cat > "$B/etc/makepkg.conf.d/pico4.conf" <<'EOF'
# Written by scripts/build-userspace.sh: the host's clang and lld when PICO4_CC is set.
if [[ -n ${PICO4_CC:-} ]]; then
    export CC=/usr/local/bin/aarch64-unknown-linux-gnu-clang CXX=/usr/local/bin/aarch64-unknown-linux-gnu-clang++
    LDFLAGS+=" -fuse-ld=lld"
    OPTIONS+=(!lto)
fi
EOF

# -- 2. project packages -------------------------------------------------------------------
# build_package NAME [SOURCE...]: makepkg userspace/pkgbuilds/NAME with the project's
# SOURCE files next to the PKGBUILD. Packages of the same recipe (PKGBUILD directory,
# compiler and makepkg settings) and sources are reused; new sources alone rebuild in the
# last build tree.
rm -f "$B"/var/cache/pico4-repo/*
build_package() {
    local name=$1 dir=$B/build/$1 recipe sources built pkgver pkgrel mode=clean extra=""
    shift
    pkgver=$(sed -n 's/^pkgver=//p' "$project/userspace/pkgbuilds/$name/PKGBUILD")
    pkgrel=$(sed -n 's/^pkgrel=//p' "$project/userspace/pkgbuilds/$name/PKGBUILD")
    recipe=$({ cat "$project/userspace/pkgbuilds/$name"/* "$B/etc/makepkg.conf.d/pico4.conf"; echo "$compiler"; } |
        sha256sum | cut -d' ' -f1)
    sources=$(cat /dev/null "$@" | sha256sum | cut -d' ' -f1)
    built=$(cat "$dir/.recipe" 2>/dev/null || true)
    if [[ "$built" != "$recipe $sources" ]] || ! ls "$dir"/*-"$pkgver-$pkgrel"-aarch64.pkg.tar.zst >/dev/null 2>&1; then
        if [[ $clean == 0 && "${built%% *}" == "$recipe" && -d "$dir/src" ]]; then
            mode=incremental extra=-e
        else
            rm -rf "$dir"
        fi
        phase "building $name $pkgver-$pkgrel ($mode, $compiler)"
        mkdir -p "$dir"
        rm -f "$dir/.recipe"
        cp "$project/userspace/pkgbuilds/$name"/* "$@" "$dir/"
        if [[ "$name" == pico4-turnip-kgsl && -s "$mesa_archive" && ! -s "$dir/${mesa_archive##*/}" ]]; then
            cp "$mesa_archive" "$dir/"
        fi
        chown -R "$builder" "$dir"
        in_b su builder -c "cd /build/$name && MAKEFLAGS=-j\$(nproc) $ccenv makepkg --noconfirm --nocheck -f $extra"
        echo "$recipe $sources" > "$dir/.recipe"
    fi
    cp "$dir"/*-"$pkgver-$pkgrel"-aarch64.pkg.tar.zst "$B/var/cache/pico4-repo/"
}
build_package pico4-turnip-kgsl "$project"/src/graphics/*
build_package pico4-sensors
build_package pico4-basalt
build_package pico4-vrdisplay "$project"/src/vrdisplay/*
build_package pico4-sysvsem "$project"/src/sysvsem/*
build_package pico4-vrlink "$project"/src/vrlink/*
build_package pico4-gtk2
build_package pico4-mangohud
build_package pico4-wpa-supplicant
build_package pico4-monado "$project"/src/monado/* "$project"/src/xr/*
in_b sh -c 'repo-add -q /var/cache/pico4-repo/pico4.db.tar.gz /var/cache/pico4-repo/*.pkg.tar.zst'
if [[ -n $llvm ]]; then
    umount "$B$multiarch" "$B$llvm"
fi

# -- 3. base root ------------------------------------------------------------------------
mapfile -t packages < <(sed -e 's/#.*//' -e 's/[[:space:]]*$//' -e '/^$/d' -e '/^!/d' "$project/userspace/packages.txt")
# "!name": dependencies on name count as satisfied, name is not installed.
mapfile -t assumed < <(sed -n 's/^!\([^[:space:]#]*\).*/--assume-installed=\1=0/p' "$project/userspace/packages.txt")
base=$B/base
# pacman copies the project repository's packages into its cache. A package rebuilt from
# changed sources keeps its version, so a copy there would no longer match the repository
# (a corrupt package for pacman, the wrong file for the manifest): only the repository has them.
rm -f "$B"/var/cache/pico4-pkgs/pico4-*.pkg.tar.zst
steamos_user=userspace/rootfs/usr/lib/sysusers.d/pico4-steamos.conf
base_key=$(cat "$project/userspace/packages.txt" "$project/userspace/pacman.conf" "$project/$steamos_user" |
    sha256sum | cut -d' ' -f1)
if [[ $refresh == 1 || ! -d "$base" || "$(cat "$build_root/base.key" 2>/dev/null)" != "$base_key" ]]; then
    phase "installing userspace/packages.txt into a new base root"
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
        cp "$B/var/cache/pico4-repo/pico4.db.tar.gz" "$base/var/lib/pacman/sync/pico4.db"
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
    [[ "$systemd_version" == 257.* ]] || { echo "systemd $systemd_version was selected; the PICO 4 kernel needs 257.x" >&2; exit 1; }
    # The project packages in the base root, by package file hash.
    (cd "$B/var/cache/pico4-repo" && sha256sum ./*.pkg.tar.zst) > "$build_root/base.project"
    echo "$base_key" > "$build_root/base.key"
fi

# -- 4. image root -----------------------------------------------------------------------
phase "assembling the image root"
unmount_below "$T"
rm -rf "$T" "$build_root/overlay"
mkdir -p "$T" "$build_root/overlay/upper" "$build_root/overlay/work"
mnt -t overlay overlay -o "lowerdir=$base,upperdir=$build_root/overlay/upper,workdir=$build_root/overlay/work" "$T"
mount_api "$T"
in_t() { chroot "$T" /usr/bin/env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/bin LANG=C.UTF-8 HOME=/root "$@"; }
cp "$B/var/cache/pico4-repo/pico4.db.tar.gz" "$T/var/lib/pacman/sync/pico4.db"
# This build's project packages where they differ from the base root's.
changed=()
while read -r sum file; do
    grep -qxF "$sum  $file" "$build_root/base.project" || [[ "$file" == *-debug-* ]] || changed+=("/var/cache/pico4-repo/${file#./}")
done < <(cd "$B/var/cache/pico4-repo" && sha256sum ./*.pkg.tar.zst)
if [[ ${#changed[@]} -gt 0 ]]; then
    phase "installing ${#changed[@]} changed project packages"
    in_b pacman --config /etc/pico4/pacman.conf -r /target --cachedir /var/cache/pico4-pkgs -U --noconfirm "${changed[@]}"
fi
# Same repositories on the headset, but an upgrade must never pull systemd 258+.
sed -e '/^\[options\]/a IgnorePkg = systemd systemd-libs systemd-sysvcompat systemd-resolvconf' \
    -e '/^\[pico4\]/,/^$/d' "$project/userspace/pacman.conf" > "$T/etc/pacman.conf"

# Packages from the Frame repository and our own were built against other library
# versions than the preview base: their programs and public libraries must resolve.
phase "checking ELF dependencies of Frame and project packages"
mapfile -t foreign < <(in_b pacman --config /etc/pico4/pacman.conf -r /target -Sl deckard-arch-hotfixes-release-0.3 pico4 |
    awk '/\[installed\]$/ {print $2}')
# Optional programs whose libraries are deliberately not installed. SteamVR's desktop
# tools need Qt5 or GLEW; its launcher puts libopenvr_api and libsteam_api on
# LD_LIBRARY_PATH for the rest. (Python regular expression.)
optional='^(systemd /usr/lib/systemd/systemd-journal-(gatewayd|remote)|libinput /usr/lib/libinput/libinput-debug-gui'
optional+='|v4l-utils /usr/bin/qv4l2|v4l-utils /usr/bin/qvidcap'
optional+='|deckard-steamvr-rel /opt/steamvr/bin/linuxarm64/(vrmonitor|overlay_viewer|hellovr_sdl)):'
optional+='|^deckard-steamvr-rel /opt/steamvr/[^:]*:\s*(\./)?lib(openvr_api|steam_api)\.so '
PYTHONPATH=$project python3 -m overpressure.elfdeps "$T" "${foreign[@]}" --ignore "$optional" >&2 ||
    { echo "unresolved ELF dependencies (above)" >&2; exit 1; }
log "${#foreign[@]} Frame/project packages: all ELF dependencies resolve"

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
    overlay "$project/initramfs/usr/libexec/pico4-poc/$f" "$T/usr/libexec/pico4-poc/$f"
done
for f in pico4-reboot pico4-splash; do
    install -o 0 -g 0 -m 0755 "$project/prebuilt/aarch64/$f" "$T/usr/bin/$f"
done
firmware_json="[]"
if [[ -n "$firmware" ]]; then
    mkdir -p "$T/usr/lib/firmware"
    for f in a650_gmu.bin a650_sqe.fw a650_zap.mdt a650_zap.b00 a650_zap.b01 a650_zap.b02; do
        [[ -s "$firmware/$f" ]] || { echo "missing GPU firmware $firmware/$f" >&2; exit 1; }
        install -m 0644 "$firmware/$f" "$T/usr/lib/firmware/$f"
    done
    # The video codec (Venus) for Steam Link's decoder (docs/session.md#steam-link); asset
    # folders extracted before it was added lack it.
    if [[ -s "$firmware/venus.mdt" ]]; then
        install -m 0644 "$firmware"/venus.* "$T/usr/lib/firmware/"
    else
        echo "warning: no venus.mdt in $firmware (scripts/extract-graphics-firmware.sh): no video codec" >&2
    fi
    firmware_json=$(cd "$T/usr/lib/firmware" && sha256sum a650_* $(ls venus.* 2>/dev/null) | python3 -c 'import json,sys; print(json.dumps([dict(zip(("sha256","file"),l.split())) for l in sys.stdin]))')
fi
# Wi-Fi: the QCA6390's firmware and board data and the driver's configuration, where the
# kernel's firmware loader looks, and the stock signed driver module, loaded by path
# (pico4-wlan.service). The module is installed byte for byte: its signature is appended. The
# MAC file links to the headset's own in persist, as PICO OS links it.
wlan_json="[]"
if [[ -n "$wlan" ]]; then
    for f in modules/qca_cld3_qca6390.ko firmware/qca6390/amss20.bin firmware/qca6390/m3.bin \
        firmware/wlan/qca_cld/qca6390/WCNSS_qcom_cfg.ini; do
        [[ -s "$wlan/$f" ]] || { echo "missing $wlan/$f (see scripts/extract-wlan.sh)" >&2; exit 1; }
    done
    mkdir -p "$T/usr/lib/firmware" "$T/usr/lib/pico4-wlan"
    cp -r --no-preserve=ownership "$wlan/firmware/." "$T/usr/lib/firmware/"
    install -m 0644 "$wlan/modules/qca_cld3_qca6390.ko" "$T/usr/lib/pico4-wlan/"
    ln -sfn /mnt/vendor/persist/wlan_mac.bin "$T/usr/lib/firmware/wlan/qca_cld/qca6390/wlan_mac.bin"
    chown -R 0:0 "$T/usr/lib/firmware/qca6390" "$T/usr/lib/firmware/wlan" "$T/usr/lib/pico4-wlan"
    wlan_json=$(cd "$T" && find usr/lib/firmware/qca6390 usr/lib/firmware/wlan usr/lib/pico4-wlan -type f -print0 |
        sort -z | xargs -0 sha256sum |
        python3 -c 'import json,sys; print(json.dumps([dict(zip(("sha256","file"),l.split())) for l in sys.stdin]))')
fi
# Audio: the ADSP's image and protection-domain maps where the PIL loader and pd-mapper look,
# the stock signed modules with their modules.dep, loaded by path (pico4-audio-modules.service),
# the tree hexagonrpcd serves to the audio process (pico4-audiopd.service) and the calibration
# database pico4-audiocal.service loads. The modules are installed byte for byte: their
# signatures are appended.
audio_json="[]"
if [[ -n "$audio" ]]; then
    for f in modules/audio_machine_kona.ko modules/modules.dep firmware/adsp.mdt firmware/adspr.jsn \
        firmware/adspua.jsn audiopd/acdb/adsp_avs_config.acdb calibration/MTP_base_Wsa8810_Global_cal.acdb; do
        [[ -s "$audio/$f" ]] || { echo "missing $audio/$f (see scripts/extract-audio.sh)" >&2; exit 1; }
    done
    mkdir -p "$T/usr/lib/firmware" "$T/usr/lib/pico4-audio" "$T/usr/share/qcom/pico4-audio"
    install -m 0644 "$audio"/firmware/adsp* "$T/usr/lib/firmware/"
    install -m 0644 "$audio"/modules/* "$T/usr/lib/pico4-audio/"
    install -D -m 0644 -t "$T/usr/lib/pico4-audio/acdb" "$audio"/calibration/*.acdb
    cp -r --no-preserve=ownership "$audio/audiopd/." "$T/usr/share/qcom/pico4-audio/"
    chown -R 0:0 "$T/usr/lib/pico4-audio" "$T/usr/share/qcom/pico4-audio"
    audio_json=$(cd "$T" && find usr/lib/firmware/adsp* usr/lib/pico4-audio usr/share/qcom/pico4-audio -type f -print0 |
        sort -z | xargs -0 sha256sum |
        python3 -c 'import json,sys; print(json.dumps([dict(zip(("sha256","file"),l.split())) for l in sys.stdin]))')
fi
# Sensor DSP: firmware for the kernel's PIL loader, protection-domain maps for
# pd-mapper, and the tree hexagonrpcd serves to the sensors process (-R).
sensor_json="[]"
if [[ -n "$sensor_data" ]]; then
    [[ -s "$sensor_data/firmware/slpi.mdt" && -d "$sensor_data/sensors/config" ]] ||
        { echo "incomplete sensor data in $sensor_data (see scripts/extract-sensor-data.sh)" >&2; exit 1; }
    mkdir -p "$T/usr/lib/firmware"
    install -m 0644 "$sensor_data"/firmware/slpi* "$T/usr/lib/firmware/"
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
    # The controllers' render models, where SteamVR finds the Monado driver's resources ({monado}name).
    models=$T/usr/share/steamvr-monado/resources/rendermodels
    if [[ -d "$sensor_data/rendermodels" ]]; then
        mkdir -p "$models"
        cp -r --no-preserve=ownership "$sensor_data"/rendermodels/* "$models/"
        chown -R 0:0 "$models"
    fi
    ln -sfn /sys/devices/soc0 "$qcom/socinfo"
    sensor_json=$(cd "$T" && find usr/lib/firmware/slpi* usr/share/qcom/pico4 \
        $( [[ -d usr/share/steamvr-monado/resources/rendermodels ]] && echo usr/share/steamvr-monado/resources/rendermodels ) \
        -type f -print0 | sort -z | xargs -0 sha256sum |
        python3 -c 'import json,sys; print(json.dumps([dict(zip(("sha256","file"),l.split())) for l in sys.stdin]))')
fi

# PICO OS's head tracker (docs/xr.md#head-tracking): the Compute DSP's firmware for the PIL
# loader and pd-mapper, the tracker's bionic root, and this project's pico4-slamd with the
# root's stand-ins, built for bionic with the host's clang against the root's own libraries.
stock_json="[]"
if [[ -n "$stock_slam" ]]; then
    [[ -s "$stock_slam/firmware/cdsp.mdt" && -s "$stock_slam/firmware/cvpss.mdt" &&
       -s "$stock_slam/root/system/lib64/libSlam.so" && -s "$stock_slam/root/system/lib64/libphotoid.so" ]] ||
        { echo "incomplete tracker data in $stock_slam (see scripts/extract-stock-slam.sh)" >&2; exit 1; }
    [[ -n $llvm ]] || { echo "--stock-slam needs the host's clang and lld (apt install clang-20 lld-20)" >&2; exit 1; }
    mkdir -p "$T/usr/lib/firmware"
    install -m 0644 "$stock_slam"/firmware/cdsp* "$T/usr/lib/firmware/"
    island=$T/usr/lib/pico4-stock-slam
    rm -rf "$island"
    mkdir -p "$island"
    cp -r --no-preserve=ownership "$stock_slam/root/." "$island/"
    # The CVP's image, which the kernel loads when the see-through's depth mesh opens /dev/cvp:
    # it reads cvpss.mdt in the opening process's root, the tracker's, but its workers load the
    # segments (cvpss.bNN) from the system's root, so both hold it.
    mkdir -p "$island/lib/firmware"
    install -m 0644 "$stock_slam"/firmware/cvpss* "$island/lib/firmware/"
    install -m 0644 "$stock_slam"/firmware/cvpss* "$T/usr/lib/firmware/"
    for f in "$island"/lib/firmware/cvpss*; do
        cmp -s "$f" "$T/usr/lib/firmware/${f##*/}" ||
            { echo "the CVP's ${f##*/} differs between the tracker's root and /usr/lib/firmware" >&2; exit 1; }
    done
    bash "$project/src/stockslam/build.sh" "$island" "$island/pico4" "$llvm"
    mv "$island/pico4/libandroid.so" "$island/system/lib64/libandroid.so"
    # Mount points: the root is read-only on the headset.
    mkdir -p "$island"/{dev,proc,sys,data,tmp,mnt/vendor/persist/pvr,mnt/vendor/pvr}
    chown -R 0:0 "$island"
    find "$island" -type d -exec chmod 0755 {} +
    find "$island" -type f -exec chmod 0644 {} +
    chmod 0755 "$island/system/bin/bootstrap/linker64" "$island/vendor/bin/cdsprpcd" "$island/pico4/pico4-slamd"
    stock_json=$(cd "$T" && find usr/lib/firmware/cdsp* usr/lib/firmware/cvpss* usr/lib/pico4-stock-slam -type f -print0 | sort -z |
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
in_t usermod -a -G wheel,video,render,input,audio,aid_inet,aid_net_raw,aid_bt,aid_bt_net "$user"
in_t passwd -d root >/dev/null
in_t passwd -d "$user" >/dev/null
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
    pico4-recovery-watch.service pico4-storage-ro.service pico4-userspace-selftest.service \
    pico4-userspace-logdisk.timer pico4-userspace-netdiag.service pico4-sensors.target pico4-imu-ready.service pico4-kthread-priorities.service pico4-console-quiet.service \
    pico4-monado.socket pico4-session.service pico4-fan.service \
    pico4-usb-internet.service systemd-timesyncd.service pico4-wlan.service pico4-audio.target \
    pico4-splash.service pico4-powerkey.service pico4-persist.service pico4-zram-swap.service \
    pico4-venus.service steam-video-codec-setup.service
# Units that would wait for console input, erase stage-1 evidence from pstore,
# need cgroup memory control, or manage boot partitions the POC never touches.
# steamos-dump-info reads the Frame's EEPROM and EFI boot configuration
# (steamos-bootconf), which do not exist here, and then fails. The Frame's
# USB gadget units would take the controller from the POC's NCM link. The kernel
# console's getty (the generator adds it for console=ttyMSM0) waits 90 s for a
# device unit that never appears and holds the graphical target back that long.
in_t systemctl mask systemd-firstboot.service systemd-pstore.service systemd-oomd.service \
    systemd-oomd.socket systemd-boot-update.service systemd-repart.service steamos-dump-info.service \
    usb-gadget.target usb-ncm-gadget@.service usb-ncm-dnsmasq@.service serial-getty@ttyMSM0.service
# zram-generator's swap unit waits for the same kind of device unit; pico4-zram-swap.service
# runs its setup from the Frame's configuration instead, and only on the installed system.
mkdir -p "$T/etc/systemd/system-generators"
ln -sfn /dev/null "$T/etc/systemd/system-generators/zram-generator"
# Valve's VR session (docs/session.md): no display manager (pico4-session.service
# starts the session), and none of the Frame-only system units: the controller
# radio programmer (a firmware OTA for the Frame's nRF, never to run here),
# the tracking DSP service, kernel-thread priorities for the Frame's kernel,
# v4l2loopback and the debug port forwards. The Frame's codec links
# (steam-video-codec-setup) stay: the PICO 4's vrdevice_path answers them with Venus.
in_t systemctl mask sddm.service dsp_service.service steamvr-program-ble.service \
    steamvr-set-kernel-thread-priorities.service steamvr-v4l2loopback.service \
    steamvr-web-debug-portforward.service steam-web-debug-portforward.service
# The session's user units for Frame hardware (the headset-view loopback camera; the
# proximity microphone mute, which cannot load this kernel's mixer: pico4-micmute does it) and
# the sched_ext CPU scheduler (scx_lavd needs Linux 6.12; steamos-manager offers no scheduler
# interface here, and stage 1 already sets schedutil).
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
install -D -m 0644 "$project/userspace/session/steamvr.vrsettings" "$T/usr/share/pico4-poc/steamvr.vrsettings"
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
# Steam Link's client drives the PICO 4's video decoder through pico4-vrlink's compatibility
# layer (docs/session.md#steam-link), preloaded by its launcher only.
vrlink_run=$T/opt/steamvr/tools/vrlink/bin/linuxarm64/run_vrlink.sh
grep -qx '\./vrlink "\$@" &' "$vrlink_run" ||
    { echo "$vrlink_run does not start vrlink as expected; check pico4-vrlink's preload" >&2; exit 1; }
sed -i 's|^\./vrlink "\$@" &$|LD_PRELOAD=/usr/lib/libpico4-vrlink-compat.so${LD_PRELOAD:+:$LD_PRELOAD} ./vrlink "$@" \&|' "$vrlink_run"
# Steam Linux Runtime's containers (pressure-vessel) need bubblewrap, which without user
# namespaces (not in the stock kernel) only works setuid; pressure-vessel takes a setuid
# /usr/bin/bwrap when its own fails (docs/session.md#the-steam-client).
[[ -x "$T/usr/bin/bwrap" ]] || { echo "bubblewrap is missing from the image" >&2; exit 1; }
chmod 4755 "$T/usr/bin/bwrap"
mkdir -p "$T/etc/systemd/system-generators"
ln -sfn /dev/null "$T/etc/systemd/system-generators/systemd-gpt-auto-generator"
in_t systemctl set-default graphical.target
in_t ldconfig

# -- manifest and archive --------------------------------------------------------------
phase "writing archive and manifest"
in_t pacman -Q > "$build_root/packages.txt"
# The package files of exactly the installed versions (the caches keep older ones too).
while read -r name version; do
    for f in "$B/var/cache/pico4-pkgs/$name-$version"-{aarch64,any}.pkg.tar.zst \
             "$B/var/cache/pico4-repo/$name-$version"-{aarch64,any}.pkg.tar.zst; do
        [[ ! -f "$f" ]] || sha256sum "$f" | sed 's|  .*/|  ./|'
    done
done < "$build_root/packages.txt" | sort -u -k2 > "$build_root/package-hashes.txt"
(cd "$T/var/lib/pacman/sync" && sha256sum ./*.db) > "$build_root/db-hashes.txt"
unmount_below "$T" inner
rm -rf "$T/var/cache/pacman/pkg"/* "$T/var/log/pacman.log"
archive=$output/pico4-userspace.tar.zst
tar --numeric-owner --xattrs --xattrs-include=security.capability --sort=name -C "$T" -cf - . |
    zstd -q -T0 -12 -o "$archive.part" -f
mv "$archive.part" "$archive"
installed_bytes=$(du -sb --apparent-size "$T" | cut -f1)
cleanup
python3 - "$archive" "$build_root" "$holo_url" "$holo_sha256" "$installed_bytes" "$firmware_json" "$sensor_json" \
    "$wlan_json" "$audio_json" "$stock_json" > "$output/pico4-userspace.manifest.json" <<'EOF'
import hashlib, json, os, sys, datetime
archive, root, holo_url, holo_sha, installed, firmware, sensors, wlan, audio, stock = sys.argv[1:11]
def lines(name):
    with open(os.path.join(root, name)) as f:
        return [l.split() for l in f if l.strip()]
h = hashlib.sha256()
with open(archive, "rb") as f:
    for block in iter(lambda: f.read(1 << 20), b""):
        h.update(block)
print(json.dumps({
    "schema": 1,
    "built": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "archive": {"file": os.path.basename(archive), "sha256": h.hexdigest(),
                "bytes": os.path.getsize(archive), "installed_bytes": int(installed)},
    "bootstrap": {"url": holo_url, "sha256": holo_sha},
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
phase "done: $archive ($(du -h "$archive" | cut -f1), installed $((installed_bytes >> 20)) MiB)"
