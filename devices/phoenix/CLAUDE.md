# CLAUDE.md: PICO 4 (phoenix)

Guidance for working on the PICO 4 in overpressure (the repository's [CLAUDE.md](../../CLAUDE.md)
has the general rules): Linux and a SteamOS-compatible userspace on the original PICO 4 (2022,
codename *phoenix*, Snapdragon XR2), on **Valve's kernel** (the Steam Frame's 7.2 with the project's
changes in [overpressure/linux](https://github.com/overpressure/linux), pinned by `kernel/release.json`,
[docs/kernel.md](docs/kernel.md)) with Valve's Mesa.
The maintenance stage 1 in `recovery` runs on Valve's kernel too. The stock Android kernel (4.19.81-perf+,
PICO OS 5.13.7/5.13.8) runs neither: it stays in the acceptance runner's boot image and on the way back to
PICO OS. The headset builds its own system (docs/install.md#built-on-the-headset); the installer needs no
build on the PC. Paths are relative to this folder (`devices/phoenix/`); commands run
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
| 6 Valve's VR session: gamescope + SteamVR with `driver_monado` in direct mode through Turnip's display WSI | verified worn; 90 fps on the Frame's compositor path; the Steam client signs in, its library runs games through Proton and FEX, Android apps through Lepton, Steam Link from a PC; heavy games run at what the GPU allows | [docs/session.md](docs/session.md) |

Installed system ([docs/install.md](docs/install.md)): Valve's kernel in `boot` (no ramdisk), the
system image read-only in `super`, data in `userdata`, the maintenance stage 1 in `recovery`. The
kernel mounts `super` and starts its `preinit`, which mounts the data and executes systemd; a failure
reboots into `recovery`. Powered off on the cable it starts in charger mode. The installer installs,
upgrades and restores to stock ([README](README.md#install)). Cooling: [docs/cooling.md](docs/cooling.md).
Build system, the build container, tests and the Windows environment: [docs/development.md](docs/development.md).

## Backlog

The repository's GitHub issues; the rules are in the repository's CLAUDE.md.

## Documentation

The PICO 4's documents are in `docs/` here, its overview in `README.md` here; the repository's
CLAUDE.md has the rules (facts only, no work logs). Keep the status in docs/roadmap.md and
README.md current.

## Layout

- `tooling/`: the PICO 4's host tooling, reached as `python -m overpressure
  build|inspect|match|stock|assets|bootchain|device|hwtest|dev|cycle`. `__init__.py` holds `HEADSET`
  (its commands, the window's steps, the installer's workflows, its backup); `install.py` the
  installer's plans (install, upgrade, restore); `device.py` EDL/fastboot/ADB and the verified
  firmware list; `devloop.py` the development loop; `cycle.py`, `hardware_test.py` the acceptance
  runs; `image.py` the stage-1 image; `stockfiles.py` PICO OS's files for the data partition, from the
  headset's own backup (through `assets.py`; `stock.py` reads PICO's public OTA instead, for development).
  What every headset shares is in `overpressure/` ([docs/tool.md](../../docs/tool.md#how-it-fits-together)).
- `initramfs/`: stage 1, the maintenance environment in `recovery` and the base of test images;
  `pico4-install` is the installer's only writer. `pico4-init` is PID 1, not busybox init: busybox's
  `kill(-1)` killed the f_mass_storage kernel thread and with it the USB link.
- `kernel/`: Valve's kernel: `release.json` (the pinned release of overpressure/linux: tag, commit,
  build image, file hashes), `build-tree.sh` (builds a checkout of it), `imx471_mode.py` (the RGB
  camera's table for the assets), `recovery_image.py` for its boot images (`--direct --boot`: the
  installed system's, from `dev boot-image`). `tooling/kernel.py`: `kernel fetch|build|check|contracts`.
  The kernel's source, its drivers, device tree, `pico4.config` and driver tests are in overpressure/linux.
- `userspace/`: stage 2. `packages.json` pins the release of
  [overpressure/packages](https://github.com/overpressure/packages) the image is built from: its
  `headsets/phoenix/` is the image's definition (`packages.txt`, `pacman.conf` with the Holo preview
  first and Frame packages named explicitly from `deckard-arch-hotfixes-release-0.3`, `packages.lock`),
  its layers the project's packages (Monado with the PICO 4 driver and its tools, Basalt, gamescope,
  hexagonrpc, the swapchain layer, GTK 2, MangoHud and others), each with its code and patches, and
  the adaptation layer as packages with their tests: `pico4-base` (`preinit`, the first process, the
  storage and recovery policy, the system's own USB gadget `usb-gadget`, network), `pico4-power`,
  `pico4-audio`, `pico4-xr`, `pico4-session` (with the SteamVR configuration) and `pico4-checks`; each
  package's preset enables its units. A change to a package goes to overpressure/packages, then a new
  tag there and the pin here.
- `src/`: the project's C sources outside the packages (`reboot/`, `splash/`, `gd32/`). `pico4-slamd`, which
  runs PICO OS's head tracker (libSlam) in a bionic root, is a package of overpressure/packages.
- `scripts/`: `build-userspace.sh`, `build-system-image.sh`, `build-kernel-step.sh`,
  `windows-share-internet.ps1` and the on-headset preflight. The image holds no file of PICO OS's, only links
  (`userspace/stock-links.txt`) to the data partition's copy, which `dev install` writes from the headset's backup,
  and to PICO OS's modem, dsp and bluetooth partitions, mounted read-only at boot (docs/install.md#pico-oss-files).
  The headset's own factory data is not extracted: the image links to persist, mounted read-only at boot
  (`pico4-persist`). `tests/`: unittest suites, `test_recovery_policy.sh`, `tests/qemu/` (the
  adaptation packages' tests run in their builds). `device.mk`: its Makefile targets.
- Private and generated, ignored, at the repository root: `backup/` (identity
  `backup/device.json`, EDL backups), `assets/`, `cache/`, `out/` (`out/dev/` for the dev loop,
  `out/cycle-*/` evidence), `.env`.

## Commands

From the repository root (the window's Advanced mode, `python -m overpressure gui --advanced`, runs the same
steps):

```sh
python -m unittest                                       # host tests, seconds, no hardware
bash devices/phoenix/tests/test_recovery_policy.sh       # stage-1 recovery policy
python -m overpressure build --firmware 5.13.7-global-seko   # stage-1 image -> out/, from the backup's boot
# In the build container, as root (incremental; the project's packages from the pinned release of
# overpressure/packages; --refresh for new Valve packages; --packages DIR takes a checkout of it and
# builds the recipes that differ from the pinned commit's, --build-packages every recipe, with native
# clang/lld on x86-64). The image holds no file of PICO OS's: dev install writes them to the data
# partition from the headset's own backup (docs/install.md#pico-oss-files):
python -m overpressure container run -- bash devices/phoenix/scripts/build-userspace.sh \
    /var/tmp/pico4-userspace-build out/userspace
# The headset stays in development mode (settings from .env). The installed system is the integration
# test (docs/install.md): the image in the build container, then from the maintenance stage 1:
python -m overpressure container run -- bash devices/phoenix/scripts/build-system-image.sh \
    out/userspace/pico4-userspace.tar.zst out/userspace/pico4-system.img
# Commands that touch the headset ask the disclaimer first; unattended (an agent, a script):
# python -m overpressure --accept-disclaimer dev ..., or OVERPRESSURE_ACCEPT_DISCLAIMER=1.
python -m overpressure dev recovery && python -m overpressure dev install   # boot, super, data; starts it
python -m overpressure dev install --on-headset   # the headset builds the system itself (the installer's way)
python -m overpressure dev installed && python -m overpressure dev check    # reconnect after a reboot; check
# Back to stock only when the user asks: the installer's restore (python -m overpressure restore, or
# the window's Restore to stock), or by hand python -m overpressure dev restore, then after PICO OS's setup
python -m overpressure cycle --restore-only              # verified stock boot and ABL, PICO OS
```

- The userspace build snapshots its inputs at the start: the tree can be edited during a build.
- Project packages come from the pinned release; built here, they rebuild only when their recipe folder
  changes. Package databases change only with `--refresh`. Details and timings: docs/development.md.
- Test on the headset. `tests/qemu/` covers stage 1 only; don't run it unless the user asks.

## Hard rules

- **Access.** SSH takes keys only, on USB and Wi-Fi, and no account has a password; keys are per PC, written
  by `dev install` to the data partition (`pico4-install keys`), never into the system image or a boot image
  ([docs/security.md](docs/security.md)).
- **Verified hardware.** The tools refuse firmware, headsets and panels the whole path hasn't run on
  (`tooling/device.py`); extend that list only after a run.
- **Private data.** Never commit or redistribute device data or vendor files: `backup/`,
  `assets/` (engineering ABL, firehose, GPU firmware, sensor data), `out/`, `cache/` and `.env`
  are ignored on purpose. Valve's packages and the built userspace archives are private too.
  Docs and code must not contain device serials or similar identifiers; they belong in `.env`.
- **Valve's kernel.** The kernel and device tree are ours to change, as commits on overpressure/linux's
  layer branches (`common/7.2`, `sm8250/7.2`, `phoenix/7.2`), by pull request merged with a merge commit
  (no rebase, no force-push; changes move up the layers by pull request), following the stock kernel's behaviour
  where it is the reference. Its tags never move; this repository pins one in `kernel/release.json`,
  and `kernel contracts` checks the names both sides share. A new boot image goes through
  `dev boot-image` and `dev install`.
  - systemd stays at 257.x, pinned with `IgnorePkg` on the device (the Frame's version).
- **Storage stays read-only, except the installed system's data.** Every physical block device
  is switched read-only in both stages; only the installed system's data partition (userdata,
  ext4 `pico4-data`) is mounted writable. Partitions are written only through the explicit,
  backup-validated paths: `dev flash` (recovery), `pico4-install` in the maintenance
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
  `recovery`, and the installed system (Valve's kernel in `boot`, system in `super`, data
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
- **Engineering ABL.** Flashing stage 1 needs the engineering ABL (`python -m overpressure dev
  stage-abl`). The stock ABL reports `DeviceUnlocked=true`, and the runner refuses it. Writing
  boot also needs the per-session `fastboot oem <token> unlock` authorization, which the tools
  derive from the identity.
- **Iterate on the installed system.** `dev install SYSTEM_IMAGE` writes the boot image
  (`out/kernel/valve-boot.img`, or `--boot PATH`) and the system image from the maintenance stage 1,
  which it enters by itself from a running installed system, skipping what is there by hash; a new
  kernel comes from `kernel fetch` (or `kernel build`) and `dev boot-image`;
  `dev installed` reconnects after a reboot, `dev check` runs the acceptance runner's stage-2 checks,
  `dev ssh CMD` a command. The session runs only on Valve's kernel, not in the maintenance stage 1. A
  changed stage-1 image needs `python -m overpressure build`, `dev image` and `dev flash` (dev image takes
  the stage-1 image as it is); the installed system's boot image holds no stage 1. `dev install
  --on-headset` sends the tree's build part and builds on the data partition: in the installed system when it
  runs, else in stage 1; scripts/build-on-headset.sh comes with the tree, `pico4-install` with stage 1.
- **Deadlines.** Device-side deadlines return the headset to fastboot; `dev reboot` starts
  stage 1 from there without a flash. The dev image's maximum runtime (an hour) counts from the
  last `python -m overpressure dev` command, which renews it; plain `ssh` does not. A kernel panic reboots after 5 s (`panic=5`, the
  tree's PS_HOLD reset).
- **Heat.** `pico4-fan.service` drives the fan with PICO OS's policy: the 30 s Valve session check
  peaks at about 65 °C. Qualcomm's `thermal-engine` does not run (it is off on PICO OS too), and
  the kernel's `step_wise` zones throttle. Keep GPU-heavy runs short (`pico4-valve-session-check`
  runs 30 s), stop them as soon as the evidence is in, and read
  `/sys/class/thermal/thermal_zone*/temp` when in doubt.
- **Back to stock, only when the user asks**: the installer's restore, or `dev restore`, PICO OS's
  setup with USB debugging, then `cycle --restore-only`. The headset can stay off after the final
  EDL reset with stock boot and ABL verified; a hard reset brings PICO OS back. If PICO OS does not
  appear, ask the user to look at the headset rather than retrying anything.
- **Venus.** A firmware exception can end the driver's recovery in an interrupt storm that starves storage or
  in a panic; both reboot the headset by themselves. Run decoder experiments that may crash it with the user near.
- **Windows NCM.** Valve's kernel takes Windows 11's NCM driver as it is: neither the installed system nor
  the maintenance stage 1 needs an initialization, and running it on a working link disturbs it. Only a stage 1
  on the stock kernel (the acceptance runner's, a dev image older than `recovery_kernel: valve` in
  `out/dev/dev-image.json`) needs `overpressure/windows_ncm.py` (UsbDk) after each boot; the dev loop and the
  runner send it only there.
- Binary files from the device: use `ssh` directly; `dev ssh` decodes output as text.

## Environment quirks

The repository's CLAUDE.md lists the Windows ones. For the PICO 4: `adb`, `fastboot`
and edl-ng come from `python -m overpressure tools` (cache/tools) unless `.env` names other
copies (`PICO4_PLATFORM_TOOLS`, `PICO4_EDL`); the Windows NCM initialization runs on the
project's own Python (pyusb, libusb-package) unless `PICO4_NCM_PYTHON` names another.
