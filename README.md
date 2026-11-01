# overpressure

Linux and a SteamOS-compatible userspace on standalone VR headsets. A headset boots a small Linux stage 1 from its boot partition, which takes a
SteamOS-compatible stage 2 over the USB cable into RAM or starts it installed on the headset;
stage 2 runs Valve's VR session (gamescope and SteamVR) with a Monado driver for the headset's
hardware.

This is experimental work on unlocked headsets. Keep your own backups; each headset's documents
say how its way back to stock works.

## Headsets

| Codename | Headset | Status | Folder |
| --- | --- | --- | --- |
| phoenix | PICO 4 (2022, Snapdragon XR2) | Valve's VR session with 6DoF head tracking and 3DoF controllers, installed or from RAM | [devices/phoenix](devices/phoenix/README.md) |

## The tool

One tool builds the images and works on the headset, as a window or on the command line
([docs/tool.md](docs/tool.md)). It needs Python 3.9+ and nothing else; run it from the
repository root:

```sh
python -m overpressure doctor                 # what this computer has and lacks, with fixes
python -m overpressure tools                  # fetch the pinned host tools (adb, fastboot, edl-ng, ...)
python -m overpressure gui                    # the window: image creation and flashing
python -m overpressure devices                # the supported headsets
python -m overpressure steps                  # the headset's steps and their command lines
python -m overpressure dev check              # a command of the headset (here the PICO 4's)
```

With more than one headset, `--device CODENAME` (or `OVERPRESSURE_DEVICE` in `.env`) picks
one. Every step in the window shows the command line it runs, so the terminal can do the same.
The builds' Linux side runs in WSL, on Linux with sudo, or in the build container on any host
with Docker (`OVERPRESSURE_LINUX=docker`, [docs/tool.md](docs/tool.md#linux-root-steps)).

## Repository

```text
overpressure/   the generic host package: image formats, headset registry, command line, GUI
devices/        one folder per headset: its tooling, images, sources, tests and documents
tests/          host tests of the generic package
docs/           the tool and how to add a headset
```

`make test` (or `python -m unittest`) runs every host test, the generic and each headset's, in
seconds. Adding a headset: [docs/adding-a-headset.md](docs/adding-a-headset.md).

Device data and vendor files never enter the repository: backups, bootloader images, firmware,
Valve's packages and the built archives stay private in the ignored `backup/`, `assets/`,
`cache/` and `out/` folders and in `.env` ([.env.example](.env.example)).
