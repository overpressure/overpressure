# PICO 4 Linux

Linux and a SteamOS-compatible userspace on the original PICO 4 (2022, codename *phoenix*,
Snapdragon XR2), on the headset's stock Android kernel (4.19.81) and device trees. A small
Alpine stage 1 boots from the boot partition and takes a SteamOS-compatible stage 2 over the
USB cable into RAM. Stage 2 runs Valve's VR session from the Steam Frame, gamescope and
SteamVR, with a Monado driver for the PICO 4's hardware: the SteamVR environment shows in the
lenses with 6DoF head tracking and 3DoF controllers.

This is experimental work on an unlocked headset. During development it keeps an engineering
bootloader and a development image in its boot partition; the acceptance cycle returns it to the
stock boot image and stock bootloader, both verified by readback. Storage stays read-only
otherwise. Keep your own EDL backup ([docs/device-preparation.md](docs/device-preparation.md)).

## Status

| Step | Result |
| --- | --- |
| 1 Recoverable device | EDL backups, engineering ABL, unlock and stock restore |
| 2 Linux shell | stock kernel + Alpine ramdisk, SSH and HTTP over USB, read-only storage, return to PICO OS |
| 3 Native graphics | Turnip (Vulkan) on KGSL; both 2160 x 2160 panels at 90 and 72 Hz through the stock KMS driver |
| 4 SteamOS-compatible userspace | Holo Core aarch64 with the Steam Frame's systemd 257.7 and SteamOS services, from RAM |
| 5 XR hardware | head IMU, Monado with KMS output, lens correction and IPD, four tracking cameras, Basalt 6DoF, controllers (inputs, 3DoF) |
| 6 Valve's VR session | gamescope and SteamVR 2.17.10 on both panels, about 50 fps (GPU-bound) |

Open: fan control over longer sessions and its tuning, performance, the Steam client and
network, controller position tracking, a standalone boot. Plan and details:
[docs/roadmap.md](docs/roadmap.md).

## Quick start

1. **Prepare the headset** once: identity, EDL backup, engineering ABL, unlock
   ([docs/device-preparation.md](docs/device-preparation.md)). Copy `.env.example` to `.env`
   and fill in the headset's serials and the paths of its backup and bootloader files.
2. **Build stage 1** (any OS, Python 3.9+; the firmware entry must match the headset, see
   `python -m pico4 match`):

   ```sh
   python -m pico4 build --firmware 5.13.7-global-seko
   ```

3. **Build stage 2** (Linux or WSL, as root; [docs/development.md](docs/development.md) lists
   the requirements):

   ```sh
   make userspace
   ```

4. **Run it on the headset.** Put it into development mode once, from PICO OS; it stays there
   between runs:

   ```sh
   python -m pico4 dev stage-abl
   python -m pico4 dev image
   python -m pico4 dev flash
   python -m pico4 dev connect
   ```

   Then boot and check each new archive:

   ```sh
   python -m pico4 dev boot
   python -m pico4 dev check
   python -m pico4 dev reboot
   ```

   `python -m pico4 dev restore`, then `python -m pico4 cycle --restore-only`, return the
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
| [docs/graphics.md](docs/graphics.md) | Turnip on KGSL and fenced KMS on both panels |
| [docs/userspace.md](docs/userspace.md) | the SteamOS-compatible userspace and the handoff |
| [docs/xr.md](docs/xr.md) | IMU, Monado driver, display, lenses, cameras, 6DoF, controllers |
| [docs/session.md](docs/session.md) | Valve's VR session: SteamVR, gamescope, the display layer |
| [docs/cooling.md](docs/cooling.md) | fan control and the thermal policy |
| [docs/development.md](docs/development.md) | layout, build system and hosts, tests, Windows and WSL |

## Repository

```text
pico4/       host tooling (Python standard library): python -m pico4 COMMAND
initramfs/   stage 1: the files added to the Alpine ramdisk
userspace/   stage 2: package list, pacman.conf, adaptation layer, SteamVR configuration, PKGBUILDs
src/         C sources: graphics tests, Monado's PICO 4 driver, OpenXR clients, SteamVR display layer
scripts/     userspace and graphics builds, sensor data extraction, boot and preflight helpers
tests/       host tests and the QEMU boot tests
docs/        documentation
```

`make test` (or `python -m unittest discover -s tests`) runs the host tests in seconds.

Device data and vendor files never enter the repository: backups, the bootloader images,
GPU firmware, sensor data, Valve's packages and the built archives stay private
(`backup/`, `assets/`, `cache/`, `out/`, `.env`).
