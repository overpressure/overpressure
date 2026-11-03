# SteamOS-compatible ARM64 userspace

Roadmap step 4 ([roadmap.md](roadmap.md)): the Steam Frame's ARM64 SteamOS userspace (systemd,
SteamOS services, gamescope, PipeWire, NetworkManager) on the **stock PICO 4 kernel**
(`4.19.81-perf+`), with services, devices, input and graphics adapted to that kernel instead of
the Frame's. The stage-1 initramfs ([stage1.md](stage1.md)) stays the recoverable entry point:
it receives the userspace into compressed RAM and hands PID 1 to its systemd. Flash storage
stays read-only throughout. The userspace reaches `running` with no failed units in QEMU on a
stock-like 4.19 kernel and on the headset, where Turnip renders through KGSL. The XR stack
([xr.md](xr.md)) and Valve's VR session ([session.md](session.md)) run in it.

## Where the packages come from

The Steam Frame's SteamOS is an Arch Linux (Holo) derivative. Two public Valve repositories serve
its ARM64 packages; the project builds the rest:

| Source | Content | Used for |
| --- | --- | --- |
| [Holo Core aarch64 preview](https://holo-packages.steamos.cloud/holo-core-aarch64-preview/), snapshot `mash-20251118.3` | Valve and Collabora's ARM64 Arch port: `core`, `extra` and a base `system.rootfs.zst` | the build environment; resolves every dependency by default (glibc 2.42, pacman 7) |
| [Steam Frame hotfix repository](https://steamdeck-packages.steamos.cloud/archlinux-deckard-hotfixes/release/0.3.x/) `deckard-arch-hotfixes-release-0.3` | about 400 of the Frame's own builds: systemd 257.7, gamescope 3.16.28, Mesa 26.3 (development), `steamos-customizations-deckard`, `steamos-manager`, the Steam client and SteamVR | the Frame packages named in [packages.txt](../userspace/packages.txt) |
| [userspace/pkgbuilds/](../userspace/pkgbuilds/) | this project's packages ([below](#project-packages)) | Turnip with KGSL, the XR stack, SteamVR's display layer, System V semaphores |

The Frame repository holds only the Frame's hotfixes; its base is not public. Several of its
libraries (ffmpeg, freerdp) need older library versions than the preview provides, so a
configuration that prefers the Frame repository cannot resolve. The preview therefore resolves
everything, and [packages.txt](../userspace/packages.txt) names each Frame package explicitly
(`deckard-arch-hotfixes-release-0.3/<name>`). A line `!name` makes dependencies on `name` count
as satisfied without installing it: `sddm`, which `pico4-session.service` replaces
([session.md](session.md)). The Steam client is installed with the libraries it needs but does
not declare ([session.md](session.md#the-steam-client)). The Frame's gamescope links `libXxf86vm` without declaring it, so
the list names that library too. The build checks that the Frame's programs find their
libraries ([Build](#build)).

Valve also publishes the Frame's complete OS image as a RAUC bundle with a casync chunk store
(`steamdeck-atomupd.steamos.cloud/meta/holo/steamos/aarch64/vr/stable.json`; `steamac`
reconstructs it). It is not used: it is a 10 GB btrfs image built for the Frame's kernel, and
its license restricts modification. Packages let the build select what the PICO's kernel can
run.

Neither repository publishes package or database signatures. Integrity rests on HTTPS, the
pinned SHA-256 of the bootstrap rootfs, and the build manifest, which records the SHA-256 of
every repository database and package file used. **Do not redistribute the output**: it
contains Valve's packages, including proprietary ones.

### systemd stays at 257.x

| systemd | Minimum kernel ([README](https://github.com/systemd/systemd/blob/v258.2/README)) | On the PICO's 4.19 |
| --- | --- | --- |
| 258.2 (Holo Core preview) | **5.4** ("not supported at all" below) | no |
| 257.7 (Steam Frame hotfix repository) | 3.15, 5.4 recommended (below it sets the `old-kernel` taint) | yes |

The build takes `systemd`, `systemd-libs` and `systemd-sysvcompat` 257.7 from the Frame
repository and fails if the installed systemd is not 257.x. The headset's `/etc/pacman.conf` is
the build's configuration without the local `pico4` repository and with
`IgnorePkg = systemd systemd-libs systemd-sysvcompat systemd-resolvconf`, so an upgrade on the
device cannot pull 258.

### Turnip needs its KGSL backend

Valve's `deckard-mesa-linux-aarch64` and the preview's `vulkan-freedreno` both build Turnip only
for the upstream `msm` DRM driver; neither binary references `/dev/kgsl-3d0`. The PICO's kernel
exposes the Adreno 650 only through Qualcomm's KGSL driver ([graphics.md](graphics.md)).
[`pico4-turnip-kgsl`](../userspace/pkgbuilds/pico4-turnip-kgsl/PKGBUILD) therefore builds the
unmodified Mesa 26.1.6 with the KGSL backend, against glibc and with Wayland and X11 window-system support. It installs beside the system Mesa
under its own names: `/usr/lib/pico4/libvulkan_freedreno_kgsl.so` with the ICD file
`/usr/share/pico4/vulkan/turnip-kgsl.json`, which is no longer in the image: the system's Mesa is
Valve's `deckard-mesa-linux-aarch64` ([kernel-feasibility.md](kernel-feasibility.md#valves-mesa)),
which has no KGSL backend, so the image no longer drives the GPU on the stock kernel. Its split package
`pico4-graphics-tests` carries the Vulkan and KMS tests of [graphics.md](graphics.md), built for
glibc. `/dev/kgsl-3d0` is open to every user (mode 0666, as on Android).

The system Mesa (OpenGL for Xwayland) is the preview's 25.2.7. Valve's Mesa 26.3 needs
`libdisplay-info.so.1` and `libsensors`, which only the Frame's private base provides.

### Project packages

| Package | Content | Details |
| --- | --- | --- |
| `pico4-turnip-kgsl`, `pico4-graphics-tests` | Turnip with the KGSL backend; the native graphics tests | [graphics.md](graphics.md) |
| `pico4-sensors` | qrtr-ns, pd-mapper and hexagonrpcd with this project's patches, for the sensor DSP | [xr.md](xr.md) |
| `pico4-monado` | Monado 25.1.0 with the PICO 4 driver, the KMS compositor target and `pico4-cam` | [xr.md](xr.md) |
| `pico4-basalt` | Basalt for Monado's VIT interface, built for `armv8.2-a` | [xr.md](xr.md) |
| `pico4-vrdisplay` | the Vulkan layer that gives SteamVR's compositor the panels and shared semaphores | [session.md](session.md) |
| `pico4-sysvsem` | System V semaphores in userspace, preloaded for every process | [session.md](session.md) |

External sources are pinned by commit or hash; the project's patches lie next to each PKGBUILD,
its own sources in [src/](../src/).

## Kernel compatibility

`python -m overpressure inspect --firmware BUILD` evaluates the stock kernel's embedded configuration
against what this userspace needs ([tooling/userspace.py](../tooling/userspace.py)) and writes
`userspace-compat.json` beside its analysis. For the stock 5.13.7 and 5.13.8 kernels
(`4.19.81-perf+`), only `DEVTMPFS` is missing among the required options, and the handoff
replaces it. The gaps and their adaptations:

| Missing in the stock kernel | Affects | Adaptation |
| --- | --- | --- |
| `DEVTMPFS` (systemd requires it) | `/dev`, udev | stage 1 hands over a tmpfs copy of its `/dev`; [`00-pico4-devnodes.rules`](../userspace/rootfs/usr/lib/udev/rules.d/00-pico4-devnodes.rules) creates and removes the node for every uevent before any other rule runs |
| `FHANDLE` | systemd mount detection, udev | none needed: systemd falls back to mount IDs |
| `USER_NS`, `PID_NS`, `IPC_NS`, `UTS_NS` | unit sandboxing; Steam's pressure-vessel containers | systemd ignores these settings; `/usr/bin/bwrap` is setuid, which pressure-vessel uses for Steam Linux Runtime's containers when its own bubblewrap fails; the containers then share the host's PIDs ([session.md](session.md#the-steam-client)) |
| `MEMCG`, `CGROUP_PIDS`, `CGROUP_DEVICE` | resource control | `systemd-oomd` masked; memory and task limits are not enforced |
| `SYSVIPC`, `POSIX_MQUEUE` | X11 MIT-SHM (`shmget`), SteamVR's process locks (`semget`), some games | semaphores: [`pico4-sysvsem`](../src/sysvsem/pico4-sysvsem.c), preloaded for every process ([session.md](session.md)); shared memory and message queues: none, clients must use memfd and fd passing |
| `BINFMT_MISC` | FEX for x86 games | Windows games need none: Proton 11 (ARM64) emulates them with its own FEX; native Linux x86 programs must be started through FEX explicitly |
| `HIDRAW` | Steam Input, SteamVR HID access | input only through evdev and uhid |
| `VT`, `DMIID`, `AUTOFS`, ext4 POSIX ACLs | logind, udev, automounts | seat0 without VTs; device identity from the device tree; no `.automount` units; no ACLs on the zram root (device ACLs live on the `/dev` tmpfs) |

Other kernel behaviour the userspace must respect:

- **`ANDROID_PARANOID_NETWORK`**: only root, holders of `CAP_NET_RAW` and members of the Android
  network groups may open IPv4, IPv6 or Bluetooth sockets.
  [`pico4-android-net.conf`](../userspace/rootfs/usr/lib/sysusers.d/pico4-android-net.conf)
  creates the group IDs the kernel checks: `aid_bt` (3001), `aid_bt_net` (3002), `aid_inet`
  (3003) and `aid_net_raw` (3004). The session user `steamos` (UID 1000, as on the Frame) is a
  member of all four. systemd-networkd and -resolved hold `CAP_NET_RAW`; `systemd-timesyncd`
  does not and gets `aid_inet` from a drop-in.
- **`RT_GROUP_SCHED`**: on cgroup v2 the kernel refuses `SCHED_FIFO` and `SCHED_RR` in any cgroup
  where the `cpu` controller is enabled, even to root, and Monado's compositor needs real-time
  threads. The drop-ins in [userspace/rootfs/etc/systemd/](../userspace/rootfs/etc/systemd/)
  keep the controller off (`DefaultCPUAccounting=no`, no `CPUWeight` on the user slices, user
  managers delegated only `memory pids`). Units must not set `CPUWeight` or `CPUQuota` or
  delegate `cpu`.
- **Root file system**: no btrfs, squashfs or erofs. The root is ext4 on `zram0`, and the stock
  kernel's zram offers only LZO. e2fsprogs 1.47 enables `orphan_file` and `metadata_csum_seed`
  by default, which 4.19 cannot mount, so the handoff turns them off.
- **Firmware loading**: the kernel loads firmware directly from `/usr/lib/firmware` (through the
  `/lib` symlink). udev has no fallback loader, so in stage 2 a missing file fails after the
  5 s fallback timeout that stage 1 sets.
- **Hotplug**: `UEVENT_HELPER` is enabled; the handoff empties the helper path, and udev
  receives events over netlink.

Mainline 4.19 differs from PICO's Android 4.19 by Android-only patches (KGSL, the SDE display
driver, `PARANOID_NETWORK`, a PSI backport). The QEMU test kernel is mainline 4.19 with
[kernel-4.19.fragment](../tests/qemu/kernel-4.19.fragment), which gives the stock kernel's
answers to the options above. It reproduces the syscall surface (no pidfd, clone3, openat2 or
new mount API) and the configuration, not those drivers.

## How the handoff works

```text
stage 1   Alpine initramfs, PID 1 pico4-init: USB NCM 172.16.42.1, SSH, read-only storage,
          recovery-watch (stage1.md)
receive   ssh root@172.16.42.1 pico4-userspace receive < pico4-userspace.tar.zst
            zram0 (LZO) -> ext4 -> /newroot; SHA-256 checked; the archive's systemd must run here
switch    ssh root@172.16.42.1 pico4-userspace switch
            SSH host keys, test-run state, machine-id, USB network settings -> /newroot;
            SIGQUIT to PID 1
pico4-init        shutdown entry; stops user processes only; execs ::restart: switch-userspace
switch-userspace  tmpfs /newroot/dev <- copy of the stage-1 nodes; move /run /sys /proc;
                  exec switch_root /newroot /usr/lib/systemd/systemd
stage 2   systemd 257.7: udev devnode rules, read-only sweep, recovery-watch,
          systemd-networkd (same address, DHCP for the PC), sshd (same host key)
```

**Receive.** [`pico4-userspace receive`](../initramfs/usr/bin/pico4-userspace) unpacks the
archive from stdin (or `--from FILE --bytes N`) and hashes it on the way; with `--sha256` a
mismatch refuses the switch. It sizes `zram0` at 8192 MiB virtual (`--size-mb`), of which only
written data takes RAM (the image takes 5.6 GiB, the rest is room for the session), formats it as ext4 without a journal and mounts it at `/newroot` with
`discard`. Before PID 1 depends on the new root, its systemd must run on this kernel
(`systemd --version` in a chroot). A second attempt needs a reboot. Stage 1's dropbear runs
with a 4 MiB receive window (`-W 4194304` in [svc](../initramfs/usr/libexec/pico4-poc/svc)):
with dropbear's default 24 KiB window, uploads over the USB link stop at about 4.4 MB/s; with
the 4 MiB window the 801 MiB archive arrives in 39 s (21.6 MB/s).

**Switch.** `pico4-userspace switch` converts the stage-1 dropbear host keys for OpenSSH, so the
hardware runner's pinned key stays valid. It copies the test-run state (`/etc/pico4-poc`) and
the authorized keys, writes `/etc/machine-id` (derived from the headset serial, stable per
headset) and a systemd-networkd configuration for the same USB interface and address with a
one-address DHCP pool for the PC. Then it signals PID 1. The SSH session ends with the switch.

**Why stage 1 has its own PID 1.** busybox init stops everything with `kill(-1, ...)`. That also
reaches the kernel's USB mass-storage thread (`file-storage`, which serves the log drive):
`f_mass_storage` accepts SIGTERM and SIGKILL and exits. The PC then resets the composite USB
device and the NCM link loses its carrier for good, while the headset still reports the UDC as
`configured`. Killing only that thread in stage 1 reproduces this.
[`pico4-init`](../initramfs/usr/libexec/pico4-poc/pico4-init) runs the same `/etc/inittab`
entries, but signals only processes whose `/proc/PID/exe` resolves, never kernel threads
(`/proc` files report size 0, so the size of `/proc/PID/cmdline` cannot tell them apart).
`pico4-userspace-check` asserts that the thread survives the switch and that no stage-1 process
does.

[`switch-userspace`](../initramfs/usr/libexec/pico4-poc/switch-userspace) gives the new root a
tmpfs `/dev` with a copy of the stage-1 nodes, because the initramfs `/dev` disappears with
`switch_root`; udev's rules maintain it from then on. Without a prepared userspace it restarts
stage-1 init.

Guarantees carried into stage 2:

- **Storage**: `pico4-storage-ro.service` and the udev helper keep every physical disk and
  partition read-only (`pico4poc.rw=1` disables this, as in stage 1). The one exception is the
  installed system's data partition (`userdata` with the `pico4-data` file system, booted with
  `pico4poc.root=installed`), which stage 1 made writable and mounted: switched read-only under
  the mounted file system, its writeback fails and the data is lost
  ([install.md](install.md)). `pico4-userspace-check` requires it to be writable. There are no fstab
  entries, the GPT auto-generator is disabled, and `systemd-repart` and `systemd-boot-update`
  are masked.
- **Recovery**: `pico4-recovery-watch.service` runs the same script and policy as stage 1
  (`recovery-watch`, `recovery-policy.sh`) with the carried-over test deadlines and survives
  isolation. In a hardware run, the runner must reach stage 2 over SSH within the connect
  deadline + 120 s of the switch, or the headset returns to fastboot without waiting for the
  whole test deadline. Emergency and rescue mode log the failed units and reboot to fastboot
  after 30 s (`pico4poc.emergency_delay`) instead of waiting for a console. A crashed PID 1
  reboots (`CrashAction=reboot`); stop timeouts are 15 s. A failure before `switch_root`
  returns to fastboot from stage 1.
- **Kernel panics**: with the stock kernel's Qualcomm download mode, a panic leaves the headset
  in crash-dump mode (USB `05c6:900e`) until someone holds the power button. Stage 1 sets
  `msm_poweroff.download_mode=0` (`pico4poc.ramdump=1` keeps it), so a panic reboots; a flashed
  stage 1 starts again, and its deadlines return the headset to fastboot.
- **Logs**: `systemd-pstore.service` is masked, so stage-1 logs in pstore stay for the next
  stage-1 boot to export. `/run/pico4-poc/boot.log` continues across the switch. journald keeps
  the journal in `/run` (at most 64 MiB) and forwards warnings to the kernel log.
  `pico4-userspace-netdiag` writes USB link samples 20, 60 and 150 s after the switch to pstore
  (pmsg), which the next stage-1 boot exports. A timer rewrites the USB log drive with the
  journal, kernel log, unit and network state 45 s after the switch and every 2 minutes after
  that.
- **Access**: sshd uses the converted host keys and the same access policy as stage 1 (root
  over the USB cable, the runner's per-run keys or the empty password).

Adaptations in the image:

- **Session user**: `steamos` (UID 1000), which the Frame's tmpfiles rules and
  `steamos-customizations-deckard` require; groups `wheel`, `video`, `render`, `input`, `audio`
  and the four Android network groups. Root and `steamos` have empty passwords. Hostname
  `pico4-steamos`.
- **`/home`**: SteamOS's `home.mount` mounts the Frame's `/dev/disk/by-partsets/shared/home`;
  without that device it waits 90 s and fails `basic.target`. The image replaces it with a
  self-bind of `/home` on the zram root, which keeps the offload bind mounts
  (`steamos-offload.target`) working; their source directories are created in the image.
- **USB**: `/tmp/usb_gadget_block` makes the Frame's Type-C plug handler leave the POC's USB
  gadget alone. systemd-networkd configures the USB link; NetworkManager manages every other
  interface.
- **Masked units**: `systemd-firstboot` (would wait for console input), `systemd-pstore`,
  `systemd-oomd` (needs memcg), `systemd-boot-update`, `systemd-repart`, `steamos-dump-info`
  (needs the Frame's EEPROM and EFI boot configuration, `steamos-bootconf`), and the Frame's USB
  gadget units (`usb-gadget.target`, `usb-ncm-gadget@`, `usb-ncm-dnsmasq@`), which would take
  the controller from the POC's link. The session's masks are in [session.md](session.md).
- **Enabled**: sshd, systemd-networkd, NetworkManager, the recovery, storage, log and self-test
  units, the sensor target, Monado's socket and `pico4-session.service`. The default target is
  `graphical.target`.
- **Battery.** The stock kernel's charger and fuel gauge (PM8150B) appear as power supplies:
  `battery` (capacity, status, voltage, current), `usb` (USB-PD, the cable), `pc_port`, `dc`
  and `bms`. UPower reads them; it is D-Bus activated, and a drop-in turns off its
  `PrivateUsers=` ([upower.service.d](../userspace/rootfs/usr/lib/systemd/system/upower.service.d/50-pico4.conf)),
  which needs user namespaces the kernel lacks (status 217/USER). The kernel's `status` says
  "Charging" whenever the charger is on; on a PC's 500 mA port the running headset still draws
  from the battery (negative current), and UPower reports that as discharging. In Valve's
  session the PICO 4 driver reads the same `battery` supply, and `driver_monado` passes it to
  SteamVR as the headset's battery level and charging state, every five seconds.

## Build

As root on Linux or WSL (`make userspace` runs the same with the Makefile's defaults):

```sh
bash devices/phoenix/scripts/build-userspace.sh /var/tmp/pico4-userspace-build "$PWD/out/userspace" \
    --gpu-firmware assets/graphics-firmware-5.13.7 --sensor-data assets/sensor-data-5.13.7
```

[scripts/build-userspace.sh](../scripts/build-userspace.sh) `BUILD_ROOT OUTPUT_DIR
[--gpu-firmware DIR] [--sensor-data DIR] [--refresh] [--clean] [--emulated-cc]`:

| Option | Effect |
| --- | --- |
| `--gpu-firmware DIR` | the six `a650_*` GPU firmware files from the stock firmware ([development.md](development.md#stock-firmware), [graphics.md](graphics.md)); without them the userspace boots, but Vulkan cannot start |
| `--sensor-data DIR` | sensor DSP firmware and configuration, lens configuration and controller data, extracted from the stock firmware by `scripts/extract-sensor-data.sh` ([xr.md](xr.md)); without it the head IMU stack stays inactive |
| `--refresh` | downloads current package databases and reinstalls the base root |
| `--clean` | rebuilds the project packages from scratch |
| `--emulated-cc` | compiles with the build environment's own GCC under QEMU instead of the host's clang |

The script snapshots its inputs into `BUILD_ROOT` (an absolute path on a Linux file system)
first, so the tree can be edited during a build. The build environment is the Holo Core
preview's SHA-256-pinned base rootfs, entered with chroot; on hosts other than arm64 its
programs run under QEMU user emulation. On x86-64 hosts the host's clang and lld
(`apt install clang-20 lld-20`) compile natively inside the arm64 build environment, which is
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
bootstrap rootfs, the SHA-256 of every repository database, package file, GPU firmware and
sensor-data file, and the installed package versions; the hardware runner selects its checks
from it. The build does not touch the headset; keep its output private. Build hosts, the steps
in detail and timings are in [development.md](development.md). The stage-1 image includes the
handoff tools (GNU tar, zstd, e2fsprogs, dropbear-convert) by default
(`python -m overpressure build --firmware BUILD` or `make image`).

## Run

Interactively, with stage 1 running:

```sh
ssh root@172.16.42.1 pico4-userspace receive < out/userspace/pico4-userspace.tar.zst
ssh root@172.16.42.1 pico4-userspace switch
ssh root@172.16.42.1 pico4-userspace-check     # JSON on stdout; exit status 0 when it passed
```

`pico4-userspace status` shows what was received. SSH answers again from systemd's sshd, with
the same host key, once stage 2 is up.
[`pico4-userspace-check`](../userspace/rootfs/usr/bin/pico4-userspace-check)
`[--wait SECONDS] [--no-vulkan]` waits up to 180 s by default for the boot to settle, then
checks:

- systemd 257 as PID 1, state `running`, no failed units;
- `/dev` on tmpfs with a node for every kernel device and a populated udev database;
- every physical block device read-only, the root on zram; or, on the installed system, the
  root read-only from `super`, `/data` on `userdata`, the only writable partition
  ([install.md](install.md));
- D-Bus, logind's seat0, sshd and the Android network groups;
- the USB link unchanged (interface, MAC, address, link up), no stage-1 process left, the
  mass-storage thread alive;
- the session user logged in with its session target active, Monado's socket listening, and the
  XR device nodes accessible to the session user ([session.md](session.md));
- `steamos-customizations-deckard`, `steamos-manager`, gamescope, PipeWire and
  `pico4-turnip-kgsl` installed;
- with a GPU node present (`/dev/kgsl-3d0`, or msm's render node on a mainline kernel), the Turnip
  Vulkan test ([graphics.md](graphics.md)).

`pico4.userspace.validate_check` validates the JSON on the host. As a bounded acceptance run,
which restores PICO OS afterwards:

```sh
python -m overpressure hwtest flash-run --serial SERIAL --confirm-device SERIAL --backup BACKUP \
    --image out/pico4-poc-5.13.7-global-seko.img --userspace out/userspace/pico4-userspace.tar.zst
```

The runner checks the archive against its manifest, streams it over the pinned SSH connection,
switches and waits for SSH from systemd as PID 1 (`--userspace-timeout`, default 600 s, covers
the transfer and the boot). It runs `pico4-userspace-check --wait 240`, collects the journal,
unit list, `systemd-analyze` output and kernel log, and repeats the stage-1 storage and identity
assertions. With GPU firmware in the manifest, the Vulkan readback must pass; with sensor data,
the head IMU and XR checks follow ([xr.md](xr.md)), and with SteamVR in the archive the session
checks ([session.md](session.md)). Stock restoration follows as in every run; Windows hosts
add `--windows-ncm-python` ([hardware-testing.md](hardware-testing.md)).
`python -m overpressure cycle --userspace ARCHIVE` runs the same acceptance unattended: it stages the
engineering ABL through EDL first and ends on verified stock boot and ABL.

The everyday route is the development loop: the headset stays in development mode with the
stage-1 image on the boot partition, and `python -m overpressure dev boot ARCHIVE` streams each new
archive; the device-side deadlines return the headset to fastboot after a failure, and
`python -m overpressure dev reboot` starts stage 1 again
([hardware-testing.md](hardware-testing.md#development-mode)). Installed on the headset, the same
archive becomes a read-only system image in `super` with its data in `userdata`
([install.md](install.md)).

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
| `/usr/lib/firmware/wlan/qca_cld/qca6390/wlan_mac.bin` | `persist/wlan_mac.bin` | the Wi-Fi driver |
| (read directly) | `persist/falcon/sn` | `pico4-fan`'s `auto` profile (the production batch, [cooling.md](cooling.md)) |
| (read directly) | `persist/time/ats_2` (the wall clock's offset to the PMIC's RTC, ms) | `pico4-clock`: the clock at boot is RTC + offset, as PICO OS's time daemon keeps it, before timesyncd; only forward |
| (read directly) | `persist/pico_bt_mac` | `pico4-bt-address` (Bluetooth on a mainline kernel) |

The files belong to Android's `system` user, uid 1000, which is `steamos` here, so the driver in
`vrserver` reads them; root reads all of them. Writes stay refused: the sensor DSP's attempt to
save `temp.json` in the registry fails, as with the image's copy before.

## Internet over USB

The PC can share its internet with the headset over the USB cable. On the headset,
[`pico4-usb-internet.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-usb-internet.service)
adds the PC's end of the link (`pico4poc.host_ip=`, 172.16.42.2 by default) to stage 1's network
file as default gateway and DNS server, at route metric 2048, below NetworkManager's Wi-Fi and
Ethernet routes. Without sharing on the PC that route leads nowhere. `systemd-timesyncd` sets the
clock once a time server answers. The headset has no working real-time clock and starts at the
image's build date, and HTTPS needs the right time.

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
the headset's reboots and the NCM initialization. ICS's DNS proxy does not answer EDNS0, so
`systemd-resolved` waits about 30 s after boot before it falls back to plain UDP; lookups are
quick after that.

## Wi-Fi

The Wi-Fi chip is a QCA6390 (v2.0) on PCIe, run by the stock kernel's built-in cnss2 and PICO OS's
own driver module, qcacld-3.0 (`qca_cld3_qca6390.ko`, module `wlan`). The module is signed with
the stock kernel's build key, so the kernel loads it as it is. It must stay byte for byte as
extracted, because its signature is appended. [`scripts/extract-wlan.sh`](../scripts/extract-wlan.sh)
reads them from the stock firmware ([development.md](development.md#stock-firmware)), checking
the module; the MAC addresses are the headset's own, linked from persist:

| File | From | Purpose |
| --- | --- | --- |
| `modules/qca_cld3_qca6390.ko` | vendor | the driver |
| `firmware/qca6390/amss20.bin`, `m3.bin`, `bdwlan*` | modem | chip firmware and board data, requested by cnss2 |
| `firmware/wlan/qca_cld/qca6390/WCNSS_qcom_cfg.ini` | vendor | the driver's configuration |
| `firmware/wlan/qca_cld/qca6390/wlan_mac.bin` | a link to `/mnt/vendor/persist/wlan_mac.bin` | this headset's MAC addresses, read by the driver |

`build-userspace.sh --wlan DIR` installs the firmware under `/usr/lib/firmware` and the module
in `/usr/lib/pico4-wlan`. The bring-up follows PICO OS, plus one rule of our own, the restart level:

1. [`pico4-wlfw-client.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-wlfw-client.service)
   starts [`pico4-wlfw-client`](../userspace/rootfs/usr/bin/pico4-wlfw-client).
2. [`pico4-wlan.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-wlan.service) sets the
   `wlan` subsystem's restart level to `RELATED`. At the kernel's default, `SYSTEM`, a firmware
   crash would reboot the headset.
3. It writes `fs_ready`, which starts the chip's cold-boot calibration.
4. It loads the module, which waits for the calibration and brings up `wlan0` and `p2p0`.
5. On stop it writes `/sys/kernel/shutdown_wlan/shutdown`, as PICO OS does; the driver is never
   unloaded.

The PICO 4's device tree sets `cnss-daemon-support`. The kernel then tells the firmware that two
control clients exist, and the firmware holds back its memory request, and with it "FW ready",
until the second has registered. On PICO OS that is `cnss-daemon`. `pico4-wlfw-client`
reimplements only its part in the firmware's control service (WLFW, QMI service 0x45), from the
message tables compiled into it:

- an indication registration for FW ready, calibration download and update and FW memory ready,
  with the daemon's client id;
- on "FW memory ready", the capability request and a calibration report without calibration
  files.

The firmware then calibrates, restarts once and comes up. Calibration data is not kept, since
storage is read-only, so every boot calibrates cold. A link file
([`10-pico4-wlan.link`](../userspace/rootfs/usr/lib/systemd/network/10-pico4-wlan.link)) keeps the
kernel's interface names. NetworkManager manages `wlan0` with `wpa_supplicant`; its connections
live in RAM and are gone after a reboot. The Steam client switches NetworkManager to the Frame's
iwd backend through steamos-manager (`99-valve-wifi-backend.conf` in `/etc`). iwd cannot run on
the stock kernel, which lacks the AF_ALG crypto sockets (`CONFIG_CRYPTO_USER_API_*`), and with
that backend `wlan0` stayed "unavailable" and listed no network. On the stock kernel
`pico4-wifi-backend.service` links
[`99-zz-pico4-wifi-backend.conf`](../userspace/rootfs/usr/share/pico4-poc/99-zz-pico4-wifi-backend.conf)
into `/run/NetworkManager/conf.d`, where it sorts after Valve's file and keeps `wpa_supplicant`;
a mainline kernel runs the Frame's iwd.

WPA3 (SAE) works through the driver's external authentication: the driver connects in
firmware and asks `wpa_supplicant` (`NL80211_CMD_EXTERNAL_AUTH`) for the SAE handshake. The
Steam client saves networks whose access points offer WPA3 as WPA3-only (`key-mgmt sae`, open
authentication), and they connect as saved. `wpa_supplicant` 2.11 asks for WPA version 3 in
the connect request whenever the kernel numbers nl80211 attributes up to
`NL80211_ATTR_SAE_PASSWORD`. The PICO 4's Android 4.19 kernel does, but its cfg80211 accepts
only versions 1 and 2 there: every WPA3 connect failed with `EINVAL` before the driver saw it.
The image's `wpa_supplicant` is therefore
[`pico4-wpa-supplicant`](../userspace/pkgbuilds/pico4-wpa-supplicant/PKGBUILD): Arch's 2.11 with
[a patch](../userspace/pkgbuilds/pico4-wpa-supplicant/sae-wpa-version-2.patch) that asks for
version 2.

## Audio

The sound card is Qualcomm's `kona-mtp-snd-card`, the audio DSP (ADSP) running its front ends.
Its outputs are two WSA881x amplifiers, one per speaker, on SoundWire. Its microphone is analog,
on the WCD938x codec's ADC1 (PICO OS's `amic1`); the card's digital-microphone inputs carry
nothing. The stock kernel has the ASoC core; the rest is PICO OS's own modules (Qualcomm's audio
techpack), signed with the kernel's build key like the Wi-Fi driver.
[`scripts/extract-audio.sh`](../scripts/extract-audio.sh) reads them from the stock firmware
([development.md](development.md#stock-firmware)):

| File | From | Purpose |
| --- | --- | --- |
| `modules/audio_*.ko`, `modules.dep` | vendor | the 24 modules PICO OS loads and the 3 they depend on |
| `firmware/adsp.mdt`, `adsp.bNN` | modem | the ADSP's image |
| `firmware/adspr.jsn`, `adspua.jsn` | modem | its protection domains, for `pd-mapper`: the root process and the audio process (`audio_pd`, service `avs/audio`) |
| `audiopd/acdb/adsp_avs_config.acdb` | vendor | the audio process's AVS configuration |
| `audiopd/dsp/adsp/` | vendor | the audio process's libraries from PICO OS's ADSP library path: its audio modules, their C++ runtime, its heap module and audio configuration |
| `calibration/*.acdb` | vendor | PICO OS's calibration database for the speakers and the handset microphone (`acdbdata/wsa8810/base/MTP`: global, speaker and handset files) |

`build-userspace.sh --audio DIR` installs the firmware and maps under `/usr/lib/firmware`, the
modules in `/usr/lib/pico4-audio`, the calibration database in `/usr/lib/pico4-audio/acdb` and
the audio process's files in `/usr/share/qcom/pico4-audio`. `pico4-audio.target` brings the card
up in PICO OS's order:

1. [`pico4-audio-modules.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-audio-modules.service)
   loads the modules (PICO OS: early-init), each after the modules it depends on, before
   `pico4-pd-mapper.service` starts. The audio notifier registers its clients only when the
   kernel's service locator answers the audio service's lookup, and while the mapper runs that
   answer comes within a millisecond of loading the first module, before the notifier module
   has subscribed: the sound card would never appear. Loaded first, the lookup waits for the
   mapper.
2. [`pico4-adsp.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-adsp.service) sets the
   ADSP's restart level to `RELATED` and boots it (PICO OS: early-boot), as for the SLPI.
3. [`pico4-audiopd.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-audiopd.service)
   starts the audio process as PICO OS's `adsprpcd audiopd` does: `hexagonrpcd -P audiopd`
   creates the static process `audiopd` from the ADSP's image (FastRPC's create-static init with
   a 2 MiB remote heap, as PICO OS's library does) and serves it its files. The channel is
   marked secure, as for the SLPI. The process reads its files with `ftell`, which the server
   answers ([xr.md](xr.md#head-imu-through-the-sensor-dsp)); without the answer the server stopped, and the DSP then
   refused the stream buffers (`hw_params` failing with `ENOMEM`). It asks for some voice
   processing modules that PICO OS does not have either.

When the audio process reports its domain up, the APR driver adds the card's devices and the
machine driver registers the card.

4. [`pico4-audiocal.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-audiocal.service)
   loads PICO OS's calibration as its audio HAL does
   ([`pico4-audiocal`](../userspace/rootfs/usr/bin/pico4-audiocal)). It reads the calibration
   database (`acdbdata/wsa8810`, its global, speaker and handset files) for the two paths: the
   speakers (ACDB device 15, app type 0x11130, deep-buffer playback) and the handset microphone
   (device 4, app type 0x11132, record). It hands the blocks to the kernel's calibration store
   (`/dev/msm_audio_cal`, data in ION buffers of the system heap):
   - the custom topologies, which the kernel registers with the DSP at once;
   - each path's device (ADM) topology and processing parameters, and the speakers' volume table;
   - each path's port (AFE) topology.

   It then switches on the kernel's instance-ID support, as PICO OS's HAL does after loading the
   database: the parameters carry instance IDs, and the DSP refuses them in the older commands
   (`ADSP_ENEEDMORE`). Last, it sets the app-type controls that make the routing driver pick
   these blocks for MultiMedia1 on the speakers' and the microphone's backends.

   The kernel uses a device or port block for one stream and then skips it as stale, so PICO
   OS's HAL sends a path's blocks before every stream. The service does the same: it watches
   MultiMedia1's two PCM nodes and sends a path's blocks again when its node is opened (before
   the stream reaches its device) and when it is closed. Without that, only the first stream
   after loading had its topologies. The stream (ASM) blocks stay the DSP's defaults: the kernel
   keeps a single stream block for both directions. The service stays running, because the
   kernel drops every block when the calibration device is closed for the last time. It
   restarts with the ADSP, which forgets the topologies.

The database has no port (AFE) parameters for these two devices, so the kernel still reports
"cal_block not found" for them, as on PICO OS.

The card has about 40 PCMs; the system uses MultiMedia1 (PCM 0) in both directions, as PICO OS
plays and records media. The other PCMs get no device node
([`00-pico4-devnodes.rules`](../userspace/rootfs/usr/lib/udev/rules.d/00-pico4-devnodes.rules)):
PipeWire's "pro-audio" profile opens every PCM it finds, and opening the capture side of the
hostless MI2S PCM dereferences NULL in the stock kernel's ASoC core (an oops that kills the
opening process with the PCM's lock held, so that every later reader of its status hangs).
PipeWire's access check only looks at the nodes that exist.

An ALSA UCM profile
([`ucm2/conf.d/kona-mtp-snd-ca`](../userspace/rootfs/usr/share/alsa/ucm2/conf.d/kona-mtp-snd-ca))
gives PipeWire the speakers and the microphone, with PICO OS's routes from its `mixer_paths.xml`
(`speaker`, `deep-buffer-playback`, `handset-mic`, `audio-record`) and default volumes. The front
end only accepts hardware parameters while its whole path to the speakers or the microphone is
connected, and PipeWire probes it before it enables a device, so the verb sets both paths; with
one output and one input there is nothing to switch. DAPM powers the amplifiers only while a
stream runs. PipeWire shows a "Speakers" sink and a "Microphone" source; volume is PipeWire's
own.

Two settings keep the sound continuous under Valve's session (load average about 12 on 8 cores):

- **Real-time audio threads.** PipeWire's data threads get real-time priority (round-robin, 80)
  from RTKit (`rtkit`), as on SteamOS: when a session bus exists, PipeWire 1.4 asks the desktop
  portal or RTKit and otherwise stays at normal priority, although its own limit would allow
  real-time. The Frame's RTKit drop-in passes `--no-polkit`, an option Arch's RTKit 0.13 does
  not have, so the daemon did not start;
  [`rtkit-limits.conf`](../userspace/rootfs/etc/systemd/system/rtkit-daemon.service.d/rtkit-limits.conf)
  replaces it with the same limits without that option.
- **Two periods of headroom.** The DSP advances the PCM's position one 1024-frame period at a
  time. SteamVR's sounds ask for quanta of 256 and 512 frames, and below a quantum of 1024 the
  sink missed the DSP about 34 times a second: the sounds broke up.
  [`51-pico4-sound-card.conf`](../userspace/rootfs/etc/wireplumber/wireplumber.conf.d/51-pico4-sound-card.conf)
  sets the card's period to 1024 frames and its headroom to 2048 (one period was not enough).
  On a mainline kernel the driver calls itself a batch device, for which PipeWire halves the period
  and adds one of headroom; `api.alsa.disable-batch` gives the period of 1024 and the buffer of
  8192 frames the stock kernel's card runs with.
- **A quantum of at least 1024 frames.** A game asks for 5 ms (Beat Saber: 220 frames at
  44.1 kHz), which runs the whole graph at 128 frames, 2.7 ms. Under a game's load, clients then
  miss cycles, and each miss is one quantum of silence in the mix, heard as a crack.
  [`51-pico4-quantum.conf`](../userspace/rootfs/etc/pipewire/pipewire.conf.d/51-pico4-quantum.conf)
  sets the minimum to 1024 frames, about PICO OS's 20 ms deep-buffer period. Measured on the sink's
  monitor with a tone and Beat Saber in its menu:

  | Minimum quantum | Graph | Holes in the mix |
  | --- | --- | --- |
  | 32 (PipeWire's default) | 128 frames | 3 to 5 per 20 s, one quantum each |
  | 256, 512 | 256, 512 frames | none in 20 s |
  | 1024 | 1024 frames | none in 60 s |

```sh
cat /proc/asound/cards                     # kona-mtp-snd-card
systemctl status pico4-audio.target        # the three units above
# as the session user (steamos):
wpctl status                               # "Speakers" sink and "Microphone" source
pw-record --rate 48000 --channels 1 /tmp/mic.wav   # a few seconds, then Ctrl-C
pw-play /tmp/mic.wav                       # through the speakers
```

### Audio on a mainline kernel

On Valve's kernel the card is mainline's SM8250 machine driver with the PICO's routing in the
device tree ([kernel-feasibility.md](kernel-feasibility.md)): the two WSA8810 speakers on the WSA
macro's SoundWire (units 1 and 2), the WCD9380 on the RX and TX SoundWire buses (units 4 and 3),
the LPASS macros and the DSP's APR services (AFE, ASM, ADM). The card is named `PICO4`. Its
bring-up:

1. `pico4-audio-modules` and `pico4-audiocal` skip themselves (stock modules, the stock
   kernel's calibration store).
2. `pico4-adsp` boots the ADSP through remoteproc (`audio boot` hands over to `remoteproc-boot`).
3. `pico4-audiopd` starts the audio process on upstream FastRPC
   ([`audiopd`](../userspace/rootfs/usr/libexec/pico4-poc/audiopd), `/dev/fastrpc-adsp`):
   `hexagonrpcd -P` creates it with `FASTRPC_IOCTL_INIT_CREATE_STATIC`, and the driver backs it
   with the stock remote heap (12 MiB of CMA), which it gives to the DSP's VMs (LPASS,
   ADSP heap) as the stock tree does.
4. [`pico4-lpass-macros`](../userspace/rootfs/usr/libexec/pico4-poc/lpass-macros) probes the VA
   macro again once the audio process runs. The macro's probe votes the LPASS hardware through
   the AFE, which refuses the vote until then, and a failed probe is final. The driver has no bind
   file, so the platform bus probes it again (`drivers_probe`). The other macros, the SoundWire
   buses and the card follow it.

Its UCM profile ([`ucm2/conf.d/sm8250/PICO4.conf`](../userspace/rootfs/usr/share/alsa/ucm2/conf.d/sm8250/PICO4.conf))
has PICO OS's routes and levels under mainline's control names. Playback is on MultiMedia1
(PCM 0) and capture on MultiMedia2 (PCM 1): mainline's ASM front end runs one stream at a time.
The speakers' voltage and current sense stays off, because PICO OS feeds it to its speaker
protection and mainline has none.

PICO OS's calibration reaches the ADSP through the PICO's own q6 calibration driver
([`q6calibration-pico4.c`](../kernel/src/sound/soc/qcom/qdsp6/q6calibration-pico4.c), patch 0013),
which does what the stock kernel does with the blocks PICO OS's HAL hands it:

1. At image build, `pico4-audiocal export` reads the same database `pico4-audiocal` loads on the
   stock kernel and writes `/usr/lib/firmware/qcom/sm8250/pico4/audio-calibration.bin`: the custom
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

**Open: the end of a sound crackles at the speakers, worn, on all sounds.** The stock kernel's build
does not. The mix is clean: Steam's volume sound, recorded before the sink, matches its source to
−84 dB, down to exact silence. The crackle remains with patches 0024 to 0026, and the microphone does
not catch it (a worn volume sound reads −62 dBFS over a floor of −75). Not yet split: the sink's
writes to the PCM from the DSP and amplifiers, by playing to the PCM directly.

Compared on the same headset with the stock kernel's build, the same sounds on the microphone at
the same settings, with patch 0024:

| Sound | Stock kernel | This kernel |
| --- | --- | --- |
| 1 kHz fading 15 dB/s, volume 0.6, per 250 ms | −41 −43 −47 −51 −55 −59 −65 −71 −81 dBFS | −48 −50 −54 −58 −63 −67 −72 −80 −85 dBFS |
| Steam's volume sound, volume 0.52 | repeats alike within 1 dB, into the room's noise | the same shape, repeats alike |

The microphone reads about 7 dB lower here, its noise floor with it (−62 and −68 dBFS).

## Memory

The headset has 7.6 GiB of RAM, shared with the GPU. Valve's idle VR session leaves about
4.2 GB available: Steam's UI processes (steamwebhelper, vrwebhelper, steam) hold about 1.4 GB,
GPU memory (KGSL) about 0.7 GB, and the page cache about 2.7 GB.

- **Swap as on the Frame.** The image installs the Frame's `holo-zram-swap`: zram-generator
  makes `zram0` a swap device of half the RAM, compressed with `lzo-rle` (which the stock
  kernel offers), with zswap off. PICO OS swaps to zram the same way
  (`init.qcom.post_boot.sh`: half the RAM, at most 4 GiB). GPU memory cannot be swapped.
- **Priority 32758.** The stock kernel carries ByteDance's swap patches: its `get_swap_pages`
  gives ordinary reclaim (kswapd, direct reclaim, `/proc/PID/reclaim anon`) swap space only on
  a device of priority exactly 32758, the priority PICO OS's script uses; a device of any other
  priority counts as PICO's storage swap, which only `/proc/PID/reclaim storage` uses. At the
  Frame's priority (100) the kernel never swapped (`pswpout` 0 with 220 MB left); at 32758 it
  swaps as soon as memory runs short. Its `/proc/swaps` has an extra `NrExtents` column, which
  systemd cannot parse ("Failed to parse /proc/swaps:2").
- **PICO OS's reclaim settings.**
  [`50-pico4-memory.conf`](../userspace/rootfs/etc/sysctl.d/50-pico4-memory.conf) sets what
  PICO OS's script sets for this kernel: `vm.swappiness` 100, `vm.watermark_scale_factor` 90,
  `vm.watermark_boost_factor` 0.
- **Without device units.** zram-generator's own swap unit waits for `dev-zram0.device`, and
  systemd here never sees udev's devices as plugged (they stay "tentative"), so it timed out
  after 90 s. The image masks the generator, and
  [`pico4-zram-swap.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-zram-swap.service)
  runs zram-generator's `--setup-device zram0` from the Frame's configuration (size,
  algorithm, `mkswap`) and `swapon` at priority 32758.
- **Only on the installed system.** In the dev boot the root file system lives on `zram0`; the
  unit starts only with `pico4poc.root=installed` on the kernel command line.
- **A game larger than the free memory.** POOLS under Proton and FEX takes about 3 GB of GPU
  memory and 1.5 GB of its own. Without swap it left 76-226 MB available for about two minutes:
  the kernel could only drop the page cache that the running processes needed again, the whole
  headset froze (head tracking lost, the session's compositor and gamescope crashed), and the
  OOM killer killed the game. With the swap above it starts and runs: 1.5 GB of the session's
  and the game's memory went to zram, at most 2.3 % memory pressure, no kill. `systemd-oomd`
  stays masked; the Frame does not enable it either.
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
cat /sys/class/kgsl/kgsl/page_alloc        # GPU memory in bytes
cat /proc/pressure/memory                  # memory pressure (PSI)
```

## Verified results

### Audio on the headset

| Check | Result |
| --- | --- |
| Bring-up at boot (installed system) | modules loaded, ADSP online and `kona-mtp-snd-card` registered 32.7 s after the kernel started; the audio process's file server without a restart; nodes for PCM 0 only; no oops |
| Speakers | a 440 Hz test tone played on MultiMedia1 heard by the user |
| Microphones | with the speakers playing that tone (−20 dBFS): the analog microphone (`amic1`) records it 58 dB above its level in silence; the six digital-microphone inputs record nothing |
| PipeWire | "Speakers" sink (default) and "Microphone" source; playback and capture at the same time; the microphone records the speakers' tone at −54.9 dBFS at volume 1.0 and −78.9 dBFS at 0.4 |
| Calibration (`pico4-audiocal`) | its blocks match those extracted from PICO OS's database byte for byte; loaded at boot 0.6 s after the card registers; five fresh streams in each direction all get the port topologies 0x112fc (speakers) and 0x112fb (microphone) instead of 0; the DSP takes the device topologies and parameters without an error; playback without xruns; the microphone records the speakers' tone 1.8 dB lower than without calibration |
| Continuous playback | data threads round-robin 80 through RTKit; no xruns at quanta from 1024 down to 64 frames; while Valve's session starts and runs (SteamVR's streams at 512 frames, load average about 10), a 20 s tone at a 256-frame latency without xruns. Capture alone at a 64-frame quantum still loses about 5 periods a second |
| `dev check` on the installed system | passes, Valve's session included (89.93 commits/s) |

### QEMU: stage 1 and the userspace on a stock-like 4.19 kernel

```sh
devices/phoenix/tests/qemu/build-test-kernel.sh 4.19.325      # once; writes cache/qemu/Image-4.19
devices/phoenix/tests/qemu/run-userspace.sh out/pico4-poc-5.13.7-global-seko-initramfs.cpio.gz \
    out/userspace/pico4-userspace.tar.zst     # or: make qemu-userspace
```

The VM boots the exact stage-1 initramfs of the headset image. A GPT partition named
`pico4_payload` holds the archive, and `pico4poc.userspace_payload=` makes stage 1 run the same
`pico4-userspace receive` and `switch` as over SSH. dummy_hcd makes the VM both ends of the USB
cable, so the NCM link and the log drive exist as on the headset. In stage 2,
`pico4-userspace-selftest.service` (`pico4poc.userspace_selftest=1`) runs
`pico4-userspace-check --no-vulkan`, logs `PICO4-USERSPACE-SELFTEST: PASS` or `FAIL` and powers
off; the harness saves the console log and the check's JSON in `out/`. The kernel is mainline
4.19.325 with [kernel-4.19.fragment](../tests/qemu/kernel-4.19.fragment), on `-cpu cortex-a76`
(`cortex-a57` where QEMU lacks it), 4 GiB, TCG and 2 vCPUs (`QEMU_SMP`). The test is optional
and slow: a boot of the current archive takes 10–20 minutes, and RCU stalls in the emulated
CPUs often push systemd's generators past their limit ("Failed to start up manager"); 4 vCPUs
stall more, 1 vCPU takes twice as long. The headset's development loop is the routine test
([hardware-testing.md](hardware-testing.md)).

Base userspace (the Frame's systemd, SteamOS packages, gamescope, PipeWire, NetworkManager,
Turnip/KGSL; 366 MiB archive, 1,416 MiB installed):

| Check | Result |
| --- | --- |
| Receive into zram | SHA-256 verified; 1,496 MiB on ext4 in 630 MiB of RAM (LZO); `systemd 257 (257.7-2.2-arch)` runs in the new root |
| Handoff | `pico4-init` -> `switch_root` -> systemd as PID 1, taint `old-kernel` only |
| System state | `running`, no failed units, `multi-user.target` reached |
| `/dev` without devtmpfs | tmpfs; every kernel device in `/sys/dev` has a node; 214 devices in the udev database |
| Storage | root on `/dev/zram0`; every physical block device read-only |
| logind, D-Bus, sshd | seat0 present, system bus up, sshd active |
| USB link and log drive | link unchanged across the switch; no stage-1 process left; mass-storage thread alive |
| Frame packages | `steamos-customizations-deckard` 20260823.1-2, `steamos-manager` 26.4.1-2, gamescope 3.16.28-1 |
| Host validation | `pico4.userspace.validate_check` accepts the JSON |
| Stage-1 regression | [run-selftest.sh](../tests/qemu/run-selftest.sh) on the 6.18 test kernel passes with `pico4-init` and the handoff tools |

The archives with the XR stack and Valve's session pass the same self-test, including the
session user's session and Monado's socket ([session.md](session.md)).

### Headset

Original PICO 4, PICO OS 5.13.7 (global, seko), stock kernel `4.19.81-perf+`, base userspace
(358 packages, 366 MiB archive). Unattended `flash-run --userspace`, with the engineering ABL
staged through EDL (`adb reboot edl`, no buttons):

| Check | Result |
| --- | --- |
| Stage 1 | shell, read-only storage, HTTP and hardware report pass as in the stage-1 acceptance ([hardware-testing.md](hardware-testing.md)) |
| Archive over USB NCM into zram | 366 MiB in 92 s with dropbear's default 24 KiB window; SHA-256 verified on the headset |
| Handoff | SSH from systemd as PID 1 about 21 s after the switch, with the same pinned host key and run identity |
| systemd 257.7 | `running`, no failed units, taint `old-kernel`; `multi-user.target` 91 s after the switch |
| `/dev` without devtmpfs | every kernel device has a node; 1,622 devices in the udev database |
| Storage | root on `/dev/zram0`; every physical block device read-only; the stage-1 storage assertions hold in stage 2 |
| USB link, logind | `usb0` unchanged (name, MAC, address, carrier); seat0 |
| **Vulkan through Turnip/KGSL (glibc)** | **Adreno 650, all 65,536 pixels verified, 0 bad** |
| Memory | 6.5 of 7.7 GB available; the 1.5 GB root takes about 0.8 GB of RAM in zram |
| Recovery | the runner restores stock boot and verifies the matching PICO OS; a full 96 MiB EDL readback of boot matches the backup; the stock ABL is written back and verified by EDL readback; PICO OS boots normally |

The errors in the stage-2 journal are the stock kernel's own early-boot messages (PMIC and SPMI
probes, display PLL debugfs) and the missing Frame modules and sysctls below. The archives with
the XR stack and Valve's session pass the same acceptance with stock restoration
([xr.md](xr.md), [session.md](session.md)).

With internet shared over USB from a Windows 11 PC on Wi-Fi, the headset resolves
store.steampowered.com through the PC and an HTTPS request to it answers 200 in 0.5 s. The clock
is set about 32 s after boot, once `systemd-resolved` has fallen back from EDNS0. SSH and the
recovery deadlines (on uptime) are unaffected by the eight-month clock jump.

Wi-Fi comes up by itself in 41 s, in parallel with the rest of the boot: registration, FW memory
ready, calibration, the firmware's restart and `wlan0`. NetworkManager scans 2.4 and 5 GHz
networks (34 seen, strongest 94 %). A WPA2 network joins: an address by DHCP, HTTPS to the
Steam store, 21 MB/s download. A WPA2/WPA3 network joins with WPA3 (SAE through the driver's
external authentication), with open or unset authentication in the profile, and HTTPS works.

### Limits and known messages

- KGSL, the SDE display driver, PICO's USB controller and `PARANOID_NETWORK` exist only in PICO's
  kernel. QEMU skips the Vulkan check (no `/dev/kgsl-3d0`); on the headset it is required
  whenever the manifest lists GPU firmware.
- `systemd-modules-load` asks for `hid_nintendo`, `hid_playstation` and `ntsync` (the Frame's
  module list); the stock kernel has none of them and loads only PICO-signed modules. Two of the
  Frame's sysctls do not exist on 4.19. Both are logged, not failures.
- In QEMU, `serial-getty@ttyAMA0` times out waiting for its device and is skipped; the emulated
  boot is slow (`multi-user.target` about 170 s after the switch). The headset has no serial
  console; the stage-2 logs are the journal and the kernel log.
- The Steam client starts with Valve's session; signing in is not verified yet ([session.md](session.md#the-steam-client)).
