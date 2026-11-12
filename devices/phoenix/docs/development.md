# Development

| | |
| --- | --- |
| What | how the PICO 4's part is laid out, built and tested, and the fastest way from a change to the headset |
| Build | `python -m overpressure assets`, then the userspace and system image in the build container (`python -m overpressure container run -- ...`) |
| Test | `python -m unittest` on the host; the installed system (`dev install`, `dev check`) is the integration test |
| Code | `devices/phoenix/` ([layout](#repository-layout)); the generic package `overpressure/` ([docs/tool.md](../../../docs/tool.md)) |

The headset-side workflows (acceptance runner, acceptance cycle, development loop) are in
[hardware-testing.md](hardware-testing.md). Paths are relative to `devices/phoenix/`; commands run from the
repository root.

## Repository layout

```text
tooling/          the PICO 4's host tooling (Python): python -m overpressure COMMAND
  __init__.py       HEADSET: its commands, the window's steps and the installer's workflows
  install.py        the installer's three workflows: install, upgrade, restore to stock
  image.py          stage-1 boot image (build, inspect, match) on overpressure/stage1.py
  firmware.json     PICO's OTA builds
  stock.py          the members of PICO's full OTA the assets need (overpressure/ota.py)
  assets.py         every file the userspace build takes from them (overpressure/assets.py)
  device.py         identity, EDL backups, engineering ABL, unlock and restore; unlockfiles.py: their downloads
  hardware_test.py  the bounded acceptance runner (preflight, flash-run)
  cycle.py          the unattended acceptance cycle that ends on stock boot and ABL
  devloop.py        the development loop: maintenance stage 1, install, checks, restore, release check
  localconfig.py    the headset's settings from .env (PICO4_ keys)
  userspace.py, graphics.py   validators of the on-device checks
  ledmodel.py, aimgrip.py, leverarm.py, controllermodel.py   controller data from PICO OS's libraries
initramfs/        stage 1: files added to the Alpine ramdisk (pico4-init, services, pico4-install)
userspace/        stage 2: the SteamOS-compatible userspace
  packages.json     the release of overpressure/packages the image is built from (its definition and packages)
  rootfs/           the adaptation layer (preinit, units, udev rules, PAM, session, device tools, checks)
  session/          the session user's SteamVR configuration
kernel/           the pinned release of overpressure/linux (release.json), build-tree.sh, imx471_mode.py,
                  recovery_image.py
src/              C sources outside the packages: stockslam/ (pico4-slamd), reboot/ (pico4-reboot),
                  splash/ (pico4-splash), gd32/
prebuilt/         static aarch64 pico4-reboot and pico4-splash, so stage 1 builds without a compiler
scripts/          build-userspace.sh, build-system-image.sh, build-kernel-step.sh, upgrade-check.sh (with
                  upstream.py), check-package-lock.py and the on-headset preflight
tests/            host tests (unittest), test_recovery_policy.sh, test_charger.sh, test_boot_attempts.sh, qemu/
docs/             documentation
```

The root package `overpressure/` holds what other headsets share (listed in the repository's
[CLAUDE.md](../../../CLAUDE.md)): `.env` settings, the doctor, the stage-1 builder, OTA handling, adb and
fastboot, Qualcomm EDL, verified backups and the backup file, processes and waits, SSH, recorded runs, the
read-only storage policy, the disclaimer (`disclaimer.py`) and the computer's power check (`power.py`), the
formats (with the ext4 and FAT readers), `elfdeps.py`, `windows_ncm.py`, and `linux.py` with `container/`
(the build container). `scripts/stock-lib.sh` serves the kernel build (a file out of the stock vendor image,
through overpressure's ext4 reader).

Private and generated, out of Git: `backup/` (identity and EDL backups), `assets/` (Firehose loader,
engineering ABL, GPU firmware, sensor data), `cache/` (downloads), `out/` (build output and test evidence;
`out/dev/` for the development loop) and `.env` (the headset's settings, see
[.env.example](../../../.env.example)).

## Build host

`python -m overpressure doctor` (*Check dependencies* in the window) checks this computer and says how to fix
what is missing; `python -m overpressure tools` fetches adb, fastboot and edl-ng
([tooling/doctor.py](../tooling/doctor.py), [docs/tool.md](../../../docs/tool.md#dependencies)).

| Task | Needs |
| --- | --- |
| Stage-1 image, stock firmware and assets, host tests, headset commands | Python 3.9+ on Windows, Linux or macOS (the start scripts or `uv sync` add the pinned brotli, pyusb and libusb-package); platform-tools and OpenSSH for the headset |
| Userspace archive, system image, Valve's kernel, the QEMU test kernel | the build container ([docs/tool.md](../../../docs/tool.md#linux-root-steps)): Docker or Podman on any host. Its image has every tool, LLVM 20 and qemu-user-static, and registers the aarch64 binfmt handler (F flag) on an x86-64 engine that has none |
| Windows NCM initialization (stage 1 on the stock kernel) | UsbDk; pyusb and libusb-package |

On an arm64 engine (Apple silicon, an arm64 Linux host) the build environment runs natively.

### Native compilers instead of an arm64 build machine

The build environment is an arm64 root file system. On x86-64 its programs run under QEMU user emulation,
roughly ten times slower than natively, except the compilers: the host's x86-64 clang and lld run directly and
compile for `aarch64-unknown-linux-gnu` with the environment as sysroot (its glibc, GCC 15 runtime and
libraries). makepkg, CMake and Meson configuration, code generators, pacman and its hooks still run emulated.
Packages built by clang use lld and no LTO (LTO's bitcode objects would need LLVM's archiver in every build
system). overpressure/packages' releases are built natively on arm64 with the environment's GCC, so a local
build on x86-64 is for trying a change, not the release's bytes.

Clean package builds with the host's clang on a 12-core i7-12700KF (20 threads) under WSL2:

| Package | Build time |
| --- | --- |
| pico4-basalt | 2.9 min |
| pico4-monado | 2.8 min |
| sm8250-hexagonrpc | 1.0 min |
| overpressure-gtk2 (GTK 2.24.33, libraries and modules; Autotools under QEMU) | 14 min |

A Raspberry Pi 5 compiles at about the speed of this host's emulation. Faster arm64 hosts: Apple silicon,
Snapdragon X, Ampere and Graviton cloud instances. Mind the private data rules before building on someone else's
machine.

## Stock firmware

The image needs PICO OS's firmware and data files (DSP images, the head tracker, configuration). They are the
same on every PICO 4 with that firmware and come from PICO's public full OTA, not from the user's backup:

```sh
# Any system, no root and no Linux: the OTA's partitions (about 3.6 GB of OTA members, kept under
# STOCK/download), then every asset from them into assets/*-5.13.7
python -m overpressure assets --firmware 5.13.7-global-seko [--stock STOCK] [--assets DIR] [KIND...]
```

`assets` ([tooling/assets.py](../tooling/assets.py), the window's *Stock firmware and assets*) builds the stock
folder (`STOCK`, by default `cache/pico4-stock/FIRMWARE`) with `python -m overpressure stock`, then each kind
into a fresh `assets/KIND-VERSION.new` that replaces the old folder only when complete. It is Python only: the
stock images are read by overpressure's read-only ext4 and FAT readers
([overpressure/ext4.py](../../../overpressure/ext4.py), [overpressure/fat.py](../../../overpressure/fat.py)), the
OTA's block images decompressed by the pinned brotli package (or the `brotli` command without it). No
e2fsprogs, mtools or Linux is involved.

`stock` ([tooling/stock.py](../tooling/stock.py)) takes from the OTA zip only the members it needs, each checked
against the zip's CRC: `system` and `vendor` as ext4 images rebuilt from their brotli block images and transfer
lists, `firmware-update/NON-HLOS.bin` as `modem.bin` (FAT16), `firmware-update/dspso.bin` as `dsp.bin` (ext4),
`recovery.img` and `boot.img`; `manifest.json` holds their sha256, which `assets` checks before it reads an image
([overpressure/assets.py](../../../overpressure/assets.py)). The firmware is the headset's (`PICO4_FIRMWARE`,
`python -m overpressure match`, [stage1.md](stage1.md)). The kernel step reads the same folder's `vendor.img` in
the build container (the repository at `/work`). The results are proprietary: keep the stock folder and
`assets/` private.

| Kind (`assets/KIND-VERSION`) | Content | From |
| --- | --- | --- |
| `graphics-firmware` | GPU firmware `a650_*`, the video codec's `venus.*` | vendor `/firmware`, modem `/image` |
| `sensor-data` | SLPI image, sensor configuration, lens configuration, controller models and data | modem, vendor, system |
| `wlan` | Wi-Fi chip firmware and board data | modem |
| `audio` | ADSP image, audio process files and DSP libraries, calibration database | vendor, modem |
| `stock-slam` | Compute DSP image, the tracker's bionic root, DSP modules and process shells | modem, system, vendor, dsp |

Each folder has a `manifest.json` (schema 2: the firmware, the sources by path prefix, every file's SHA-256),
which `build-userspace.sh` reads. Files keep the image's permissions and modification times where the host's
file system has them, not its ownership. On Windows they carry no POSIX modes (the bind mount shows 0777 or
0755), so the userspace build installs them as 0644 files and 0755 directories, programs as 0755 explicitly.

| Measured on PICO OS 5.13.7 (Windows 11, i7-12700KF, NVMe) | Result |
| --- | --- |
| Assets compared with an extraction by debugfs and mtools | every file byte-identical, every manifest identical |
| The ext4 reader on system, vendor, dsp and persist | every file, link target, mode, owner and modification time as debugfs reads them |
| The FAT reader on `modem.bin` | every file and its time as mtools reads them |
| Time and disk, from an empty folder | 20 min, almost all the 3.6 GB download (at about 3 MB/s); both images rebuilt in about 30 s, the five kinds in about 10 s. 10.8 GB in the stock folder (3.6 GB OTA members, 7.0 GB images), 133 MB of assets |

For 5.13.7 every asset file is byte-identical to the same file in an EDL backup of a headset on that firmware.
The headset's own factory data (sensor registry and calibration, camera, IMU and lens calibration, the serial
the fan's table follows, the Wi-Fi MAC addresses) stays in its `persist` partition, mounted read-only at boot
([userspace.md](userspace.md#the-headsets-own-data)). The dev image takes its stock recovery (device-tree
overlay and AVB footer) from the same OTA. The user's backup remains the way back to stock and the reference
of the write checks ([install.md](install.md)).

## The userspace build

```sh
# In the build container, as root (the window's "Userspace archive" step and make userspace do the
# same with these defaults):
python -m overpressure container run -- bash devices/phoenix/scripts/build-userspace.sh \
    /var/tmp/pico4-userspace-build out/userspace \
    --gpu-firmware assets/graphics-firmware-5.13.7 --sensor-data assets/sensor-data-5.13.7 \
    --wlan assets/wlan-5.13.7 --audio assets/audio-5.13.7 --stock-slam assets/stock-slam-5.13.7
```

| Option | Effect |
| --- | --- |
| `--gpu-firmware DIR` | GPU and Venus firmware, `assets/graphics-firmware-VERSION`; without it Vulkan cannot start |
| `--sensor-data DIR` | sensor DSP firmware and configuration, lens configuration and controller data, `assets/sensor-data-VERSION` |
| `--wlan DIR` | Wi-Fi firmware and board data, `assets/wlan-VERSION` |
| `--audio DIR` | ADSP firmware and the audio process's files, `assets/audio-VERSION` |
| `--stock-slam DIR` | PICO OS's head tracker, the Compute DSP's firmware and the tracker's bionic root, `assets/stock-slam-VERSION` ([xr.md](xr.md#head-tracking)); needs the host's clang |
| `--refresh` | download current package databases and reinstall the base root |
| `--update-lock` | write `OUTPUT_DIR/packages.lock` for review instead of checking the build against the pinned lock ([userspace.md](userspace.md#package-lock)) |
| `--packages DIR` | build a checkout of [overpressure/packages](https://github.com/overpressure/packages) instead of the commit `userspace/packages.json` pins: a change to a package, tried in the image |
| `--packages-release` | take the pinned release's packages (built on arm64 with GCC) instead of building them: downloaded into `cache/packages-release/TAG`, each checked against the release's `SHA256SUMS`, whose hash the pin holds |

Each step is skipped when its inputs did not change:

1. **Snapshot.** The inputs (`overpressure/`, `src/`, `userspace/`, the shared stage-1 scripts, `prebuilt/`) and
   overpressure/packages at the pinned commit (fetched once with git into `cache/packages/COMMIT`, or the
   `--packages` checkout) are copied into the build root first, so the trees can be edited during a build.
2. **Build environment and project packages.** overpressure/packages' `build/packages.sh`: Valve's Holo Core
   aarch64 preview root file system, pinned by SHA-256, unpacked once, with the packages the recipes build
   against, each pinned by the lock or the Holo snapshot. Package databases are downloaded by the first build
   and with `--refresh` only. makepkg builds each recipe of the PICO 4's layers when its folder or the compiler
   changes; each layer becomes a local pacman repository `overpressure-LAYER`.
3. **Base root.** The definition's `packages.txt` installed by pacman from the project's layers, the Steam Frame
   hotfix repository and the Holo Core preview; reinstalled when the package list or `pacman.conf` changes, and
   with `--refresh`.
4. **Image root.** An overlay on the base root: the project packages that differ from the base root's, the
   adaptation layer (`userspace/rootfs`, shared stage-1 scripts, users, units) and the session configuration.
   [overpressure/elfdeps.py](../../../overpressure/elfdeps.py) checks natively that the programs and libraries of
   the Frame's and the project's packages, and of those depending on them, find their libraries and symbol
   versions and that the programs' symbols are all defined; the script excludes a few optional programs by name.
5. **Archive.** The upstream packages are checked against the definition's `packages.lock`, then
   `pico4-userspace.tar.zst` (zstd level 12, multithreaded) and `pico4-userspace.manifest.json` with the
   sources, hashes and installed package versions.

Times on the same host, with an archive of about 800 MB:

| Change | Build time |
| --- | --- |
| nothing, or only the adaptation layer (`userspace/rootfs`, `userspace/session`) | 45 s |
| the package list or `pacman.conf` (new base root, packages reused) | 2 min |
| everything: a new build root | about 16 min |

Writing the archive takes about 25 s of each. A changed package rebuilds in full; makepkg's stripping, debug
packages and compression run emulated: about a minute for Monado.

## Upgrade check

The image pins every package, which move only with a release
([docs/releasing.md](../../../docs/releasing.md)). Whether the image would still build on what Valve publishes
meanwhile is checked weekly in CI on GitHub's ARM64 runner ([upgrade.yml](../../../.github/workflows/upgrade.yml),
Mondays, or by hand), and on any computer with the build container:

```sh
python -m overpressure container run -- bash devices/phoenix/scripts/upgrade-check.sh \
    /var/tmp/pico4-upgrade-check out/upgrade-check
```

[`upgrade-check.sh`](../scripts/upgrade-check.sh) needs none of PICO OS's files:

| Part | What it does | Fails when |
| --- | --- | --- |
| Userspace | `build-userspace.sh --refresh --update-lock` in its own build root, without the asset folders; [`upstream.py lock-diff`](../scripts/upstream.py) lists every package that moved since the pinned `packages.lock`, the session's packages first | a project package does not build, a patch does not apply, or a dependency stays unresolved |

`out/upgrade-check/upgrade-check.md` is the summary; the CI run's page shows it. A moved package that still
builds fails nothing: whether the session runs on it is the headset's check before a release.

## Tests

| Level | Command | Takes |
| --- | --- | --- |
| Host tests: protocols, validators, the installer, runner, cycle and dev loop against fakes (with the generic tests) | `python -m unittest` | seconds |
| Stage-1 recovery policy | `bash devices/phoenix/tests/test_recovery_policy.sh` | seconds |
| Stage-1 charger mode | `sh devices/phoenix/tests/test_charger.sh` | seconds |
| Count of unfinished boots | `bash devices/phoenix/tests/test_boot_attempts.sh` | seconds |
| The host tests and every shell test above | `make test` (on Linux) | seconds |
| The same on any host, in the build container, as root | `python -m overpressure container run -- make test` (`make container-test`) | as `make test`, once the image is built |
| C tests of this repository's own sources (CI runs them) | `bash scripts/c-tests.sh` (a C compiler) | seconds |
| Shellcheck over every tracked shell script, down to warnings (CI runs it) | `python -m overpressure container run -- python3 scripts/run-shellcheck.py` | seconds |
| On the headset: the integration test | `python -m overpressure dev install` and `dev check`, on the installed system | minutes; [hardware-testing.md](hardware-testing.md) |
| Optional: stage 1 on a QEMU test kernel (from `tests/qemu/build-test-kernel.sh`) | `devices/phoenix/tests/qemu/run-selftest.sh` (`make qemu-stage1`) | about 3 min |

New behaviour gets host tests with fakes for hardware and golden data for protocols. The packages' tests (the
driver's C protocol tests, the controller tracker's C++ tests) run inside their builds in overpressure/packages.

## From a change to the headset

The headset stays in development mode: the engineering ABL, the dev image in `recovery` on the stock kernel and
the installed system on Valve's kernel ([hardware-testing.md](hardware-testing.md#development-mode),
[install.md](install.md)). Commands that touch the headset ask the disclaimer first; `--accept-disclaimer` (or
`OVERPRESSURE_ACCEPT_DISCLAIMER=1`) accepts it for an unattended run.

1. Change the code, then build: `make userspace` (or the script above), incremental. A change to a package is
   made in a checkout of overpressure/packages and built into the image with `--packages DIR`; once it is
   released there, `userspace/packages.json` pins the new tag.
2. The system image: the window's *System image for super*, or `build-system-image.sh` in the build container
   ([install.md](install.md#install-and-update)).
3. `python -m overpressure dev recovery` (it stays until `dev normal`), then `python -m overpressure dev install`,
   which starts the installed system.
4. `python -m overpressure dev check`; `dev installed` reconnects after a reboot.

A changed stage-1 image is built with `python -m overpressure build` and written with `dev image` and
`dev flash`; the installed system's boot image holds no stage 1. A new kernel goes through `dev boot-image`,
`dev recovery` and `dev install --boot PATH` ([install.md](install.md#install-and-update)). A task ends in
development mode; returning to stock is a separate step, for when PICO OS is needed
([hardware-testing.md](hardware-testing.md#back-to-stock)).

### Power while tethered

Tethered work needs a charging USB port (CDP) or a wall charger. The headset's charger sets its input by BC1.2
detection, also through a USB-C to USB-A adapter; Type-C's "high current" is the adapter's pull-up resistor,
which the charger rightly ignores.

| Port | Charger input | Battery |
| --- | --- | --- |
| Standard port (`real_type` `USB`) | 0.49 A at 4.74 V, about 2.3 W | drains during a session, 0.29–0.78 A |
| Charging downstream port (`USB_CDP`) | settles at 1.5 A and draws 1.49 A at 4.49 V, about 6.7 W | session stopped: +1.13 A; Valve's session in standby: +0.78 A; rendering: drains, about 0.37 A |

On a CDP the input is the limit (`input_current_limited` 1, `input_current_settled` 1.5 A): 6.7 W is about what
Valve's session draws while it renders. The charger reports "Charging" also while the battery drains; the
battery's `current_now` (positive while charging) tells. `pico4-userspace-check` reports port type, input limit,
input current, VBUS and the battery's state (`power`); `dev check` warns on a standard port, when the battery
drains while tethered, and when it charges at less than 200 mA below 95 % (`power_warnings` in
[tooling/userspace.py](../tooling/userspace.py)).

## Windows host

- From Git Bash, set `MSYS_NO_PATHCONV=1`; otherwise `/abs/paths` in arguments and environment values become
  `C:/Program Files/Git/...`.
- Run project PowerShell scripts with `pwsh` (7); Windows PowerShell 5.1 aborts on native stderr.
- `python -m overpressure tools` fetches `adb` and `fastboot` into `cache/tools`; `PICO4_PLATFORM_TOOLS` can name
  an Android SDK's own (`%LOCALAPPDATA%\Android\Sdk\platform-tools`) instead.
- Line endings: `.gitattributes` checks out `userspace/`, `initramfs/`, scripts and sources with LF; PKGBUILD
  checksums depend on it. Python on Windows writes CRLF unless told `newline="\n"`.
- Windows' real-time scan reads every freshly written boot image once, about a quarter of a second each; the
  host tests share one image for that reason.
- The build roots live in the build container's volume (`/var/tmp`, a Linux file system); the repository's bind
  mount from Windows is much slower, so build trees do not go under `/work`.

## Conventions

The repository's [CLAUDE.md](../../../CLAUDE.md) has them: Python on the standard library and the packages
`pyproject.toml` pins, each with a fallback; `set -euo pipefail` in shell scripts; SPDX headers in C (BSL-1.0
for Monado code); external sources pinned by commit or hash, patches as `.patch` files next to their PKGBUILD
with regenerated `sha256sums`; the project's own sources in `src/`, `SKIP` in the recipes; documents state facts;
conventional commits.
