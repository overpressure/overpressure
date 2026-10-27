# Stage 1: the bootable Linux image

Stage 1 is a standard Android boot image for the original PICO 4 (A8110, *phoenix*): the stock
PICO kernel (`4.19.81-perf+`) and device trees byte for byte, with the ramdisk replaced by
Alpine Linux 3.24 (aarch64) and the project's init from [initramfs/](../initramfs/). Over the
USB-C cable it offers a network link with SSH, telnet and a status web page, and a read-only USB
drive with the boot logs. Every disk and partition of the headset is read-only while it runs,
except what the installed system's tools write on purpose ([install.md](install.md)).
Stage 1 runs on the tested headset (original PICO 4, PICO OS 5.13.7), returns to fastboot by
itself when no PC uses it, and hands PID 1 over to the SteamOS-compatible userspace of stage 2
([userspace.md](userspace.md)).

## The image

| Part | Content |
| --- | --- |
| Kernel and device trees | the stock image's, byte for byte, with every other header field (load addresses, page size, OS version and patch level) |
| Kernel command line | the stock one plus `printk.devkmsg=on`, so the init's lines in `/dev/kmsg` are not rate-limited, and `rng_core.default_quality=1000`, so the Qualcomm hardware RNG credits entropy and dropbear's first host-key generation cannot block |
| Ramdisk | Alpine Linux 3.24.2 aarch64: the minirootfs, the packages listed in [tooling/image.py](../tooling/image.py) (BusyBox extras, dropbear, an SFTP server, mtools, exploration tools and the stage-2 handoff tools GNU tar, zstd, e2fsprogs, dropbear-convert), the overlay in [initramfs/](../initramfs/) and the `pico4-reboot` helper |
| PID 1 | `pico4-init`, which runs `/etc/inittab` like busybox init ([PID 1](#pid-1)) |

Over the USB-C cable:

| Function | What it offers |
| --- | --- |
| Network (CDC NCM) | the headset at `172.16.42.1/24`; DHCP gives the PC `172.16.42.2` and no router or DNS, so the PC keeps its internet route. SSH with SFTP, telnet, HTTP |
| Log drive (mass storage) | a read-only FAT drive `PICO4POC` with the boot logs and the hardware report, readable without any network driver |
| Serial shell (CDC ACM) | only on kernels with ACM; the stock kernel has none |

## Prerequisites

* An original PICO 4, PICO 4 Pro or PICO 4 Enterprise (A8110, *phoenix*) with an unlocked
  bootloader and the engineering ABL staged, as described in
  [device-preparation.md](device-preparation.md). That workflow independently implements the
  approach documented by [more-picohaxx-tool](https://github.com/chaixshot/more-picohaxx-tool),
  without bundling that repository or its binaries. Unlocking wipes user data. Unlock
  preparation is limited to the upstream reported range through PICO OS 5.13.8, which is not a
  hardware compatibility guarantee.
* Android platform-tools (`adb`, `fastboot`) and an SSH client on the PC.
* Python 3.9+ (standard library only) on Windows, Linux or macOS. [development.md](development.md)
  lists what the other builds and the QEMU tests need.

## Preflight on the unmodified headset

Optional, and useful before any bootloader work. With USB debugging authorized (the bootloader
may still be locked), after a build:

```sh
python -m overpressure hwtest preflight        # the newest rootfs tar in out/, or --rootfs TAR
```

The preflight runs the image's own binaries on the real PICO kernel from `adb shell`, without
root, through the image's musl loader: BusyBox (shell, arithmetic, fractional sleep, md5sum,
awk), the DHCP, telnet and web servers, dropbear and host-key generation, mtools building a FAT
image, and the other tools. It prints what stage 1 relies on (USB device controller, LED names,
DRM and backlight devices, CPU governors and idle state, pstore, kernel options) and the
headset's fingerprint and OEM state for `python -m overpressure match`. It uses a directory under
`/data/local/tmp` and removes it afterwards. Results go to a new `out/hardware-<id>/` directory;
a failed check gives a nonzero exit status. `--serial` selects the headset when several are
attached (`-Serial` for the PowerShell script, the second argument of the shell script). The
preflight exists because the PICO community's Magisk port had to replace its bundled BusyBox to
run on this kernel.

## Building

```sh
python -m overpressure build --firmware 5.13.7-global-seko
```

Pick the firmware entry that matches the headset (with PICO OS running):

```sh
adb shell getprop ro.build.fingerprint   # e.g. Pico/Phoenix_ovs/PICOA8110:10/5.13.8/smartcm.1788408867:user/dev-keys
adb shell getprop ro.oem.state           # true = "seko" build, false = "sek" build
python -m overpressure match --fingerprint "Pico/Phoenix_ovs/..." --oem-state false
```

[tooling/firmware.json](../tooling/firmware.json) lists PICO's official full OTAs: 5.13.3 (global),
5.13.7 (global and CN) and 5.13.8 (global), each as sek and seko build. The same OTA serves
PICO 4, PICO 4 Pro and PICO 4 Enterprise. `match` returns the entry with the same fingerprint,
or else the one with the same version, region (`_ovs` = global) and OEM state, with a note that
the build number is not confirmed (`ro.pvr.internal.version` contains it as `_bNNNN_`). The
stock kernel, device trees and kernel configuration are identical across the 5.13.7 and 5.13.8
builds compared, so a close match works. An exact match is the safe choice, because the stock
boot image of the same build is what restores PICO OS after a flashed test.

Without `--firmware`, the build uses `5.13.8-global-sek`. `--stock-boot` takes a `boot.img`
(for example from the headset's own EDL backup), an OTA zip, a `payload.bin`, or a URL of
either. The first build downloads about 17 MB of the stock OTA and about 12 MB of Alpine
packages into `cache/`: only the zip's central directory and its `boot.img` member are fetched,
with HTTP range requests, and checked against the CRC-32 in the zip directory.

[tooling/image.py](../tooling/image.py):

1. Gets the stock `boot.img` from a file, an OTA zip or a URL ([tooling/firmware.py](../tooling/firmware.py)).
   PICO 4 OTAs are non-A/B; full A/B OTAs with `payload.bin` work too ([overpressure/payload.py](../../../overpressure/payload.py)).
2. Parses it ([overpressure/bootimg.py](../../../overpressure/bootimg.py), header versions 0–4) and analyses the
   kernel ([overpressure/kernelinfo.py](../../../overpressure/kernelinfo.py)): version, embedded configuration
   (`IKCONFIG`), device trees. The build stops if the kernel lacks `BLK_DEV_INITRD`, `RD_GZIP`,
   `USB_CONFIGFS`, `CONFIGFS_FS` or `UNIX98_PTYS`, or has none of the NCM, RNDIS and ECM gadget
   functions. `--force` overrides these checks, except `RD_GZIP`.
3. Assembles the root file system in memory ([overpressure/cpio.py](../../../overpressure/cpio.py)), so device
   nodes, modes and ownership are right on Windows too:
   * the Alpine minirootfs, checked against its published SHA-256;
   * the packages of `PACKAGES` in `tooling/image.py` with their dependencies, each verified
     through Alpine's checksum chain: APKINDEX, SHA-1 of the control segment, SHA-256 of the
     data segment ([overpressure/alpine.py](../../../overpressure/alpine.py)). Each APKINDEX's RSA signature is
     verified against Alpine's aarch64 signing keys pinned in
     [overpressure/apksign.py](../../../overpressure/apksign.py); an unsigned index, an unknown key or a bad
     signature stops the build and drops the index from the cache;
   * kernel modules from the stock ramdisk's `lib/modules` (current firmware has none);
   * the [initramfs/](../initramfs/) overlay;
   * `pico4-reboot` and `pico4-splash` from [prebuilt/aarch64/](../prebuilt/aarch64/);
   * static device nodes (`console`, `null`, `zero`, `kmsg`, `tty`, `ptmx`, `random`,
     `urandom`): the kernel opens `/dev/console` before `/init` runs, and there is no devtmpfs;
   * a root account without password, the host name `pico4-poc` and the build record
     `/etc/pico4-poc/build-info`.
4. Writes a gzip-compressed newc cpio, extends the command line and repacks the image with every
   other stock header field. It parses its own output and checks kernel, device trees, header
   version, page size, OS version, load addresses, the header ID and an executable `/init`
   before writing to `out/`.

| Output in `out/` | Use |
| --- | --- |
| `pico4-poc-<firmware>.img` | the boot image |
| `pico4-poc-<firmware>-initramfs.cpio.gz` | the bare ramdisk, for the QEMU tests |
| `pico4-poc-<firmware>-rootfs.tar` | the root file system without device nodes, for the preflight |
| `pico4-poc-<firmware>.json` | build record: image SHA-256 and size, command line, package versions, analysis of the stock image |
| `stock-boot-<firmware>.img` | the untouched stock boot image of the same OTA |

| Option | Effect |
| --- | --- |
| `--firmware NAME`, `--stock-boot PATH` | source of the stock image (see above) |
| `--cmdline-append ARG...` | extra kernel arguments ([options](#kernel-command-line-options)) |
| `--minimal` | only the minirootfs and the overlay |
| `--name NAME`, `--out DIR` | output base name (default `pico4-poc-<firmware>`) and directory (default `out/`) |
| `--force` | build even if the kernel checks fail |

`python -m overpressure inspect` (with `--firmware` or `--stock-boot`) prints the same analysis
without building and writes `out/inspect/<firmware>/`: `analysis.json`, the kernel's
`kernel.config`, and `userspace-compat.json`, the kernel's answers to what systemd and the
SteamOS userspace need ([userspace.md](userspace.md)).

With `SOURCE_DATE_EPOCH` set, the image is byte-reproducible from the same downloads, on
Windows and Linux alike; without it, only the build time in `/etc/pico4-poc/build-info` differs
between builds. The build warns when the image is larger than the stock partition image; such
an image can only be booted from RAM.

`pico4-reboot` ([source](../src/reboot/pico4-reboot.c)) is a freestanding aarch64 program
without libc, about 1.5 KB. BusyBox's `reboot` cannot pass a command string to the kernel, and
that string is how the Qualcomm restart driver chooses the next boot target (fastboot,
recovery). Any aarch64 cross compiler rebuilds it:

```sh
aarch64-linux-gnu-gcc -Os -static -nostdlib -ffreestanding -fno-stack-protector \
    -fno-asynchronous-unwind-tables -Wl,--build-id=none -s \
    -o prebuilt/aarch64/pico4-reboot src/reboot/pico4-reboot.c
```

`pico4-splash` ([source](../src/splash/pico4-splash.c)), the installed system's boot indication
in the lenses ([install.md](install.md)), is freestanding the same way (about 9 KB), so stage 1
and stage 2 run the same file:

```sh
aarch64-linux-gnu-gcc -Os -static -nostdlib -ffreestanding -fno-stack-protector \
    -fno-asynchronous-unwind-tables -fno-builtin -Wall -Wextra -Werror \
    -Wl,--build-id=none,-z,max-page-size=4096 -s \
    -o prebuilt/aarch64/pico4-splash src/splash/pico4-splash.c
```

## Booting

Stage 1 boots from a partition: the development loop writes the dev image to `recovery` (the
maintenance stage 1) or to `boot` with `python -m overpressure dev flash`, and the acceptance
runner and cycle write a test image to `boot` and restore the backup's image after their run
(`python -m overpressure hwtest flash-run`, `python -m overpressure cycle`,
[hardware-testing.md](hardware-testing.md)). The signed Neo3 engineering ABL used on the
PICO 4 rejects `fastboot boot` with an unknown-command error after the image transfer, even
after persistent normal and critical unlock, so there is no RAM boot.

Back to PICO OS: `python -m overpressure dev restore` ([hardware-testing.md](hardware-testing.md)).
`pico4-reboot bootloader` in stage 1 returns to fastboot for the next attempt.

### Writing boot by hand

The tools above check the headset's verified backup and the image before writing and restore
the original boot image afterwards. The equivalent manual sequence replaces the only Android boot partition (the PICO 4 has no A/B slots), so PICO OS cannot start
until the original image is flashed back. Keep the engineering ABL and a working EDL recovery
path ([device-preparation.md](device-preparation.md)) available:

```sh
fastboot oem <token> unlock               # engineering ABL: authorizes writes in this session
fastboot flash boot out/pico4-poc-5.13.7-global-seko.img
fastboot reboot                           # every normal boot now starts stage 1
# back to PICO OS: into fastboot (pico4-reboot bootloader in stage 1, or its 5-minute
# auto-reboot when no PC answers), then:
fastboot oem <token> unlock
fastboot flash boot out/stock-boot-5.13.7-global-seko.img
fastboot reboot
```

`python -m overpressure device token CHIP_ID` prints the `oem` command for the headset's SoC ID, which
`python -m overpressure device probe` records. It is the engineering ABL's per-session command
authorization, not `flashing unlock`, and resets nothing. `out/stock-boot-<firmware>.img` is the
untouched image from the same OTA: flash back the one that matches the firmware on the headset,
or the boot image of the headset's own EDL backup. The sequence writes only `boot`; recovery and
the bootloader are separate partitions.

## Connecting

About 20–40 s after the boot the headset answers on the USB link:

| How | Command |
| --- | --- |
| SSH (Windows has a client built in) | `ssh root@172.16.42.1`, empty password |
| Telnet (Windows: the optional client, or PuTTY) | `telnet 172.16.42.1` |
| Browser: status, logs, report, probe and reboot buttons | <http://172.16.42.1/> |
| Copy files (SFTP server included) | `scp root@172.16.42.1:/run/pico4-poc/report.tar.gz .` |
| Logs without any network | the USB drive `PICO4POC` on the PC |
| Serial shell, on kernels with CDC ACM | the gadget's serial port on the PC (`/dev/ttyACM0` on Linux); any login name, no password |

dropbear runs with `-R` (host keys are generated at the first connection, so they are new on
every boot), `-B` (the empty root password is accepted) and `-W 4194304`, a 4 MiB receive
window: with dropbear's 24 KiB default, uploads over the USB link, such as the userspace
archive, are limited to about 4.4 MB/s; with the 4 MiB window the 801 MiB userspace archive
arrives in 39 s (21.6 MB/s). Images that the hardware runner or the development loop prepare also carry an
`authorized_keys` entry for the runner's per-run key or the loop's key
([hardware-testing.md](hardware-testing.md)).

The web server is BusyBox `httpd` on port 80 (`/srv/www`):

| Path | Content |
| --- | --- |
| `/` | status, refreshed every 5 s; links; buttons to rerun the probe and to reboot into PICO OS, fastboot or recovery |
| `/cgi-bin/status` | `pico4-info` as text |
| `/cgi-bin/log?boot`, `?dmesg`, `?services`, `?pstore` | `boot.log`, kernel log, service logs (last 200 lines each), pstore contents from the previous boot |
| `/report/`, `/cgi-bin/report` | the hardware report, browsable and as `report.tar.gz` (collected first if missing) |
| `POST /cgi-bin/probe` | rerun `pico4-probe` |
| `POST /cgi-bin/reboot`, body `target=android`, `bootloader` or `recovery` | reboot after 2 s |

The log drive is a 32 MiB FAT image in RAM, exported read-only and removable:

| File | Content |
| --- | --- |
| `README.txt` | how to connect |
| `boot.log` | what `/init` and the USB setup did |
| `dmesg.txt`, `cmdline.txt`, `build-info.json` | kernel log, kernel command line, build record of the image |
| `report.tar.gz` | hardware report, once the probe has run |
| `svc-*.log`, `usb.state`, `usb.link` | service logs, gadget and link state |
| `pstore/` | logs the kernel kept from the previous boot |

The drive is rebuilt after the probe and swapped in as a media change. When the PC holds the
medium, eject and replug it to see the newer files.

The USB device is a composite gadget `1d6b:0104` (Linux Foundation Multifunction Composite
Gadget, device class `EF/02/01` with interface association descriptors for Windows), product
`PICO 4 Linux POC`. Its serial number is `androidboot.serialno` from the kernel command line,
normally the headset's fastboot serial (the ADB serial under PICO OS can differ). The network
function is the first one in `pico4poc.usbnet` that the kernel provides. Its MAC addresses are
locally administered and derived from the serial number, so the PC sees the same adapter on
every boot. If the controller rejects the set of functions, the setup binds again without the
serial function, then without the log drive.

## What happens during boot

| Step | What happens | Status LED |
| --- | --- | --- |
| 1 | [/init](../initramfs/init) mounts proc, sysfs, `/run` and `/tmp`, fills `/dev` with `mdev` (the stock kernel has no devtmpfs) and keeps `mdev` running for hotplug; mounts devpts, configfs, pstore and debugfs | |
| 2 | every disk and partition becomes read-only (`blockdev --setro`) as soon as it appears; `/dev/block/by-name/*` links as on Android | blue |
| 3 | modules listed in the stock ramdisk's `modules.load` are loaded (current firmware has none) | |
| 4 | USB gadget via configfs: NCM network, mass-storage log drive, ACM serial if the kernel has it | amber once bound |
| 5 | `usb0` = `172.16.42.1/24`; `udhcpd` gives the PC `172.16.42.2` | green when a PC configures the device |
| 6 | CPU power management as PICO OS's post-boot script does it: cpufreq governor `schedutil`, deep idle states enabled after 30 s, Qualcomm crash-dump mode off | |
| 7 | `pico4-init` becomes PID 1 and starts `udhcpd`, `telnetd`, `dropbear`, `httpd`, the serial getty, the USB link watcher and the recovery watchdog | |
| 8 | `pico4-probe` collects a hardware inventory 15 s after the USB setup and refreshes the log drive | |

Steps 4 to 8 run concurrently. The stock kernel boots with the `performance` governor and with
deep idle states disabled (`lpm_levels.sleep_disabled=1` on its command line); without step 6
every core would stay at its maximum clock. Firmware requests fall back to userspace on this
kernel; `/init` lets them fail after 5 s instead of waiting 60 s for Android's `ueventd`, which
is absent. The USB setup waits up to 60 s for a device controller.

The LED column applies when the kernel exposes LEDs named red, green and blue. Only those are
driven, never flash, torch, IR or backlight LEDs. A failed USB setup shows blinking red.

Every step is logged to the kernel log (lines start with `pico4-poc:`), to
`/run/pico4-poc/boot.log` and to pstore's `pmsg`, which survives a warm reboot. Services log to
`/run/pico4-poc/svc-<name>.log`.

### PID 1

[pico4-init](../initramfs/usr/libexec/pico4-poc/pico4-init) runs the `::respawn:`,
`::shutdown:` and `::restart:` entries of [/etc/inittab](../initramfs/etc/inittab) like busybox
init, but its stop signals reach only user processes (those with an executable), never kernel
threads. busybox init stops everything with `kill(-1)`, which also reaches the kernel thread of
the USB mass-storage function that serves the log drive; that thread exits on SIGTERM or
SIGKILL, the PC then resets the composite device, and the NCM link is lost. Each service runs in
its own respawn loop through [svc](../initramfs/usr/libexec/pico4-poc/svc), which waits for the
service's prerequisites and rate-limits restarts.

| Signal to PID 1 | Effect |
| --- | --- |
| SIGQUIT | stop, then run the `::restart:` entry: `switch-userspace` hands PID 1 to stage 2's systemd, or restarts stage-1 init when no userspace is prepared |
| SIGTERM | stop, reboot |
| SIGUSR1, SIGUSR2 | stop, then halt or power off |

## Recovery behaviour

* **Watchdog.** `recovery-watch`, independent of the USB setup, reboots into fastboot after
  300 s (`pico4poc.autoreboot`) unless a packet from the PC has arrived on the USB network or a
  login shell has started (SSH, telnet or serial: the shell profile creates
  `/run/pico4-poc/no-autoreboot`). USB enumeration and carrier alone do not count.
* **Test images.** Images that the hardware runner prepares use its deadlines instead: the
  runner must authenticate over SSH within the connection deadline (network traffic and login
  shells do not count), and a maximum runtime returns the headset to fastboot even after
  authentication. After a switch to stage 2, the runner must reach it within the connection
  deadline plus 120 s. These limits apply even when the image disables the watchdog
  ([hardware-testing.md](hardware-testing.md)). On the tested headset, a test image whose USB
  link is configured but unusable returns to fastboot this way, and the runner restores PICO OS
  without a button press.
* **USB re-plug.** If the link is configured but no packet has arrived from the PC for 20 s
  (`pico4poc.usbkick`), the USB link watcher unbinds the gadget from the controller and binds it
  again, at most twice per boot and never after the first received packet. It then re-applies
  the address and restarts `udhcpd`. A PC whose side of the link is up sends packets at once
  (DHCP, IPv6 router solicitations). In QEMU, the PC side intermittently (about one run in
  three) misses NCM's "connected" notification and shows the cable as unplugged; a re-plug
  fixes that. Unbind and rebind is what Android's init does when it switches USB functions;
  toggling the controller's `soft_connect` instead leaves the QEMU test controller unable to
  enumerate the device.
* **Kernel panics.** The stock kernel enables Qualcomm download mode, so a panic would leave the
  headset in crash-dump mode (USB `05c6:900e`) until the power button is held. Stage 1 sets
  `msm_poweroff`'s `download_mode` to 0, so a panic reboots; a flashed stage 1 then starts again,
  and its deadlines return the headset to fastboot. `pico4poc.ramdump=1` keeps crash-dump mode.
* **Limits.** A kernel hard hang or loss of host power can still need a hardware reset (power
  button held for more than 10 s) or EDL recovery.

## Kernel command-line options

Add them at build time, e.g.
`python -m overpressure build --cmdline-append pico4poc.autoreboot=0 pico4poc.cpugov=keep`. A bare
`pico4poc.NAME` means `pico4poc.NAME=1`. [lib.sh](../initramfs/usr/libexec/pico4-poc/lib.sh)
reads them; the scripts next to it use them.

| Option | Default | Meaning |
| --- | --- | --- |
| `pico4poc.usbnet=` | `ncm,rndis,ecm` | network functions to try, in order; the stock kernel has only NCM |
| `pico4poc.usbserial=` | `acm` | serial shell function, absent from the stock kernel; `gser` needs a host driver, `none` disables it. The serial getty asks for a login name first (any name, no password) |
| `pico4poc.logdisk=` | `1` | read-only USB log drive |
| `pico4poc.autoreboot=` | `300` | seconds without a usable connection before rebooting into fastboot; `0` = never |
| `pico4poc.usbkick=` | `20` | seconds of silence on a configured link before a software USB re-plug (at most 2); `0` = never |
| `pico4poc.ip=`, `pico4poc.host_ip=` | `172.16.42.1`, `172.16.42.2` | addresses on the USB link (/24) |
| `pico4poc.rw=` | `0` | `1` leaves block devices writable; the hardware runner refuses such images |
| `pico4poc.led=` | `1` | drive the status LED (only LEDs named red, green, blue) |
| `pico4poc.cpugov=` | `schedutil` | cpufreq governor; `keep` leaves the stock `performance` |
| `pico4poc.lpm=` | `1` | enable the CPU low-power idle states |
| `pico4poc.ramdump=` | `0` | `1` keeps Qualcomm crash-dump mode after a kernel panic |
| `pico4poc.probe=` | `1` | run `pico4-probe` automatically |
| `pico4poc.modules=` | `1` | load modules from the stock ramdisk (none in current firmware) |
| `pico4poc.devtmpfs=` | `1` | mount devtmpfs when the kernel has it (the stock kernel does not) |
| `pico4poc.probe_delay=` | `15` | seconds between the USB setup and the automatic probe |
| `pico4poc.lpm_delay=` | `30` | seconds before the idle states are enabled |
| `pico4poc.udc_timeout=` | `60` | seconds to wait for a USB device controller |
| `pico4poc.logdisk_mb=` | `32` | size of the log drive in MiB |

Options for the test harnesses:

| Option | Used by |
| --- | --- |
| `pico4poc.selftest=1` | the QEMU self-test ([QEMU self-test](#qemu-self-test)); with `pico4poc.selftest_hostdelay=N` the PC side stays silent for N s, with `pico4poc.selftest_poweroff=0` the VM stays on afterwards. The hardware runner refuses images with `selftest` |
| `pico4poc.userspace_payload=PART:BYTES:SHA256`, `pico4poc.userspace_selftest=1` | the QEMU userspace test: stage 1 receives the archive from a partition and switches to it ([userspace.md](userspace.md)) |
| `pico4poc.root=installed` (with `pico4poc.autoreboot=0`) | the install boot image: mount the installed system from `super` and `userdata` and switch to it; on failure, reboot into recovery ([install.md](install.md)) |
| `pico4poc.part=recovery` | the dev image written to `recovery` (the maintenance stage 1), so the dev loop can tell which partition started it |
| `pico4poc.test_run=ID` | set by the hardware runner in its per-run image |

Do not add a bare `earlycon` (without a value). According to PICO's published kernel source
([bytedance/phoenix-kernel](https://github.com/bytedance/phoenix-kernel), 4.19.81, without the
board DTS), its earlycon parser dereferences the missing argument, and the kernel panics before
anything is logged.

## Commands on the headset

| Command | Purpose |
| --- | --- |
| `pico4-info` | status summary, also the login banner |
| `pico4-probe` | hardware inventory into `/run/pico4-poc/report/` and `report.tar.gz`, also on the web page and the log drive |
| `pico4-mount-ro PART [DIR]` | mount a stock partition read-only, by default on `/mnt/PART`: ext4 with `noload` (no journal replay), f2fs with `norecovery`, vfat. The dynamic partitions inside `super` are not handled |
| `pico4-reboot [bootloader\|recovery\|poweroff]` | reboot into a mode; without argument, a normal reboot. `-r STRING` passes any other reboot reason; unknown words are refused. Aliases: `fastboot-mode`, `android` |
| `pico4-userspace receive\|switch\|status` | stage-2 handoff: unpack the userspace archive from stdin into an ext4 file system on `/dev/zram0` and check that its systemd runs on this kernel, then hand PID 1 to it ([userspace.md](userspace.md)) |
| `pico4-userspace installed` | the installed system instead: `super` read-only as the new root, `userdata` at `/data` with `/etc`, `/var`, `/home` and `/root` on it ([install.md](install.md)) |
| `pico4-install write\|format-data\|wipe\|bootmode\|status` | the installer of the maintenance stage 1: the only writer of `boot`, `super`, `userdata`, `metadata` and `misc`'s bootloader message, each against the backup's size and read back ([install.md](install.md)) |
| `drm_info`, `modetest`, `evtest`, `i2cdetect`, `gpioinfo`, `tinymix`, `lsusb`, `lspci`, `lsblk`, `dtc`, `dmsetup`, `strace`, `htop` | exploration tools |

`pico4-probe` reads procfs, sysfs and device-tree data only. It skips debugfs GPIO and clock
dumps, PCIe configuration reads and audio mixer reads, because those can hard-crash Qualcomm
SoCs; run such tools by hand, knowing the risk. Its progress also goes to pstore, so a crash
during the probe can be located.

Three details keep a PC terminal that echoes text back from running commands on the serial
console: the getty asks for a login name first, so echoed text lands in that prompt; every line
of `pico4-info`, the login banner, starts with `#`; and `pico4-reboot` refuses unknown words.

## Troubleshooting

* **The PC sees a "PICO 4 Linux POC" device but no network adapter (Windows).** Windows 11 ships
  `UsbNcm.sys` as the in-box class driver for CDC NCM (class 02h, subclass 0Dh) and should load
  it automatically ([Microsoft](https://learn.microsoft.com/en-us/windows-hardware/drivers/usbcon/supported-usb-classes)).
  If it does not, use Device Manager: *Update driver → Browse my computer → Let me pick →
  Network adapters → Microsoft → UsbNcm Host Device*. Windows 10 has no NCM driver, and the
  stock kernel has no RNDIS function; there, use the log drive, a Windows 11 or Linux PC, or a
  Linux VM with USB passthrough.
* **Windows has an NCM adapter, but no DHCP or SSH, and the kernel log shows `Wrong NDP SIGN`.**
  The stock kernel's NCM function rejects the packets of Windows' driver. Sending the CDC
  request `SET_CRC_MODE(0)` through UsbDk and handing the device back to UsbNcm makes DHCP, SSH
  and HTTP work without changing the kernel. [overpressure/windows_ncm.py](../../../overpressure/windows_ncm.py)
  does this; the hardware runner (`--windows-ncm-python`) and `python -m overpressure dev connect` run
  it after every stage-1 boot ([hardware-testing.md](hardware-testing.md) has the setup). Leave
  the adapter on DHCP. UsbDk briefly disconnects the whole composite device, including the log
  drive. Run it once per stage-1 boot: running it again after the switch to stage 2 stalls
  UsbDk and leaves the PC's NCM and storage drivers in an error state. Tested with Windows 11,
  UsbDk 1.0.22 and the `windows-ncm` extra of [pyproject.toml](../../../pyproject.toml) (PyUSB 1.3.1,
  libusb-package 1.0.30.0).
* **No network, but a PICO4POC drive appeared.** `boot.log` on it shows what the USB setup did.
  The drive is rebuilt after the probe; eject and replug it to see the newer files.
* **Nothing appears on the PC.** After 5 minutes the watchdog reboots into fastboot. The next
  stage-1 boot shows the previous boot's logs from pstore (`pstore/` on the log drive,
  `/cgi-bin/log?pstore`); under PICO OS they are in `/sys/fs/pstore/console-ramoops*` and
  `pmsg-ramoops*` (readable by root only). Every stage-1 line starts with `pico4-poc`.
* **`check-unlocked` fails although the bootloader is unlocked.** The stock ABL reports the
  state as `DeviceUnlocked=true`, which the tools do not accept; stage the engineering ABL
  ([device-preparation.md](device-preparation.md)).
* **SSH warns about a changed host key.** Stage 1 creates new host keys on every boot;
  `ssh-keygen -R 172.16.42.1` removes the old entry.
* **The headset is stuck.** Hold the power button for more than 10 s; that is a hardware reset.
  The next boot starts what `boot` holds: stage 1 while it is flashed, PICO OS after the
  restore.

## Safety notes

* The PICO 4 is not an A/B device: `fastboot flash boot` replaces the only boot partition, and PICO OS does not start until the original image is
  back. Copies of the stock image: `cache/stock/*.boot.img` (the build's download),
  `out/stock-boot-<firmware>.img` and the boot image in the headset's own EDL backup.
* Stage 1 switches every disk and every partition to read-only individually: the 4.19 kernel
  tracks a partition's read-only flag separately from its disk's. Newer kernels also treat a
  partition as read-only while its disk is, so making a partition writable there takes
  `blockdev --setrw` on both.
* `persist` (sensor and camera calibration) and `fsg` (PICO VR data) are unique to each headset
  and cannot be downloaded again. Treat them as irreplaceable in every stage.
* `pico4-mount-ro` mounts stock partitions read-only with journal replay disabled, e.g. `modem`
  for firmware files.
* The shell has no password. The only network is the USB cable to one PC, and nothing is
  forwarded.

## The stock kernel as found

Read from the boot image of PICO OS 5.13.8 and the kernel's embedded configuration
(`IKCONFIG`); 5.13.7 has the same kernel, device trees and configuration.

| | |
| --- | --- |
| Boot image | header v2, page 4096, 96 MiB partition, kernel `0x8000`, ramdisk `0x1000000`, DTB `0x1f00000` (base 0) |
| Kernel | `4.19.81-perf+`, clang 8, uncompressed `Image` (35 MB), `CMDLINE_EXTEND`, `PANIC_TIMEOUT=-1` |
| Ramdisk | gzip only (`RD_GZIP`); the stock one is Android 10's first-stage init with `fstab.qcom` and no modules, so no `skip_initramfs` patch is needed |
| DTB | 7 trees: kona v1/v2/v2.1, kona-iot v1/v2/v2.1, RTIC; identical in 5.13.7 and 5.13.8 |
| `/dev` | no `DEVTMPFS`, hence mdev; `UEVENT_HELPER=y`; firmware loading falls back to userspace (`FW_LOADER_USER_HELPER_FALLBACK`) |
| USB gadget | configfs with NCM, mass storage, gser, FunctionFS, UAC1/2, UVC, HID, MIDI, MTP, Qualcomm GSI; no ACM, no generic RNDIS, no ECM |
| Logs | `PSTORE_RAM`, `PSTORE_CONSOLE`, `PSTORE_PMSG` |
| Graphics | `DRM` and the KGSL GPU driver (`QCOM_KGSL`); the display driver comes from techpack (not in `IKCONFIG`); `video=vfb:640x400` on the command line ([graphics.md](graphics.md)) |
| Other | `HID_QVR` (Qualcomm VR HID), LEDs `QTI_TRI_LED` and `AW210XX`, `ANDROID_PARANOID_NETWORK`, SELinux (no policy loaded, hence permissive), cpufreq default `performance`, 39-bit VA with 4K pages |
| Modules | `MODULE_SIG_FORCE`: only modules signed with PICO's per-build key load, so new drivers mean rebuilding the kernel |
| Storage | UFS; dynamic partitions (system, vendor, product, odm in `super`); f2fs userdata with file-based encryption; separate `persist`, `fsg`, `modem`, `dsp`, `bluetooth`, `cache` |

## Verification

### Offline

* **Stock image.** `python -m overpressure inspect` downloads only the `boot.img` member of the
  official OTA zip, checks it against the zip's CRC-32 and reads the kernel's `IKCONFIG`. For
  5.13.8 global sek (b10113), CRC-32 `87016fbf` matches the zip directory, with 17 MB
  downloaded of the 3.72 GB zip.
* **Image format.** Google's `unpack_bootimg.py` parses the output: header v2, addresses, OS
  version and patch level as stock. Repacking its extracted parts with Google's `mkbootimg.py`
  reproduces the image byte for byte, except `second_addr`: that unused field keeps the stock
  value `0x00f00000`, where current mkbootimg writes 0. Kernel and DTB are identical to stock.
  The header's SHA-1 ID is computed the way mkbootimg computes it, which also reproduces the
  stock image's ID.
* **Ramdisk.** GNU cpio lists it with the expected device nodes, symlinks and modes, and the
  scripts pass `sh -n` under the image's aarch64 BusyBox.
* **Recovery policy.** `bash tests/test_recovery_policy.sh` checks the watchdog's decisions
  ([recovery-policy.sh](../initramfs/usr/libexec/pico4-poc/recovery-policy.sh)) on the host.

### QEMU self-test

[tests/qemu/](../tests/qemu/) boots the exact ramdisk on a mainline arm64 kernel (6.18.56)
configured like the stock one where it matters: no devtmpfs, no modules, a configfs gadget, 4K
pages and a 39-bit address space. `dummy_hcd` connects the gadget to a virtual host controller
in the same kernel, so the VM is both headset and PC. A 32 MiB GPT disk provides a `poc_test`
partition for the read-only checks and a `poc_export` partition that receives the probe report.

```sh
devices/phoenix/tests/qemu/build-test-kernel.sh       # once, on Linux or WSL: cache/qemu/Image
devices/phoenix/tests/qemu/run-selftest.sh            # newest out/*-initramfs.cpio.gz; prints PASS or FAIL
```

Further arguments to `run-selftest.sh` are kernel arguments: `pico4poc.usbserial=none` gives
the stock kernel's function set (NCM and log drive, no serial), and
`pico4poc.selftest_hostdelay=35` keeps the PC side silent for 35 s, which forces the re-plug
path. The console log goes to `out/qemu-console.log`, the probe report to `out/qemu-report/`.
`make qemu-stage1` runs the test for the Makefile's firmware.

The self-test checks:

* `/dev` populated by mdev without devtmpfs, the gadget bound, the headset address on its
  interface;
* the PC side's enumeration of the network function, the configured controller state, and a
  DHCP lease of `172.16.42.2` across the USB link;
* SSH, telnet and HTTP listening, SSH login as root with the empty password, the web status
  page and boot log;
* the `/dev/block/by-name` and `/dev/block/bootdevice/by-name` links, a read-only partition and
  a refused write;
* the log drive mounted from the PC side, with `boot.log` on it;
* a login shell on the USB serial port (ACM);
* the hardware report: collected, downloadable and browsable over HTTP;
* with a silent PC side, one or two re-plugs. With 35 s of silence the headset re-plugs twice,
  and DHCP, the log drive and the serial shell work afterwards. Normal runs need no re-plug.

`tests/qemu/run-userspace.sh` tests stage 1 together with the handoff to stage 2 on a
stock-like 4.19 kernel ([userspace.md](userspace.md)).

### On the headset

The hardware acceptance runner ([hardware-testing.md](hardware-testing.md)) checks stage 1 on a
prepared headset: the preflight under PICO OS, a per-run image with its own SSH key, the SSH
identity, the root shell, storage protection, HTTP diagnostics and the return to the original
PICO OS. `python -m overpressure hwtest flash-run` writes `boot` and restores it.

Results on the tested headset (original PICO 4, PICO OS 5.13.7, engineering ABL, Windows 11
host):

| Check | Result |
| --- | --- |
| Preflight under PICO OS | all checks pass |
| Kernel and userspace | stock `4.19.81-perf+`, Alpine 3.24.2; build metadata matches the image |
| Shell | SSH with the per-run key; run identifier and SoC ID match; aarch64, UID 0 |
| Storage | all 99 physical block devices (disks and partitions) read-only; root file system in RAM; no device file system mounted writable |
| HTTP | status, boot log and hardware report of the same boot |
| Flashed image | `boot` reads back with the SHA-256 of the per-run image |
| Restoration | original boot image flashed; PICO OS boots with the original identity; full EDL readbacks of `boot` (96 MiB) and `abl` (1 MiB) match the backup |
| Watchdog | a test image whose USB link is configured but unusable returns to fastboot, and the runner restores PICO OS without a button press |
| Windows | the automatic NCM initialization makes DHCP, SSH and HTTP work; a run needs no button presses, administrator actions or manual network commands |
