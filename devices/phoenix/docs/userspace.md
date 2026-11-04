# SteamOS-compatible ARM64 userspace

Roadmap step 4 ([roadmap.md](roadmap.md)): the Steam Frame's ARM64 SteamOS userspace (systemd,
SteamOS services, gamescope, PipeWire, NetworkManager) on **Valve's kernel from the Frame**
([kernel-feasibility.md](kernel-feasibility.md)), with the PICO's own drivers and device tree. The
stage-1 initramfs ([stage1.md](stage1.md)) stays the recoverable entry point:
it receives the userspace into compressed RAM and hands PID 1 to its systemd. Flash storage
stays read-only throughout. The userspace reaches `running` with no failed units on the headset, where Valve's Mesa runs
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

### Valve's Mesa

The system's Mesa is Valve's `deckard-mesa-linux-aarch64`
([kernel-feasibility.md](kernel-feasibility.md#valves-mesa)): Turnip on msm and OpenGL through zink.
It has no KGSL backend, so the image no longer drives the GPU on the stock kernel.
`pico4-graphics-tests` carries the Vulkan and KMS tests of [graphics.md](graphics.md), built for
glibc.

### Project packages

| Package | Content | Details |
| --- | --- | --- |
| `pico4-graphics-tests` | the native graphics tests | [graphics.md](graphics.md) |
| `pico4-sensors` | hexagonrpcd with this project's patches, for the DSPs | [xr.md](xr.md) |
| `pico4-monado` | Monado 25.1.0 with the PICO 4 driver, the KMS compositor target and `pico4-cam` | [xr.md](xr.md) |
| `pico4-basalt` | Basalt for Monado's VIT interface, built for `armv8.2-a` | [xr.md](xr.md) |
| `pico4-swapchain-usage` | the stock path's frame pacing and compressed scanout in SteamVR's compositor | [session.md](session.md) |

External sources are pinned by commit or hash; the project's patches lie next to each PKGBUILD,
its own sources in [src/](../src/).

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
- **Battery.** The charger and fuel gauge (PM8150B) appear as power supplies:
  `battery` (capacity, status, voltage, current), `usb` (USB-PD, the cable), `pc_port`, `dc`
  and `bms`. UPower reads them; it is D-Bus activated. The kernel's `status` says
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
  `deckard-mesa-linux-aarch64` installed;
- with msm's render node present, the Turnip
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

The kernel's ath11k drives the QCA6390 with PICO OS's firmware and the headset's board data, and
NetworkManager uses the Frame's iwd backend. [`pico4-wlan-address`](../userspace/rootfs/usr/libexec/pico4-poc/wlan-address)
sets `wlan0`'s address from `persist` before NetworkManager starts. It joins WPA2 and WPA3 networks and
reconnects by itself after a reboot ([kernel-feasibility.md](kernel-feasibility.md#results)).

## Audio

The card is mainline's SM8250 machine driver with the PICO's routing in the
device tree ([kernel-feasibility.md](kernel-feasibility.md)): the two WSA8810 speakers on the WSA
macro's SoundWire (units 1 and 2), the WCD9380 on the RX and TX SoundWire buses (units 4 and 3),
the LPASS macros and the DSP's APR services (AFE, ASM, ADM). The card is named `PICO4`. Its
bring-up:

1. `pico4-adsp` boots the ADSP through remoteproc (`remoteproc-boot`).
2. `pico4-audiopd` starts the audio process on upstream FastRPC
   ([`audiopd`](../userspace/rootfs/usr/libexec/pico4-poc/audiopd), `/dev/fastrpc-adsp`):
   `hexagonrpcd -P` creates it with `FASTRPC_IOCTL_INIT_CREATE_STATIC`, and the driver backs it
   with the stock remote heap (12 MiB of CMA), which it gives to the DSP's VMs (LPASS,
   ADSP heap) as the stock tree does.
3. [`pico4-lpass-macros`](../userspace/rootfs/usr/libexec/pico4-poc/lpass-macros) probes the VA
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
GPU memory about 0.7 GB, and the page cache about 2.7 GB.

- **Swap as on the Frame.** The image installs the Frame's `holo-zram-swap`: zram-generator
  makes `zram0` a swap device of half the RAM, compressed with `lzo-rle`, with zswap off. PICO OS swaps to zram the same way
  (`init.qcom.post_boot.sh`: half the RAM, at most 4 GiB). GPU memory cannot be swapped.
- **Priority 32758.** The swap device has the priority PICO OS's script uses.
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
cat /proc/pressure/memory                  # memory pressure (PSI)
```

## Verified results

Current results are in [kernel-feasibility.md](kernel-feasibility.md#results).

### Limits and known messages

- The Steam client starts with Valve's session, signs in and runs games ([session.md](session.md#the-steam-client)).
