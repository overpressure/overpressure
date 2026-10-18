# CLAUDE.md

Guidance for working in this repository: Linux and a SteamOS-compatible userspace on the
original PICO 4 (2022, codename *phoenix*, Snapdragon XR2), always on its **stock** Android kernel
(4.19.81-perf+, PICO OS 5.13.7/5.13.8) and stock device trees.

## Where things stand

The roadmap is in [RESEARCH.md](RESEARCH.md) §5. Each step has its own document with
measurements and reproduction steps:

| Step | Status | Document |
| --- | --- | --- |
| 2 Linux shell (Alpine initramfs, USB NCM/SSH, read-only storage) | verified on hardware | [README.md](README.md), [HARDWARE-TEST.md](HARDWARE-TEST.md) |
| 3 Native graphics (Turnip/KGSL Vulkan, fenced KMS on both panels) | verified | [GRAPHICS.md](GRAPHICS.md) |
| 4 SteamOS-compatible userspace (Holo Core aarch64 + Steam Frame systemd 257.7) | verified | [USERSPACE.md](USERSPACE.md) |
| 5 XR hardware: head IMU via the sensor DSP, Monado 3DoF driver, Monado display output (KMS target) | verified; Monado service, cameras, controllers, 6DoF, lens distortion open | [XR.md](XR.md) |

Device preparation (EDL backups, engineering ABL, unlock, restore) is in [DEVICE.md](DEVICE.md).
For new work, continue from XR.md's "Next steps". Each doc ends with an evidence table: add
the new results there, and update the status notes in README.md and RESEARCH.md.

## Layout

- `build.py`, `device.py`, `hardware_test.py`: thin CLIs over `tools/` (standard-library Python 3.9+).
  - `tools/`: boot images, compression, cpio, kernel inspection, Alpine packages,
    `device.py` (EDL/fastboot/ADB),
  - `hardware_test.py` (bounded flash-run acceptance with stock restoration),
  - `userspace.py` (kernel compatibility, check/IMU/OpenXR validators),
  - `superimg.py` (Android super images), `windows_ncm.py`.
- `initramfs/`: stage 1. `pico4-init` is PID 1, not busybox init: busybox's `kill(-1)` killed the
  f_mass_storage kernel thread and with it the USB link. `pico4-userspace receive/switch` hands
  over to stage 2.
- `userspace/`: stage 2.
  - `packages.txt` and `pacman.conf`: the Holo preview first; Frame packages are named
    explicitly from `deckard-arch-hotfixes-release-0.3`.
  - `rootfs/`: overlay with udev devnode rules, units and the `pico4-imu` client.
  - `pkg/`: project PKGBUILDs:
    - `pico4-turnip-kgsl`;
    - `pico4-sensors` (qrtr-ns, pd-mapper and hexagonrpcd with this project's patches);
    - `pico4-monado` (Monado 25.1.0 + `pico4` driver and KMS compositor target).
- `scripts/`:
  - `build-userspace.sh` and `build-graphics.sh`: root on Linux/WSL, aarch64 via QEMU user
    emulation;
  - `extract-sensor-data.sh`;
  - preflight helpers.
- `src/`: project C sources (graphics tests, `pico4-reboot`, the OpenXR clients in `xr/`).
- `test/`: unittest suites, `test_recovery_policy.sh`, `test/qemu/` (4.19 test kernel,
  `run-selftest.sh`, `run-userspace.sh`).

## Commands

```sh
python -m unittest discover -s test -p "test_*.py"     # host tests (113), no hardware, ~45 s
bash test/test_recovery_policy.sh                      # 13 checks
python build.py build --firmware 5.13.7-global-seko    # stage-1 image -> out/
python build.py inspect                                # stock kernel config/DTB analysis
# Linux/WSL as root, on a Linux filesystem:
bash scripts/build-userspace.sh /var/tmp/pico4-userspace-build "$PWD/out/userspace" \
    --gpu-firmware assets/graphics-firmware-5.13.7 --sensor-data assets/sensor-data-5.13.7
test/qemu/run-userspace.sh out/pico4-poc-5.13.7-global-seko-initramfs.cpio.gz out/userspace/pico4-userspace.tar.zst
```

Notes on these commands:

- **Package caching.** The userspace build caches each project package by a hash of its
  recipe. Mesa is rebuilt only when that recipe changes; it takes about 30 minutes under QEMU.
- **Snapshot builds.** Build from a snapshot copy of the tree (`PICO4_PROJECT=...`), because
  editing files during a build broke one earlier.
- **QEMU vCPUs.** The QEMU harness defaults to 2 vCPUs: with 4 TCG vCPUs, RCU stalls
  intermittently timed out systemd's generator fork.

## Hard rules

- **Private data.** Never commit or redistribute device data or vendor files: `backup/`,
  `assets/` (engineering ABL, firehose, GPU firmware, sensor data), `out/` and `cache/` are
  ignored on purpose. Valve's packages and the built userspace archives are private too. Docs
  and code must not contain device serials or similar identifiers.
- **Stock kernel only.** No kernel or DTB changes; work around the 4.19 kernel in userspace.
  - systemd must stay at 257.x (258 needs Linux 5.4), pinned with `IgnorePkg` on the device.
  - There is no devtmpfs; udev creates device nodes through a PROGRAM rule.
  - The QRTR name service runs in userspace.
  - FastRPC uses the downstream `adsprpc` ABI.
- **Storage stays read-only.** Every physical block device is switched read-only in both
  stages. Nothing may write to the eMMC/UFS partitions except the explicit,
  backup-validated flash and restore paths.
- **Hardware actions need a recoverable path.** Boot flashing goes through
  `hardware_test.py flash-run` or the documented fastboot sequence. Check every write against
  the backup hash and verify it by readback. The headset must end on stock boot + stock ABL.
  Ask the user before the first flash or EDL action in a new conversation.
- **SLPI restart level.** Set it to `RELATED` before any restart (`slpi-boot` does this). The
  kernel default `SYSTEM` reboots the whole headset.
- **Never send controller/station OTA commands** when working on the controller SPI protocol.

## Hardware workflow (keep it fast)

The user wants no unnecessary flashes, reboots or waits, and no re-running of checks that just
passed.

- **Unattended setup.** The headset is usually reachable without the user: `adb reboot edl`
  works from PICO OS, and EDL needs no buttons.
- **Engineering ABL.** Flashing the POC needs the engineering ABL. The stock ABL reports
  `DeviceUnlocked=true`, and the runner refuses it. Writing boot also needs
  `fastboot oem picoQXXDTDFT unlock` first.
- **Iterate in stage 2.** Flash the dev image to `boot` once, then iterate over SSH:
  - stream the userspace archive, then push changed files into `/newroot` before the switch,
    or straight into the running stage 2;
  - for Monado, rebuild incrementally in the Holo bootstrap chroot and push a tarball.
- **Deadlines.** Device-side deadlines return the headset to fastboot. A kernel panic reboots
  rather than parking in crash-dump mode (stage 1 sets `msm_poweroff.download_mode=0`).
- **Acceptance.** Finish with one acceptance cycle and restore the stock image:
  `flash-run --userspace ...`, then EDL readback of boot, stock ABL restore, PICO OS check.
- **Local helpers.** Unversioned helpers for this live in `out/dev-loop/` and
  `out/userspace-hardware-cycle.ps1`. Run project PowerShell scripts with `pwsh` (7): Windows
  PowerShell 5.1 aborts on native stderr.
- **Windows NCM.** The Windows NCM link needs `tools/windows_ncm.py` (UsbDk) after each stage-1
  boot. Do not re-initialize NCM in stage 2.

## Environment quirks (Windows host + WSL)

- **Variable expansion.** `wsl -u root -- bash -c '... $VAR ...'` expands `$VAR` before bash
  runs, even inside single quotes. Write the commands to a script file and run
  `wsl -u root -- bash /mnt/c/.../script.sh`.
- **MSYS paths.** From Git Bash, export `MSYS_NO_PATHCONV=1`. Otherwise `/abs/paths` in
  arguments and environment values become `C:/Program Files/Git/...`.
- **Heredoc backslashes.** Git Bash heredocs passed to `python -` mangle backslashes: `"\n"`
  becomes a newline and `"\0"` a NUL byte. Use the Edit tool or a script file for such edits.
- **Platform tools.** `adb`/`fastboot` are in `%LOCALAPPDATA%\Android\Sdk\platform-tools`,
  not on PATH.
- **Line endings.** `userspace/**` is checked out with LF (`.gitattributes`); PKGBUILD
  checksums depend on it.

## Conventions

- **Match the code around you.** Python uses the standard library only, dataclasses and
  type hints, and short docstrings that explain why. Shell scripts use `set -euo pipefail`.
  C sources carry SPDX headers (BSL-1.0 for Monado code).
- **Pinned sources.** Pin external sources by commit or hash. Keep patches as `.patch` files
  under `userspace/pkg/<name>/`, and regenerate their `sha256sums` after any change.
- **Tests.** New behaviour gets host tests (fakes for hardware, golden data for protocols).
  The C protocol test runs inside the package build, because `makepkg --nocheck` is used.
- **Commits.** Use conventional commits (`feat:`, `docs:`, ...) on a step branch,
  fast-forwarded into `main`. Commit and push only when asked. Remote: `origin`.
