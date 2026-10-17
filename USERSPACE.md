# SteamOS-compatible ARM64 userspace

Roadmap step 4 in [RESEARCH.md](RESEARCH.md): run the Steam Frame's ARM64 SteamOS userspace
(systemd, SteamOS services, gamescope, PipeWire, NetworkManager) on the **stock PICO 4 kernel**,
adapting services, devices, input and graphics to that kernel instead of using the Frame's.

It builds on the verified Linux shell (step 2) and native graphics (step 3). The stage-1 POC
initramfs stays the recoverable entry point; the SteamOS userspace is a second stage that runs
from compressed RAM. Flash storage remains read-only throughout.

> **Step 4 verified on the headset — 2026-10-17.** On an original PICO 4 with PICO OS 5.13.7 and
> its stock 4.19 kernel, the userspace (358 packages: systemd 257.7, SteamOS customizations and
> `steamos-manager` from the Steam Frame, gamescope 3.16.28, PipeWire, NetworkManager, KGSL
> Turnip) took over PID 1 from the POC initramfs and reached `running` with no failed units.
> Turnip rendered and verified all 65,536 pixels of the Vulkan test inside it. The automated
> `flash-run --userspace` acceptance passed, followed by byte-verified stock boot and ABL
> restoration and a normal PICO OS boot. See [Verification](#verification).

## Where the packages come from

The Steam Frame's SteamOS is an Arch Linux (Holo) derivative. Two public Valve repositories
serve its ARM64 packages:

| Source | Content | Used for |
| --- | --- | --- |
| [Holo Core aarch64 preview](https://holo-packages.steamos.cloud/holo-core-aarch64-preview/), snapshot `mash-20251118.3` | Valve/Collabora's ARM64 Arch port: `core`, `extra` and a base `system.rootfs.zst` | build environment; resolves every dependency by default (glibc 2.42, pacman 7) |
| [Steam Frame hotfix repository](https://steamdeck-packages.steamos.cloud/archlinux-deckard-hotfixes/release/0.3.x/) `deckard-arch-hotfixes-release-0.3` | about 400 of the Frame's own builds: systemd 257.7, gamescope 3.16.28, Mesa 26.3 devel, `steamos-customizations-deckard`, `steamos-manager`, Steam client and SteamVR packages | the Frame packages named in [packages.txt](userspace/packages.txt) |

The Frame repository holds only the Frame's hotfixes; its base is not public. Several of its
libraries (ffmpeg, freerdp) need older library versions than the preview has, so a
"Frame repository first" configuration cannot resolve. The preview therefore resolves
everything, and [packages.txt](userspace/packages.txt) names the Frame packages explicitly
(`deckard-arch-hotfixes-release-0.3/<name>`). The build then runs `ldd` on every ELF file of
the Frame and project packages and fails on any unresolved library.

The complete image is also published as a RAUC bundle and casync chunk store
(`steamdeck-atomupd.steamos.cloud/meta/holo/steamos/aarch64/vr/stable.json`, build
`20261007.6125817` at the time of writing; `steamac` reconstructs it). It is not used here:
it is a 10 GB btrfs image built for the Frame's kernel, and its license restricts
modification. Packages let the build select what the PICO kernel can run.

Neither repository publishes package or database signatures. Integrity rests on HTTPS, the
pinned SHA-256 of the bootstrap rootfs, and the manifest that records the SHA-256 of every
repository database and package file used. The build is local and private. **Do not
redistribute the output**: it contains Valve packages, including proprietary ones.

### The systemd version is the kernel's hard limit

| systemd | Minimum kernel ([README](https://github.com/systemd/systemd/blob/v258.2/README)) | On the PICO's 4.19 |
| --- | --- | --- |
| 258.2 (Holo Core preview) | **5.4** ("not supported at all" below) | no |
| 257.7 (Steam Frame hotfix repository) | 3.15, 5.4 recommended (sets the `old-kernel` taint) | yes |

The build takes systemd 257.7 from the Frame repository and fails if any other version is
installed. The headset's `/etc/pacman.conf` adds `IgnorePkg = systemd ...`, so an upgrade
on the device cannot pull 258.

### Graphics: Turnip needs its KGSL backend

Both Valve's `deckard-mesa-linux-aarch64` and the preview's `vulkan-freedreno` build Turnip
only for the upstream `msm` DRM driver (neither binary references `/dev/kgsl-3d0`). The
PICO's kernel exposes the Adreno 650 through Qualcomm's KGSL driver. The build therefore
compiles [`pico4-turnip-kgsl`](userspace/pkg/pico4-turnip-kgsl/PKGBUILD): the same unmodified
Mesa 26.1.6 release and KGSL option that passed step 3, now against glibc, with Wayland/X11
window-system support. It installs beside the system Mesa under its own library and ICD
names. `pico4-graphics-tests` rebuilds the step-3 Vulkan and KMS tests for glibc.

The system Mesa (OpenGL for Xwayland) is the preview's 25.2.7. Valve's 26.3 build needs
`libdisplay-info.so.1` and `libsensors`, which only the Frame's private base provides; the
build's `ldd` check found this.

## Kernel compatibility

`python build.py inspect --firmware <build>` now also evaluates the kernel's embedded
configuration against what this userspace needs ([tools/userspace.py](tools/userspace.py)) and
writes `userspace-compat.json`. For the stock 5.13.7/5.13.8 kernel (`4.19.81-perf+`):

| Missing in the stock kernel | Affects | Adaptation |
| --- | --- | --- |
| `DEVTMPFS` (systemd requires it) | `/dev`, udev | stage 1 hands over a tmpfs copy of its `/dev`; [`00-pico4-devnodes.rules`](userspace/rootfs/usr/lib/udev/rules.d/00-pico4-devnodes.rules) creates and removes nodes for every uevent before any other rule runs |
| `FHANDLE` | systemd mount detection, udev | none needed: systemd falls back to mount IDs |
| `USER_NS`, `PID_NS`, `IPC_NS`, `UTS_NS` | unit sandboxing; Steam's pressure-vessel containers | systemd ignores the settings; Steam Linux Runtime isolation needs a later decision (setuid bubblewrap or a kernel rebuild) |
| `MEMCG`, `CGROUP_PIDS`, `CGROUP_DEVICE` | resource control | `systemd-oomd` masked; limits are not enforced |
| `SYSVIPC`, `POSIX_MQUEUE` | X11 MIT-SHM via `shmget`, some games | clients must use memfd/fd passing |
| `BINFMT_MISC` | FEX for x86 games | FEX must be launched explicitly (step 6 question) |
| `HIDRAW` | Steam Input, SteamVR HID access | input only through evdev/uhid |
| `VT`, `DMIID`, `AUTOFS`, ext4 POSIX ACLs | logind, udev, automounts | seat0 without VTs; device identity from the device tree |

Other kernel behavior the userspace must respect:

* **`ANDROID_PARANOID_NETWORK`**: only root, `CAP_NET_RAW` holders and the Android groups may
  open IPv4/IPv6/Bluetooth sockets. [`pico4-android-net.conf`](userspace/rootfs/usr/lib/sysusers.d/pico4-android-net.conf)
  creates `aid_inet` (3003), `aid_net_raw` (3004), `aid_bt` (3001) and `aid_bt_net` (3002); the
  session user `steamos` (UID 1000, as on the Frame) is a member.
* **Root filesystem types**: no btrfs, squashfs or erofs. The root is ext4 on `zram0`; the
  stock kernel's zram offers LZO only. e2fsprogs 1.47 enables `orphan_file` and
  `metadata_csum_seed` by default, which 4.19 cannot mount, so the handoff disables them.
* **Firmware loading**: the kernel loads GPU firmware directly from `/usr/lib/firmware`
  (through the `/lib` symlink). There is no udev fallback loader; a missing file fails after
  the 5 s timeout stage 1 sets.

Mainline 4.19 differs from PICO's Android 4.19 by Android-only patches (KGSL, the SDE display
driver, `PARANOID_NETWORK`, a PSI backport). The QEMU test below uses mainline 4.19 with the
options above; it reproduces the syscall surface and kernel configuration, not those drivers.

## How the handoff works

```text
fastboot/flash ─► stage 1 (Alpine initramfs, PID 1 pico4-init)    unchanged recovery path
                   USB NCM 172.16.42.1, SSH, read-only storage, recovery-watch
PC ── ssh pico4-userspace receive < pico4-userspace.tar.zst ──►  ext4 on /dev/zram0 → /newroot
                   (sha256 checked, then the archive's systemd must run on this kernel)
PC ── ssh pico4-userspace switch ──►  SSH host key, test-run state, USB network settings copied
                   SIGQUIT → pico4-init stops user processes only → ::restart: switch-userspace
switch-userspace:  tmpfs /newroot/dev ← copy of stage-1 nodes; move /run /sys /proc
                   exec switch_root /newroot /usr/lib/systemd/systemd
stage 2 (systemd 257.7): udev devnode rules, storage read-only sweep, recovery-watch,
                   systemd-networkd (same address + DHCP for the PC), sshd (same host key)
```

**Why stage 1 has its own PID 1.** busybox init stops everything with `kill(-1, …)`. That
also reaches the kernel's USB mass-storage thread (`file-storage`, which serves the log
drive): `f_mass_storage` accepts SIGTERM/SIGKILL and exits. The PC then resets the composite
USB device and the NCM link loses carrier for good, while the headset still shows the UDC as
`configured`. On the headset this looked like a stage 2 without network; once it also ended
in a kernel panic. [`pico4-init`](initramfs/usr/libexec/pico4-poc/pico4-init) runs the same
`/etc/inittab` entries, but signals only processes that have an executable, never kernel
threads. Killing just that thread in stage 1 reproduced the failure; QEMU reproduced it with
the log drive and passed without it, and passes with `pico4-init`. The acceptance check
asserts that the thread survives and that no stage-1 process does.

Guarantees carried into stage 2:

* **Storage**: `pico4-storage-ro.service` and the udev helper keep every physical disk and
  partition read-only (`pico4poc.rw=1` disables this, as in stage 1). No fstab, the GPT
  auto-generator is masked, and `systemd-repart`/`boot-update` are masked.
* **Recovery**: `pico4-recovery-watch.service` runs the *same* script and policy as stage 1
  (`recovery-watch`, `recovery-policy.sh`) with the carried-over test deadlines; it survives
  isolation. Emergency and rescue mode log the failed units and reboot to fastboot after 30 s
  instead of waiting for a console. A crashed PID 1 reboots (`CrashAction=reboot`). A failure
  before `switch_root` returns to fastboot from stage 1. In a hardware run, stage 2 must be
  reached over SSH within the connect deadline + 120 s of the switch, or it returns to
  fastboot instead of waiting for the whole test deadline.
* **Kernel panics**: the stock kernel enables Qualcomm download mode, so a panic leaves the
  headset in crash-dump mode (USB `05c6:900e`) until someone holds the power button. Stage 1
  sets `msm_poweroff.download_mode=0` (`pico4poc.ramdump=1` keeps it), so a panic reboots
  into the POC, whose deadlines return it to fastboot.
* **Evidence**: `systemd-pstore.service` is masked, so stage-1 logs in pstore are not moved
  away. `/run/pico4-poc/boot.log` continues across the switch. journald keeps the journal in
  `/run` and copies warnings to the kernel log. `pico4-userspace-netdiag` writes USB link
  samples to pstore (pmsg), which the next stage-1 boot exports, and a timer refreshes the
  USB log drive with the journal, unit and network state every 2 minutes.
* **Identity**: the stage-1 dropbear host keys are converted for OpenSSH, so the hardware
  runner's pinned key stays valid; `/etc/machine-id` is derived from the headset serial.

Units masked because they cannot work or would interfere here: `systemd-firstboot` (would wait
for console input), `systemd-pstore`, `systemd-oomd` (needs memcg), `systemd-boot-update`,
`systemd-repart`, `steamos-dump-info` (needs the Frame's EFI tools), and the Frame's own USB
gadget units (`usb-gadget.target`, `usb-ncm-gadget@`, `usb-ncm-dnsmasq@`), which would take the
controller from the POC's link; `/tmp/usb_gadget_block` also stops the Frame's Type-C plug
handler.

## Build

On Linux/WSL with QEMU user emulation (`binfmt` handler with the `F` flag), as root, on a Linux
filesystem:

```sh
bash scripts/build-userspace.sh /var/tmp/pico4-userspace-build "$PWD/out/userspace" \
    --gpu-firmware out/graphics-overlay/lib/firmware
```

`--gpu-firmware` takes the `a650_*` files from **your own** stock backup (see
[GRAPHICS.md](GRAPHICS.md)); without them the userspace still boots, but Vulkan cannot start.
`--sensor-data` (optional) adds the sensor DSP firmware and registry for the head IMU, extracted
from your own backup by `scripts/extract-sensor-data.sh` (see [XR.md](XR.md#build)).
The first run compiles Mesa under emulation (about half an hour on a 20-thread PC). Outputs:
`out/userspace/pico4-userspace.tar.zst` and `pico4-userspace.manifest.json`.

The stage-1 image needs the handoff tools (GNU tar, zstd, e2fsprogs, dropbear-convert), which
are part of the default build now: `python build.py build --firmware <your build>`.

## Run

Interactively, with stage 1 running:

```sh
ssh root@172.16.42.1 pico4-userspace receive < out/userspace/pico4-userspace.tar.zst
ssh root@172.16.42.1 pico4-userspace switch
ssh root@172.16.42.1 pico4-userspace-check          # after ~1 minute; prints JSON
```

As a bounded hardware acceptance run, which restores PICO OS afterwards:

```sh
python hardware_test.py flash-run --serial SERIAL --confirm-device SERIAL --backup BACKUP \
    --image out/pico4-poc-5.13.7-global-seko.img --userspace out/userspace/pico4-userspace.tar.zst
```

The runner streams the archive over the pinned SSH connection, switches, waits for SSH from
systemd as PID 1, runs `pico4-userspace-check`, collects the journal, unit list, boot timing and
kernel log, and repeats the stage-1 storage and identity assertions. With GPU firmware in the
manifest, the Turnip/KGSL Vulkan readback must also pass; with sensor data, the head IMU stream
check (`pico4-imu stream --check`, [XR.md](XR.md#run)) must pass as well. The usual restoration
follows.

## Verification

### QEMU: stage 1 → userspace on a stock-like 4.19 kernel

```sh
test/qemu/build-test-kernel.sh 4.19.325                       # once; writes cache/qemu/Image-4.19
test/qemu/run-userspace.sh out/pico4-poc-5.13.7-global-seko-initramfs.cpio.gz
```

The VM boots the exact stage-1 initramfs of the headset image. A GPT partition holds the
archive; `pico4poc.userspace_payload=` makes stage 1 run the same `pico4-userspace receive`
and `switch` used over SSH. In stage 2, `pico4-userspace-selftest.service` runs the
acceptance check and powers off. Kernel: mainline 4.19.325 with
[kernel-4.19.fragment](test/qemu/kernel-4.19.fragment) (no devtmpfs, `FHANDLE`, PID/IPC/UTS/user
namespaces, memcg, SysV IPC, binfmt_misc, hidraw, VT), `-cpu cortex-a76`, 4 vCPUs, 4 GiB, TCG.

Result on 2026-10-17 (archive SHA-256 `dc0796a82b3e0527eb391d924a6f3617b53bbc7cdd945e0cd830e4c97b7ec71f`,
383,874,179 bytes, 1,416 MiB installed):

| Check | Result |
| --- | --- |
| Receive into zram, SHA-256, systemd runs in the new root | passed: 1,496 MiB on ext4, 630 MiB of RAM (LZO), `systemd 257 (257.7-2.2-arch)` |
| Handoff | busybox init → `switch_root` → systemd as PID 1, taint `old-kernel` only |
| System state | `running`, **0 failed units**, `multi-user.target` reached |
| `/dev` without devtmpfs | tmpfs; every kernel device in `/sys/dev` has a node; 214 devices in the udev database |
| Storage | root `/dev/zram0`; every physical block device read-only |
| logind / D-Bus / sshd | seat0 present; system bus up; sshd active |
| Frame packages | `steamos-customizations-deckard` 20260823.1-2, `steamos-manager` 26.4.1-2, gamescope 3.16.28-1 |
| Host-side validation | `tools/userspace.validate_check` accepts the JSON |
| Stage-1 regression ([run-selftest.sh](test/qemu/run-selftest.sh), 6.18 kernel) | 23/23 checks with `pico4-init` and the handoff tools |
| Handoff with the USB log drive, final archive | passed: USB link unchanged, no stage-1 process left, mass-storage thread alive |
| Host unit tests | 86 passed |

Problems found and fixed on the way, each by the build's own checks or this test:

* "Frame repository first" could not resolve (Frame `ffmpeg`/`freerdp` need older sonames).
* Valve's Mesa needs `libdisplay-info.so.1`/`libsensors`; gamescope links an undeclared
  `libXxf86vm` (both found by the `ldd` gate).
* SteamOS's `home.mount` waits 90 s for the Frame's `/dev/disk/by-partsets/shared/home` and
  fails `basic.target`; replaced by a self-bind. The Frame's session user is `steamos`
  (UID 1000), which its tmpfiles rules require; the offload directories are created in the image.
* `steamos-dump-info.service` fails without the Frame's `steamos-bootconf`; masked.

Not covered by QEMU, and remaining observations:

* **KGSL, the SDE display driver, PICO's USB controller and `PARANOID_NETWORK`** exist only in
  PICO's kernel. The Vulkan check is skipped in QEMU (no `/dev/kgsl-3d0`); on the headset it
  is required whenever the manifest lists GPU firmware.
* `systemd-modules-load` asks for `hid_nintendo`, `hid_playstation` and `ntsync` (Frame module
  list); the stock kernel has none of them and only loads PICO-signed modules. Two Frame
  sysctls do not exist on 4.19. Both are logged, not failures.
* In QEMU, `serial-getty@ttyAMA0` timed out waiting for its device unit and was skipped (the
  emulated boot is slow: `multi-user.target` at 247 s, about 170 s after the switch). The
  headset has no serial console; the stage-2 log is the journal and the kernel log.
* Graphical session: gamescope, Xwayland and the Steam client are installed but not started.
  gamescope's DRM backend does not know the PICO's two-eye plane routing or the fence contract
  from step 3, and Steam needs the decisions listed under Kernel compatibility. That is the
  compositor work of step 5/6.

### Headset

Original PICO 4, PICO OS 5.13.7 (global seko), stock kernel `4.19.81-perf+`, 2026-10-17.
Unattended `flash-run --userspace` with the engineering ABL staged through EDL
(`adb reboot edl`, no buttons), all eleven phases passed; archive SHA-256
`19a8d36b9cda233d5d5c5030290bdf6f6c35eb1567a988a48544ecdd581989ef` (384,202,143 bytes):

| Check | Result |
| --- | --- |
| Stage 1 (shell, read-only storage, HTTP, report) | passed, as in step 2 |
| Archive over USB NCM into zram | 92 s for 366 MiB, SHA-256 verified on the headset |
| Handoff | SSH from systemd as PID 1 about 21 s after the switch, same pinned host key and run identity |
| systemd 257.7 | `running`, **0 failed units**, taint `old-kernel`; `multi-user.target` 91 s after the switch |
| `/dev` without devtmpfs | every kernel device has a node; 1,622 devices in the udev database |
| Storage | root `/dev/zram0`; every physical block device read-only; stage-1 storage assertions repeated in stage 2 |
| USB link, logind | `usb0` unchanged (name, MAC, address, carrier); seat0 |
| **Vulkan through Turnip/KGSL (glibc)** | **Adreno 650, all 65,536 pixels verified, 0 bad** |
| Memory | 6.5 of 7.7 GB available; the 1.5 GB root uses about 0.8 GB of RAM in zram |
| Recovery | stock boot flashed back by the runner, matching PICO OS verified; afterwards full 96 MiB boot readback identical to the backup, stock ABL written and verified by EDL readback, normal PICO OS boot |

Evidence (private, not versioned): `out/userspace-cycle-20261017-211413/` with the runner's
`result.json`, `userspace-check.json`, `userspace-journal.txt`, `-units.txt`, `-analyze.txt`
and `-dmesg.txt`. The journal's errors are the stock kernel's own early-boot messages
(PMIC/SPMI probes, display PLL debugfs) and the expected missing Frame modules and sysctls.

How it got there, for the record:

1. The first headset runs booted systemd (its recovery service returned the headset to
   fastboot on time) but lost the USB network after the switch; one ended in a kernel panic
   that parked the headset in Qualcomm crash-dump mode. Hence the crash-dump setting above.
2. Bisecting on the headset (minimal target, without networkd) showed the NCM carrier dropping
   while the UDC stayed `configured`, independent of any network daemon. Killing only the
   mass-storage thread in stage 1 reproduced it: busybox init's `kill(-1)` was the cause.
   `pico4-init` replaced it.
3. The first `pico4-init` selected processes with `[ -s /proc/PID/cmdline ]`, which is always
   false for `/proc` files; stage-1 dropbear survived, blocked sshd's port and answered with a
   new host key. Selecting by `/proc/PID/exe` fixed it; the check now asserts it.

Iterations kept the POC image on the boot partition and streamed new archives over SSH, with
device-side deadlines returning the headset to fastboot after a failure; stock boot and ABL
were restored once at the end. A run needs `--windows-ncm-python` on Windows hosts, as in
[HARDWARE-TEST.md](HARDWARE-TEST.md#windows-ncm-initialization).
