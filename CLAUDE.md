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
| 6 Valve's VR session: gamescope + SteamVR with `driver_monado`, `pico4-vrdisplay`, shared GPU and System V semaphores | verified with stock restore; 90 fps on the Frame's compositor path; Steam client, performance and cooling open | [docs/session.md](docs/session.md) |

Installed system: the system image read-only in `super`, data in `userdata`, the maintenance
stage 1 in `recovery`, following PICO OS's layout; installed, starts without the PC, persistent
across reboots and updates; the way back is open ([docs/install.md](docs/install.md)).
Powering off with the USB cable attached restarts the headset; a short power press powers off.
Cooling: `pico4-fan.service` runs PICO OS's fan policy on the lens-motor MCU; on the headset the
MCU acknowledges, and longer sessions and tuning are open ([docs/cooling.md](docs/cooling.md)).
Build system, tests and the Windows/WSL environment: [docs/development.md](docs/development.md).

## Backlog

The backlog lives in the issue tracker: project "PICO 4" (key `PICO`) in the "Devices"
workspace. Every new bug, gap (something unverified or unmeasured) or missing feature found
during work gets an issue there, in the active release, when it is found; search the project
first so that nothing is filed twice.

- **Kind.** `story` for features and gaps, `bug` for defects, `task` for technical work.
- **Description.** A user story ("As a ... I want ... so that ..."), a "Now:" line with the
  measured state, and short, exact acceptance criteria.
- **Labels.** The area (graphics, tracking, controllers, cooling, steam, ...) and, except for
  bugs, the category: `gap`, `feature`, `limitation` or `disabled`. Link blocking issues as
  dependencies.

Keeping the issues current is mandatory whenever work touches one, not a wrap-up step:

- **Status.** Move an issue to "In progress" when work on it starts, and back to "To do" when
  it is paused. "In review" means implemented with only a check left that needs the user or
  hardware; "Done" only when every acceptance criterion has been verified. Check the states of
  all issues the work touched before ending a task, including ones resolved on the side.
- **Comments.** Comment as results come in: what landed (commit), what was measured, which
  criteria are met and which are still open. A partial result keeps the issue open, with the
  open criteria named in the comment.

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
  `windows_ncm.py`, `avb.py` (AVB footers), `sparse.py` (fastboot sparse images), and the boot-image, cpio,
  Alpine (`apksign.py`: APKINDEX signatures), OTA and kernel helpers.
- `initramfs/`: stage 1. `pico4-init` is PID 1, not busybox init: busybox's `kill(-1)` killed
  the f_mass_storage kernel thread and with it the USB link. `pico4-userspace receive/switch`
  hands over to stage 2.
- `userspace/`: stage 2. `packages.txt` and `pacman.conf` (the Holo preview first; Frame
  packages named explicitly from `deckard-arch-hotfixes-release-0.3`), `rootfs/` (the adaptation
  layer), `session/` (SteamVR configuration), `pkgbuilds/` (pico4-turnip-kgsl, pico4-sensors,
  pico4-basalt, pico4-monado, pico4-vrdisplay, pico4-sysvsem, pico4-gtk2 for the Steam client,
  pico4-mangohud).
- `src/`: the project's C sources (`graphics/`, `monado/` driver and KMS target, `xr/` OpenXR
  clients, `vrdisplay/`, `sysvsem/`, `reboot/`). Recipes list them as `SKIP`; the build copies
  them in.
- `scripts/`: `build-userspace.sh`, `build-system-image.sh`, `build-graphics.sh`, `extract-sensor-data.sh`,
  `extract-wlan.sh`, `extract-audio.sh`, boot and
  preflight helpers. `tests/`: unittest suites, `test_recovery_policy.sh`, `test_vrdisplay_fake.sh`, `tests/qemu/`.
- Private and generated, ignored: `backup/` (identity `backup/device.json`, EDL backups),
  `assets/`, `cache/`, `out/` (`out/dev/` for the dev loop, `out/cycle-*/` evidence), `.env`.

## Commands

```sh
python -m unittest discover -s tests                     # host tests, ~6 s, no hardware
bash tests/test_recovery_policy.sh                       # stage-1 recovery policy
bash tests/test_vrdisplay_fake.sh                        # display layer on lavapipe (WSL; VULKAN_INCLUDE=...)
python -m pico4 build --firmware 5.13.7-global-seko      # stage-1 image -> out/
# Linux/WSL as root (incremental; native clang/lld on x86-64; --refresh for new Valve packages):
scripts/build-userspace.sh /var/tmp/pico4-userspace-build out/userspace \
    --gpu-firmware assets/graphics-firmware-5.13.7 --sensor-data assets/sensor-data-5.13.7 \
    --wlan assets/wlan-5.13.7 --audio assets/audio-5.13.7
# The headset, which stays in development mode (settings from .env); the integration test:
python -m pico4 dev boot && python -m pico4 dev check    # iterate on the flashed dev image
python -m pico4 dev reboot                               # next attempt; also from fastboot
# The installed system (docs/install.md): image as root, then from the maintenance stage 1:
scripts/build-system-image.sh out/userspace/pico4-userspace.tar.zst out/userspace/pico4-system.img
python -m pico4 dev recovery && python -m pico4 dev install   # boot, super, data; starts it
python -m pico4 dev installed && python -m pico4 dev check    # reconnect after a reboot; check
# Back to stock only when the user asks: python -m pico4 dev restore, then
python -m pico4 cycle --restore-only                     # verified stock boot and ABL, PICO OS
```

- The userspace build snapshots its inputs at the start: the tree can be edited during a build.
- Project packages rebuild only when their recipe changes, and incrementally when only `src/`
  changed; `--clean` forces a clean package build. Package databases change only with
  `--refresh`. Details and timings: docs/development.md.
- Test on the headset, not in QEMU. The QEMU boot tests in `tests/qemu/` emulate the whole
  system under TCG: a userspace boot takes 10–20 minutes and often fails on RCU stalls
  ("Failed to start up manager"), while `dev boot` and `dev check` answer in minutes on the real
  kernel. Don't run them unless the user asks.

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
- **Storage stays read-only, except the installed system's data.** Every physical block device
  is switched read-only in both stages; only the installed system's data partition (userdata,
  ext4 `pico4-data`) is mounted writable. Partitions are written only through the explicit,
  backup-validated paths: `dev flash` (boot, recovery), `pico4-install` in the maintenance
  stage 1 (boot, super, formatting userdata, wiping userdata and metadata, misc's bootloader
  message), `dev restore`/`dev restore-partition` and the EDL tools. `recovery` and `misc`
  must never be written by anything else: the ABL checks recovery's AVB chain on every boot.
- **Hardware actions need a recoverable path.** Boot flashing goes through
  `python -m pico4 hwtest flash-run`, `python -m pico4 cycle`, `python -m pico4 dev flash` or the
  documented fastboot sequence. Check every write against the backup hash and verify it by
  readback. The way back to stock (`dev restore`, `cycle --restore-only`) must keep working,
  but tasks do not end on stock: the headset stays in development mode. Ask the user before the
  first flash or EDL action in a new conversation.
- **SLPI, ADSP and WLAN restart level.** Set it to `RELATED` before any restart or start of the
  firmware (`slpi-boot`, `pico4-adsp.service` and `pico4-wlan.service` do this). The kernel
  default `SYSTEM` reboots the whole headset.
- **Sound card PCMs.** Only MultiMedia1 (PCM 0) gets device nodes: opening the capture side of
  the hostless MI2S PCM oopses the stock kernel and leaves that PCM's lock held until a reboot.
- **Never send controller/station OTA commands** when working on the controller SPI protocol.
  The station and GD32 (IPD, fan) modules build frames only from allowlists; keep it that way.
- **Cameras need the station.** The OV7251s only stream while the station pulses FSIN, so each
  capture resets it first. Only one process may own the cameras and the station at a time
  (stop Monado clients before `pico4-cam`).

## Hardware workflow (keep it fast)

The user wants no unnecessary flashes, reboots or waits, and no re-running of checks that just
passed.

- **Development mode.** The headset stays in development mode between tasks and
  conversations: the engineering ABL in `abl`, the dev image as the maintenance stage 1 in
  `recovery`, and the installed system (install boot image in `boot`, system in `super`, data
  in `userdata`). `dev recovery` keeps every boot in the maintenance stage 1 until `dev normal`
  (misc's bootloader message). Tasks do not restore stock boot, stock ABL or PICO OS, and do not
  run the cycle to prove the restoration. `hwtest flash-run` and `cycle` leave stock boot in
  `boot`, so they end development mode; with the system installed they cannot start PICO OS
  (its system is gone): use `dev restore` first.
- **Recovery and AVB.** `recovery` is a chained AVB partition that the ABL checks on every boot:
  write it only as the whole-partition image `dev image` builds (stock vbmeta kept), never a
  plain boot image, or every boot ends in fastboot. Large raw fastboot downloads crash the ABL;
  `dev flash`/`dev restore-partition` send big images sparse.
- **Entering development mode** (from PICO OS): `dev stage-abl`, `dev image`, `dev flash`,
  `dev connect`. It runs unattended: `adb reboot edl` works from PICO OS, and EDL needs no
  buttons. `.env` holds the headset's serials and the paths of its backup, loader and
  engineering ABL (see `.env.example`).
- **Engineering ABL.** Flashing the POC needs the engineering ABL (`python -m pico4 dev
  stage-abl`). The stock ABL reports `DeviceUnlocked=true`, and the runner refuses it. Writing
  boot also needs the per-session `fastboot oem <token> unlock` authorization, which the tools
  derive from the identity.
- **Iterate in stage 2.** Repeat `dev reboot`, `dev boot [ARCHIVE] [--push LOCAL=REMOTE]`,
  `dev check`, `dev ssh CMD` in the maintenance stage 1 (`dev recovery`); no flash or restore
  between attempts or tasks. A changed stage-1 image needs `dev image`, `dev flash recovery` and
  (for the installed boot) `dev install` again. `dev check` runs the acceptance runner's
  stage-2 checks, also on the installed system after `dev installed`.
- **Deadlines.** Device-side deadlines return the headset to fastboot; `dev reboot` starts
  stage 1 from there without a flash. The dev image's maximum runtime (an hour) counts from the
  last `python -m pico4 dev` command, which renews it; plain `ssh` does not. A kernel panic reboots rather than parking in crash-dump
  mode (stage 1 sets `msm_poweroff.download_mode=0`).
- **Heat.** `pico4-fan.service` drives the fan with PICO OS's policy: with the fan at 30 % the
  30 s Valve session check peaks at 69 °C (86 °C without fan control). Qualcomm's
  `thermal-engine` does not run (it is off on PICO OS too), and the kernel's `step_wise` zones
  throttle. Without fan control a minute of Valve's session took the hottest zone from about
  54 °C to 92 °C. Keep GPU-heavy runs short (`pico4-valve-session-check`
  runs 30 s), stop them as soon as the evidence is in, and read
  `/sys/class/thermal/thermal_zone*/temp` when in doubt.
- **Back to stock, only when the user asks** (PICO OS needed, a release acceptance):
  `dev restore`, then `python -m pico4 cycle --restore-only`, or `cycle --skip-stage
  --userspace ...` for an acceptance run that ends on stock. Once the headset stayed off after
  the final EDL reset with stock boot and ABL already verified; a hard reset by the user brought
  PICO OS back. If PICO OS does not appear, ask the user to look at the headset rather than
  retrying anything.
- **Windows NCM.** The Windows NCM link needs `pico4/windows_ncm.py` (UsbDk) after each stage-1
  boot (`dev connect`, `dev installed` and the runner do it). Re-initializing a working link in
  stage 2 disturbs it; `dev installed` repeats it at most once a minute while SSH times out.
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
