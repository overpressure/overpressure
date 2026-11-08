# SteamOS-compatible ARM64 userspace

Roadmap step 4 ([roadmap.md](roadmap.md)): the Steam Frame's ARM64 SteamOS userspace (systemd,
SteamOS services, gamescope, PipeWire, NetworkManager) on **Valve's kernel from the Frame**
([kernel.md](kernel.md)), with the PICO's own drivers and device tree. It is installed
([install.md](install.md)): the kernel mounts its image from `super` and starts it without a
ramdisk; the stage-1 initramfs ([stage1.md](stage1.md)) is the maintenance environment in
`recovery`, where the system is installed and rescued. Flash storage stays read-only, except the
installed system's data partition. The userspace reaches `running` with no failed units on the headset, where Valve's Mesa runs
Turnip on msm. The XR stack ([xr.md](xr.md)) and Valve's VR session ([session.md](session.md)) run
in it.

## Where the packages come from

The Steam Frame's SteamOS is an Arch Linux (Holo) derivative. Two public Valve repositories serve
its ARM64 packages; the project builds the rest:

| Source | Content | Used for |
| --- | --- | --- |
| [Holo Core aarch64 preview](https://holo-packages.steamos.cloud/holo-core-aarch64-preview/), snapshot `mash-20251118.3` | Valve and Collabora's ARM64 Arch port: `core`, `extra` and a base `system.rootfs.zst` | the build environment; resolves every dependency by default (glibc 2.42, pacman 7) |
| [Steam Frame hotfix repository](https://steamdeck-packages.steamos.cloud/archlinux-deckard-hotfixes/release/0.3.x/) `deckard-arch-hotfixes-release-0.3` | about 400 of the Frame's own builds: systemd 257.7, gamescope 3.16.28, Mesa 26.3 (development), `steamos-customizations-deckard`, `steamos-manager`, the Steam client and SteamVR | the Frame packages named in [packages.txt](../userspace/packages.txt) |
| [userspace/pkgbuilds/](../userspace/pkgbuilds/) | this project's packages ([below](#project-packages)) | the XR stack, frame pacing, patched gamescope and `vrlink` support |

The Frame repository holds only the Frame's hotfixes; its base is not public. Several of its
libraries (ffmpeg, freerdp) need older library versions than the preview provides, so a
configuration that prefers the Frame repository cannot resolve. The preview therefore resolves
everything, and [packages.txt](../userspace/packages.txt) names each Frame package explicitly
(`deckard-arch-hotfixes-release-0.3/<name>`). A line `!name` makes dependencies on `name` count
as satisfied without installing it: `sddm`, which `pico4-session.service` replaces
([session.md](session.md)). The Steam client is installed with the libraries it needs but does
not declare ([session.md](session.md#the-steam-client)). gamescope is the Frame's version built
here with a fix (`pico4-gamescope`). The build checks that the Frame's programs find their
libraries ([Build](#build)).

Valve also publishes the Frame's complete OS image as a RAUC bundle with a casync chunk store
(`steamdeck-atomupd.steamos.cloud/meta/holo/steamos/aarch64/vr/stable.json`; `steamac`
reconstructs it). It is not used: it is a 10 GB btrfs image, and its license restricts
modification. Packages let the build select what the PICO needs.

Neither repository publishes package or database signatures. Integrity rests on HTTPS, the
pinned SHA-256 of the bootstrap rootfs, and the build manifest, which records the SHA-256 of
every repository database and package file used. **Do not redistribute the output**: it
contains Valve's packages, including proprietary ones.

### Package lock

[`userspace/packages.lock`](../userspace/packages.lock) names every upstream package of the image by SHA-256.
A build stops on a package that differs or isn't listed, so an image holds what was tested even though the Frame's
hotfix repository moves on and neither repository signs anything. SteamVR is one of these packages, so it updates
only with the image. If Valve removes a locked package, a build from a clean machine fails until the lock moves to
what Valve then publishes ([docs/releasing.md](../../../docs/releasing.md#moving-to-new-upstream-versions)).

**What updates by itself.** On the headset the system image is read-only and Valve's updater is not installed
([session.md](session.md)), so nothing of the image moves; no timer installs packages (the only package-related timer,
`archlinux-keyring-wkd-sync`, refreshes pacman's keys). What moves lives in the data partition:

| What | Where | How it updates |
| --- | --- | --- |
| Steam client | `~/.local/share/Steam` (`package/`, `linuxarm64/`, `steamrtarm64/`, `steamui`) | unpacked from the image's `deckard-steam-rel` on the first start, then the client's own updater: at each start and in a background loop, from the Frame's ARM64 client beta that `package/beta` names; it is past the image's bundle within days |
| Games, Proton (ARM64), Steam Linux Runtime 4.0 (Arm64), Lepton | `~/.local/share/Steam/steamapps` | Steam, each app set to keep itself updated |
| SteamVR, Mesa, PipeWire, systemd, the kernel and every other package | the system image (`/opt/steamvr` is the runtime `openvrpaths.vrpath` names; no SteamVR app in `steamapps`) | only with a new image |

Holding the client at a tested version is open: the client's update is what signs in today, and whether an older
client keeps working against Steam's servers is not known. The image reports its version and commit in
`/usr/share/pico4/version`.

### systemd stays at 257.x

systemd stays at the Frame's 257.7. The build takes `systemd`, `systemd-libs` and
`systemd-sysvcompat` 257.7 from the Frame repository and fails if the installed systemd is not
257.x. The headset's `/etc/pacman.conf` is
the build's configuration without the local `pico4` repository and with
`IgnorePkg = systemd systemd-libs systemd-sysvcompat systemd-resolvconf`, so an upgrade on the
device cannot pull 258.

### Valve's Mesa

The system's Mesa is Valve's `deckard-mesa-linux-aarch64`
([kernel.md](kernel.md#valves-mesa)): Turnip on msm and OpenGL through zink.
`pico4-graphics-tests` carries the Vulkan test of [graphics.md](graphics.md), built for glibc.

### Project packages

| Package | Content | Details |
| --- | --- | --- |
| `pico4-graphics-tests` | the native Vulkan test | [graphics.md](graphics.md) |
| `pico4-sensors` | hexagonrpcd with this project's patches, for the DSPs | [xr.md](xr.md) |
| `pico4-monado` | Monado 25.1.0 with the PICO 4 driver and `pico4-cam` | [xr.md](xr.md) |
| `pico4-basalt` | Basalt for Monado's VIT interface, built for `armv8.2-a` | [xr.md](xr.md) |
| `pico4-swapchain-usage` | the stock path's frame pacing and compressed scanout in SteamVR's compositor | [session.md](session.md) |
| `pico4-gamescope` | the Frame's gamescope with a fix for a crash when a game starts | [session.md](session.md) |
| `pico4-vrlink` | `vrlink` support for Steam Link | [session.md](session.md) |
| `pico4-spa-alsa` | PipeWire's ALSA plugin at Valve's version with the capture fix of [Audio](#audio); the build installs it over `pipewire-audio`'s | this document |
| `pico4-gtk2` | GTK 2 for the Steam client | [session.md](session.md) |
| `pico4-mangohud` | MangoHud's mangoapp, the client's performance overlay | [session.md](session.md) |
| `pico4-libdisplay-info` | libdisplay-info 0.1 for Valve's Mesa | [kernel.md](kernel.md#valves-mesa) |

External sources are pinned by commit or hash; the project's patches lie next to each PKGBUILD,
its own sources in [src/](../src/).

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

[`preinit`](../userspace/rootfs/usr/libexec/pico4/preinit) runs from the read-only image as PID 1
until it executes systemd; [install.md](install.md#how-it-starts) has its steps, the USB link
and the Wi-Fi re-probe. A failure before systemd reboots into `recovery`, the maintenance stage 1.
The kernel's devtmpfs is what systemd's device monitor needs: it listens to udev only when `/dev`
is a devtmpfs or udev already runs. The stock kernel has none, so the system runs only on Valve's
kernel.

What the system keeps:

- **Storage**: preinit's `devnode sweep`, then `pico4-storage-ro.service` and the udev helper,
  keep every physical disk and partition read-only (`pico4.rw=1` disables this, as in stage 1).
  The one exception is the installed system's data partition (`userdata` with the `pico4-data`
  file system, booted with `pico4.root=installed`), which preinit makes writable and mounts:
  switched read-only under the mounted file system, its writeback fails and the data is lost
  ([install.md](install.md)). `pico4-userspace-check` requires it to be writable. There are no fstab
  entries, the GPT auto-generator is disabled, and `systemd-repart` and `systemd-boot-update`
  are masked.
- **Recovery**: `pico4-recovery-watch.service` runs the same script and policy as stage 1
  (`recovery-watch`, `recovery-policy.sh`) and survives isolation; with `pico4.autoreboot=0` and
  no test run it sets no deadline on the installed system. Emergency and rescue mode log the failed
  units and reboot to fastboot after 30 s (`pico4.emergency_delay`) instead of waiting for a
  console. A crashed PID 1 reboots (`CrashAction=reboot`); stop timeouts are 15 s. systemd arms
  the SoC's watchdog (30 s; its bark panics, so a stopped PID 1 restarts the headset after 33 s), and preinit sends the third boot in a row that never reached the
  graphical target to the maintenance stage 1 ([boot attempts](install.md#boot-attempts)).
- **Kernel panics**: the kernel reboots 5 s after a panic (`panic=5`). The firmware refuses the
  warm reset the command line asks for, and the tree's PS_HOLD reset restarts the headset, about
  40 s from a forced panic to SSH.
- **Logs**: `systemd-pstore.service` is masked, so logs in pstore stay for the maintenance stage 1
  to export. preinit and the system's own scripts log to `/run/pico4/boot.log`. journald keeps
  the journal in `/run` (at most 64 MiB) and forwards warnings to the kernel log.
  `pico4-userspace-netdiag` writes USB link samples 20, 60 and 150 s after it starts to pstore
  (pmsg), which the maintenance stage 1 exports. A timer rewrites the USB log drive with the
  journal, kernel log, unit and network state 45 s after boot and every 2 minutes after that, at
  idle CPU and I/O priority, so that it takes no time from a session.
- **Access**: sshd takes the keys `dev install` wrote to the data partition, on the USB link and on
  Wi-Fi; its host keys come from the first boot and stay in the `/etc` overlay
  ([security.md](security.md)).

## Adaptations in the image

- **Session user**: `steamos` (UID 1000), which the Frame's tmpfiles rules and
  `steamos-customizations-deckard` require; groups `wheel`, `video`, `render`, `input`, `audio`.
  Neither root nor `steamos` has a password. Hostname
  `pico4-steamos`.
- **`/home`**: SteamOS's `home.mount` mounts the Frame's `/dev/disk/by-partsets/shared/home`;
  without that device it waits 90 s and fails `basic.target`. The image replaces it with a
  self-bind of `/home` on the data partition, which keeps the offload bind mounts
  (`steamos-offload.target`) working; their source directories are created in the image.
- **USB**: `pico4-usb-gadget.service` sets up the gadget the maintenance stage 1 has too
  ([install.md](install.md#how-it-starts)); `/tmp/usb_gadget_block` makes the Frame's Type-C plug
  handler leave it alone. systemd-networkd configures the USB link; NetworkManager manages every
  other interface.
- **Masked units**: `systemd-firstboot` (would wait for console input), `systemd-pstore`,
  `systemd-boot-update`, `systemd-repart`,
  `steamos-dump-info` (needs the Frame's EEPROM and EFI boot configuration, `steamos-bootconf`),
  `serial-getty@ttyMSM0` (no one logs in on the UART), and the Frame's USB
  gadget units (`usb-gadget.target`, `usb-ncm-gadget@`, `usb-ncm-dnsmasq@`), which would take
  the controller from `pico4-usb-gadget`'s link. The session's masks are in [session.md](session.md).
- **Enabled**: sshd, systemd-networkd, NetworkManager, systemd-timesyncd, bluetoothd, the
  recovery, storage and log units, the sensor and audio targets, Monado's socket,
  `pico4-session.service`, and the units for the USB link, the fan, USB internet, splash, power key,
  persist, zram swap, clock, kernel-thread priorities, IMU readiness, panel and Venus probes,
  Bluetooth firmware and address, the Wi-Fi probe and address and the video codec links. The default target is
  `graphical.target`.
- **Battery.** The charger and fuel gauge (PM8150B) appear as power supplies:
  `battery` (capacity, status, voltage, current), `usb` (USB-PD, the cable), `pc_port`, `dc`
  and `bms`. UPower reads them; it is D-Bus activated. The kernel's `status` says
  "Charging" whenever the charger is on; on a PC's 500 mA port the running headset still draws
  from the battery (negative current), and UPower reports that as discharging. In Valve's
  session the PICO 4 driver reads the same `battery` supply, and `driver_monado` passes it to
  SteamVR as the headset's battery level and charging state, every five seconds.

## Build

As root in the build container ([docs/tool.md](../../../docs/tool.md#linux-root-steps); the window's
*Userspace archive* step and `make userspace` run the same with the defaults):

```sh
python -m overpressure container run -- bash devices/phoenix/scripts/build-userspace.sh \
    /var/tmp/pico4-userspace-build out/userspace \
    --gpu-firmware assets/graphics-firmware-5.13.7 --sensor-data assets/sensor-data-5.13.7 \
    --wlan assets/wlan-5.13.7 --audio assets/audio-5.13.7 --stock-slam assets/stock-slam-5.13.7
```

[scripts/build-userspace.sh](../scripts/build-userspace.sh) `BUILD_ROOT OUTPUT_DIR
[--gpu-firmware DIR] [--sensor-data DIR] [--wlan DIR] [--audio DIR] [--stock-slam DIR] [--refresh]
[--clean] [--emulated-cc]`:

| Option | Effect |
| --- | --- |
| `--gpu-firmware DIR` | the six `a650_*` GPU firmware files from the stock firmware ([development.md](development.md#stock-firmware), [graphics.md](graphics.md)); without them the userspace boots, but Vulkan cannot start |
| `--sensor-data DIR` | sensor DSP firmware and configuration, lens configuration and controller data, extracted from the stock firmware by `python -m overpressure assets` ([xr.md](xr.md)); without it the head IMU stack stays inactive |
| `--wlan DIR` | Wi-Fi firmware and board data, extracted by `python -m overpressure assets`; without it Wi-Fi stays off |
| `--audio DIR` | ADSP firmware and the audio process's files, extracted by `python -m overpressure assets`; without it there is no sound card |
| `--stock-slam DIR` | PICO OS's head tracker, its Compute DSP firmware and bionic root, extracted by `python -m overpressure assets` ([xr.md](xr.md)); without it head tracking falls back to 3DoF unless `PICO4_SLAM_BACKEND=basalt` |
| `--refresh` | downloads current package databases and reinstalls the base root |
| `--clean` | rebuilds the project packages from scratch |
| `--emulated-cc` | compiles with the build environment's own GCC under QEMU instead of the host's clang |

The script snapshots its inputs into `BUILD_ROOT` (an absolute path on a Linux file system)
first, so the tree can be edited during a build. The build environment is the Holo Core
preview's SHA-256-pinned base rootfs, entered with chroot; on hosts other than arm64 its
programs run under QEMU user emulation, through the aarch64 binfmt handler the build container
registers when the engine has none. On x86-64 hosts the container's clang and lld (LLVM 20)
compile natively inside the arm64 build environment, which is
their sysroot (no LTO; lld links). Project packages ([userspace/pkgbuilds/](../userspace/pkgbuilds/))
rebuild only when their recipe changes, and incrementally (`makepkg -e` in the previous build
tree) when only the project's own sources in `src/` change. Package databases are downloaded
only by the first build and with `--refresh`, so builds repeat the same package versions. The
base root ([packages.txt](../userspace/packages.txt)) is kept between builds; each build
assembles the image root as an overlay on it with the changed project packages and the
adaptation layer ([userspace/rootfs/](../userspace/rootfs/), users, units, the session
configuration). [overpressure/elfdeps.py](../../../overpressure/elfdeps.py) checks natively that the programs and
libraries of the Frame and project packages find their libraries, including symbol versions.

The outputs are `pico4-userspace.tar.zst` and `pico4-userspace.manifest.json`, which records the
version, the Git commit and its date, the bootstrap rootfs, the SHA-256 of every upstream repository
database, package file, GPU firmware and sensor-data file, and the installed package versions; the
hardware runner selects its checks from it. The build does not touch the headset; keep its output
private. Build hosts, the steps in detail and timings are in [development.md](development.md).

### Reproducible builds

Two clean builds of a commit (a new `BUILD_ROOT`, the same assets and package lock) give the same
archive, and the same archive gives the same system image. The build takes every time it records
from the commit's date, as `SOURCE_DATE_EPOCH` (set it to override):

| What would differ | How the build fixes it |
| --- | --- |
| project packages' build dates and file times | makepkg takes `SOURCE_DATE_EPOCH`; with the same compiler the packages are identical |
| fontconfig caches, the Java keystore of `ca-certificates` | the chroots and pacman's hooks get `SOURCE_DATE_EPOCH`, which fc-cache and p11-kit's `trust` take |
| pacman's install dates | written as `SOURCE_DATE_EPOCH` into `/var/lib/pacman/local/*/desc` |
| the project repository's database (`repo-add` records when it ran) | not in the image: the headset's `pacman.conf` has no `[pico4]` |
| ldconfig's `aux-cache` (inode numbers, change times) | removed; it only speeds up the next ldconfig run |
| file times of what the build wrote, access and change times | the archive clamps every time to `SOURCE_DATE_EPOCH` and keeps no access or change times |
| the ext4 image's UUID, hash seed, time stamps and inode times | [build-system-image.sh](../scripts/build-system-image.sh) derives the UUID and hash seed from the archive's SHA-256 and takes every time from the archive's `/usr` |

A build that reuses project packages from an earlier commit (no `--clean`, an old `BUILD_ROOT`) keeps
those packages' build dates, so its archive differs from a clean build's in them.

## Run

The archive becomes a read-only system image in `super` with its data in `userdata`
([install.md](install.md)):

```sh
python -m overpressure container run -- bash devices/phoenix/scripts/build-system-image.sh \
    out/userspace/pico4-userspace.tar.zst out/userspace/pico4-system.img
python -m overpressure dev recovery
python -m overpressure dev install
python -m overpressure dev check
python -m overpressure dev installed    # reconnects after a reboot
```

[`pico4-userspace-check`](../userspace/rootfs/usr/bin/pico4-userspace-check)
`[--wait SECONDS] [--no-vulkan]` prints JSON on stdout, with exit status 0 when it passed. It waits
up to 180 s by default for the boot to settle, then checks:

- systemd 257 as PID 1, state `running`, no failed units;
- `/dev` the kernel's devtmpfs, with a node for every kernel device, and a populated udev database;
- the root read-only from `super`, `/data` on `userdata`, the only writable partition
  ([install.md](install.md));
- D-Bus, logind's seat0 and sshd;
- the USB link up as `pico4-usb-gadget` recorded it (interface, MAC, address), and the
  mass-storage thread that serves the log drive alive;
- the session user logged in with its session target active, Monado's socket listening, and the
  XR device nodes accessible to the session user ([session.md](session.md));
- `steamos-customizations-deckard`, `steamos-manager`, gamescope, PipeWire and
  `deckard-mesa-linux-aarch64` installed;
- with msm's render node present, the Turnip
  Vulkan test ([graphics.md](graphics.md)).

`devices.phoenix.tooling.userspace.validate_check` validates the JSON on the host.

The device-side deadlines return the maintenance stage 1 to fastboot after a failure, and
`python -m overpressure dev reboot` starts it again
([hardware-testing.md](hardware-testing.md#development-mode)).

`python -m overpressure hwtest flash-run` and `python -m overpressure cycle` verify stage 1 and the
way back, on the stock kernel ([hardware-testing.md](hardware-testing.md)).

## The headset's own data

Each headset's factory data stays in its `persist` partition (ext4), where PICO OS keeps it:
[`pico4-persist.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-persist.service)
mounts it read-only at `/mnt/vendor/persist` before `local-fs.target` (`ro,noload`: the journal
is never replayed, the block device stays read-only), and the image links to it instead of
shipping a copy:

| In the image | Links to | Read by |
| --- | --- | --- |
| `/usr/share/qcom/pico4/sensors/registry/` | `persist/sensors/registry/registry/` (registry and factory calibration of every sensor) | hexagonrpcd for the sensor DSP |
| `/usr/share/qcom/pico4/sensors/sns_reg_version` | `persist/sensors/registry/sns_reg_version` | hexagonrpcd |
| `/usr/share/qcom/pico4/calibration/pvr/` | `persist/pvr/` (camera, IMU and lens calibration, gyro bias tables) | Monado's driver, PICO OS's tracker |
| (read directly) | `persist/wlan_mac.bin` | `pico4-wlan-address` (Wi-Fi) |
| (read directly) | `persist/falcon/sn` | `pico4-fan`'s `auto` profile (the production batch, [cooling.md](cooling.md)) |
| (read directly) | `persist/time/ats_2` (the wall clock's offset to the PMIC's RTC, ms) | `pico4-clock`: the clock at boot is RTC + offset, as PICO OS's time daemon keeps it, before timesyncd; only forward |
| (read directly) | `persist/pico_bt_mac` | `pico4-bt-address` (Bluetooth) |

The files belong to Android's `system` user, uid 1000, which is `steamos` here, so the driver in
`vrserver` reads them; root reads all of them. Writes stay refused: the sensor DSP's attempt to
save `temp.json` in the registry fails.

## Internet over USB

The PC can share its internet with the headset over the USB cable. On the headset,
[`pico4-usb-internet.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-usb-internet.service)
adds the PC's end of the link (`pico4.host_ip=`, 172.16.42.2 by default) to the USB link's network
file (`50-pico4-usb.network`) as default gateway and DNS server, at route metric 2048, below NetworkManager's Wi-Fi and
Ethernet routes. Without sharing on the PC that route leads nowhere. `pico4-clock` sets the clock
at boot from the PMIC's RTC and PICO OS's offset in persist; `systemd-timesyncd` corrects the drift
once a time server answers.

On Windows, internet connection sharing (ICS) shares one connection with the headset's link (the
"UsbNcm Host Device" adapter). By default ICS renumbers the shared adapter to 192.168.137.1. Its
`ScopeAddress` setting keeps the adapter at 172.16.42.2 instead, so SSH and the dev tools keep
working, and ICS answers DNS there. In an elevated PowerShell 7, with the headset booted and its
link up:

```sh
pwsh -File devices/phoenix/scripts/windows-share-internet.ps1            # shares the adapter with the default route
pwsh -File devices/phoenix/scripts/windows-share-internet.ps1 -Off       # turns sharing off, restores 192.168.137.1
```

[`windows-share-internet.ps1`](../scripts/windows-share-internet.ps1) turns off any sharing that
is already on (ICS shares one connection per PC), sets `ScopeAddress` and enables sharing from the
adapter with the PC's default route (or `-Internet NAME`) to the headset's link. Sharing survives
the headset's reboots and the maintenance stage 1's NCM initialization. ICS's DNS proxy does not answer EDNS0, so
`systemd-resolved` waits about 30 s after boot before it falls back to plain UDP; lookups are
quick after that.

## Wi-Fi

The kernel's ath11k drives the QCA6390 with PICO OS's firmware and the headset's board data, and
NetworkManager uses the Frame's iwd backend. ath11k's first probe, while the kernel starts, finds no
firmware; [`pico4-wlan-probe`](../userspace/rootfs/usr/libexec/pico4/wlan-probe) probes the chip again
once the system image is mounted ([install.md](install.md#how-it-starts)).
[`pico4-wlan-address`](../userspace/rootfs/usr/libexec/pico4/wlan-address)
sets `wlan0`'s address from `persist` before NetworkManager starts. It joins WPA2 and WPA3 networks and
reconnects by itself after a reboot ([kernel.md](kernel.md#results)).

## Audio

The card is mainline's SM8250 machine driver with the PICO's routing in the
device tree ([kernel.md](kernel.md)): the two WSA8810 speakers on the WSA
macro's SoundWire (units 1 and 2), the WCD9380 on the RX and TX SoundWire buses (units 4 and 3),
the LPASS macros and the DSP's APR services (AFE, ASM, ADM). The card is named `PICO4`. Its
bring-up:

1. `pico4-adsp` boots the ADSP through remoteproc (`remoteproc-boot`).
2. `pico4-audiopd` starts the audio process on upstream FastRPC
   ([`audiopd`](../userspace/rootfs/usr/libexec/pico4/audiopd), `/dev/fastrpc-adsp`):
   `hexagonrpcd -P` creates it with `FASTRPC_IOCTL_INIT_CREATE_STATIC`, and the driver backs it
   with the stock remote heap (12 MiB of CMA), which it gives to the DSP's VMs (LPASS,
   ADSP heap) as the stock tree does.
3. [`pico4-lpass-macros`](../userspace/rootfs/usr/libexec/pico4/lpass-macros) probes the VA
   macro again once the audio process runs. The macro's probe votes the LPASS hardware through
   the AFE, which refuses the vote until then, and a failed probe is final. The driver has no bind
   file, so the platform bus probes it again (`drivers_probe`). The other macros, the SoundWire
   buses and the card follow it.

Its UCM profile ([`ucm2/conf.d/sm8250/PICO4.conf`](../userspace/rootfs/usr/share/alsa/ucm2/conf.d/sm8250/PICO4.conf))
has PICO OS's routes and levels under mainline's control names. Playback is on MultiMedia1
(PCM 0) and capture on MultiMedia2 (PCM 1): mainline's ASM front end runs one stream at a time.
The speakers' voltage and current sense stays off: nothing here reads it. PICO OS's speaker protection is
off as well (`speaker.prot.enable=false`), and PICO OS has no volume cap ([cooling.md](cooling.md#hardware-limits)).

PICO OS's calibration reaches the ADSP through the PICO's own q6 calibration driver
([`q6calibration-pico4.c`](../kernel/src/sound/soc/qcom/qdsp6/q6calibration-pico4.c), patch 0013),
which does what the stock kernel does with the blocks PICO OS's HAL hands it:

1. At image build, `pico4-audiocal export` reads PICO OS's calibration database and writes
   `/usr/lib/firmware/qcom/sm8250/pico4/audio-calibration.bin`: the custom
   topologies, and per path its AFE port, its ADM topology and its parameters (the processing
   blocks and the volume table's step 0). The speakers (WSA_CODEC_DMA_RX_0) get topology
   0x11000003 with 1376 bytes of parameters, the microphone (TX_CODEC_DMA_TX_3) 0x10010315 with
   728.
2. The driver maps a buffer through the audio SMMU stream and registers the custom topologies with
   the ADSP core (all earlier ones dropped first, as the stock driver does). The ADSP maps it only
   once PICO OS's audio process runs, after the ADM service's first probe, and refuses it for a
   while longer on some boots, so `pico4-lpass-macros` probes the driver again every 2 s once the
   card is up, for up to a minute. Without it the sound has none of PICO's processing.
3. q6routing opens each calibrated path with its topology and sends its parameters in band after
   the open. A path without calibration, or one whose topology the ADSP refuses, keeps the null
   topology.

The card runs as the stock kernel's does during playback: every device path, with PICO's processing
in it, at 16 bits (patch 0025; PICO OS's platform configuration names 24 for the speakers, the stock
kernel's back end reads S16_LE). The ADSP's 24-bit PCM has its samples in the top 24 bits of a 32-bit
word, so the front end offers it as S32_LE (patch 0023), which PipeWire picks for both directions.
Mainline offered it as S24_LE, whose samples are in the low 24 bits.

The speakers' amplifiers and the WSA macro run with the registers the stock kernel has during
playback (patch 0024): the compander sets the amplifier's gain over its COMP port, with its boost,
over-current and path settings. The profile's verb sets the boost level and the microphone's gain,
because a saved mixer state overrides its boot sequence. The DSP's stream and port get the stock
kernel's topologies (patch 0025), and the speakers' SoundWire bus its synchronization period and port
offsets (patch 0026).

Two of the amplifiers' switches are not to be changed on a running system with this driver.
`VISENSE` on, as the stock kernel has it, adds the sense port to the playback stream: about 9,600 bus
clashes in 13 s of playback. `COMP` off leaves that port's enable set in the active bus bank: no sound
and white noise on the right channel, with no kernel error. Off again, with the PCM closed and the
path powered down, recovers both.

Compared on the same headset with the stock kernel's build, the same sounds on the microphone at
the same settings, with patch 0024:

| Sound | Stock kernel | This kernel |
| --- | --- | --- |
| 1 kHz fading 15 dB/s, volume 0.6, per 250 ms | −41 −43 −47 −51 −55 −59 −65 −71 −81 dBFS | −48 −50 −54 −58 −63 −67 −72 −80 −85 dBFS |
| Steam's volume sound, volume 0.52 | repeats alike within 1 dB, into the room's noise | the same shape, repeats alike |

The microphone reads about 7 dB lower here, its noise floor with it (−62 and −68 dBFS).

### Capture through a suspend

A capture stream that runs through a suspend is restarted by PipeWire at the wake. Its timer path takes the time
from the planned wakeup, not the clock, so while no data has come yet its next timeout is already over: PipeWire's
real-time thread spins, about 7 ms per second asleep on top of about a second, until the kernel's RT watchdog kills
it at 200 ms (`RT Watchdog Timeout (hard): data-loop.0`). Without the fix all 9 of 9 such suspends killed PipeWire,
none of 9 with playback alone; the recording ended, and Steam's interface kept a dead connection and stayed silent
until a restart, while new clients had sound. `pico4-spa-alsa` takes that timeout from the clock: PipeWire then
survives, the burst is 0 or 1 tick of 10 ms, and the recording goes on. Interrupt scheduling
(`api.alsa.disable-tsched`) is no alternative here: its recordings stay empty.

## Memory

The headset has 7.6 GiB of RAM, shared with the GPU. Valve's idle VR session leaves about
4.2 GB available: Steam's UI processes (steamwebhelper, vrwebhelper, steam) hold about 1.4 GB,
GPU memory about 0.7 GB, and the page cache about 2.7 GB.

- **Swap as on the Frame.** The image installs the Frame's `holo-zram-swap`: zram-generator
  makes `zram0` a swap device of half the RAM, compressed with `lzo-rle`, with zswap off. PICO OS swaps to zram the same way
  (`init.qcom.post_boot.sh`: half the RAM, at most 4 GiB). GPU memory cannot be swapped.
- **Priority 32758.** The swap device has the priority PICO OS's script uses.
- **PICO OS's reclaim settings.**
  [`50-pico4-memory.conf`](../userspace/rootfs/etc/sysctl.d/50-pico4-memory.conf) sets what
  PICO OS's script sets for this kernel: `vm.swappiness` 100, `vm.watermark_scale_factor` 90,
  `vm.watermark_boost_factor` 0.
- **The swap unit.** zram-generator's own swap unit has no PICO OS priority. The image masks the
  generator, and
  [`pico4-zram-swap.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-zram-swap.service)
  runs zram-generator's `--setup-device zram0` from the Frame's configuration (size,
  algorithm, `mkswap`) and `swapon` at priority 32758.
- **A game larger than the free memory.** POOLS under Proton and FEX takes about 3 GB of GPU
  memory and 1.5 GB of its own. Without swap it left 76-226 MB available for about two minutes:
  the kernel could only drop the page cache that the running processes needed again, the whole
  headset froze (head tracking lost, the session's compositor and gamescope crashed), and the
  OOM killer killed the game. With the swap above it starts and runs: 1.5 GB of the session's
  and the game's memory went to zram, at most 2.3 % memory pressure, no kill. `systemd-oomd`
  is not enabled, as on the Frame.
- **PICO OS's overcommit and its tracker.** `vm.overcommit_memory` 1, as PICO OS's `init.rc`
  sets it: allocations never fail, and when memory and swap run out the OOM killer takes the
  process with the highest score. `pico4-slamd` runs at `oom_score_adj` −1000, as Android's init
  runs its core services (`pvrtrackingservice` among them). With the kernel's default
  overcommit, an allocation of the tracker failed once in two tests below and `libSlam` crashed.
- **A game larger than memory and swap together.** A process that fills memory with
  incompressible data at `oom_score_adj` 900 (Steam's games run at 900) in a running VR session:
  RAM is gone after 3 s, swap 5 s later, and the kernel's OOM killer kills it 8–10 s after its
  start. With the settings above, in two runs: no process waits longer than 4.3 s; gamescope,
  Xwayland, SteamVR's server and compositor, and the head tracker keep running; the tracker
  reports tracking lost once or twice and relocalises.

```sh
swapon --show                              # /dev/zram0, partition, about 3.8G, prio 32758
cat /proc/pressure/memory                  # memory pressure (PSI)
```

## Verified results

Current results are in [kernel.md](kernel.md#results).
