# PICO 4 (phoenix)

Linux and a SteamOS-compatible userspace ([overpressure](../../README.md)) on the original PICO 4 (2022, codename *phoenix*,
Snapdragon XR2), on Valve's kernel from the Steam Frame with the PICO's own drivers and device tree.
The stock Android kernel is no longer pursued; the way back to PICO OS stays. A small
Alpine stage 1 boots from the boot partition and takes a SteamOS-compatible stage 2 over the
USB cable into RAM, or starts it installed on the headset (system in `super`, data in
`userdata`, PICO OS's own layout). Stage 2 runs Valve's VR session from the Steam Frame, gamescope and
SteamVR, with a Monado driver for the PICO 4's hardware: the SteamVR environment shows in the
lenses with 6DoF head and controller tracking.

This is experimental work on an unlocked headset. During development it keeps an engineering
bootloader and a development image in its boot partition; the acceptance cycle returns it to the
stock boot image and stock bootloader, both verified by readback. Storage stays read-only
otherwise; installing the system replaces PICO OS's system and data, and the way back restores
them from the backup ([docs/install.md](docs/install.md)). Keep your own EDL backup
([docs/device-preparation.md](docs/device-preparation.md)).

## Status

| Step | Result |
| --- | --- |
| 1 Recoverable device | EDL backups, engineering ABL, unlock and stock restore |
| 2 Linux shell | Alpine ramdisk, SSH and HTTP over USB, read-only storage, return to PICO OS |
| 3 Native graphics | Valve's Mesa (Turnip, OpenGL through zink) on msm; both 2160 x 2160 panels at 90 Hz |
| 4 SteamOS-compatible userspace | Holo Core aarch64 with the Steam Frame's systemd 257.7 and SteamOS services, from RAM or installed (read-only system, 104 GiB of data); speakers and microphone through PipeWire |
| 5 XR hardware | head IMU, Monado inside SteamVR, lens correction and IPD, four tracking cameras, 6DoF with PICO OS's tracker on the CDSP, controllers (inputs, vibration, positions), the colour room view |
| 6 Valve's VR session | gamescope and SteamVR 2.17.10 on both panels, 90 fps on the Steam Frame's compositor path; the Steam client in the headset, games from its library through Proton and FEX, Android apps through Lepton, Steam Link from a PC |

Open: a power press after a long sleep, the codec's alert after a cable replug during a suspend. Plan and details:
[docs/roadmap.md](docs/roadmap.md).

## Quick start

0. **Check this computer**: `python -m overpressure doctor` lists what is missing for each of
   the steps below and how to get it.
1. **Prepare the headset** once: identity, EDL backup, engineering ABL, unlock
   ([docs/device-preparation.md](docs/device-preparation.md)). Copy `.env.example` to `.env`
   and fill in the headset's serials and the paths of its backup and bootloader files.
2. **Build stage 1** (any OS, Python 3.9+; the firmware entry must match the headset, see
   `python -m overpressure match`):

   ```sh
   python -m overpressure build --firmware 5.13.7-global-seko
   ```

3. **Build stage 2** (Linux or WSL, as root; [docs/development.md](docs/development.md) lists
   the requirements):

   ```sh
   make userspace
   ```

4. **Run it on the headset.** Put it into development mode once, from PICO OS; it stays there
   between runs:

   ```sh
   python -m overpressure dev stage-abl
   python -m overpressure dev image
   python -m overpressure dev flash
   python -m overpressure dev connect
   ```

   Then boot and check each new archive:

   ```sh
   python -m overpressure dev boot
   python -m overpressure dev check
   python -m overpressure dev reboot
   ```

   `python -m overpressure dev restore`, then `python -m overpressure cycle --restore-only`, return the
   headset to stock boot, stock bootloader and PICO OS
   ([docs/hardware-testing.md](docs/hardware-testing.md)).

## Documentation

| Document | Contents |
| --- | --- |
| [docs/roadmap.md](docs/roadmap.md) | the plan, the status of each step, open work |
| [docs/research.md](docs/research.md) | background: the PICO 4 and the Steam Frame, related projects |
| [docs/device-preparation.md](docs/device-preparation.md) | identity, EDL backups, engineering ABL, unlock, restore |
| [docs/stage1.md](docs/stage1.md) | the boot image: contents, boot sequence, options, troubleshooting |
| [docs/hardware-testing.md](docs/hardware-testing.md) | acceptance runner, acceptance cycle, development loop |
| [docs/kernel-feasibility.md](docs/kernel-feasibility.md) | Valve's kernel on the PICO 4: the study, its patches and what is verified on it |
| [docs/graphics.md](docs/graphics.md) | the graphics stack and both panels |
| [docs/userspace.md](docs/userspace.md) | the SteamOS-compatible userspace and the handoff |
| [docs/xr.md](docs/xr.md) | IMU, Monado driver, display, lenses, cameras, 6DoF, controllers |
| [docs/session.md](docs/session.md) | Valve's VR session: SteamVR, gamescope, games, Steam Link |
| [docs/cooling.md](docs/cooling.md) | fan control and the thermal policy |
| [docs/development.md](docs/development.md) | layout, build system and hosts, tests, Windows and WSL |
| [docs/stock-components.md](docs/stock-components.md) | what the images take unchanged from PICO OS, and what could replace each |

## This folder

```text
tooling/     host tooling (Python standard library): python -m overpressure COMMAND, and the GUI's steps
kernel/      Valve's kernel: build script, patches, the PICO's drivers and device tree
initramfs/   stage 1: the files added to the Alpine ramdisk
userspace/   stage 2: package list, pacman.conf, adaptation layer, SteamVR configuration, PKGBUILDs
src/         C sources: graphics tests, Monado's PICO 4 driver, the tracker's host, OpenXR clients, SteamVR helpers
scripts/     userspace and system image builds, stock firmware extraction, the on-headset preflight
prebuilt/    static aarch64 helpers for stage 1
tests/       host tests and the QEMU boot tests
docs/        documentation
```

`make test` (or `python -m unittest` at the repository root) runs the host tests in seconds.
Every step above is also in the GUI (`python -m overpressure gui`, [the tool](../../docs/tool.md)).

Device data and vendor files never enter the repository: backups, the bootloader images,
GPU firmware, sensor data, Valve's packages and the built archives stay private
(`backup/`, `assets/`, `cache/`, `out/`, `.env` at the repository root).
