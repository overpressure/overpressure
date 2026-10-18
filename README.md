# PICO 4 Linux POC: a bootable Linux userspace for the original PICO 4

This implements steps 2 and 3 of the roadmap in [RESEARCH.md](RESEARCH.md): **boot a minimal
ARM64 Linux on the original PICO 4 (2022, "phoenix"), get a shell and validate native graphics**.
It is the foundation for the later SteamOS userspace work.

The output is a standard Android boot image. Use `fastboot boot` where supported, or the
explicit backup-validated `flash-run` test on the bootloader tested here:

* **kernel + device trees:** the stock PICO kernel (Linux 4.19.81) and DTBs, byte for byte
* **ramdisk:** replaced with Alpine Linux 3.24 (aarch64) plus the POC init in [initramfs/](initramfs/)
* **USB-C cable:** brings up a network link (`ssh root@172.16.42.1`), a web page with logs and a
  hardware report, and a read-only USB drive with the boot logs
* **storage:** the POC userspace switches physical disks and partitions to read-only at boot

> **Step 2 complete on the tested headset — 2026-10-17.** An original PICO 4 on PICO OS 5.13.7
> passed the full hardware acceptance cycle: authenticated ARM64 Linux root shell, read-only
> storage, HTTP diagnostics and automatic return to the matching PICO OS. Windows NCM
> initialization is automated by the [hardware runner](HARDWARE-TEST.md#windows-ncm-initialization).
> The engineering ABL does not support RAM boot, so the verified route is
> [temporary boot-partition flashing with stock restoration](HARDWARE-TEST.md#explicit-boot-partition-test).
> This establishes the Linux shell milestone.

> **Step 3 complete on the tested headset — 2026-10-17.** Hardware Turnip rendering verified
> every pixel of the offscreen Vulkan test. Both lenses showed stable 2160 x 2160 grids with
> continuous framebuffer swaps at measured 90/72 Hz, including a fresh-boot acceptance run.
> The working display path uses the stock RGB-layer arrangement and release/retire fences.
> Original boot and ABL were restored and verified by full EDL readback. See [GRAPHICS.md](GRAPHICS.md)
> for measurements, reproduction and the boundary between this bring-up and compositor/XR work.

> **Step 4 complete on the tested headset — 2026-10-17.** A SteamOS-compatible ARM64 userspace
> (the Steam Frame's systemd 257.7, SteamOS services, gamescope, PipeWire, KGSL Turnip) is
> streamed into zram and takes over PID 1 from this image's init (`pico4-userspace receive` /
> `switch`). On the stock 4.19 kernel it reached `running` with no failed units and passed the
> Turnip Vulkan readback; stock boot and ABL were restored and verified. See [USERSPACE.md](USERSPACE.md).
>
> **Step 5 started: head IMU working on the tested headset, 2026-10-17.** A free software stack
> (qrtr-ns, pd-mapper and hexagonrpcd, ported to the stock kernel's FastRPC ABI, plus the
> `pico4-imu` client) boots the sensor DSP and streams the ICM-42688 at 512 Hz into the
> SteamOS-compatible userspace. A guided 90° turn read 90.3°. Since 2026-10-18, Monado 25.1.0
> with a PICO 4 driver also gives OpenXR clients the head orientation: 1 kHz fusion, about 1 ms
> latency, 0.009° of rotation in 60 s on a still headset. Display output through Monado,
> cameras, controllers and 6DoF are still open. See [XR.md](XR.md).

## Prerequisites

* An original PICO 4 / PICO 4 Pro / Enterprise (A8110, codename *phoenix*) with an **unlocked
  bootloader**. Use our [device preparation workflow](DEVICE.md) for identification, EDL backups,
  engineering ABL staging, unlock and stock ABL restoration. It independently implements the
  approach documented by [more-picohaxx-tool](https://github.com/chaixshot/more-picohaxx-tool),
  without bundling that repository or its binaries. Unlocking wipes user data. The workflow
  limits unlock preparation to the upstream reported PICO 4 range through PICO OS 5.13.8;
  this is not a hardware compatibility guarantee.
* Android platform-tools (`adb`, `fastboot`) on the PC.
* Python 3.9+ to build (no packages needed). Windows, Linux and macOS all work.

## Quick start

0. **Optional, recommended: preflight on the unmodified headset.** With USB debugging on and the
   bootloader still locked, run `.\scripts\preflight.ps1` (or `scripts/preflight.sh`) after a
   build. It runs the POC's own binaries on the real PICO kernel from `adb shell`, without root:
   * the musl loader, busybox, the DHCP/telnet/web servers, dropbear and key generation
   * mtools building a FAT image
   * the other tools

   It also prints facts the POC relies on (USB controller, LED names, governors) and the
   matching `--firmware` value. It only uses `/data/local/tmp/pico4-poc` and cleans up after
   itself. Logs and a machine-readable result land in a new `out/hardware-*/` directory.
   Failed checks return a nonzero exit status. (The PICO community's Magisk port had to
   swap its bundled BusyBox to get it working on this kernel, so this checks that question
   before any bootloader work.)

1. **Find your firmware build** (with the headset in PICO OS; preflight prints this too):

   ```sh
   adb shell getprop ro.build.fingerprint     # e.g. ...PICOA8110:10/5.13.8/smartcm.1788408867:user/...
   adb shell getprop ro.oem.state             # true = "seko" build, false = "sek" build
   ```

   Pick the matching entry in [firmware.json](firmware.json) (global/CN, sek/seko). The stock
   kernel, device trees and kernel config were identical across all the 5.13.7 and 5.13.8 builds I
   compared, so a close match works. An exact match is still the safe choice.

2. **Build** (downloads ~17 MB of the stock boot image plus ~12 MB of Alpine packages, cached in
   `cache/`):

   ```sh
   python build.py build --firmware 5.13.8-global-sek
   ```

   Or start from your own dump: `python build.py build --stock-boot path\to\boot.img`. The result
   is `out/pico4-poc-<firmware>.img`.

3. **Boot it** (RAM only):

   ```powershell
   .\scripts\boot-poc.ps1          # Windows; Linux/macOS: scripts/boot-poc.sh
   ```

   The script does `adb reboot bootloader`, checks unlock state with `device.py check-unlocked`, runs
   `fastboot boot out\pico4-poc-....img` and waits until the headset answers on 172.16.42.1.

4. **Connect** (about 20–40 s after `fastboot boot`):

   | How | Command |
   | --- | --- |
   | SSH (Windows has it built in) | `ssh root@172.16.42.1` (empty password) |
   | Telnet (Windows: enable the optional client, or use PuTTY) | `telnet 172.16.42.1` |
   | Browser: status, logs, report download, reboot buttons | <http://172.16.42.1/> |
   | Copy files (SFTP server included) | `scp root@172.16.42.1:/run/pico4-poc/report.tar.gz .` |
   | Logs without any network | the USB drive **PICO4POC** that appears on the PC |

5. **Go back to PICO OS:** `pico4-reboot` in the shell, the button on the web page, or hold the
   power button until the headset turns off. Nothing was flashed, so a normal boot is stock PICO OS
   again. `pico4-reboot bootloader` goes straight back to fastboot for the next attempt.

## What happens during boot

| Step | What [/init](initramfs/init) does | Status LED (if the kernel exposes the RGB LED) |
| --- | --- | --- |
| 1 | mount proc/sys/run; `/dev` via `mdev` because the stock kernel has **no devtmpfs** | |
| 2 | all disks/partitions → read-only (`blockdev --setro`), `/dev/block/by-name/*` links | blue |
| 3 | USB gadget via configfs: **NCM** network + mass-storage log drive (+ ACM serial if the kernel had it) | amber once bound |
| 4 | `usb0` = 172.16.42.1/24, DHCP hands the PC 172.16.42.2 (no gateway/DNS, so the PC's internet route is untouched) | green when a PC configures the device |
| 5 | cpufreq governor → `schedutil`, CPU idle states re-enabled (PICO OS does this in its post-boot script) | |
| 6 | `pico4-init` (PID 1, runs `/etc/inittab` like busybox init) starts `dropbear` (ssh), `telnetd`, `httpd`, `udhcpd` | |
| 7 | `pico4-probe` collects a hardware inventory ~15 s later and refreshes the log drive | |

If no usable USB traffic or shell login arrives within 5 minutes, an independent watchdog
reboots into fastboot (`pico4poc.autoreboot=0` disables this for interactive boots). USB
enumeration alone does not cancel recovery. Hardware tests require the runner to authenticate
and also have a maximum device runtime; see [test recovery](HARDWARE-TEST.md#failure-and-timeout-behavior).

If the link is up but no packet arrives from the PC for 20 s, the headset re-plugs the USB device
in software, at most twice. A PC whose side of the link is up always sends something right away
(DHCP, IPv6 router solicitations). The QEMU tests caught an intermittent race where the PC side
never received NCM's "connected" notification and showed the cable as unplugged. A re-plug fixes
that, which is what this step automates.

Every step is logged to the kernel log, to `/run/pico4-poc/boot.log` and to pstore (`pmsg`). Pstore
survives a warm reboot.

## Troubleshooting

* **The PC sees a "PICO 4 Linux POC" device but no network adapter (Windows).** Windows 11
  ships `UsbNcm.sys` as the in-box class driver for CDC NCM (class 02h, subclass 0Dh) and should
  load it automatically
  ([Microsoft](https://learn.microsoft.com/en-us/windows-hardware/drivers/usbcon/supported-usb-classes)).
  If it doesn't, use Device Manager: *Update driver → Browse my computer → Let me pick → Network
  adapters → Microsoft → UsbNcm Host Device*. Windows 10 has no NCM driver, and the stock kernel
  has no RNDIS function. There, use the log drive, a Windows 11 or Linux PC, or a Linux VM with
  USB passthrough.
* **No network, but a PICO4POC drive appeared.** Open `boot.log` on it; it shows what the USB
  setup did. The drive is rebuilt after the probe; eject and replug, or look again, to see the
  newer files.
* **Windows has an NCM adapter but no DHCP or SSH, with `Wrong NDP SIGN` in the kernel log.**
  Use the runner's [Windows NCM initialization](HARDWARE-TEST.md#windows-ncm-initialization)
  and leave the USB adapter on DHCP. This is the tested workaround for the 5.13.7 stock kernel.
* **Nothing appears on the PC at all.** Wait 5 minutes: the headset should come back in fastboot
  mode (auto-reboot). Boot PICO OS, then read the last kernel log as root:
  `cat /sys/fs/pstore/console-ramoops*` and `cat /sys/fs/pstore/pmsg-ramoops*`. Every POC message
  starts with `pico4-poc`.
* **`fastboot boot` is refused.** If the bootloader is locked (`fastboot getvar unlocked`), unlock
  it first. If it is unlocked and the error is an unknown/unsupported command, see
  [If `fastboot boot` is not available](#if-fastboot-boot-is-not-available).
* **The headset is stuck.** Hold the power button for >10 s. That is a hardware reset, and the
  next boot is PICO OS.

### If fastboot boot is not available

The signed Neo3 engineering ABL tested on the PICO 4 rejects `fastboot boot` with an
unknown-command error even after persistent normal and critical unlock. For that bootloader,
use the explicitly selected [boot-partition test](HARDWARE-TEST.md#explicit-boot-partition-test),
which checks the device's verified backup and attempts stock-image restoration after testing.
The RAM runner never switches to flashing automatically.

The equivalent manual sequence is below. It replaces the sole Android boot partition, so
PICO OS cannot start until the original image is flashed back. Keep engineering ABL and a
working EDL recovery path available:

```powershell
fastboot flash boot out\pico4-poc-5.13.8-global-sek.img
fastboot reboot                      # every normal boot now starts the POC
# back to PICO OS: get into fastboot (pico4-reboot bootloader in the POC shell, or the
# POC's 5-minute auto-reboot when no PC answers), then:
fastboot flash boot out\stock-boot-5.13.8-global-sek.img
fastboot reboot
```

`out/stock-boot-<firmware>.img` is the untouched partition image from the same OTA. Flash back
the one matching the firmware installed on the headset (that is why the build should match it).
Recovery and the bootloader are separate partitions and are never touched.

## Kernel command-line options

Add them at build time, e.g. `python build.py build --cmdline-append pico4poc.autoreboot=0 pico4poc.cpugov=keep`.

| Option | Default | Meaning |
| --- | --- | --- |
| `pico4poc.usbnet=` | `ncm,rndis,ecm` | network functions to try, in order (the stock kernel only has NCM) |
| `pico4poc.usbserial=` | `acm` | serial shell function, absent from the stock kernel (`gser` needs a host driver, `none` disables). The serial getty asks for a login name (any name, no password) |
| `pico4poc.logdisk=` | `1` | read-only USB log drive |
| `pico4poc.autoreboot=` | `300` | seconds without a PC before rebooting into fastboot, `0` = never |
| `pico4poc.usbkick=` | `20` | seconds of silence on a configured link before a software USB re-plug (max. 2), `0` = never |
| `pico4poc.ip=` / `pico4poc.host_ip=` | `172.16.42.1` / `.2` | addresses on the USB link (/24) |
| `pico4poc.rw=` | `0` | `1` leaves block devices writable. Don't, unless you know exactly why |
| `pico4poc.led=` | `1` | drive the RGB status LED (only LEDs named red/green/blue) |
| `pico4poc.cpugov=` | `schedutil` | cpufreq governor, `keep` = stock (`performance`) |
| `pico4poc.lpm=` | `1` | re-enable CPU low-power idle states 30 s after boot |
| `pico4poc.probe=` | `1` | run `pico4-probe` automatically |
| `pico4poc.modules=` | `1` | load modules from the stock ramdisk (none in current firmware) |

Timing knobs (`probe_delay`, `lpm_delay`, `udc_timeout`, `logdisk_mb`) are read the same way; see
[lib.sh](initramfs/usr/libexec/pico4-poc/lib.sh) and the scripts next to it.

Do not add a bare `earlycon` (without a value). According to the published PICO kernel source,
its earlycon parser dereferences the missing argument and the kernel panics before anything is
logged.

## Safety notes

* Use **`fastboot boot`**. The PICO 4 is *not* an A/B device: `fastboot flash boot` would replace
  the only boot partition, and PICO OS would not boot until you flash the stock image back. The
  stock image the build used is kept at `cache/stock/*.boot.img`.
* In the POC environment all block devices are read-only. `persist` (sensor/camera calibration)
  and `fsg` (PICO VR data) are unique to each headset and cannot be re-downloaded. Treat them as
  irreplaceable in later stages too.
* `pico4-mount-ro <partition>` mounts stock partitions read-only (journal replay disabled), e.g.
  `modem` for firmware blobs.
* The shell has no password. The only network is the USB cable to one PC, and nothing is
  forwarded.

## Verification

### Automated test on a prepared headset

Use the [hardware acceptance runner](HARDWARE-TEST.md) to test the complete cycle:

```sh
python hardware_test.py run --serial YOUR_ADB_SERIAL --image out/pico4-poc-5.13.8-global-sek.img
```

Start with the already-unlocked headset running PICO OS and USB debugging authorized.
In its default RAM mode, the runner performs preflight, boots a per-run image, checks SSH identity, Linux shell,
storage protection and HTTP diagnostics, then verifies return to the original PICO OS.
It records image hashes and all commands/results under `out/hardware-*/`. No unlocking or
flashing is performed in RAM mode; the explicit `flash-run` mode temporarily writes and restores
the boot partition. The regular `boot-poc` scripts remain interactive helpers that leave
Linux running; their ping check alone is not a hardware acceptance result.

Host simulation tests: `python -m unittest discover -s test -p "test_*.py" -v` — **110 passed**.

On 2026-10-17, two real-headset `flash-run` cycles passed on an original PICO 4 running 5.13.7.
The first established the Windows NCM workaround; the second performed it automatically,
using the default POC configuration and a fresh per-run credential and nonce. That repeat
needed no button presses, administrator actions or manual network commands during the run.

| Real-headset acceptance | Result |
| --- | --- |
| Exact-image userspace preflight | 20 passed, 0 failed |
| SSH identity and root shell execution | Matching per-run nonce and SoC ID; ARM64, UID 0 |
| Running kernel and build | Stock 4.19.81-perf+, Alpine 3.24.2; metadata matched |
| Persistent storage protection | All 99 physical block entries read-only; RAM-backed root; no writable device mounts |
| Flashed POC readback | SHA-256 matched the instrumented image |
| HTTP status, boot log, hardware report | Retrieved and validated against this run |
| Stock boot restoration | Original image flashed; matching Android identity and boot completion verified |

The unassisted run's private evidence is `out/hardware-20261017-flash-02/result.json`
(all ten phases passed). Its source image SHA-256 is
`c98b5372b3b8454670b1f3eaf264efa55ae7b3220b9b8b87e2fd77a9e798dfc8`;
the instrumented test image is recorded separately in the result. The tested host used
Windows 11, UsbDk 1.0.22, PyUSB 1.3.1 and libusb-package 1.0.30.0.

Complete EDL readbacks of restored `boot` (96 MiB) and `abl` (1 MiB) matched the original
recovery backup after testing. Stock ABL was restored as a separate preparation/recovery
operation; `flash-run` itself only writes `boot`. The bootloader remains unlocked for further
development. Evidence and backups stay private and are excluded from Git.

The failed-network recovery path was also verified on the real headset on 2026-10-16:
the independent watchdog returned to fastboot and the runner restored PICO OS without a
button press. This covers a configured but unusable USB link; a kernel hard hang or loss
of host power can still require physical/EDL recovery.

### Offline and emulated verification

All of this was done before any hardware was involved:

* **Stock image analysis.** `python build.py inspect` downloads only the boot.img member of the
  official OTA zip via HTTP range requests, checks it against the zip's CRC-32, and reads the
  kernel's embedded config (`IKCONFIG`). The `build` command refuses to run if a required option
  is missing.
* **Image format.** Google's own `unpack_bootimg.py` parses the output. Repacking its extracted
  parts with Google's `mkbootimg.py` reproduces the image byte for byte, except `second_addr`:
  that unused field keeps the stock value (`0x00f00000`), where current mkbootimg writes 0. Kernel
  and DTB are identical to stock. The header SHA-1 id is recomputed the way mkbootimg does it,
  which also reproduces the stock image's id.
* **Userspace.** Every script passes `sh -n` with the real aarch64 BusyBox, and the ramdisk lists
  correctly with GNU cpio (device nodes, symlinks, modes).
* **Boot test in QEMU** ([test/qemu/](test/qemu/)). A mainline arm64 kernel (6.18.56) configured
  like the stock one where it matters boots the exact ramdisk: no devtmpfs, configfs gadget, 4K
  pages with a 39-bit address space. `dummy_hcd`
  makes the VM both headset and PC, and the self-test checks the parts listed in
  [Verification results](#verification-results).

```sh
test/qemu/build-test-kernel.sh       # once (Linux/WSL)
test/qemu/run-selftest.sh            # boots out/*-initramfs.cpio.gz, expects PASS
```

### Verification results

Final image `out/pico4-poc-5.13.8-global-sek.img`, 46,956,544 bytes,
sha256 `fe27a956ccb1ec2314052555c852aaa7bf21a5d53a873aec04c299926203a6f2`. Your own build
differs in the build-time timestamp unless `SOURCE_DATE_EPOCH` is set.

| Check | Result |
| --- | --- |
| Stock boot.img from the official OTA (5.13.8 global sek, b10113) | CRC-32 `87016fbf` matches the zip directory; 17 MB downloaded of a 3.72 GB zip |
| Google `unpack_bootimg.py` | parses; header v2, addresses, OS version/patch level as stock |
| Google `mkbootimg.py` repack of the extracted parts | byte-identical except `second_addr` (see above) |
| Kernel, DTB vs. stock | identical |
| QEMU self-test, all functions incl. CDC-ACM serial | 23/23 checks |
| QEMU self-test, stock-kernel function set (NCM + log drive, no serial) | 22/22 checks |
| QEMU self-test, PC side silent for 35 s (forces the re-plug path) | 24/24 checks, re-plugged exactly twice, then DHCP, log drive and serial all work |

The final build passed one run of each configuration on the 39-bit kernel. Earlier builds
passed 7 more runs on a 48-bit test kernel; the last change since then only made the
auto-reboot also cover a failed USB setup. No re-plug was triggered in any normal run.

The self-test covers:

* `/dev` via mdev, and the gadget binding
* PC-side enumeration and a DHCP lease across the USB link
* ssh/telnet/http listening, and SSH login with the empty password
* web status, logs and report
* `/dev/block/by-name` links, and refused writes on read-only partitions
* mounting the log drive from the PC side
* a login shell on USB serial
* report collection and export

Bugs these tests caught, all fixed in the code above:

* **Serial echo hazard.** When the PC opened its serial port, its tty echoed the headset's banner
  back for a moment. The headset ran the echoed `pico4-reboot …` line and rebooted. Fixes: the
  serial getty now asks for a login name first, the banner lines start with `#`, and
  `pico4-reboot` refuses unknown words.
* **NCM carrier race.** In about 1 of 3 runs the PC side never saw NCM's "connected" notification.
  The fix is the silent-link re-plug described above. A first version toggled the controller's
  `soft_connect` and left the device unable to enumerate at all, so the re-plug now does an
  unbind/rebind, the same thing Android does when it switches USB functions.
* **Read-only scope.** On newer kernels a partition is also read-only while its disk is. Writing
  later (a future stage) means `blockdev --setrw` on both the disk and the partition. The stock
  4.19 kernel tracks partitions separately, and the POC marks every partition individually.

## The stock kernel, as found (5.13.8, `IKCONFIG`)

| | |
| --- | --- |
| Boot image | header v2, page 4096, 96 MiB partition, kernel 0x8000 / ramdisk 0x1000000 / dtb 0x1f00000 (base 0) |
| Kernel | `4.19.81-perf+`, clang 8, uncompressed `Image` (35 MB), `CONFIG_CMDLINE_EXTEND`, `PANIC_TIMEOUT=-1` |
| Ramdisk | gzip only (`RD_GZIP`); stock one is Android 10 first-stage init + `fstab.qcom`, no modules, so no `skip_initramfs` patch is needed |
| DTB | 7 trees: kona v1/v2/v2.1, kona-iot v1/v2/v2.1, RTIC; identical in 5.13.7 and 5.13.8 |
| `/dev` | **no `DEVTMPFS`** → mdev; `UEVENT_HELPER=y`, firmware fallback to userspace (`FW_LOADER_USER_HELPER_FALLBACK`) |
| USB gadget | configfs with **NCM**, mass storage, gser, FunctionFS, UAC1/2, UVC, HID, MIDI, MTP, Qualcomm GSI; **no ACM, no generic RNDIS, no ECM** |
| Logs | `PSTORE_RAM`, `PSTORE_CONSOLE`, `PSTORE_PMSG` |
| Graphics | `DRM`, KGSL (`QCOM_KGSL`) GPU driver; display driver from techpack (not in `IKCONFIG`); `video=vfb:640x400` in cmdline |
| Other | `HID_QVR` (Qualcomm VR HID), LEDs `QTI_TRI_LED` + `AW210XX`, `ANDROID_PARANOID_NETWORK`, SELinux (no policy loaded → permissive), cpufreq default `performance`, 39-bit VA / 4K pages |
| Modules | `MODULE_SIG_FORCE`: only modules signed with PICO's per-build key load, so new drivers mean rebuilding the kernel |
| Storage | UFS, dynamic partitions (system/vendor/product/odm in `super`), f2fs userdata with FBE, separate `persist`, `fsg`, `modem`, `dsp`, `bluetooth`, `cache` |

## How it is built

[build.py](build.py) (pure Python, no dependencies):

1. Gets the stock boot.img from a file, an OTA zip, or an OTA URL ([tools/firmware.py](tools/firmware.py)).
   PICO 4 OTAs are non-A/B; A/B `payload.bin` is supported too ([tools/payload.py](tools/payload.py)).
2. Parses it ([tools/bootimg.py](tools/bootimg.py), header v0–v4) and analyses the kernel
   ([tools/kernelinfo.py](tools/kernelinfo.py)).
3. Assembles the root filesystem in memory ([tools/cpio.py](tools/cpio.py)), so device nodes and
   modes are right even on Windows:
   * the Alpine minirootfs, sha256-checked
   * the packages listed in `build.py`, verified through Alpine's index → control → data hash
     chain ([tools/alpine.py](tools/alpine.py))
   * the [initramfs/](initramfs/) overlay
   * [pico4-reboot](src/pico4-reboot.c), a 1.5 KB static helper that can reboot into fastboot or
     recovery
4. Writes a gzip newc cpio and repacks with every stock header field kept. It then re-parses its
   own output and checks it before writing `out/`.

Outputs:

* the image `out/pico4-poc-<fw>.img`
* the bare ramdisk `-initramfs.cpio.gz`, used by the QEMU test
* the rootfs as a tar, used by the preflight
* a JSON build record
* `stock-boot-<fw>.img`, an untouched copy of the stock image

Rebuild the reboot helper with any aarch64 cross compiler (command in the source header). The
prebuilt copy is in `prebuilt/aarch64/`.

## On the headset

| Command | Purpose |
| --- | --- |
| `pico4-info` | status summary (also the login banner) |
| `pico4-probe` | hardware inventory → `/run/pico4-poc/report/`, `report.tar.gz` |
| `pico4-mount-ro <part> [dir]` | mount a stock partition read-only |
| `pico4-reboot [bootloader\|recovery\|poweroff]` | reboot into a mode (no argument: PICO OS); `-r <string>` passes any other reboot reason. Unknown words are refused |
| `pico4-userspace receive\|switch\|status` | step 4: unpack the SteamOS userspace archive from stdin into zram, then hand PID 1 to its systemd ([USERSPACE.md](USERSPACE.md)) |
| `drm_info`, `modetest`, `evtest`, `i2cdetect`, `lsusb`, `dtc`, `strace`, `htop`, `dmsetup`, `tinymix`, `gpioinfo` | exploration tools (the probe avoids the risky ones) |

The probe skips debugfs GPIO/clock dumps, PCIe config reads and audio mixer reads, because those
can hard-crash Qualcomm SoCs. Run them by hand, knowing the risk.

## Next steps toward SteamOS

1. **Run it on a headset and collect the report.** It answers the questions everything else
   depends on: display pipeline (DRM connectors, panel/backlight), input and HID devices (IMU via
   `HID_QVR`?), cameras (V4L2 nodes), remote processors, thermal zones, LEDs, battery.
2. **Display.** Light the panels from Linux via the downstream SDE DRM driver (`modetest`/`drm_info`
   are included), then a simple compositor.
3. **GPU.** Mesa Turnip has a KGSL backend, so Vulkan may work on the stock kernel. It needs the
   GPU firmware from the `modem`/vendor partitions in `/lib/firmware`.
4. **A persistent root filesystem.** Switch from the initramfs into a real distro rootfs
   (Arch Linux ARM is SteamOS-like). SteamOS 3's systemd, glibc and Mesa expect much newer kernels
   than 4.19, so check feature requirements early. Rebuilding
   [bytedance/phoenix-kernel](https://github.com/bytedance/phoenix-kernel) (4.19.81, no board DTS;
   reuse the stock DTB) allows adding devtmpfs, ACM, cgroup2/BPF features and so on. Mainline
   SM8250 support (RB5, Xperia 1 II) is the long-term alternative.
5. **XR.** Tracking cameras, IMU, controllers and lens calibration (from `persist`/`fsg`) via
   Monado, as described in RESEARCH.md.

## Repository layout

```text
build.py                 build / inspect entry point
firmware.json            official PICO 4 OTA builds (URLs on PICO's CDN)
tools/                   boot image, cpio, Alpine, OTA and kernel analysis modules
initramfs/               files added to the ramdisk (init, scripts, web UI, pico4-userspace handoff)
userspace/               step-4 SteamOS userspace: package list, pacman config, PICO overlay, PKGBUILDs
src/, prebuilt/aarch64/  pico4-reboot helper (source + static binary), graphics tests
scripts/                 boot-poc.ps1/.sh (fastboot boot), preflight.ps1/.sh (+ preflight-device.sh),
                         build-graphics.sh, build-userspace.sh
test/qemu/               test kernels (6.18, stock-like 4.19), stage-1 and userspace QEMU self-tests
cache/, out/             downloads and build output (not versioned)
```
