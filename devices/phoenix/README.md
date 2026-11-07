# PICO 4 (phoenix)

Linux and a SteamOS-compatible userspace ([overpressure](../../README.md)) on the original PICO 4 (2022, codename *phoenix*,
Snapdragon XR2), on Valve's kernel from the Steam Frame with the PICO's own drivers and device tree.
The stock Android kernel is no longer pursued; the way back to PICO OS stays. Valve's kernel boots from
the boot partition straight into a SteamOS-compatible stage 2 installed on the headset (system in `super`,
data in `userdata`, PICO OS's own layout); a small Alpine stage 1 in `recovery` is the maintenance system
that installs and rescues it. Stage 2 runs Valve's VR session from the Steam Frame, gamescope and
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

Open: the direct boot's last checks on the headset (the power key in charger mode, a boot without the
PC), the kernel's RDS listener on tcp/16385, and a power press after a long sleep. Plan and details:
[docs/roadmap.md](docs/roadmap.md).

## Install

**Before you start.** This replaces PICO OS on your headset: unlocking and installing wipe its apps and data.
It voids what warranty is left, and a failed step can leave the headset needing the recovery in
[docs/device-preparation.md](docs/device-preparation.md). Everything here has run on **one** headset, and the way back
to PICO OS from an installed system has not been run on it yet ([docs/roadmap.md](docs/roadmap.md)). The installer
chains steps that have each run on it; as one path it has not run yet ([what is open](docs/install.md#the-installer)). The tools
refuse a headset, firmware or panel that hasn't been verified
([list](docs/device-preparation.md#verified-hardware)): today only PICO OS 5.13.7 of the `global-seko` build. You
build your own image; the project publishes no image and no vendor file ([docs/distribution.md](docs/distribution.md)).

**You need.** Your original PICO 4, a USB-C data cable, a computer with Windows, Linux or macOS, about 40 GB free and
a few hours for the first build (it downloads about 15 GB). The stock firmware and its assets are taken out in Python
on the computer itself; the Linux part of the build (userspace, system image, kernel) runs in a container, which needs Docker or Podman on
any system, however it runs its containers (the installer says so when neither answers, and builds the container's
image itself).

**The installer.** Double-click `Install.cmd` (Windows) or `Install.command` (macOS), or run `sh install.sh` (Linux),
in the repository's folder. The start script brings its own Python and the window's toolkit into `cache/`; nothing
is installed on the computer. The window first asks which headset you have (the PICO 4), then goes through five
stages, one main button each:

| Stage | What happens | Headset |
| --- | --- | --- |
| 1 Check this computer | the host tools (adb, fastboot, edl-ng) and the unlock files (the engineering ABL and Firehose loader, from PICO's public downloads), pinned and hash-checked; then what else this computer lacks, with the fixes in plain words | not needed |
| 2 Build the system | the stock firmware and assets, the maintenance system, the SteamOS-compatible system, its image, the boot images, Valve's kernel. Hours; each build is skipped when its output is there. **This makes your SSH key** (below) | not needed |
| 3 Connect your headset | you turn on developer mode and USB debugging and plug it in; the installer finds it, writes its serials to `.env`, reads its identity and refuses a headset, firmware or panel that isn't verified, before anything is written | in PICO OS |
| 4 Prepare your headset | one consent (it erases the headset), then a verified EDL backup to this computer, the engineering bootloader, the unlock and the maintenance system in `recovery`. **Keep the backup safe: it is your way back** | PICO OS, EDL, fastboot |
| 5 Install | Wi-Fi if you like (network name and password go to the headset only), the system, the acceptance checks; then "Unplug it and put it on" | on the cable |

The installer goes on where it stopped when it is started again, and after a failure it says what state the
headset is in and what to do. *Advanced mode* in the window's header (`python -m overpressure gui --advanced`
opens it right after the choice) has every step on its own.

**In a terminal.** `python -m overpressure install` runs the same stages as text, and asks through the keyboard
(`--plan` shows them and what is done; [the tool](../../docs/tool.md#the-installer)). Each task runs one of these
commands, which also work on their own:

| Stage | Commands |
| --- | --- |
| 1 | `python -m overpressure tools`, `python -m overpressure device fetch-unlock-files`, `python -m overpressure doctor` |
| 2 | `python -m overpressure assets`, `python -m overpressure build --firmware 5.13.7-global-seko`, `devices/phoenix/scripts/build-userspace.sh ...` and `build-system-image.sh ...` (Linux), `python -m overpressure dev image`, `devices/phoenix/scripts/build-kernel-step.sh ...` (Linux), `python -m overpressure dev boot-image`; `python -m overpressure steps` shows each with its parameters |
| 3 | `adb devices` (the serial goes to `PICO4_ADB_SERIAL` in `.env`), `python -m overpressure device probe` |
| 4 | `edl-ng ... printgpt` (the LUNs, and `PICO4_EDL_SERIAL`), `python -m overpressure device backup --execute` and `device verify-backup`, `dev stage-abl`, `device unlock --execute --accept-data-loss` (`PICO4_FASTBOOT_SERIAL`), `dev flash`, `dev connect`; by hand in [docs/device-preparation.md](docs/device-preparation.md) |
| 5 | `python -m overpressure dev install`, `dev installed`, `dev check` |

**Your login.** The headset takes SSH on the cable and on Wi-Fi only with a key. The build stage makes one for this
PC, `out/dev/id_ed25519`; it stays on your PC. To use a key of your own, name its public file as
`PICO4_SSH_PUBLIC_KEY` in `.env` before the install, which writes the keys to the headset's data partition. No account has a password ([docs/security.md](docs/security.md)).

**After the install.** Unplug it and put it on: sign in to Steam, find the controllers and set up your room in the
headset (and Wi-Fi, if you skipped it). `python -m overpressure dev install` with a new image updates the system and
keeps your data. **The way back:** `python -m overpressure dev restore`, then
`python -m overpressure cycle --restore-only`, both from your backup; see
[docs/install.md](docs/install.md#back-to-stock).

**If something goes wrong.** `python -m overpressure dev support-bundle` collects the logs for a bug report, with your
identifiers masked ([docs/hardware-testing.md](docs/hardware-testing.md#support-bundle)); the installer's own logs are
in `out/install/phoenix/`.

## Documentation

| Document | Contents |
| --- | --- |
| [docs/roadmap.md](docs/roadmap.md) | the plan, the status of each step, open work |
| [docs/research.md](docs/research.md) | background: the PICO 4 and the Steam Frame, related projects |
| [docs/device-preparation.md](docs/device-preparation.md) | identity, EDL backups, engineering ABL, unlock, restore |
| [docs/stage1.md](docs/stage1.md) | the maintenance system's boot image: contents, boot sequence, options, troubleshooting |
| [docs/hardware-testing.md](docs/hardware-testing.md) | acceptance runner, acceptance cycle, development loop |
| [docs/kernel.md](docs/kernel.md) | Valve's kernel on the PICO 4: its files, its patches, what is verified on it and how it works |
| [docs/graphics.md](docs/graphics.md) | the graphics stack and both panels |
| [docs/userspace.md](docs/userspace.md) | the SteamOS-compatible userspace and how it starts |
| [docs/install.md](docs/install.md) | the installed system: layout, boot modes, install and update, the way back |
| [docs/xr.md](docs/xr.md) | IMU, Monado driver, display, lenses, cameras, 6DoF, controllers |
| [docs/session.md](docs/session.md) | Valve's VR session: SteamVR, gamescope, games, Steam Link |
| [docs/cooling.md](docs/cooling.md) | fan control and the thermal policy |
| [docs/development.md](docs/development.md) | layout, build system and hosts, the build container, tests, Windows |
| [docs/stock-components.md](docs/stock-components.md) | what the images take unchanged from PICO OS, and what could replace each |
| [docs/security.md](docs/security.md) | logins, listening services, what is protected and what isn't |
| [docs/distribution.md](docs/distribution.md) | what the project publishes, and where every other file comes from |
| [docs/release-checklist.md](docs/release-checklist.md) | every check of a release image |

## This folder

```text
tooling/     host tooling (Python): python -m overpressure COMMAND, the stock firmware's assets, and the window's steps
kernel/      Valve's kernel: build script, patches, the PICO's drivers and device tree
initramfs/   stage 1: the files added to the Alpine ramdisk
userspace/   stage 2: package list, pacman.conf, adaptation layer, SteamVR configuration, PKGBUILDs
src/         C sources: graphics tests, Monado's PICO 4 driver, the tracker's host, OpenXR clients, SteamVR helpers
scripts/     userspace, system image and kernel builds, the on-headset preflight, sharing a Windows PC's internet
prebuilt/    static aarch64 helpers for stage 1 and the system image
tests/       host tests and the QEMU boot tests
docs/        documentation
```

`make test` (or `python -m unittest` at the repository root) runs the host tests in seconds.
Every command above is also a step of the window's Advanced mode (`python -m overpressure gui --advanced`, [the tool](../../docs/tool.md#advanced-mode)).

Device data and vendor files never enter the repository: backups, the bootloader images,
GPU firmware, sensor data, Valve's packages and the built archives stay private
(`backup/`, `assets/`, `cache/`, `out/`, `.env` at the repository root).
