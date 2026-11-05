# Development

How the project is laid out, built and tested, what a build host needs, and the fastest way
from a change to a result on the headset. The headset-side workflows (acceptance runner,
acceptance cycle, development loop) are in [hardware-testing.md](hardware-testing.md).

## Repository layout

The PICO 4's part of overpressure lives in `devices/phoenix/`; the repository root holds the
generic host package `overpressure/` (image formats, the headset registry, the command line and
the GUI, [docs/tool.md](../../../docs/tool.md)). Paths below are relative to `devices/phoenix/`;
commands run from the repository root.

```text
tooling/          the PICO 4's host tooling (Python standard library): python -m overpressure COMMAND
  __init__.py       HEADSET: its commands and the GUI's workflow steps
  image.py          stage-1 boot image (build, inspect, match) on overpressure/stage1.py; firmware.json: PICO's OTA builds
  stock.py          the members of PICO's full OTA the extraction needs (overpressure/ota.py)
  device.py         identity, EDL backups, engineering ABL, unlock and restore
  hardware_test.py  the bounded acceptance runner (preflight, flash-run)
  cycle.py          the unattended acceptance cycle that ends on stock boot and ABL
  devloop.py        the development loop on the flashed dev image
  ramboot.py        the target of overpressure's RAM boot (the engineering ABL refuses fastboot boot)
  localconfig.py    the headset's settings from .env (PICO4_ keys)
  userspace.py, graphics.py   validators of the on-device checks
  ledmodel.py, aimgrip.py, leverarm.py, controllermodel.py   controller data from PICO OS's libraries
initramfs/        stage 1: files added to the Alpine ramdisk (pico4-init, services, handoff)
userspace/        stage 2: the SteamOS-compatible userspace
  packages.txt, pacman.conf   package list and repositories
  rootfs/           the PICO 4 adaptation layer (units, udev rules, PAM, session, device tools, checks)
  session/          the session user's SteamVR configuration
  pkgbuilds/        the project's packages: pico4-sensors, pico4-basalt, pico4-monado, pico4-gamescope,
                    pico4-vrlink, pico4-swapchain-usage, pico4-gtk2 and others
kernel/           Valve's kernel: build-kernel.sh, patches/, the PICO's drivers, the device tree
src/              C sources: graphics/ (the Vulkan test), monado/ (Monado's PICO 4 driver),
                  stockslam/ (pico4-slamd), vrlink/ (Steam Link), xr/ (OpenXR test clients), swapchain/ (frame pacing),
                  reboot/ (pico4-reboot), splash/ (pico4-splash)
prebuilt/         the static aarch64 pico4-reboot and pico4-splash, so stage 1 builds without a compiler
scripts/          build-userspace.sh, build-system-image.sh, the extract-*.sh scripts
                  (stock firmware, with overpressure's scripts/stock-lib.sh) and the on-headset preflight
tests/            host tests (unittest), test_recovery_policy.sh, test_charger.sh, qemu/ (test kernels and boot tests)
docs/             documentation
```

The generic modules the PICO 4 uses are in the root package: `stage1.py` (the stage-1 builder),
`ota.py` (stock images and partitions from an OTA), `android.py` (adb and fastboot),
`qualcomm.py` (the SoC ID, EDL and its partition maps), `backup.py` (verified backups),
`process.py` (programs and bounded waits), `ssh.py`, `fastboot.py` (the RAM boot), the formats
(`bootimg.py`, `cpio.py`, `compress.py`, `alpine.py`, `apksign.py`, `payload.py`, `kernelinfo.py`,
`avb.py`, `sparse.py`), `elfdeps.py` (native ELF dependency check of the userspace root) and
`windows_ncm.py` (Windows NCM initialization through UsbDk); `scripts/stock-lib.sh` serves the
extraction scripts.

Private and generated directories stay out of Git: `backup/` (device identity and EDL
backups), `assets/` (Firehose loader, engineering ABL, GPU firmware, sensor data), `cache/`
(downloads), `out/` (build output and test evidence; `out/dev/` for the development loop) and
`.env` at the repository root (the headset's settings, see [.env.example](../../../.env.example)).

## Build host

`python -m overpressure doctor` (or *Check dependencies* in the window) checks all of this on
the computer at hand and says how to fix what is missing; `python -m overpressure tools` fetches
adb, fastboot, edl-ng and (Windows) the NCM venv, and the build container
(`OVERPRESSURE_LINUX=docker`) replaces the Linux list below with Docker
([tooling/doctor.py](../tooling/doctor.py), [docs/tool.md](../../../docs/tool.md#dependencies)).

| Task | Needs |
| --- | --- |
| Stage-1 image, host tests, headset commands | Python 3.9+ on Windows, Linux or macOS; Android platform-tools and OpenSSH for the headset |
| Userspace archive, QEMU tests | Linux or WSL2 as root: curl, tar, zstd, python3, chroot, overlayfs |
| on x86-64 hosts additionally | an aarch64 binfmt handler with the F flag (qemu-user-static) and LLVM: `apt install clang-20 lld-20` |
| Windows NCM initialization | UsbDk and the NCM venv: `python -m overpressure tools ncm-venv` |

The userspace build runs unchanged on an arm64 Linux host. There the build environment runs
natively, without binfmt or LLVM.

### Native compilers instead of an arm64 build machine

The build environment is an arm64 root file system. On x86-64 its programs run under QEMU user
emulation, roughly ten times slower than natively. The compilers are the exception: the kernel
runs the host's x86-64 clang and lld directly inside the build environment, and they compile for
`aarch64-unknown-linux-gnu` with the environment as their sysroot (its glibc, GCC 15 runtime and
libraries). Only makepkg, the CMake and Meson configuration steps, code generators, pacman and
its hooks still run emulated.

Clean package builds on a 12-core i7-12700KF (20 threads) under WSL2:

| Package | GCC under QEMU | host clang |
| --- | --- | --- |
| pico4-basalt | 8.7 min (ccache warm) | 2.9 min |
| pico4-monado | 4.6–8.3 min | 2.8 min |
| pico4-sensors | 1.6 min | 1.0 min |
| pico4-gtk2 (GTK 2.24.33, libraries and modules; Autotools under QEMU) | – | 14 min |

A Raspberry Pi 5 (four Cortex-A76 cores) compiles at about the speed of this host's emulation,
so it would not shorten builds. Machines that would, if the host's native compilers are not
enough: Apple silicon (Linux VM), Snapdragon X (WSL2), or Ampere and Graviton cloud instances.
Keep the private data rules in mind before building on someone else's machine.

`--emulated-cc` builds with the environment's own GCC under QEMU, for comparison or when no
LLVM is installed. Packages built by clang use lld and no LTO (LTO's bitcode objects would need
LLVM's archiver in every build system).

## Stock firmware

The image needs PICO OS's firmware and data files (DSP images, the head tracker,
configuration). They are the same on every PICO 4 with that firmware and
come from PICO's public full OTA, not from the user's backup:

```sh
# Linux or WSL as root (or the build container): the OTA's partitions (about 3.9 GB of OTA
# members, kept under STOCK/download), then every asset from them into assets/*-5.13.7
devices/phoenix/scripts/extract-assets.sh 5.13.7-global-seko /var/tmp/pico4-stock/5.13.7-global-seko
```

[scripts/extract-assets.sh](../scripts/extract-assets.sh) (the window's *Stock firmware and
assets*) runs `python3 -m overpressure stock` and then each extraction script below, each into a
fresh folder that replaces the old one only when its script succeeded.

`python -m overpressure stock` ([tooling/stock.py](../tooling/stock.py)) takes from the OTA zip only the
members it needs, each checked against the zip's CRC: `system` and `vendor` as ext4 images
rebuilt from their brotli-compressed block images and transfer lists (the `brotli` command),
`firmware-update/NON-HLOS.bin` as `modem.bin`, `firmware-update/dspso.bin` as `dsp.bin`, and
`recovery.img` and `boot.img`; `manifest.json` holds their sha256, which every extraction script
checks first ([scripts/stock-lib.sh](../../../scripts/stock-lib.sh)). The firmware entry is the
headset's (`PICO4_FIRMWARE`, `python -m overpressure match`, [stage1.md](stage1.md)). The results are
proprietary: keep the stock directory and `assets/` private.

| Asset | Script | From |
| --- | --- | --- |
| GPU firmware `a650_*` | extract-graphics-firmware.sh | vendor `/firmware` |
| SLPI image, sensor configuration, lens configuration, controller models and data | extract-sensor-data.sh | modem, vendor, system |
| Wi-Fi chip firmware and board data, driver configuration | extract-wlan.sh | vendor, modem |
| ADSP image, audio process files and DSP libraries, calibration database | extract-audio.sh | vendor, modem |
| Compute DSP image, the tracker's bionic root, DSP modules and process shells | extract-stock-slam.sh | modem, system, vendor, dsp |

For 5.13.7 every file these scripts take is byte-identical to the same file in an EDL backup of
a headset running that firmware. The headset's own factory data is not in the image: the sensor
registry with its calibration, the camera, IMU and lens calibration, the serial number the
fan's table follows and the Wi-Fi MAC addresses stay in its `persist` partition, which the
system mounts read-only at boot ([userspace.md](userspace.md#the-headsets-own-data)). The dev
image takes its stock recovery (device-tree overlay and AVB footer) from the same OTA. The
user's backup remains the way back to stock and the reference of the write checks (partition
sizes and hashes, [install.md](install.md)).

## The userspace build

```sh
# Linux or WSL, as root (make userspace does the same with the defaults below):
devices/phoenix/scripts/build-userspace.sh /var/tmp/pico4-userspace-build out/userspace \
    --gpu-firmware assets/graphics-firmware-5.13.7 --sensor-data assets/sensor-data-5.13.7 \
    --wlan assets/wlan-5.13.7 --audio assets/audio-5.13.7 --stock-slam assets/stock-slam-5.13.7
```

| Option | Effect |
| --- | --- |
| `--gpu-firmware DIR` | `a650_*` GPU firmware from `scripts/extract-graphics-firmware.sh`; without it Vulkan cannot start |
| `--sensor-data DIR` | sensor DSP firmware and configuration, lens configuration and controller data from `scripts/extract-sensor-data.sh` |
| `--wlan DIR` | Wi-Fi firmware and configuration from `scripts/extract-wlan.sh` |
| `--audio DIR` | ADSP firmware and the audio process's files from `scripts/extract-audio.sh` |
| `--stock-slam DIR` | PICO OS's head tracker, the Compute DSP's firmware and the tracker's bionic root from `scripts/extract-stock-slam.sh` ([xr.md](xr.md#head-tracking)); needs the host's clang |
| `--refresh` | download current package databases and reinstall the base root |
| `--clean` | rebuild the project packages from scratch |
| `--emulated-cc` | compile under QEMU with the environment's GCC |

Each step is skipped when its inputs did not change:

1. **Snapshot.** The script copies its inputs (`overpressure/`, `src/`, `userspace/`, the shared stage-1
   scripts, `prebuilt/`) into the build root first, so the tree can be edited during a build.
2. **Build environment.** Valve's Holo Core aarch64 preview root file system, pinned by SHA-256,
   unpacked once. Package databases are downloaded by the first build and with `--refresh`
   only, so repeated builds install the same package versions.
3. **Project packages.** makepkg builds each recipe in `userspace/pkgbuilds/` when the recipe
   (its directory and the compiler) changes. When only the project's own sources in `src/`
   change, the package is rebuilt incrementally in its last build tree (`makepkg -e`): the
   recipes copy those sources with `install -C`, which leaves unchanged files and their
   timestamps alone, and configure only a new build tree.
4. **Base root.** `userspace/packages.txt` installed by pacman from the Steam Frame hotfix
   repository and the Holo Core preview. It is kept between builds and reinstalled when the
   package list or `userspace/pacman.conf` changes, and with `--refresh`.
5. **Image root.** An overlay on the base root: the project packages that differ from the
   base root's, the adaptation layer (`userspace/rootfs`, shared stage-1 scripts, users, units)
   and the session configuration. [overpressure/elfdeps.py](../../../overpressure/elfdeps.py) checks natively that
   the programs and libraries of the Frame's and the project's packages find their libraries and
   symbol versions; a few optional programs are excluded by name in the script.
6. **Archive.** `pico4-userspace.tar.zst` (zstd level 12, multithreaded) and
   `pico4-userspace.manifest.json` with the sources, hashes and installed package versions.

Times on the same host, measured with an archive of about 800 MB:

| Change | Build time |
| --- | --- |
| nothing, or only the adaptation layer (`userspace/rootfs`, `userspace/session`) | 45 s |
| one source file of a project package (here a Monado driver file, incremental) | 2.3 min |
| the package list or `pacman.conf` (new base root, packages reused) | 2 min |
| everything: `--clean` packages and a new base root | about 16 min |

Writing that archive took about 25 s of each (tar, zstd and the copy to the output
directory). Package builds keep emulated overhead outside the compiler: makepkg's stripping,
debug packages and compression take about a minute for Monado.

## Tests

| Level | Command | Takes |
| --- | --- | --- |
| Host tests: protocols, validators, the runner, cycle and dev loop against fakes | `python -m unittest` (from the repository root: these and the generic tests) | about 8 s |
| Stage-1 recovery policy | `bash devices/phoenix/tests/test_recovery_policy.sh` | seconds |
| Stage-1 charger mode | `sh devices/phoenix/tests/test_charger.sh` | seconds |
| Both of the above | `make test` | seconds |
| On the headset: the integration test | `python -m overpressure dev install` and `dev check`, on the installed system | minutes; [hardware-testing.md](hardware-testing.md) |
| Optional: stage 1 on a QEMU test kernel | `devices/phoenix/tests/qemu/run-selftest.sh` (`make qemu-stage1`) | about 3 min |

The headset is the integration test: the development loop installs a new system image on Valve's
kernel and runs the acceptance checks in minutes. The QEMU test covers stage 1 without a headset
(its test kernel comes from `tests/qemu/build-test-kernel.sh`).

New behaviour gets host tests with fakes for hardware and golden data for protocols; the C
protocol tests and the controller tracker's C++ tests (`test_pico4_ctrack_*`: solvers,
acquisition, blobs, IMU fusion, the engine and its thread on synthetic data) run inside the
package builds, because makepkg runs with `--nocheck`.

## From a change to the headset

The headset stays in development mode, with the engineering ABL, the dev image in `recovery` on
the stock kernel and the installed system on Valve's kernel in `boot`, `super` and `userdata`
([hardware-testing.md](hardware-testing.md#development-mode), [install.md](install.md)):

1. Change the code, then build: `make userspace` (or the script above), incremental.
2. `devices/phoenix/scripts/build-system-image.sh` (as root).
3. `python -m overpressure dev recovery` (it stays until `dev normal`), then
   `python -m overpressure dev install`, which starts the installed system.
4. `python -m overpressure dev check`; `python -m overpressure dev installed` reconnects to the
   system after a reboot.

The session runs only on Valve's kernel, so `dev boot` and `dev check` from the maintenance stage 1
need a recovery image with Valve's kernel (`kernel/recovery_image.py`). A changed stage-1 image is
built with `python -m overpressure build` and written with `python -m overpressure dev image` and
`python -m overpressure dev flash recovery`; the installed boot image takes it through
`recovery_image.py` and `dev install --boot PATH`
([install.md](install.md#install-and-update)). A task ends with the headset still in development
mode. Returning it to stock is a separate step, for when PICO OS is needed
([hardware-testing.md](hardware-testing.md#back-to-stock)).

### Power while tethered

Tethered work needs a charging USB port (CDP) or a wall charger. The headset's charger sets its
input by BC1.2 detection, also through a USB-C to USB-A adapter. Type-C's "high current" is the
adapter's pull-up resistor, which the charger rightly ignores.

| Port | Charger input | Battery |
| --- | --- | --- |
| Standard port (`real_type` `USB`) | 0.49 A at 4.74 V, about 2.3 W | drains during a session, 0.29–0.78 A |
| Charging downstream port (`USB_CDP`) | settles at 1.5 A and draws 1.49 A at 4.49 V, about 6.7 W | session stopped: +1.13 A; Valve's session in standby: +0.78 A; Valve's session rendering: drains, about 0.37 A |

On a CDP the input is the limit (`input_current_limited` 1, `input_current_settled` 1.5 A): the
port's 6.7 W are about what Valve's session draws while it renders. The charger reports
"Charging" also while the battery drains; the battery's `current_now` (positive while charging)
tells. `pico4-userspace-check` reports the port type, the input limit and the measured input
current, VBUS and the battery's state (`power`). `dev check` prints a warning on a standard
port, when the battery drains while tethered, and when it charges at less than 200 mA below
95 % (`power_warnings` in [tooling/userspace.py](../tooling/userspace.py)).

## Windows host and WSL

- `wsl -u root -- bash -c '... $VAR ...'` expands `$VAR` before bash runs, even inside single
  quotes. Put such commands in a script file and run `wsl -u root -- bash /mnt/c/.../script.sh`.
- From Git Bash, set `MSYS_NO_PATHCONV=1`; otherwise `/abs/paths` in arguments and environment
  values become `C:/Program Files/Git/...`.
- Run project PowerShell scripts with `pwsh` (7); Windows PowerShell 5.1 aborts on native stderr.
- `python -m overpressure tools` fetches `adb` and `fastboot` into `cache/tools`; an Android
  SDK's own (`%LOCALAPPDATA%\Android\Sdk\platform-tools`) can be named with
  `PICO4_PLATFORM_TOOLS` instead.
- Line endings: `.gitattributes` checks out `userspace/`, `initramfs/`, scripts and sources with
  LF; PKGBUILD checksums depend on it. Python on Windows writes CRLF unless told
  `newline="\n"`.
- Windows' real-time scan reads every freshly written boot image once, about a quarter of a
  second each; the host tests share one image for that reason.
- Build on the WSL file system (`/var/tmp/...`), not under `/mnt/c`, which is much slower.

## Conventions

- Python uses the standard library only, dataclasses and type hints, and short docstrings that
  explain why. Shell scripts use `set -euo pipefail`. C sources carry SPDX headers (BSL-1.0 for
  Monado code).
- External sources are pinned by commit or hash; patches live as `.patch` files next to their
  PKGBUILD, whose `sha256sums` are regenerated after a change. The project's own sources stay in
  `src/` and are `SKIP` in the recipes.
- Documentation states facts: what the system is, how it works, how to build, run and verify it,
  verified results and open work. History belongs in Git, not in the documents.
- Commits follow conventional commits (`feat:`, `docs:`, ...) on a step branch that is
  fast-forwarded into `main`.
