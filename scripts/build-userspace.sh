#!/bin/bash
# Build the SteamOS-compatible ARM64 userspace for the PICO 4 (roadmap step 4).
#
# Usage (Linux/WSL, root):
#   scripts/build-userspace.sh BUILD_ROOT OUTPUT_DIR [--gpu-firmware DIR] [--sensor-data DIR]
#
# 1. Unpacks Valve/Collabora's Holo Core aarch64 preview rootfs (sha256-pinned)
#    as an isolated build environment, run through QEMU user emulation.
# 2. Builds the project packages (userspace/pkg: pico4-turnip-kgsl,
#    pico4-graphics-tests, pico4-sensors, pico4-monado) there.
# 3. Installs userspace/packages.txt into a fresh root with pacman, from the
#    Steam Frame hotfix repository and the Holo Core preview (userspace/pacman.conf).
# 4. Adds the PICO adaptation layer (userspace/rootfs and shared stage-1
#    scripts), configures users and units, and writes:
#      OUTPUT_DIR/pico4-userspace.tar.zst        root filesystem for the handoff
#      OUTPUT_DIR/pico4-userspace.manifest.json  sources, hashes, package list
#
# --gpu-firmware DIR adds a650_* GPU firmware from YOUR OWN stock backup (see
# GRAPHICS.md). Without it the userspace boots, but Vulkan cannot start.
# --sensor-data DIR adds the sensor DSP firmware, sensor registry and config
# extracted from YOUR OWN backup by scripts/extract-sensor-data.sh (XR.md).
# Without it the head IMU stack stays inactive.
# Requires host curl, tar, zstd, sha256sum, python3, chroot and an aarch64
# binfmt handler with the F (fix-binary) flag. Nothing touches the headset.
# The output contains Valve packages: keep it private, do not redistribute.
set -euo pipefail
project=${PICO4_PROJECT:-$(cd "$(dirname "$0")/.." && pwd)}
build_root=${1:?specify an isolated Linux-filesystem build directory}
output=${2:?specify an output directory}
shift 2
firmware=""
sensor_data=""
while [[ $# -gt 0 ]]; do
    case "$1" in
    --gpu-firmware) firmware=$(realpath "${2:?}"); shift 2 ;;
    --sensor-data) sensor_data=$(realpath "${2:?}"); shift 2 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
    esac
done
[[ $EUID == 0 && "$build_root" = /* && "$build_root" != / ]] || { echo 'Root and an absolute isolated build directory are required' >&2; exit 2; }
grep -qs '^flags: .*F' /proc/sys/fs/binfmt_misc/qemu-aarch64 || { echo 'An aarch64 binfmt handler with the F flag is required' >&2; exit 2; }
mkdir -p "$build_root" "$output"
build_root=$(realpath "$build_root")
output=$(realpath "$output")
[[ "$build_root" != / ]] || { echo 'The build root must not resolve to /' >&2; exit 2; }

holo_snapshot=mash-20251118.3
holo_url=https://holo-packages.steamos.cloud/holo-core-aarch64-preview/$holo_snapshot/system.rootfs.zst
holo_sha256=7e3fb88454e1ac633b7488abb72d3ca0cc7d2578a38146fdd7d58b50fcbd60bf
holo_archive=$project/cache/holo/$holo_snapshot-system.rootfs.zst
mesa_archive=$project/cache/mesa-26.1.6.tar.xz
B=$build_root/bootstrap
T=$B/target
log() { printf '>> %s\n' "$*"; }

mounted=()
mnt() { mount "$@"; mounted+=("${!#}"); }
cleanup() { for ((i=${#mounted[@]}-1;i>=0;i--)); do umount -l "${mounted[$i]}" 2>/dev/null || true; done; mounted=(); }
trap cleanup EXIT
# unmount_below DIR: detach every mount at or below DIR (left over from an
# interrupted build) so that removing DIR can never reach the host's /dev or /sys.
unmount_below() {
    local p
    while read -r p; do umount -l "$p"; done < <(awk -v d="$1" '$2 == d || index($2, d "/") == 1 {print $2}' /proc/mounts | sort -r)
    ! awk -v d="$1" '$2 == d || index($2, d "/") == 1 {found=1} END {exit !found}' /proc/mounts || { echo "mounts remain below $1" >&2; exit 1; }
}

# -- 1. build environment ------------------------------------------------------
mkdir -p "$project/cache/holo"
if [[ ! -s "$holo_archive" ]]; then
    log "downloading $holo_url"
    curl --fail --location --output "$holo_archive.part" "$holo_url"
    mv "$holo_archive.part" "$holo_archive"
fi
printf '%s  %s\n' "$holo_sha256" "$holo_archive" | sha256sum --check --quiet
if [[ ! -f "$B/.pico4-bootstrap" ]]; then
    log "unpacking Holo Core aarch64 $holo_snapshot"
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
mnt --bind /proc "$B/proc"
mnt --bind /sys "$B/sys"
mnt --bind /dev "$B/dev"
mnt -t devpts -o newinstance,ptmxmode=0666 devpts "$B/dev/pts"
mnt -t tmpfs tmpfs "$B/run"
in_b() { chroot "$B" /usr/bin/env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/bin LANG=C.UTF-8 HOME=/root "$@"; }

# The build environment keeps its own preview-only configuration.
in_b pacman -Sy --noconfirm --needed base-devel git meson ninja cmake eigen openxr python-mako python-yaml python-packaging \
    glslang libdrm vulkan-icd-loader vulkan-headers spirv-tools zlib zstd expat libelf wayland \
    wayland-protocols libx11 libxcb libxshmfence libxrandr xcb-util-keysyms opencv onetbb fmt boost ccache
in_b id builder >/dev/null 2>&1 || in_b useradd -m builder
# Every change to a recipe rebuilds the package from scratch under emulation;
# ccache (in the builder's home, kept between builds) makes that cheap.
sed -i -E 's/^(BUILDENV=\(.*)!ccache/\1ccache/' "$B/etc/makepkg.conf"

# -- 2. project packages ---------------------------------------------------------
# build_package NAME [EXTRA_FILE...]: makepkg userspace/pkg/NAME unless the
# packages of the same recipe (PKGBUILD, patches and extra files) exist.
rm -f "$B"/var/cache/pico4-repo/*
build_package() {
    local name=$1 dir=$B/build/$1 recipe pkgver pkgrel
    shift
    pkgver=$(sed -n 's/^pkgver=//p' "$project/userspace/pkg/$name/PKGBUILD")
    pkgrel=$(sed -n 's/^pkgrel=//p' "$project/userspace/pkg/$name/PKGBUILD")
    recipe=$(cat "$project/userspace/pkg/$name"/* "$@" | sha256sum | cut -d' ' -f1)
    if [[ "$(cat "$dir/.recipe" 2>/dev/null)" != "$recipe" ]] || ! ls "$dir"/*-"$pkgver-$pkgrel"-aarch64.pkg.tar.zst >/dev/null 2>&1; then
        log "building $name $pkgver-$pkgrel (under QEMU, this can take a while)"
        rm -rf "$dir"
        mkdir -p "$dir"
        cp "$project/userspace/pkg/$name"/* "$@" "$dir/"
        [[ "$name" != pico4-turnip-kgsl || ! -s "$mesa_archive" ]] || cp "$mesa_archive" "$dir/"
        in_b chown -R builder: "/build/$name"
        in_b su builder -c "cd /build/$name && MAKEFLAGS=-j\$(nproc) makepkg --noconfirm --nocheck -f"
        echo "$recipe" > "$dir/.recipe"
    fi
    cp "$dir"/*-"$pkgver-$pkgrel"-aarch64.pkg.tar.zst "$B/var/cache/pico4-repo/"
}
build_package pico4-turnip-kgsl "$project"/src/graphics/*
build_package pico4-sensors
build_package pico4-basalt
build_package pico4-vrdisplay "$project"/src/vrdisplay/*
build_package pico4-sysvsem "$project"/src/sysvsem/*
build_package pico4-monado "$project/src/xr/pico4-openxr-pose.c" "$project/src/xr/pico4-openxr-display.c"     "$project/src/xr/pico4-cam.c"
in_b sh -c 'repo-add -q /var/cache/pico4-repo/pico4.db.tar.gz /var/cache/pico4-repo/*.pkg.tar.zst'

# -- 3. the userspace root ------------------------------------------------------
log "installing userspace/packages.txt into a fresh root"
unmount_below "$T"
rm -rf "$T"
mkdir -p "$T"/{dev,proc,sys,run,tmp,etc,var/lib/pacman,var/cache/pacman/pkg}
chmod 1777 "$T/tmp"
mnt --bind /proc "$T/proc"
mnt --bind /sys "$T/sys"
mnt --bind /dev "$T/dev"
mnt -t devpts -o newinstance,ptmxmode=0666 devpts "$T/dev/pts"
mnt -t tmpfs tmpfs "$T/run"
mapfile -t packages < <(sed -e 's/#.*//' -e 's/[[:space:]]*$//' -e '/^$/d' -e '/^!/d' "$project/userspace/packages.txt")
# "!name": dependencies on name count as satisfied, name is not installed.
mapfile -t assumed < <(sed -n 's/^!\([^[:space:]#]*\).*/--assume-installed=\1=0/p' "$project/userspace/packages.txt")
in_b pacman --config /etc/pico4/pacman.conf -r /target --cachedir /var/cache/pico4-pkgs \
    -Sy --noconfirm --needed "${assumed[@]}" "${packages[@]}"
in_t() { chroot "$T" /usr/bin/env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/bin LANG=C.UTF-8 HOME=/root "$@"; }
systemd_version=$(in_t pacman -Q systemd | cut -d' ' -f2)
[[ "$systemd_version" == 257.* ]] || { echo "systemd $systemd_version was selected; the PICO 4 kernel needs 257.x" >&2; exit 1; }
# Same repositories on the headset, but an upgrade must never pull systemd 258+.
sed -e '/^\[options\]/a IgnorePkg = systemd systemd-libs systemd-sysvcompat systemd-resolvconf' \
    -e '/^\[pico4\]/,/^$/d' "$project/userspace/pacman.conf" > "$T/etc/pacman.conf"

# Packages from the Frame repository and our own were built against other
# library versions than the preview base: their programs and public libraries
# must resolve. (ldd follows dependencies transitively; libraries in private
# directories such as /usr/lib/systemd are reached through their programs.)
log "checking shared libraries of Frame and project packages"
mapfile -t foreign < <(in_b pacman --config /etc/pico4/pacman.conf -r /target -Sl deckard-arch-hotfixes-release-0.3 pico4 |
    awk '/\[installed\]$/ {print $2}')
# Optional programs whose libraries are deliberately not installed. SteamVR's
# desktop tools need Qt5 or GLEW; its launcher puts libopenvr_api and
# libsteam_api on LD_LIBRARY_PATH for the rest.
optional='^(systemd /usr/lib/systemd/systemd-journal-(gatewayd|remote)|libinput /usr/lib/libinput/libinput-debug-gui|v4l-utils /usr/bin/qv4l2|v4l-utils /usr/bin/qvidcap'
optional+='|deckard-steamvr-rel /opt/steamvr/bin/linuxarm64/(vrmonitor|overlay_viewer|hellovr_sdl)):'
optional+='|^deckard-steamvr-rel /opt/steamvr/[^:]*:[[:space:]]*(\./)?lib(openvr_api|steam_api)\.so '
unresolved=$(for p in "${foreign[@]}"; do
    in_t pacman -Qlq "$p" | while read -r f; do
        [[ -f "$T$f" && ! -L "$T$f" && "$(od -An -tx1 -N4 "$T$f")" == " 7f 45 4c 46" ]] || continue
        # Programs, public libraries, our private driver directory and SteamVR drivers.
        [[ ( -x "$T$f" && "$f" != *.so* ) || "$f" =~ ^/usr/lib/[^/]+\.so || "$f" == /usr/lib/pico4/* ||
           "$f" == /usr/share/steamvr-monado/*.so ]] || continue
        in_t ldd "$f" 2>/dev/null | grep -F 'not found' | sed "s|^|$p $f:|"
    done
done | grep -Ev "$optional" || true)
[[ -z "$unresolved" ]] || { echo "unresolved libraries:" >&2; echo "$unresolved" >&2; exit 1; }
log "${#foreign[@]} Frame/project packages: all ELF dependencies resolve"

# -- 4. PICO adaptation layer -----------------------------------------------------
log "applying the PICO 4 adaptation layer"
# overlay FILE DEST: root-owned; scripts executable, everything else 0644
# (modes from a Windows checkout are meaningless, as in build.py).
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
install -o 0 -g 0 -m 0755 "$project/prebuilt/aarch64/pico4-reboot" "$T/usr/bin/pico4-reboot"
firmware_json="[]"
if [[ -n "$firmware" ]]; then
    mkdir -p "$T/usr/lib/firmware"
    for f in a650_gmu.bin a650_sqe.fw a650_zap.mdt a650_zap.b00 a650_zap.b01 a650_zap.b02; do
        [[ -s "$firmware/$f" ]] || { echo "missing GPU firmware $firmware/$f" >&2; exit 1; }
        install -m 0644 "$firmware/$f" "$T/usr/lib/firmware/$f"
    done
    firmware_json=$(cd "$T/usr/lib/firmware" && sha256sum a650_* | python3 -c 'import json,sys; print(json.dumps([dict(zip(("sha256","file"),l.split())) for l in sys.stdin]))')
fi
# Sensor DSP: firmware for the kernel's PIL loader, protection-domain maps for
# pd-mapper, and the tree hexagonrpcd serves to the sensors process (-R).
sensor_json="[]"
if [[ -n "$sensor_data" ]]; then
    [[ -s "$sensor_data/firmware/slpi.mdt" && -d "$sensor_data/sensors/config" && -d "$sensor_data/sensors/registry" ]] ||
        { echo "incomplete sensor data in $sensor_data (see scripts/extract-sensor-data.sh)" >&2; exit 1; }
    mkdir -p "$T/usr/lib/firmware"
    install -m 0644 "$sensor_data"/firmware/slpi* "$T/usr/lib/firmware/"
    qcom=$T/usr/share/qcom/pico4
    mkdir -p "$qcom"
    cp -r --no-preserve=ownership "$sensor_data/sensors" "$qcom/"
    # Factory camera/IMU/lens calibration for Monado's PICO 4 driver.
    [[ ! -d "$sensor_data/calibration" ]] || cp -r --no-preserve=ownership "$sensor_data/calibration" "$qcom/"
    chown -R 0:0 "$qcom"
    ln -sfn /sys/devices/soc0 "$qcom/socinfo"
    sensor_json=$(cd "$T" && find usr/lib/firmware/slpi* usr/share/qcom/pico4 -type f -print0 | sort -z | xargs -0 sha256sum |
        python3 -c 'import json,sys; print(json.dumps([dict(zip(("sha256","file"),l.split())) for l in sys.stdin]))')
fi

in_t systemd-sysusers
# The Frame's session user (steamos-customizations-deckard refers to it).
user=steamos
in_t id "$user" >/dev/null 2>&1 || in_t useradd -m -u 1000 -U -s /bin/bash "$user"
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
in_t systemctl enable sshd.service systemd-networkd.service NetworkManager.service \
    pico4-recovery-watch.service pico4-storage-ro.service pico4-userspace-selftest.service \
    pico4-userspace-logdisk.timer pico4-userspace-netdiag.service pico4-sensors.target \
    pico4-monado.socket pico4-session.service
# Units that would wait for console input, erase stage-1 evidence from pstore,
# need cgroup memory control, or manage boot partitions the POC never touches.
# steamos-dump-info reads the Frame's EEPROM and EFI boot configuration
# (steamos-bootconf), which do not exist here, and then fails. The Frame's
# USB gadget units would take the controller from the POC's NCM link.
in_t systemctl mask systemd-firstboot.service systemd-pstore.service systemd-oomd.service \
    systemd-oomd.socket systemd-boot-update.service systemd-repart.service steamos-dump-info.service \
    usb-gadget.target usb-ncm-gadget@.service usb-ncm-dnsmasq@.service
# Valve's VR session (SESSION.md): no display manager (pico4-session.service
# starts the session), and none of the Frame-only system units: the controller
# radio programmer (a firmware OTA for the Frame's nRF, never to run here),
# the tracking DSP service, kernel-thread priorities for the Frame's kernel,
# v4l2loopback, the Frame's video codec nodes and the debug port forwards.
in_t systemctl mask sddm.service dsp_service.service steamvr-program-ble.service \
    steamvr-set-kernel-thread-priorities.service steamvr-v4l2loopback.service \
    steamvr-web-debug-portforward.service steam-video-codec-setup.service \
    steam-web-debug-portforward.service
# The session's user units for Frame hardware (the headset-view loopback camera,
# proximity microphone mute), input methods (ibus is not installed), the
# sched_ext CPU scheduler (scx_lavd needs Linux 6.12; steamos-manager offers
# no scheduler interface here, and stage 1 already sets schedutil) and
# SteamVR's log forwarder (its inotify module is built for the Frame's Python;
# SteamVR's logs stay in ~/.local/share/Steam/logs).
in_t systemctl --global mask steamvr-v4l2cam.service steamvr-proxmicmute.service ibus-gamescope.service \
    steamos-scheduler.service steamvr-logs.service
# gamescope's session starts MangoHud's mangoapp, the Steam client's
# performance overlay. The Frame's MangoHud needs libraries of the Frame's
# private base (spdlog 1.13, fmt 10, libtracefs), and without the Steam client
# the overlay has nothing to show: the session runs without it instead of
# restarting the missing program in a loop.
sed -i '/^[[:space:]]*--mangoapp$/d' "$T/usr/lib/steamos/gamescope-session"
if grep -q -- '--mangoapp' "$T/usr/lib/steamos/gamescope-session"; then
    echo "gamescope-session still starts mangoapp" >&2
    exit 1
fi
# SteamVR's headset driver is Monado's plugin (driver_monado, pico4-monado);
# the Frame's drivers stay off. steamvr_first_init.sh keeps these files.
install -d -o 1000 -g 1000 -m 0755 "$T/home/$user/.config/openvr" "$T/home/$user/.local/share/Steam/config" \
    "$T/home/$user/.local/share/Steam/logs" "$T/home/$user/.steam"
install -o 1000 -g 1000 -m 0644 "$project/userspace/session/openvrpaths.vrpath" "$T/home/$user/.config/openvr/"
install -o 1000 -g 1000 -m 0644 "$project/userspace/session/steamvr.vrsettings" "$T/home/$user/.local/share/Steam/config/"
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
mkdir -p "$T/etc/systemd/system-generators"
ln -sfn /dev/null "$T/etc/systemd/system-generators/systemd-gpt-auto-generator"
in_t systemctl set-default graphical.target
in_t ldconfig

# -- manifest and archive -----------------------------------------------------------
log "writing archive and manifest"
in_t pacman -Q > "$build_root/packages.txt"
(cd "$B/var/cache/pico4-pkgs" && sha256sum ./*.pkg.tar.zst) > "$build_root/package-hashes.txt"
(cd "$B/var/cache/pico4-repo" && sha256sum ./*.pkg.tar.zst) >> "$build_root/package-hashes.txt"
(cd "$T/var/lib/pacman/sync" && sha256sum ./*.db) > "$build_root/db-hashes.txt"
cleanup
rm -rf "$T/var/cache/pacman/pkg"/* "$T/var/log/pacman.log"
archive=$output/pico4-userspace.tar.zst
tar --numeric-owner --xattrs --xattrs-include=security.capability --sort=name -C "$T" -cf - . |
    zstd -q -T0 -12 -o "$archive.part" -f
mv "$archive.part" "$archive"
installed_bytes=$(du -sb --apparent-size "$T" | cut -f1)
python3 - "$archive" "$build_root" "$holo_url" "$holo_sha256" "$installed_bytes" "$firmware_json" "$sensor_json" > "$output/pico4-userspace.manifest.json" <<'EOF'
import hashlib, json, os, sys, datetime
archive, root, holo_url, holo_sha, installed, firmware, sensors = sys.argv[1:8]
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
}, indent=1, sort_keys=True))
EOF
log "done: $archive ($(du -h "$archive" | cut -f1), installed $((installed_bytes >> 20)) MiB)"
