# PICO 4 (phoenix)

Linux and a SteamOS-compatible userspace ([overpressure](../../README.md)) on the original PICO 4 (2022, codename *phoenix*,
Snapdragon XR2), on Valve's kernel from the Steam Frame with the PICO's own drivers and device tree.
The stock Android kernel is no longer pursued; the way back to PICO OS stays. A small
Alpine stage 1 boots from the boot partition and starts a SteamOS-compatible stage 2 installed on the
headset (system in `super`, data in `userdata`, PICO OS's own layout). Stage 2 runs Valve's VR session from the Steam Frame, gamescope and
SteamVR, with a Monado driver for the PICO 4's hardware: the SteamVR environment shows in the
lenses with 6DoF head and controller tracking.

This is experimental work on an unlocked headset. During development it keeps an engineering
bootloader, the development image in `recovery` and the installed system in `boot`; the acceptance
cycle returns it to the stock boot image and stock bootloader, both verified by readback. Storage stays read-only
otherwise; installing the system replaces PICO OS's system and data, and the way back restores
them from the backup ([docs/install.md](docs/install.md)). Keep your own EDL backup
([docs/device-preparation.md](docs/device-preparation.md)).

## Status

| Step | Result |
| --- | --- |
| 1 Recoverable device | EDL backups, engineering ABL, unlock and stock restore |
| 2 Linux shell | Alpine ramdisk, SSH and HTTP over USB, read-only storage, return to PICO OS |
| 3 Native graphics | Valve's Mesa (Turnip, OpenGL through zink) on msm; both 2160 x 2160 panels at 90 Hz |
| 4 SteamOS-compatible userspace | Holo Core aarch64 with the Steam Frame's systemd 257.7 and SteamOS services, installed (read-only system, 104 GiB of data); speakers and microphone through PipeWire |
| 5 XR hardware | head IMU, Monado inside SteamVR, lens correction and IPD, four tracking cameras, 6DoF with PICO OS's tracker on the CDSP, controllers (inputs, vibration, positions), the colour room view |
| 6 Valve's VR session | gamescope and SteamVR 2.17.10 on both panels, 90 fps on the Steam Frame's compositor path; the Steam client in the headset, games from its library through Proton and FEX, Android apps through Lepton, Steam Link from a PC |

Open: a power press after a long sleep. Plan and details:
[docs/roadmap.md](docs/roadmap.md).

## Install

**Before you start.** This replaces PICO OS on your headset: unlocking and installing wipe its apps and data.
It voids what warranty is left, and a failed step can leave the headset needing the recovery in
[docs/device-preparation.md](docs/device-preparation.md). Everything here has run on **one** headset, and the way back
to PICO OS from an installed system has not been run on it yet ([docs/roadmap.md](docs/roadmap.md)). The tools
refuse a headset, firmware or panel that hasn't been verified
([list](docs/device-preparation.md#verified-hardware)): today only PICO OS 5.13.7 of the `global-seko` build. You
build your own image; the project publishes no image and no vendor file ([docs/distribution.md](docs/distribution.md)).

**You need.** Your original PICO 4 with USB debugging on, a USB-C data cable, a PC with Python 3.9+, about 40 GB free
and a few hours for the first build (it downloads about 15 GB). The Linux part of the build runs in WSL on Windows,
with sudo on Linux, and in Docker or Podman on macOS (`OVERPRESSURE_LINUX=docker`, any host).

**The path.** `python -m overpressure gui` shows these steps, in this order, each with the command it runs; the
same commands work in a terminal. `python -m overpressure doctor` says what this computer lacks, and how to get it.

| # | Step (in the window) | Command | What happens | Headset |
| --- | --- | --- | --- | --- |
| 1 | Host tools | `python -m overpressure tools` | adb, fastboot, edl-ng, pinned and hash-checked | |
| 2 | Unlock files | `python -m overpressure device fetch-unlock-files` | the engineering ABL and Firehose loader, from PICO's public downloads | |
| 3 | Settings | copy `.env.example` to `.env` | the headset's serials: `adb devices`, then as [device preparation](docs/device-preparation.md) shows | in PICO OS |
| 4 | Prepare | [docs/device-preparation.md](docs/device-preparation.md) | identity, a verified EDL backup, engineering ABL, unlock, factory reset. Keep the backup safe: it is your way back | in PICO OS, EDL, fastboot |
| 5 | Stage-1 boot image | `python -m overpressure build --firmware 5.13.7-global-seko` | the maintenance system | |
| 6 | Stock firmware and assets | `devices/phoenix/scripts/extract-assets.sh ...` (Linux) | PICO's public OTA, about 11 GB | |
| 7 | Userspace archive | `devices/phoenix/scripts/build-userspace.sh ...` (Linux) | Valve's packages and this project's | |
| 8 | System image for super | `devices/phoenix/scripts/build-system-image.sh ...` (Linux) | | |
| 9 | Dev image | `python -m overpressure dev image` | the boot images. **This makes your SSH key** (below) | |
| 10 | Valve's kernel | `devices/phoenix/scripts/build-kernel-step.sh ...` (Linux) | Valve's public source package, this project's patches | |
| 11 | Boot image | `python -m overpressure dev boot-image` | `out/kernel/valve-boot.img` | |
| 12 | Enter development mode | `dev stage-abl`, `dev flash`, `dev connect` | the maintenance system in `recovery` | in PICO OS |
| 13 | Install the system | `dev recovery`, `dev install` | boot, system, data; then it starts. About 5 minutes | on the cable |
| 14 | Acceptance checks | `dev check` | | on the cable |

**Your login.** The headset takes SSH on the cable and on Wi-Fi only with a key. Step 9 makes one for this PC,
`out/dev/id_ed25519`; it stays on your PC. To use a key of your own, name its public file as
`PICO4_SSH_PUBLIC_KEY` in `.env` before step 9. No account has a password ([docs/security.md](docs/security.md)).

**After the install.** Unplug it and put it on: set up Wi-Fi, sign in to Steam, find the controllers and set up
your room in the headset. `python -m overpressure dev install` with a new image updates the system and keeps your
data. **The way back:** `python -m overpressure dev restore`, then `python -m overpressure cycle --restore-only`, both
from your backup; see [docs/install.md](docs/install.md#back-to-stock).

**If something goes wrong.** `python -m overpressure dev support-bundle` collects the logs for a bug report, with your
identifiers masked ([docs/hardware-testing.md](docs/hardware-testing.md#support-bundle)).

## Documentation

| Document | Contents |
| --- | --- |
| [docs/roadmap.md](docs/roadmap.md) | the plan, the status of each step, open work |
| [docs/research.md](docs/research.md) | background: the PICO 4 and the Steam Frame, related projects |
| [docs/device-preparation.md](docs/device-preparation.md) | identity, EDL backups, engineering ABL, unlock, restore |
| [docs/stage1.md](docs/stage1.md) | the boot image: contents, boot sequence, options, troubleshooting |
| [docs/hardware-testing.md](docs/hardware-testing.md) | acceptance runner, acceptance cycle, development loop |
| [docs/kernel.md](docs/kernel.md) | Valve's kernel on the PICO 4: its files, its patches, what is verified on it and how it works |
| [docs/graphics.md](docs/graphics.md) | the graphics stack and both panels |
| [docs/userspace.md](docs/userspace.md) | the SteamOS-compatible userspace and the handoff |
| [docs/install.md](docs/install.md) | the installed system: layout, boot modes, install and update, the way back |
| [docs/xr.md](docs/xr.md) | IMU, Monado driver, display, lenses, cameras, 6DoF, controllers |
| [docs/session.md](docs/session.md) | Valve's VR session: SteamVR, gamescope, games, Steam Link |
| [docs/cooling.md](docs/cooling.md) | fan control and the thermal policy |
| [docs/development.md](docs/development.md) | layout, build system and hosts, tests, Windows and WSL |
| [docs/stock-components.md](docs/stock-components.md) | what the images take unchanged from PICO OS, and what could replace each |
| [docs/security.md](docs/security.md) | logins, listening services, what is protected and what isn't |
| [docs/distribution.md](docs/distribution.md) | what the project publishes, and where every other file comes from |
| [docs/release-checklist.md](docs/release-checklist.md) | every check of a release image |

## This folder

```text
tooling/     host tooling (Python standard library): python -m overpressure COMMAND, and the GUI's steps
kernel/      Valve's kernel: build script, patches, the PICO's drivers and device tree
initramfs/   stage 1: the files added to the Alpine ramdisk
userspace/   stage 2: package list, pacman.conf, adaptation layer, SteamVR configuration, PKGBUILDs
src/         C sources: graphics tests, Monado's PICO 4 driver, the tracker's host, OpenXR clients, SteamVR helpers
scripts/     userspace and system image builds, stock firmware extraction, the on-headset preflight, sharing a Windows PC's internet
prebuilt/    static aarch64 helpers for stage 1
tests/       host tests and the QEMU boot tests
docs/        documentation
```

`make test` (or `python -m unittest` at the repository root) runs the host tests in seconds.
Every step above is also in the GUI (`python -m overpressure gui`, [the tool](../../docs/tool.md)).

Device data and vendor files never enter the repository: backups, the bootloader images,
GPU firmware, sensor data, Valve's packages and the built archives stay private
(`backup/`, `assets/`, `cache/`, `out/`, `.env` at the repository root).
