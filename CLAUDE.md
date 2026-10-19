# CLAUDE.md

Guidance for working in this repository: Linux and a SteamOS-compatible userspace on the original
PICO 4 (2022, codename *phoenix*, Snapdragon XR2), always on its **stock** Android kernel
(4.19.81-perf+, PICO OS 5.13.7/5.13.8) and stock device trees.

## Where things stand

The plan and the status of each step are in [docs/roadmap.md](docs/roadmap.md); its "Open work"
section lists what comes next. Each step has its own document:

| Step | Status | Document |
| --- | --- | --- |
| 1 Recoverable device (EDL backups, engineering ABL, unlock, restore) | done | [docs/device-preparation.md](docs/device-preparation.md) |
| 2 Linux shell (Alpine initramfs, USB NCM/SSH, read-only storage) | verified on hardware | [docs/stage1.md](docs/stage1.md), [docs/hardware-testing.md](docs/hardware-testing.md) |
| 3 Native graphics (Turnip/KGSL Vulkan, fenced KMS on both panels) | verified | [docs/graphics.md](docs/graphics.md) |
| 4 SteamOS-compatible userspace (Holo Core aarch64 + Steam Frame systemd 257.7) | verified | [docs/userspace.md](docs/userspace.md) |
| 5 XR hardware: IMU, Monado driver and KMS display, lenses and IPD, cameras, controllers (3DoF), Basalt 6DoF | verified with stock restore; controller positions open | [docs/xr.md](docs/xr.md) |
| 6 Valve's VR session: gamescope + SteamVR with `driver_monado`, `pico4-vrdisplay`, shared GPU and System V semaphores | verified with stock restore; ~50 fps GPU-bound; Steam client, performance and cooling open | [docs/session.md](docs/session.md) |

Cooling (fan control on the lens-motor MCU, a thermal policy) is in progress:
[docs/cooling.md](docs/cooling.md). Build system, tests and the Windows/WSL environment:
[docs/development.md](docs/development.md).

## Documentation

All documentation lives in `docs/`; the root README is the overview. Documents state facts:
what the system is, how it works, how to build, run and verify it, verified results (a table of
current facts) and open work. No work logs: no dated entries, no history of attempts, no
evidence file paths, no test counts. Git history is the log. When a result changes, update the
fact in place, and keep the status in docs/roadmap.md and README.md current.

## Layout

- `pico4/`: the host tooling, one standard-library Python package (3.9+): `python -m pico4
  build|inspect|match|device|hwtest|dev|cycle`. Modules: `image.py` (stage-1 image),
  `device.py` (EDL/fastboot/ADB), `hardware_test.py` (bounded acceptance runner), `cycle.py`
  (acceptance cycle), `devloop.py` (development loop), `localconfig.py` (`.env`),
  `userspace.py` and `graphics.py` (validators), `elfdeps.py` (ELF dependency check),
  `windows_ncm.py`, and the boot-image, cpio, Alpine, OTA and kernel helpers.
- `initramfs/`: stage 1. `pico4-init` is PID 1, not busybox init: busybox's `kill(-1)` killed
  the f_mass_storage kernel thread and with it the USB link. `pico4-userspace receive/switch`
  hands over to stage 2.
- `userspace/`: stage 2. `packages.txt` and `pacman.conf` (the Holo preview first; Frame
  packages named explicitly from `deckard-arch-hotfixes-release-0.3`), `rootfs/` (the adaptation
  layer), `session/` (SteamVR configuration), `pkgbuilds/` (pico4-turnip-kgsl, pico4-sensors,
  pico4-basalt, pico4-monado, pico4-vrdisplay, pico4-sysvsem).
- `src/`: the project's C sources (`graphics/`, `monado/` driver and KMS target, `xr/` OpenXR
  clients, `vrdisplay/`, `sysvsem/`, `reboot/`). Recipes list them as `SKIP`; the build copies
  them in.
- `scripts/`: `build-userspace.sh`, `build-graphics.sh`, `extract-sensor-data.sh`, boot and
  preflight helpers. `tests/`: unittest suites, `test_recovery_policy.sh`, `tests/qemu/`.
- Private and generated, ignored: `backup/` (identity `backup/device.json`, EDL backups),
  `assets/`, `cache/`, `out/` (`out/dev/` for the dev loop, `out/cycle-*/` evidence), `.env`.

## Commands

```sh
python -m unittest discover -s tests                     # host tests, ~6 s, no hardware
bash tests/test_recovery_policy.sh                       # stage-1 recovery policy
python -m pico4 build --firmware 5.13.7-global-seko      # stage-1 image -> out/
# Linux/WSL as root (incremental; native clang/lld on x86-64; --refresh for new Valve packages):
scripts/build-userspace.sh /var/tmp/pico4-userspace-build out/userspace \
    --gpu-firmware assets/graphics-firmware-5.13.7 --sensor-data assets/sensor-data-5.13.7
tests/qemu/run-userspace.sh out/pico4-poc-5.13.7-global-seko-initramfs.cpio.gz out/userspace/pico4-userspace.tar.zst
# The headset (settings from .env):
python -m pico4 dev boot && python -m pico4 dev check    # iterate on the flashed dev image
python -m pico4 cycle --userspace out/userspace/pico4-userspace.tar.zst   # acceptance, ends on stock
```

- The userspace build snapshots its inputs at the start: the tree can be edited during a build.
- Project packages rebuild only when their recipe changes, and incrementally when only `src/`
  changed; `--clean` forces a clean package build. Package databases change only with
  `--refresh`. Details and timings: docs/development.md.
- The QEMU userspace test takes about 10 minutes under TCG; run it when stage 1, the handoff
  or early boot change, on an otherwise idle host: concurrent heavy work (or 4 TCG vCPUs
  instead of the harness's 2) pushes systemd's generators past their limit and systemd stops
  with "Failed to start up manager".

## Hard rules

- **Private data.** Never commit or redistribute device data or vendor files: `backup/`,
  `assets/` (engineering ABL, firehose, GPU firmware, sensor data), `out/`, `cache/` and `.env`
  are ignored on purpose. Valve's packages and the built userspace archives are private too.
  Docs and code must not contain device serials or similar identifiers; they belong in `.env`.
- **Stock kernel only.** No kernel or DTB changes; work around the 4.19 kernel in userspace.
  - systemd must stay at 257.x (258 needs Linux 5.4), pinned with `IgnorePkg` on the device.
  - There is no devtmpfs; udev creates device nodes through a PROGRAM rule.
  - The QRTR name service runs in userspace.
  - FastRPC uses the downstream `adsprpc` ABI.
  - Real-time threads (Monado's compositor) need cgroups without the `cpu` controller:
    `CONFIG_RT_GROUP_SCHED` on cgroup v2 refuses `SCHED_FIFO` otherwise, even to root. The
    drop-ins in `userspace/rootfs/etc/systemd` keep it off; units added later must not set
    `CPUWeight`/`CPUQuota` or delegate `cpu`.
- **Storage stays read-only.** Every physical block device is switched read-only in both
  stages. Nothing may write to the eMMC/UFS partitions except the explicit,
  backup-validated flash and restore paths.
- **Hardware actions need a recoverable path.** Boot flashing goes through
  `python -m pico4 hwtest flash-run`, `python -m pico4 cycle`, `python -m pico4 dev flash` or the
  documented fastboot sequence. Check every write against the backup hash and verify it by
  readback. The headset must end on stock boot + stock ABL. Ask the user before the first flash
  or EDL action in a new conversation.
- **SLPI restart level.** Set it to `RELATED` before any restart (`slpi-boot` does this). The
  kernel default `SYSTEM` reboots the whole headset.
- **Never send controller/station OTA commands** when working on the controller SPI protocol.
  The station and GD32 (IPD, fan) modules build frames only from allowlists; keep it that way.
- **Cameras need the station.** The OV7251s only stream while the station pulses FSIN, so each
  capture resets it first. Only one process may own the cameras and the station at a time
  (stop Monado clients before `pico4-cam`).

## Hardware workflow (keep it fast)

The user wants no unnecessary flashes, reboots or waits, and no re-running of checks that just
passed.

- **Unattended setup.** The headset is usually reachable without the user: `adb reboot edl`
  works from PICO OS, and EDL needs no buttons. `.env` holds the headset's serials and the
  paths of its backup, loader and engineering ABL (see `.env.example`).
- **Engineering ABL.** Flashing the POC needs the engineering ABL (`python -m pico4 dev
  stage-abl`). The stock ABL reports `DeviceUnlocked=true`, and the runner refuses it. Writing
  boot also needs the per-session `fastboot oem <token> unlock` authorization, which the tools
  derive from the identity.
- **Iterate in stage 2.** Flash the dev image once (`dev image`, `dev flash`), then repeat
  `dev reboot`, `dev boot [ARCHIVE] [--push LOCAL=REMOTE]`, `dev check`, `dev ssh CMD`; no
  restore between attempts. `dev check` runs the acceptance runner's stage-2 checks.
- **Deadlines.** Device-side deadlines return the headset to fastboot. A kernel panic reboots
  rather than parking in crash-dump mode (stage 1 sets `msm_poweroff.download_mode=0`).
- **Heat.** Nothing cools the headset actively under this userspace yet (no fan control, no
  Qualcomm `thermal-engine`; only the kernel's `step_wise` zones throttle the CPUs). Valve's
  session takes the hottest zone from about 54 °C to 92 °C within a minute. Keep GPU-heavy runs
  short (`pico4-valve-session-check` runs 30 s), stop them as soon as the evidence is in, and
  read `/sys/class/thermal/thermal_zone*/temp` when in doubt.
- **Acceptance.** Finish with one cycle: `dev restore`, then `python -m pico4 cycle --skip-stage
  --userspace ...` (or the full cycle from PICO OS). Once the headset stayed off after the
  final EDL reset with stock boot and ABL already verified; a hard reset by the user brought
  PICO OS back. If PICO OS does not appear, ask the user to look at the headset rather than
  retrying anything.
- **Windows NCM.** The Windows NCM link needs `pico4/windows_ncm.py` (UsbDk) after each stage-1
  boot (`dev connect` and the runner do it). Do not re-initialize NCM in stage 2.
- Binary files from the device: use `ssh` directly; `dev ssh` decodes output as text.

## Environment quirks (Windows host + WSL)

- **Variable expansion.** `wsl -u root -- bash -c '... $VAR ...'` expands `$VAR` before bash
  runs, even inside single quotes. Write the commands to a script file and run
  `wsl -u root -- bash /mnt/c/.../script.sh`.
- **MSYS paths.** From Git Bash, export `MSYS_NO_PATHCONV=1`. Otherwise `/abs/paths` in
  arguments and environment values become `C:/Program Files/Git/...`.
- **Backslashes.** Commands sent through the Bash tool collapse `\\` to `\`, also inside quoted
  heredocs, so file content with double backslashes arrives mangled. Write such files with the
  file tools, or build backslashes with `chr(92)` in Python.
- **CRLF.** Python on Windows writes CRLF unless `newline="\n"`; files under `userspace/`,
  `initramfs/` and scripts must stay LF (PKGBUILD checksums depend on it).
- **Platform tools.** `adb`/`fastboot` are in `%LOCALAPPDATA%\Android\Sdk\platform-tools`,
  not on PATH; `PICO4_PLATFORM_TOOLS` in `.env` points there.
- **PowerShell.** Run project PowerShell scripts with `pwsh` (7): Windows PowerShell 5.1 aborts
  on native stderr.

## Conventions

- **Match the code around you.** Python uses the standard library only, dataclasses and
  type hints, and short docstrings that explain why. Shell scripts use `set -euo pipefail`.
  C sources carry SPDX headers (BSL-1.0 for Monado code).
- **Pinned sources.** Pin external sources by commit or hash. Keep patches as `.patch` files
  under `userspace/pkgbuilds/<name>/`, and regenerate their `sha256sums` after any change.
- **Tests.** New behaviour gets host tests (fakes for hardware, golden data for protocols).
  The C protocol tests run inside the package builds, because `makepkg --nocheck` is used.
- **Commits.** Use conventional commits (`feat:`, `docs:`, ...) on a step branch,
  fast-forwarded into `main`. Commit and push only when asked. Remote: `origin`.
