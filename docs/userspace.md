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
as satisfied without installing it: the Steam client (`deckard-steam-rel`, `deckard-steam`),
which needs a network and a Steam login, and `sddm`, which `pico4-session.service` replaces
([session.md](session.md)). The Frame's gamescope links `libXxf86vm` without declaring it, so
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
same unmodified Mesa 26.1.6 release with the KGSL backend as the stage-1 graphics overlay,
against glibc and with Wayland and X11 window-system support. It installs beside the system Mesa
under its own names: `/usr/lib/pico4/libvulkan_freedreno_kgsl.so` with the ICD file
`/usr/share/vulkan/icd.d/pico4_turnip_kgsl.aarch64.json`. Its split package
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

`python -m pico4 inspect --firmware BUILD` evaluates the stock kernel's embedded configuration
against what this userspace needs ([pico4/userspace.py](../pico4/userspace.py)) and writes
`userspace-compat.json` beside its analysis. For the stock 5.13.7 and 5.13.8 kernels
(`4.19.81-perf+`), only `DEVTMPFS` is missing among the required options, and the handoff
replaces it. The gaps and their adaptations:

| Missing in the stock kernel | Affects | Adaptation |
| --- | --- | --- |
| `DEVTMPFS` (systemd requires it) | `/dev`, udev | stage 1 hands over a tmpfs copy of its `/dev`; [`00-pico4-devnodes.rules`](../userspace/rootfs/usr/lib/udev/rules.d/00-pico4-devnodes.rules) creates and removes the node for every uevent before any other rule runs |
| `FHANDLE` | systemd mount detection, udev | none needed: systemd falls back to mount IDs |
| `USER_NS`, `PID_NS`, `IPC_NS`, `UTS_NS` | unit sandboxing; Steam's pressure-vessel containers | systemd ignores these settings; Steam Linux Runtime containers would need a setuid bubblewrap and cannot isolate PIDs (open) |
| `MEMCG`, `CGROUP_PIDS`, `CGROUP_DEVICE` | resource control | `systemd-oomd` masked; memory and task limits are not enforced |
| `SYSVIPC`, `POSIX_MQUEUE` | X11 MIT-SHM (`shmget`), SteamVR's process locks (`semget`), some games | semaphores: [`pico4-sysvsem`](../src/sysvsem/pico4-sysvsem.c), preloaded for every process ([session.md](session.md)); shared memory and message queues: none, clients must use memfd and fd passing |
| `BINFMT_MISC` | FEX for x86 games | FEX must be invoked explicitly |
| `HIDRAW` | Steam Input, SteamVR HID access | input only through evdev and uhid |
| `VT`, `DMIID`, `AUTOFS`, ext4 POSIX ACLs | logind, udev, automounts | seat0 without VTs; device identity from the device tree; no `.automount` units; no ACLs on the zram root (device ACLs live on the `/dev` tmpfs) |

Other kernel behaviour the userspace must respect:

- **`ANDROID_PARANOID_NETWORK`**: only root, holders of `CAP_NET_RAW` and members of the Android
  network groups may open IPv4, IPv6 or Bluetooth sockets.
  [`pico4-android-net.conf`](../userspace/rootfs/usr/lib/sysusers.d/pico4-android-net.conf)
  creates the group IDs the kernel checks: `aid_bt` (3001), `aid_bt_net` (3002), `aid_inet`
  (3003) and `aid_net_raw` (3004). The session user `steamos` (UID 1000, as on the Frame) is a
  member of all four.
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
mismatch refuses the switch. It sizes `zram0` at 6144 MiB virtual (`--size-mb`), of which only
written data takes RAM, formats it as ext4 without a journal and mounts it at `/newroot` with
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
  partition read-only (`pico4poc.rw=1` disables this, as in stage 1). There are no fstab
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

## Build

As root on Linux or WSL (`make userspace` runs the same with the Makefile's defaults):

```sh
bash scripts/build-userspace.sh /var/tmp/pico4-userspace-build "$PWD/out/userspace" \
    --gpu-firmware assets/graphics-firmware-5.13.7 --sensor-data assets/sensor-data-5.13.7
```

[scripts/build-userspace.sh](../scripts/build-userspace.sh) `BUILD_ROOT OUTPUT_DIR
[--gpu-firmware DIR] [--sensor-data DIR] [--refresh] [--clean] [--emulated-cc]`:

| Option | Effect |
| --- | --- |
| `--gpu-firmware DIR` | the six `a650_*` GPU firmware files from **your own** backup ([graphics.md](graphics.md)); without them the userspace boots, but Vulkan cannot start |
| `--sensor-data DIR` | sensor DSP firmware, registry, configuration and calibration, extracted from **your own** backup by `scripts/extract-sensor-data.sh` ([xr.md](xr.md)); without it the head IMU stack stays inactive |
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
configuration). [pico4/elfdeps.py](../pico4/elfdeps.py) checks natively that the programs and
libraries of the Frame and project packages find their libraries, including symbol versions.

The outputs are `pico4-userspace.tar.zst` and `pico4-userspace.manifest.json`, which records the
bootstrap rootfs, the SHA-256 of every repository database, package file, GPU firmware and
sensor-data file, and the installed package versions; the hardware runner selects its checks
from it. The build does not touch the headset; keep its output private. Build hosts, the steps
in detail and timings are in [development.md](development.md). The stage-1 image includes the
handoff tools (GNU tar, zstd, e2fsprogs, dropbear-convert) by default
(`python -m pico4 build --firmware BUILD` or `make image`).

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
- every physical block device read-only, the root on zram;
- D-Bus, logind's seat0, sshd and the Android network groups;
- the USB link unchanged (interface, MAC, address, link up), no stage-1 process left, the
  mass-storage thread alive;
- the session user logged in with its session target active, Monado's socket listening, and the
  XR device nodes accessible to the session user ([session.md](session.md));
- `steamos-customizations-deckard`, `steamos-manager`, gamescope, PipeWire and
  `pico4-turnip-kgsl` installed;
- with `/dev/kgsl-3d0` present, the Turnip/KGSL Vulkan test ([graphics.md](graphics.md)).

`pico4.userspace.validate_check` validates the JSON on the host. As a bounded acceptance run,
which restores PICO OS afterwards:

```sh
python -m pico4 hwtest flash-run --serial SERIAL --confirm-device SERIAL --backup BACKUP \
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
`python -m pico4 cycle --userspace ARCHIVE` runs the same acceptance unattended: it stages the
engineering ABL through EDL first and ends on verified stock boot and ABL.

For repeated attempts, `python -m pico4 dev` keeps the stage-1 image on the boot partition and
streams each new archive (`python -m pico4 dev boot ARCHIVE`); the device-side deadlines return
the headset to fastboot after a failure ([development.md](development.md)).

## Verified results

### QEMU: stage 1 and the userspace on a stock-like 4.19 kernel

```sh
tests/qemu/build-test-kernel.sh 4.19.325      # once; writes cache/qemu/Image-4.19
tests/qemu/run-userspace.sh out/pico4-poc-5.13.7-global-seko-initramfs.cpio.gz \
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
- The Steam client is not installed: it needs a network and a Steam login.
