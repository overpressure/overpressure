# CLAUDE.md: PICO 4 (phoenix)

Guidance for working on the PICO 4 in overpressure (the repository's [CLAUDE.md](../../CLAUDE.md)
has the general rules): Linux and a SteamOS-compatible userspace on the original PICO 4 (2022,
codename *phoenix*, Snapdragon XR2), on **Valve's kernel** (the Steam Frame's 7.2, built by
`kernel/build-kernel.sh`, [docs/kernel-feasibility.md](docs/kernel-feasibility.md)) with Valve's Mesa.
The stock Android kernel (4.19.81-perf+, PICO OS 5.13.7/5.13.8) is no longer pursued: the way back
to PICO OS stays, the image no longer drives the GPU there, and the documents' stock-kernel paths are
history until they are removed. Paths are relative to this folder (`devices/phoenix/`); commands run
from the repository root.

## Where things stand

The plan and the status of each step are in [docs/roadmap.md](docs/roadmap.md); its "Open work"
section lists what comes next. Each step has its own document:

| Step | Status | Document |
| --- | --- | --- |
| 1 Recoverable device (EDL backups, engineering ABL, unlock, restore) | done | [docs/device-preparation.md](docs/device-preparation.md) |
| 2 Linux shell (Alpine initramfs, USB NCM/SSH, read-only storage) | verified on hardware | [docs/stage1.md](docs/stage1.md), [docs/hardware-testing.md](docs/hardware-testing.md) |
| 3 Native graphics (Valve's Mesa on msm, compressed scanout on both panels) | verified | [docs/graphics.md](docs/graphics.md) |
| 4 SteamOS-compatible userspace (Holo Core aarch64 + Steam Frame systemd 257.7) | verified | [docs/userspace.md](docs/userspace.md) |
| 5 XR hardware: IMU, Monado driver, lenses and IPD, cameras, controllers (inputs, vibration, positions), 6DoF (PICO OS's libSlam on the CDSP; Basalt selectable), colour room view | verified worn | [docs/xr.md](docs/xr.md) |
| 6 Valve's VR session: gamescope + SteamVR with `driver_monado` in direct mode through Turnip's display WSI | verified worn; 90 fps on the Frame's compositor path; the Steam client signs in, its library runs games through Proton and FEX, Android apps through Lepton, Steam Link from a PC; heavy games' frame rate open | [docs/session.md](docs/session.md) |

Installed system: the system image read-only in `super`, data in `userdata`, the maintenance
stage 1 in `recovery`, following PICO OS's layout; installed, starts without the PC, persistent
across reboots and updates; the way back is open ([docs/install.md](docs/install.md)).
Powering off with the USB cable attached starts the headset in charger mode (stage 1 waits for a
2 s power press; `pico4-reboot` from there starts the system); a short power press does nothing.
Cooling: `pico4-fan.service` runs PICO OS's fan policy on the lens-motor MCU; on the headset the
MCU acknowledges, and longer sessions and tuning are open ([docs/cooling.md](docs/cooling.md)).
Build system, tests and the Windows/WSL environment: [docs/development.md](docs/development.md).

## Backlog

Project "Overpressure" (key `the backlog`) in the issue tracker; the rules are in the repository's
CLAUDE.md.

## Documentation

The PICO 4's documents are in `docs/` here, its overview in `README.md` here; the repository's
CLAUDE.md has the rules (facts only, no work logs). Keep the status in docs/roadmap.md and
README.md current.

## Layout

- `tooling/`: the PICO 4's host tooling, a standard-library Python package (3.9+), reached as
  `python -m overpressure build|inspect|match|stock|device|hwtest|dev|cycle`; `__init__.py`
  holds `HEADSET` (its commands and the GUI's steps). Modules: `image.py` (stage-1 image on `overpressure/stage1.py`),
  `device.py` (EDL/fastboot/ADB), `hardware_test.py` (bounded acceptance runner), `cycle.py`
  (acceptance cycle), `devloop.py` (development loop), `localconfig.py` (`.env`, `PICO4_` keys),
  `userspace.py` and `graphics.py` (validators), `stock.py` (PICO's OTA members),
  `ramboot.py` (its target for overpressure's RAM boot; the engineering ABL refuses it),
  `doctor.py` (its dependency checks), and the
  controller data extractors. What other headsets share is in the root package `overpressure/`: the stage-1 builder,
  OTA handling, adb/fastboot, Qualcomm EDL, verified backups, processes and waits, SSH, the RAM
  boot, the formats, `elfdeps.py` and `windows_ncm.py`.
- `initramfs/`: stage 1. `pico4-init` is PID 1, not busybox init: busybox's `kill(-1)` killed
  the f_mass_storage kernel thread and with it the USB link. `pico4-userspace receive/switch`
  hands over to stage 2.
- `kernel/`: Valve's kernel: `build-kernel.sh`, `patches/`, the PICO's drivers in `src/`, the device
  tree `sm8250-pico4-phoenix.dts`, `recovery_image.py` for its boot images.
- `userspace/`: stage 2. `packages.txt` and `pacman.conf` (the Holo preview first; Frame
  packages named explicitly from `deckard-arch-hotfixes-release-0.3`), `rootfs/` (the adaptation
  layer), `session/` (SteamVR configuration), `pkgbuilds/` (pico4-sensors, pico4-basalt, pico4-monado,
  pico4-gamescope, pico4-vrlink, pico4-gtk2 for the Steam client, pico4-mangohud, and others;
  `pico4-swapchain-usage`).
- `src/`: the project's C sources (`graphics/`, `monado/` driver, `xr/` OpenXR clients, `vrlink/`,
  `swapchain/`, `reboot/`, `splash/`). Recipes list them as `SKIP`; the build copies
  them in. `stockslam/` is `pico4-slamd`, which runs PICO OS's head tracker (libSlam) in a
  bionic root; `build-userspace.sh` builds it with clang for Android, without an NDK.
- `scripts/`: `build-userspace.sh`, `build-system-image.sh`, the extraction
  scripts `extract-graphics-firmware.sh`, `extract-sensor-data.sh`, `extract-wlan.sh`,
  `extract-audio.sh`, `extract-stock-slam.sh` (from the stock directory of `python -m
  overpressure stock`, PICO's public OTA; overpressure's `scripts/stock-lib.sh`) and the on-headset preflight. The
  headset's own factory data is not extracted: the image links to persist, mounted read-only at
  boot (`pico4-persist`). `tests/`: unittest suites, `test_recovery_policy.sh`,
  `test_charger.sh`, `tests/qemu/`. `device.mk`: its Makefile targets.
- Private and generated, ignored, at the repository root: `backup/` (identity
  `backup/device.json`, EDL backups), `assets/`, `cache/`, `out/` (`out/dev/` for the dev loop,
  `out/cycle-*/` evidence), `.env`.

## Commands

From the repository root (the window, `python -m overpressure gui`, runs the same steps):

```sh
python -m unittest                                       # host tests, seconds, no hardware
bash devices/phoenix/tests/test_recovery_policy.sh       # stage-1 recovery policy
sh devices/phoenix/tests/test_charger.sh                 # stage-1 charger mode
python -m overpressure build --firmware 5.13.7-global-seko   # stage-1 image -> out/
# Linux/WSL as root (or OVERPRESSURE_LINUX=docker): the stock firmware from PICO's public OTA, then the assets
# (docs/development.md#stock-firmware; about 11 GB of OTA members and images, on the WSL disk):
devices/phoenix/scripts/extract-assets.sh 5.13.7-global-seko /var/tmp/pico4-stock/5.13.7-global-seko
# Linux/WSL as root (incremental; native clang/lld on x86-64; --refresh for new Valve packages):
devices/phoenix/scripts/build-userspace.sh /var/tmp/pico4-userspace-build out/userspace \
    --gpu-firmware assets/graphics-firmware-5.13.7 --sensor-data assets/sensor-data-5.13.7 \
    --wlan assets/wlan-5.13.7 --audio assets/audio-5.13.7 --stock-slam assets/stock-slam-5.13.7
# The headset, which stays in development mode (settings from .env); the integration test:
python -m overpressure dev boot && python -m overpressure dev check   # iterate on the flashed dev image
python -m overpressure dev reboot                        # next attempt; also from fastboot
# The installed system (docs/install.md): image as root, then from the maintenance stage 1:
devices/phoenix/scripts/build-system-image.sh out/userspace/pico4-userspace.tar.zst out/userspace/pico4-system.img
python -m overpressure dev recovery && python -m overpressure dev install   # boot, super, data; starts it
python -m overpressure dev installed && python -m overpressure dev check    # reconnect after a reboot; check
# Back to stock only when the user asks: python -m overpressure dev restore, then
python -m overpressure cycle --restore-only              # verified stock boot and ABL, PICO OS
```

- The userspace build snapshots its inputs at the start: the tree can be edited during a build.
- Project packages rebuild only when their recipe changes, and incrementally when only `src/`
  changed; `--clean` forces a clean package build. Package databases change only with
  `--refresh`. Details and timings: docs/development.md.
- Test on the headset. `tests/qemu/` covers stage 1 only; don't run it unless the user asks.

## Hard rules

- **Private data.** Never commit or redistribute device data or vendor files: `backup/`,
  `assets/` (engineering ABL, firehose, GPU firmware, sensor data), `out/`, `cache/` and `.env`
  are ignored on purpose. Valve's packages and the built userspace archives are private too.
  Docs and code must not contain device serials or similar identifiers; they belong in `.env`.
- **Valve's kernel.** The kernel and device tree are ours to change, as patches and sources under
  `kernel/`, following the stock kernel's behaviour where it is the reference. A new boot image goes
  through `dev recovery` and `dev install --boot`.
  - systemd stays at 257.x, pinned with `IgnorePkg` on the device (the Frame's version).
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
  `python -m overpressure hwtest flash-run`, `python -m overpressure cycle`, `python -m overpressure dev flash` or the
  documented fastboot sequence. Check every write against the backup hash and verify it by
  readback. The way back to stock (`dev restore`, `cycle --restore-only`) must keep working,
  but tasks do not end on stock: the headset stays in development mode. Ask the user before the
  first flash or EDL action in a new conversation.
- **DSP recovery.** `remoteproc-boot` refuses to start a DSP with remoteproc's crash recovery off: a
  crash would leave it down.
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
- **Engineering ABL.** Flashing the POC needs the engineering ABL (`python -m overpressure dev
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
  last `python -m overpressure dev` command, which renews it; plain `ssh` does not. A kernel panic reboots rather than parking in crash-dump
  mode (stage 1 sets `msm_poweroff.download_mode=0`).
- **Heat.** `pico4-fan.service` drives the fan with PICO OS's policy: the 30 s Valve session check
  peaks at about 64 °C. Qualcomm's `thermal-engine` does not run (it is off on PICO OS too), and
  the kernel's `step_wise` zones throttle. Keep GPU-heavy runs short (`pico4-valve-session-check`
  runs 30 s), stop them as soon as the evidence is in, and read
  `/sys/class/thermal/thermal_zone*/temp` when in doubt.
- **Back to stock, only when the user asks** (PICO OS needed, a release acceptance):
  `dev restore`, then `python -m overpressure cycle --restore-only`, or `cycle --skip-stage
  --userspace ...` for an acceptance run that ends on stock. Once the headset stayed off after
  the final EDL reset with stock boot and ABL already verified; a hard reset by the user brought
  PICO OS back. If PICO OS does not appear, ask the user to look at the headset rather than
  retrying anything.
- **Venus.** Never run FFmpeg's `h264_v4l2m2m` unattended: it crashes Venus's firmware, storage
  stalls and the headset needs a 10 s power press.
- **Windows NCM.** The Windows NCM link needs `overpressure/windows_ncm.py` (repository root) (UsbDk) after each stage-1
  boot (`dev connect`, `dev installed` and the runner do it). Re-initializing a working link in
  stage 2 disturbs it; `dev installed` repeats it at most once a minute while SSH times out.
- Binary files from the device: use `ssh` directly; `dev ssh` decodes output as text.

## Environment quirks

The repository's CLAUDE.md lists the Windows/WSL ones. For the PICO 4: `adb`, `fastboot`,
edl-ng and the NCM venv come from `python -m overpressure tools` (cache/tools) unless `.env`
names other copies (`PICO4_PLATFORM_TOOLS`, `PICO4_EDL`, `PICO4_NCM_PYTHON`).
