# SteamOS-compatible ARM64 userspace

| | |
| --- | --- |
| What | The Steam Frame's ARM64 SteamOS userspace (systemd 257.7, SteamOS services, gamescope, PipeWire, NetworkManager, the Steam client, SteamVR) with the PICO 4's adaptations, on Valve's kernel ([kernel.md](kernel.md)) |
| How | Packages from Valve's Holo Core aarch64 preview and the Frame's hotfix repository plus the project's own, locked by SHA-256, installed into a reproducible archive; that becomes a read-only system image in `super`, with data in `userdata` ([install.md](install.md)) |
| Code | [userspace/](../userspace/) (`packages.txt`, `packages.lock`, `pacman.conf`, `rootfs/`, `pkgbuilds/`), [scripts/build-userspace.sh](../scripts/build-userspace.sh), [scripts/build-system-image.sh](../scripts/build-system-image.sh) |
| State | Reaches `running` with no failed units on the headset; the XR stack ([xr.md](xr.md)) and Valve's VR session ([session.md](session.md)) run in it |

The stage-1 initramfs ([stage1.md](stage1.md)) is the maintenance environment in `recovery`, where the system is
installed and rescued. Flash storage stays read-only, except the installed system's data partition.

## Where the packages come from

The Frame's SteamOS is an Arch Linux (Holo) derivative. Two public Valve repositories serve its ARM64 packages; the
project builds the rest:

| Source | Content | Used for |
| --- | --- | --- |
| [Holo Core aarch64 preview](https://holo-packages.steamos.cloud/holo-core-aarch64-preview/), snapshot `mash-20251118.3` | Valve and Collabora's ARM64 Arch port: `core`, `extra` and a base `system.rootfs.zst` | the build environment; resolves every dependency by default (glibc 2.42, pacman 7) |
| [Steam Frame hotfix repository](https://steamdeck-packages.steamos.cloud/archlinux-deckard-hotfixes/release/0.3.x/) `deckard-arch-hotfixes-release-0.3` | about 400 of the Frame's own builds: systemd 257.7, gamescope 3.16.28, Mesa 26.3 (development), `steamos-customizations-deckard`, `steamos-manager`, the Steam client and SteamVR | the Frame packages named in [packages.txt](../userspace/packages.txt) |
| [userspace/pkgbuilds/](../userspace/pkgbuilds/) | the project's packages ([below](#project-packages)) | the XR stack, frame pacing, patched gamescope and `vrlink` |

The Frame repository holds only the Frame's hotfixes; its base is not public, and several of its libraries (ffmpeg,
freerdp) need older libraries than the preview has. So the preview resolves everything, and
[packages.txt](../userspace/packages.txt) names each Frame package explicitly
(`deckard-arch-hotfixes-release-0.3/<name>`). A line `!name` makes dependencies on `name` count as satisfied without
installing it: `sddm`, which `pico4-session.service` replaces ([session.md](session.md)). The Steam client gets the
libraries it needs but does not declare ([session.md](session.md#the-steam-client)). The build environment takes the
same Frame packages, so that the project's packages build against the image's versions.

**Packages both repositories have.** The Frame's image updates as a whole on its private base, so the Frame's builds
of base packages assume that base. Where the Frame's build is the same version or newer, the image takes it; where it
is older, core and extra's:

| Package | From | Why |
| --- | --- | --- |
| `filesystem` | Frame | SteamOS's: `/etc/os-release` says `ID=steamos`, `/etc/steamos-release` exists; no `/lib64` |
| `pipewire`, `pipewire-audio`, `pipewire-pulse`, `libpipewire`, `alsa-card-profiles` 1.6.8; `wireplumber`, `libwireplumber` 0.5.14 | Frame | the Frame's audio stack; `pico4-spa-alsa` is built from the same version |
| `bluez-libs` 5.79 | Frame | the version of the Frame's `bluez` |
| `wayland`, `libdrm`, `libxkbcommon`, `libxkbcommon-x11`, `rtkit`, `v4l-utils`, `linux-api-headers` | Frame | newer; the Frame's `wayland` and `libxkbcommon` link `libxml2.so.2`, which extra's `libxml2-legacy` provides next to core's `libxml2` 2.15; `v4l-utils`' Qt tools (`qv4l2`, `qvidcap`) need the Frame's Qt and are not used |
| `bash`, `gptfdisk`, `mkinitcpio-busybox` | Frame | the Frame's rebuild of the same version |
| `xorg-xwayland` 24.1.9 | extra | the Frame's rebuild calls `ei_device_text_keysym`, which extra's `libei` 1.5 lacks: Xwayland would not start |
| `ffmpeg` 8.0 (Frame: 7.0) | extra | the Frame's needs libraries only its private base has (`libjxl` 0.10, `libplacebo` 338, x264, x265, `libvpx` 9, `rav1e` 0.7, `rubberband` 2) |
| `podman` 5.6 (5.5) | extra | the Frame's needs `libgpgme` 11 |
| `glib2` 2.86 and the `gsettings` schemas 49 (2.84, 47) | core | the Frame's is older and also holds the tools of Holo's `glib2-devel`, which it conflicts with |
| `gcc`, `gcc-libs` 15.2 (15.1), `qt6-base`, `qt6-translations` 6.10 (6.8), `protobuf`, `python-protobuf` 33 (25.3), `libinput` 1.29 (1.26), `libei` 1.5 (1.4), `wpa_supplicant` 2.11 (2.10), `openxr` 1.1.53 (1.1.47), `vulkan-icd-loader`, `vulkan-tools`, `spirv-tools` 1.4.328 (1.4.309), `libtool` 2.6 (2.5), `mkinitcpio` 40 (39) | core, extra | the Frame's are older; core and extra's packages are built against the newer versions |
| `e2fsprogs`, `zram-generator` | core, extra | the same version, Holo's rebuild newer |

Valve also publishes the Frame's whole OS image as a RAUC bundle with a casync chunk store
(`steamdeck-atomupd.steamos.cloud/meta/holo/steamos/aarch64/vr/stable.json`). It is not used: a 10 GB btrfs image
whose license restricts modification. Packages let the build select what the PICO needs.

Neither repository signs packages or databases. Integrity rests on HTTPS, the pinned SHA-256 of the bootstrap rootfs,
the package lock and the build manifest. **Do not redistribute the output**: it contains Valve's packages, including
proprietary ones.

### Package lock

[`userspace/packages.lock`](../userspace/packages.lock) names every upstream package of the image by SHA-256. A build
stops on a package that differs or is not listed, so an image holds what was tested, although the Frame's hotfix
repository moves on and nothing is signed. SteamVR is one of these packages, so it updates only with the image.
`build-userspace.sh --update-lock` writes a new lock to `OUTPUT_DIR/packages.lock` instead of checking it.

**Rebuilding a release.** The Holo repositories are a fixed snapshot, but the hotfix repository keeps only each
package's current file. A release rebuilds only while every locked hotfix package is still published, or from a build
root whose package cache (`BUILD_ROOT/bootstrap/var/cache/pico4-pkgs`) and databases still hold them. Otherwise a clean
build fails the lock until the lock moves to what Valve publishes
([docs/releasing.md](../../../docs/releasing.md#moving-to-new-upstream-versions)). There is no mirror: Valve's packages
may not be redistributed.

**What updates by itself.** The system image is read-only and Valve's updater is not installed (`atomupd-manager` is a
stand-in that refuses updates, [session.md](session.md)); no timer installs packages (`archlinux-keyring-wkd-sync`
only refreshes pacman's keys). What moves lives in the data partition:

| What | Where | How it updates |
| --- | --- | --- |
| Steam client | `~/.local/share/Steam` (`package/`, `linuxarm64/`, `steamrtarm64/`, `steamui`) | unpacked from the image's `deckard-steam-rel` on the first start, then the client's own updater, at each start and in a background loop, from the Frame's ARM64 client beta that `package/beta` names |
| Games, Proton (ARM64), Steam Linux Runtime 4.0 (Arm64), Lepton | `~/.local/share/Steam/steamapps` | Steam |
| SteamVR, Mesa, PipeWire, systemd, the kernel, every other package | the system image (`/opt/steamvr` is the runtime `openvrpaths.vrpath` names) | only with a new image |

Holding the client at a tested version is open: whether an older client keeps working against Steam's servers is not
known. The image reports its version and commit in `/usr/share/pico4/version`.

### systemd stays at 257.x

The build takes `systemd`, `systemd-libs` and `systemd-sysvcompat` 257.7 from the Frame repository and fails if the
installed systemd is not 257.x. The headset's `/etc/pacman.conf` is the build's without the local `pico4` repository
and with `IgnorePkg = systemd systemd-libs systemd-sysvcompat systemd-resolvconf`, so an upgrade cannot pull 258.

### Valve's Mesa

The system's Mesa is Valve's `deckard-mesa-linux-aarch64` ([kernel.md](kernel.md#valves-mesa)): Turnip on msm and
OpenGL through zink.

### Project packages

| Package | Content | Details |
| --- | --- | --- |
| `pico4-graphics-tests` | the native Vulkan test | [graphics.md](graphics.md) |
| `pico4-sensors` | hexagonrpcd with the project's patches, for the DSPs | [xr.md](xr.md) |
| `pico4-monado` | Monado 25.1.0 with the PICO 4 driver and `pico4-cam` | [xr.md](xr.md) |
| `pico4-basalt` | Basalt for Monado's VIT interface, built for `armv8.2-a` | [xr.md](xr.md) |
| `pico4-swapchain-usage` | the stock path's frame pacing and compressed scanout in SteamVR's compositor | [session.md](session.md) |
| `pico4-gamescope` | the Frame's gamescope 3.16.28 with a fix for a crash when a game starts | [session.md](session.md) |
| `pico4-vrlink` | `vrlink` support for Steam Link | [session.md](session.md) |
| `pico4-spa-alsa` | PipeWire's ALSA plugin at Valve's 1.6.8 with the capture fix of [Audio](#audio), installed over `pipewire-audio`'s | this document |
| `pico4-gtk2` | GTK 2 for the Steam client | [session.md](session.md) |
| `pico4-mangohud` | MangoHud's mangoapp, the client's performance overlay | [session.md](session.md) |
| `pico4-libdisplay-info` | libdisplay-info 0.1 for Valve's Mesa | [kernel.md](kernel.md#valves-mesa) |

External sources are pinned by commit or hash; patches lie next to each PKGBUILD, the project's own sources in
[src/](../src/).

## How the installed system starts

```text
kernel    Valve's kernel from boot, no ramdisk: root=PARTLABEL=super ro, devtmpfs on /dev,
          init=/usr/libexec/pico4/preinit
preinit   proc, sys, /run; the boot indication; devnode sweep (every block device read-only);
          userdata writable, at /data; /etc overlay, /var /home /root binds; machine-id;
          exec systemd (charger mode: --unit=pico4-charger.target)
systemd   257.7: udev devnode rules, read-only sweep, pico4-usb-gadget (USB NCM 172.16.42.1,
          log drive, ACM), systemd-networkd (DHCP for the PC), pico4-wlan-probe, sshd
```

[`preinit`](../userspace/rootfs/usr/libexec/pico4/preinit) runs as PID 1 until it executes systemd;
[install.md](install.md#how-it-starts) has its steps. A failure before systemd reboots into `recovery`. systemd's
device monitor listens to udev only when `/dev` is a devtmpfs or udev already runs; the stock kernel has no devtmpfs,
so the system runs only on Valve's kernel.

What the system keeps:

- **Storage.** preinit's devnode sweep, `pico4-storage-ro.service` and the udev helper keep every physical disk and
  partition read-only (`pico4.rw=1` disables this, as in stage 1). The exception is the data partition (`userdata`,
  file system `pico4-data`, booted with `pico4.root=installed`), which preinit makes writable: switched read-only
  under the mounted file system, its writeback fails and data is lost. `pico4-userspace-check` requires it writable.
  No fstab entries; the GPT auto-generator is disabled; `systemd-repart` and `systemd-boot-update` are masked.
- **Recovery.** `pico4-recovery-watch.service` runs stage 1's `recovery-watch` and `recovery-policy.sh` and survives
  isolation; with `pico4.autoreboot=0` and no test run it sets no deadline. Emergency and rescue mode log the failed
  units and reboot to fastboot after 30 s (`pico4.emergency_delay`). A crashed PID 1 reboots (`CrashAction=reboot`);
  stop timeouts are 15 s. systemd arms the SoC's watchdog (30 s; its bark panics, so a stopped PID 1 restarts the
  headset after about 33 s). The third boot in a row that never reached the graphical target goes to the maintenance
  stage 1 ([boot attempts](install.md#boot-attempts)).
- **Kernel panics.** `panic=5`; the firmware refuses the warm reset, and the tree's PS_HOLD reset restarts the
  headset, about 40 s from a forced panic to SSH.
- **Logs.** `systemd-pstore.service` is masked, so pstore logs stay for the maintenance stage 1 to export. preinit
  and the system's scripts log to `/run/pico4/boot.log`. journald forwards warnings to the kernel log; it keeps the
  journal on the data partition, up to 1 GiB: Valve's `persistent-store.conf` and `system-max-use.conf`
  (steamos-customizations-deckard) sort after the image's `50-pico4.conf` (`Storage=volatile`) and win. `pico4-userspace-netdiag` writes USB link samples 20, 60 and 150 s after boot
  to pstore (pmsg). A timer rewrites the USB log drive with the journal, kernel log, unit and network state 45 s
  after boot and every 2 minutes, at idle CPU and I/O priority. The journal goes last, in the room the 32 MiB
  drive has left: a boot with a session outgrows it within the hour, and then the drive holds its newest lines.
- **Access.** sshd takes the keys `dev install` wrote to the data partition, on USB and Wi-Fi; its host keys come
  from the first boot and stay in the `/etc` overlay ([security.md](security.md)).

## Adaptations in the image

- **Session user.** `steamos` (UID 1000), which the Frame's tmpfiles rules and `steamos-customizations-deckard`
  require; groups `wheel`, `video`, `render`, `input`, `audio`. Neither root nor `steamos` has a password. Hostname
  `pico4-steamos`.
- **`/home`.** SteamOS's `home.mount` mounts the Frame's `/dev/disk/by-partsets/shared/home` and without it fails
  `basic.target` after 90 s. The image replaces it with a self-bind of `/home` on the data partition, which keeps the
  offload bind mounts (`steamos-offload.target`) working; their source directories are created in the image.
- **USB.** `pico4-usb-gadget.service` sets up the same gadget as the maintenance stage 1
  ([install.md](install.md#how-it-starts)); `/tmp/usb_gadget_block` makes the Frame's Type-C plug handler leave it
  alone. systemd-networkd configures the USB link, NetworkManager every other interface.
- **Masked units.** `systemd-firstboot` (would wait for console input), `systemd-pstore`, `systemd-boot-update`,
  `systemd-repart`, `steamos-dump-info` (needs the Frame's EEPROM and EFI boot configuration),
  `serial-getty@ttyMSM0`, and the Frame's USB gadget units (`usb-gadget.target`, `usb-ncm-gadget@`,
  `usb-ncm-dnsmasq@`), which would take the controller from `pico4-usb-gadget`. The session's masks are in
  [session.md](session.md).
- **Enabled.** sshd, systemd-networkd, NetworkManager, systemd-timesyncd, bluetooth, `pico4-session.service`,
  Monado's socket, the sensor and audio targets, and the project's units for recovery, storage, logs, the USB link
  and USB internet, fan, battery guard, splash, power key, persist, zram swap, clock, kernel-thread priorities, IMU
  readiness, panel and Venus probes, Bluetooth firmware and address, Wi-Fi probe and address, and the video codec
  links. The default target is `graphical.target`.
- **Battery.** The PM8150B charger and fuel gauge appear as power supplies `battery` (capacity, status, voltage,
  current, temp), `usb`, `pc_port`, `dc` and `bms`; UPower (D-Bus activated) reads them. `status` says "Charging"
  whenever the charger is on; on a PC's 500 mA port the running headset still drains the battery (negative current),
  which UPower reports as discharging. `driver_monado` passes the `battery` supply to SteamVR as the headset's level
  and charging state every 5 s.

## Build

As root in the build container ([docs/tool.md](../../../docs/tool.md#linux-root-steps)); the window's *Userspace
archive* step and `make userspace` run the same with the defaults:

```sh
python -m overpressure container run -- bash devices/phoenix/scripts/build-userspace.sh \
    /var/tmp/pico4-userspace-build out/userspace \
    --gpu-firmware assets/graphics-firmware-5.13.7 --sensor-data assets/sensor-data-5.13.7 \
    --wlan assets/wlan-5.13.7 --audio assets/audio-5.13.7 --stock-slam assets/stock-slam-5.13.7
```

`build-userspace.sh BUILD_ROOT OUTPUT_DIR [OPTIONS]`; the asset folders come from `python -m overpressure assets`
([development.md](development.md#stock-firmware)):

| Option | Effect |
| --- | --- |
| `--gpu-firmware DIR` | the six `a650_*` GPU firmware files ([graphics.md](graphics.md)); without them the system boots, but Vulkan cannot start |
| `--sensor-data DIR` | sensor DSP firmware and configuration, lens configuration and controller data ([xr.md](xr.md)); without it the head IMU stack stays inactive |
| `--wlan DIR` | Wi-Fi firmware and board data; without it Wi-Fi stays off |
| `--audio DIR` | ADSP firmware and the audio process's files; without it there is no sound card |
| `--stock-slam DIR` | PICO OS's head tracker, its Compute DSP firmware and bionic root ([xr.md](xr.md)); without it head tracking falls back to 3DoF unless `PICO4_SLAM_BACKEND=basalt` |
| `--refresh` | downloads current package databases and reinstalls the base root |
| `--update-lock` | writes `OUTPUT_DIR/packages.lock` instead of checking [the lock](#package-lock) |
| `--clean` | rebuilds the project packages from scratch |
| `--emulated-cc` | compiles with the build environment's GCC under QEMU instead of the host's clang |

How it works:

- The script snapshots its inputs into `BUILD_ROOT` (absolute, on a Linux file system), so the tree can be edited
  during a build.
- The build environment is the Holo preview's SHA-256-pinned base rootfs, entered with chroot; on non-arm64 hosts its
  programs run under QEMU through the aarch64 binfmt handler, which the build container registers when the engine has
  none. On x86-64 the container's clang and lld (LLVM 20) compile natively with the build environment as sysroot (no
  LTO).
- Project packages rebuild only when their recipe changes, and incrementally (`makepkg -e` in the previous build
  tree) when only `src/` changes. Package databases are downloaded only by the first build and with `--refresh`.
- The base root ([packages.txt](../userspace/packages.txt)) is kept between builds; each build assembles the image
  root as an overlay on it with the project packages and the adaptation layer ([userspace/rootfs/](../userspace/rootfs/),
  users, units, the session configuration).
- [overpressure/elfdeps.py](../../../overpressure/elfdeps.py) checks natively, as `ldd -r` would, that the programs and
  libraries of the Frame and project packages and of the packages that depend on them find their libraries, with
  symbol versions, and that every symbol they need is defined in what the program loads (a build against a newer
  library can call a function the installed one lacks, as the Frame's Xwayland does). Libraries are checked through
  the programs that load them, since their symbols can come from the program (bash's loadable builtins,
  `libthread_db`, speech-dispatcher's modules).

The outputs are `pico4-userspace.tar.zst` and `pico4-userspace.manifest.json`: version, Git commit and date, the
bootstrap rootfs, the SHA-256 of every repository database, package, GPU firmware and sensor-data file, and the
installed package versions; the hardware runner selects its checks from it. Keep the output private. Build hosts and
timings are in [development.md](development.md).

### Reproducible builds

Two clean builds of a commit (a new `BUILD_ROOT`, the same assets and lock) give the same archive, and the same
archive gives the same system image. Every time the build records is the commit's date, as `SOURCE_DATE_EPOCH` (set
it to override):

| What would differ | How the build fixes it |
| --- | --- |
| project packages' build dates and file times | makepkg takes `SOURCE_DATE_EPOCH`; with the same compiler the packages are identical |
| fontconfig caches, the Java keystore of `ca-certificates` | the chroots and pacman's hooks get `SOURCE_DATE_EPOCH`, which fc-cache and p11-kit's `trust` take |
| pacman's install dates | written as `SOURCE_DATE_EPOCH` into `/var/lib/pacman/local/*/desc` |
| the project repository's database (`repo-add` records when it ran) | not in the image: the headset's `pacman.conf` has no `[pico4]` |
| ldconfig's `aux-cache` (inode numbers, change times) | removed; it only speeds up the next ldconfig run |
| file times, access and change times | the archive clamps every time to `SOURCE_DATE_EPOCH` and keeps no access or change times |
| the ext4 image's UUID, hash seed and times | [build-system-image.sh](../scripts/build-system-image.sh) derives UUID and hash seed from the archive's SHA-256 and takes every time from the archive's `/usr` |

A build that reuses project packages from an earlier commit (no `--clean`, an old `BUILD_ROOT`) keeps those packages'
build dates, so its archive differs from a clean build's in them.

## Run

The archive becomes the read-only system image in `super`, with data in `userdata` ([install.md](install.md)):

```sh
python -m overpressure container run -- bash devices/phoenix/scripts/build-system-image.sh \
    out/userspace/pico4-userspace.tar.zst out/userspace/pico4-system.img
python -m overpressure dev recovery
python -m overpressure dev install
python -m overpressure dev check
python -m overpressure dev installed    # reconnects after a reboot
```

[`pico4-userspace-check`](../userspace/rootfs/usr/bin/pico4-userspace-check) `[--wait SECONDS] [--no-vulkan]` prints
JSON, exit status 0 when it passed. It waits up to 180 s for the boot to settle, then checks:

- systemd 257 as PID 1, state `running`, no failed units;
- `/dev` the kernel's devtmpfs with a node for every kernel device, and a populated udev database;
- the root read-only from `super`, `/data` on `userdata` the only writable partition;
- D-Bus, logind's seat0 and sshd;
- the USB link as `pico4-usb-gadget` recorded it (interface, MAC, address) and the log drive's mass-storage thread;
- the session user logged in with its session target active, Monado's socket listening, the XR device nodes
  accessible to the session user ([session.md](session.md));
- `steamos-customizations-deckard`, `steamos-manager`, gamescope, PipeWire and `deckard-mesa-linux-aarch64` installed;
- with msm's render node present, the Turnip Vulkan test ([graphics.md](graphics.md));
- the fan service ([cooling.md](cooling.md#checks)).

`devices.phoenix.tooling.userspace.validate_check` validates the JSON on the host. After a failure the device-side
deadlines return the maintenance stage 1 to fastboot, and `python -m overpressure dev reboot` starts it again
([hardware-testing.md](hardware-testing.md#development-mode)). `hwtest flash-run` and `cycle` verify stage 1 and the
way back on the stock kernel ([hardware-testing.md](hardware-testing.md)).

## The headset's own data

Each headset's factory data stays in its `persist` partition (ext4), where PICO OS keeps it.
[`pico4-persist.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-persist.service) mounts it read-only at
`/mnt/vendor/persist` before `local-fs.target` (`ro,noload`: no journal replay, the block device stays read-only), and
the image links to it instead of shipping a copy:

| In the image | Links to | Read by |
| --- | --- | --- |
| `/usr/share/qcom/pico4/sensors/registry/` | `persist/sensors/registry/registry/` (registry and factory calibration of every sensor) | hexagonrpcd for the sensor DSP |
| `/usr/share/qcom/pico4/sensors/sns_reg_version` | `persist/sensors/registry/sns_reg_version` | hexagonrpcd |
| `/usr/share/qcom/pico4/calibration/pvr/` | `persist/pvr/` (camera, IMU and lens calibration, gyro bias tables) | Monado's driver, PICO OS's tracker |
| (read directly) | `persist/wlan_mac.bin` | `pico4-wlan-address` (Wi-Fi) |
| (read directly) | `persist/falcon/sn` | `pico4-fan`'s `auto` profile (the production batch, [cooling.md](cooling.md)) |
| (read directly) | `persist/time/ats_2` (the wall clock's offset to the PMIC's RTC, ms) | `pico4-clock`: the clock at boot is RTC + offset, as PICO OS keeps it, before timesyncd; only forward |
| (read directly) | `persist/pico_bt_mac` | `pico4-bt-address` (Bluetooth) |

The files belong to Android's `system` user, uid 1000, which is `steamos` here, so the driver in `vrserver` reads
them. Nothing is written: the sensors process rewrites its registry at every start (each group to `temp.json`,
renamed over the group), and `pico4-sensors`' `hexagonrpcd` takes those writes and drops them, so the IMU comes in
about 1 s and the DSP runs from the registry in persist.

## Internet over USB

[`pico4-usb-internet.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-usb-internet.service) adds the PC's end
of the link (`pico4.host_ip=`, default 172.16.42.2) to the USB link's network file (`50-pico4-usb.network`) as default
gateway and DNS server, at route metric 2048, below NetworkManager's Wi-Fi and Ethernet routes. Without sharing on the
PC that route leads nowhere. `systemd-timesyncd` corrects the clock once a time server answers.

On Windows, internet connection sharing (ICS) shares one connection with the headset's adapter ("UsbNcm Host
Device"). ICS renumbers the shared adapter to 192.168.137.1 unless its `ScopeAddress` keeps it at 172.16.42.2, which
the script sets. In an elevated PowerShell 7, with the headset's link up:

```sh
pwsh -File devices/phoenix/scripts/windows-share-internet.ps1            # share the default route's adapter
pwsh -File devices/phoenix/scripts/windows-share-internet.ps1 -Off       # sharing off, 192.168.137.1 restored
```

[`windows-share-internet.ps1`](../scripts/windows-share-internet.ps1) turns off any existing sharing (ICS shares one
connection per PC), sets `ScopeAddress` and shares the default route's adapter (or `-Internet NAME`). Sharing survives
the headset's reboots and the stage 1 NCM initialization. ICS's DNS proxy does not answer EDNS0, so
`systemd-resolved` falls back to plain UDP only about 30 s after boot; lookups are quick after that.

## Wi-Fi

The kernel's ath11k drives the QCA6390 with PICO OS's firmware and board data from PICO's public package (modem and
bluetooth partitions); only the addresses are the headset's, from `persist`. NetworkManager uses the Frame's iwd
backend. ath11k's first probe, while the kernel starts, finds no firmware;
[`wlan-probe`](../userspace/rootfs/usr/libexec/pico4/wlan-probe) probes the chip again once the system image is
mounted ([install.md](install.md#how-it-starts)), and [`wlan-address`](../userspace/rootfs/usr/libexec/pico4/wlan-address)
sets `wlan0`'s address before NetworkManager starts. WPA2 and WPA3 networks join and reconnect after a reboot
([kernel.md](kernel.md#results)).

## Audio

The card `PICO4` is mainline's SM8250 machine driver with the PICO's routing in the device tree
([kernel.md](kernel.md)): two WSA8810 speakers on the WSA macro's SoundWire (units 1 and 2), the WCD9380 on the RX and
TX SoundWire buses (units 4 and 3), the LPASS macros and the DSP's APR services (AFE, ASM, ADM). Bring-up:

1. `pico4-adsp` boots the ADSP through remoteproc (`remoteproc-boot`).
2. `pico4-audiopd` starts the audio process on upstream FastRPC ([`audiopd`](../userspace/rootfs/usr/libexec/pico4/audiopd),
   `/dev/fastrpc-adsp`): `hexagonrpcd -P` creates it with `FASTRPC_IOCTL_INIT_CREATE_STATIC`, backed by the stock
   remote heap (12 MiB of CMA), which the driver gives to the DSP's VMs (LPASS, ADSP heap) as the stock tree does.
3. [`lpass-macros`](../userspace/rootfs/usr/libexec/pico4/lpass-macros) probes the VA macro again once the audio
   process runs: its probe votes the LPASS hardware through the AFE, which refuses until then, and a failed probe is
   final. The driver has no bind file, so it uses the platform bus's `drivers_probe`. The other macros, the SoundWire
   buses and the card follow.

The UCM profile ([`ucm2/conf.d/sm8250/PICO4.conf`](../userspace/rootfs/usr/share/alsa/ucm2/conf.d/sm8250/PICO4.conf))
has PICO OS's routes and levels under mainline's control names. Playback is on MultiMedia1 (PCM 0), capture on
MultiMedia2 (PCM 1): mainline's ASM front end runs one stream at a time. Speaker voltage and current sense stays off.
PICO OS's speaker protection is off too (`speaker.prot.enable=false`), and PICO OS has no volume cap
([cooling.md](cooling.md#hardware-limits)).

**Calibration.** PICO OS's calibration reaches the ADSP through the PICO's q6 calibration driver
([`q6calibration-pico4.c`](../kernel/src/sound/soc/qcom/qdsp6/q6calibration-pico4.c), patch 0013), which does what the
stock kernel does with the blocks PICO OS's HAL hands it:

1. At image build, `pico4-audiocal export` reads PICO OS's calibration database and writes
   `/usr/lib/firmware/qcom/sm8250/pico4/audio-calibration.bin`: the custom topologies, and per path its AFE port, ADM
   topology and parameters (processing blocks and the volume table's step 0). The speakers (WSA_CODEC_DMA_RX_0) get
   topology 0x11000003 with 1376 bytes of parameters, the microphone (TX_CODEC_DMA_TX_3) 0x10010315 with 728.
2. The driver maps a buffer through the audio SMMU stream and registers the custom topologies with the ADSP core
   (earlier ones dropped first, as the stock driver does). The ADSP accepts it only once PICO OS's audio process runs
   and on some boots a while later, so `pico4-lpass-macros` probes the driver again every 2 s once the card is up, for
   up to a minute. Without it the sound has none of PICO's processing.
3. q6routing opens each calibrated path with its topology and sends its parameters in band after the open. A path
   without calibration, or whose topology the ADSP refuses, keeps the null topology.

**The card as the stock kernel runs it.** Every device path, with PICO's processing, runs at 16 bits (patch 0025; the
stock back end reads S16_LE although PICO OS's platform configuration names 24 for the speakers). The ADSP's 24-bit
PCM has its samples in the top 24 bits of a 32-bit word, so the front end offers it as S32_LE (patch 0023), which
PipeWire picks for both directions. The amplifiers and the WSA macro run with the registers the stock kernel has
during playback (patch 0024): the compander sets the amplifier's gain over its COMP port, with its boost, over-current
and path settings. The DSP's stream and port get the stock topologies (patch 0025), the speakers' SoundWire bus its
synchronization period and port offsets (patch 0026).

**Levels.** The profile's verb sets PICO OS's levels (`WSA_RXn Digital Volume` 84, 0 dB; `TX_DEC0 Volume` 84;
`ADC1 Volume` 12), the boost level and the routes each time the card opens; ALSA's saved state, restored at boot after
the card's boot sequence, would otherwise keep whatever it holds. Around a playback the WSA macro's driver moves the
digital volume by 2 steps (its gain offset) and keeps it there.

**Do not change on a running system:** `VISENSE` on (as the stock kernel has it) adds the sense port to the playback
stream: thousands of bus clashes in seconds. `COMP` off leaves that port's enable set in the active bus bank: no
sound and white noise on the right channel, with no kernel error. Switching back, with the PCM closed and the path
powered down, recovers both.

Speakers and microphone together, the same sounds on the same headset at the same settings:

| Sound | Stock kernel | This kernel |
| --- | --- | --- |
| 1 kHz from full scale fading 15 dB/s, volume 0.6, per 250 ms from its peak | −41 −43 −47 −51 −55 dBFS | −41.4 −45.1 −48.9 −52.6 −56.4 dBFS |
| Steam's volume sound, volume 0.52 | repeats alike within 1 dB, into the room's noise | the same shape, repeats alike |

So they are as loud as on the stock kernel with PICO's levels. Speech at a normal distance records at about −34 to
−40 dBFS. PICO OS processes recordings no further for apps (its `audio_effects.xml` adds echo cancellation and noise
suppression to voice calls only, no gain).

### Capture through a suspend

PipeWire restarts a capture stream that runs through a suspend at the wake. Upstream, its timer path takes the time
from the planned wakeup, not the clock, so before data comes its next timeout is already over: the real-time thread
spins until the kernel's RT watchdog kills it at 200 ms (`RT Watchdog Timeout (hard): data-loop.0`). The recording
ends, and Steam's interface keeps a dead connection and stays silent until a restart. `pico4-spa-alsa` takes that
timeout from the clock: PipeWire survives, the burst is 0 or 1 tick of 10 ms, and the recording goes on. Interrupt
scheduling (`api.alsa.disable-tsched`) is no alternative: its recordings stay empty.

## Memory

The headset has 7.6 GiB of RAM, shared with the GPU. Valve's idle VR session leaves about 4.2 GB available: Steam's UI
processes (steamwebhelper, vrwebhelper, steam) hold about 1.4 GB, GPU memory about 0.7 GB, the page cache about
2.7 GB.

- **Swap as on the Frame and PICO OS.** The Frame's `holo-zram-swap` configures `zram0` at half the RAM, `lzo-rle`,
  zswap off; PICO OS swaps to zram the same way (`init.qcom.post_boot.sh`: half the RAM, at most 4 GiB). zram-generator
  is masked because its swap unit has no priority;
  [`pico4-zram-swap.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-zram-swap.service) runs its
  `--setup-device zram0` and `swapon` at PICO OS's priority 32758. GPU memory cannot be swapped.
- **PICO OS's reclaim and overcommit.** [`50-pico4-memory.conf`](../userspace/rootfs/etc/sysctl.d/50-pico4-memory.conf):
  `vm.swappiness` 100, `vm.watermark_scale_factor` 90, `vm.watermark_boost_factor` 0, `vm.overcommit_memory` 1 (as
  PICO OS's `init.rc`: allocations never fail; when memory and swap run out the OOM killer takes the highest score).
  With the default overcommit an allocation of the tracker can fail and crash `libSlam`. `pico4-slamd` runs at
  `oom_score_adj` −1000, as Android's init runs its core services. `systemd-oomd` is not enabled, as on the Frame.
- **A game larger than the free memory.** POOLS under Proton and FEX takes about 3 GB of GPU memory and 1.5 GB of its
  own. Without swap the headset froze for minutes (head tracking lost, compositor and gamescope crashed) until the
  OOM killer killed the game. With swap it runs: 1.5 GB went to zram, at most 2.3 % memory pressure, no kill.
- **A game larger than memory and swap.** A process filling memory with incompressible data at `oom_score_adj` 900
  (Steam's games' value) in a VR session is killed 8–10 s after its start. No process waits longer than 4.3 s;
  gamescope, Xwayland, SteamVR's server and compositor and the head tracker keep running; the tracker may report
  tracking lost and relocalises.

```sh
swapon --show                              # /dev/zram0, partition, about 3.8G, prio 32758
cat /proc/pressure/memory                  # memory pressure (PSI)
```

## Verified results

Current results are in [kernel.md](kernel.md#results).
