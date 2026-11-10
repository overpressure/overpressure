<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo/overpressure.svg">
    <img src="docs/logo/overpressure-light.svg" width="128" height="128" alt="The overpressure logo: a headset whose two lenses are pressure gauges reading in the red">
  </picture>
</p>

<h1 align="center">overpressure</h1>

<p align="center">
  <b>Linux and a SteamOS-compatible userspace on standalone VR headsets.</b>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a>
  &nbsp;&middot;&nbsp;
  <a href="#headsets">Headsets</a>
  &nbsp;&middot;&nbsp;
  <a href="docs/tool.md">The tool</a>
  &nbsp;&middot;&nbsp;
  <a href="devices/phoenix/docs/roadmap.md">Roadmap</a>
  &nbsp;&middot;&nbsp;
  <a href="LICENSE">Unlicense</a>
</p>

A headset boots a SteamOS-compatible stage 2 installed on it, and a small Linux stage 1 in another boot partition
installs and rescues it; stage 2 runs Valve's VR session (gamescope and SteamVR) with a Monado driver for the
headset's hardware.

> [!WARNING]
> This is a beta for owners who accept the risk. It replaces the headset's system, wipes its data and voids what
> warranty is left; a failed step can leave a headset needing recovery. It has run on one headset. Keep your backup;
> each headset's documents say how its way back to stock works. This project isn't affiliated with Valve or PICO.

## Headsets

| Codename | Headset | Status | Folder |
| --- | --- | --- | --- |
| phoenix | PICO 4 (2022, Snapdragon XR2), PICO OS 5.13.7 | Valve's kernel and VR session with 6DoF head and controller tracking, games and Steam Link | [devices/phoenix](devices/phoenix/README.md) |

| | On the tested PICO 4 |
| --- | --- |
| **Works** | Both lenses at 72 and 90 Hz, head and controller tracking, the room view, sound and microphone, Wi-Fi, sleep, Steam with Proton and FEX, Android apps through Lepton and Steam Link from a PC |
| **Doesn't, or isn't known** | Heavy games run at what the GPU allows; no other unit, firmware or panel has run this; a boot from an empty data partition wearing only the headset, the way back to PICO OS and battery life unplugged aren't verified ([roadmap](devices/phoenix/docs/roadmap.md)) |
| **Isn't included** | No image and no vendor file. You build your own from the public downloads of Valve and PICO ([what comes from where](devices/phoenix/docs/distribution.md)) |

## Quick start

1. Download the release zip and unpack it on a drive with room for the build (the headset's README says how
   much).
2. Start the installer in that folder: double-click `Install.cmd` on Windows or `Install.command` on macOS (the
   first time: right-click, *Open*), or run `sh install.sh` on Linux. The start script fetches a pinned
   [uv](https://docs.astral.sh/uv/), which brings Python and the window's toolkit into `cache/`; nothing is
   installed on the computer.
3. Choose your headset on the window's first page, *Which headset do you have?*, then follow its five stages,
   one main button each: check this computer, build SteamOS, connect your headset, prepare your headset,
   install. It goes on where it stopped when it is started again.

What each stage does on the PICO 4, and what it needs: [devices/phoenix/README.md](devices/phoenix/README.md#install).

## The tool

One tool builds the images and works on the headset, as a window or on the command line
([docs/tool.md](docs/tool.md)). It needs Python 3.9+ and nothing else (the window adds Tk and
CustomTkinter, which the start scripts bring); run it from the repository root:

```sh
python -m overpressure doctor                 # what this computer has and lacks, with fixes
python -m overpressure tools                  # fetch the pinned host tools (adb, fastboot, edl-ng, ...)
python -m overpressure gui                    # the window: the installer (--advanced: every step, one click each)
python -m overpressure devices                # the supported headsets
python -m overpressure steps                  # the headset's steps and their command lines
python -m overpressure dev check              # a command of the headset (here the PICO 4's)
```

With more than one headset, `--device CODENAME` (or `OVERPRESSURE_DEVICE` in `.env`) picks
one. Every step in the window shows the command line it runs, so the terminal can do the same.
The builds' Linux side runs in the build container on every host, which needs Docker or Podman
([docs/tool.md](docs/tool.md#linux-root-steps)).

## Repository

```text
overpressure/   the generic host package: image formats, headset registry, command line, the window
devices/        one folder per headset: its tooling, images, sources, tests and documents
tests/          host tests of the generic package
docs/           the tool, how to add a headset, and the logo (docs/logo)
Install.cmd, Install.command, install.sh
                the installer's start scripts (Windows, macOS, Linux)
```

`make test` (or `python -m unittest`) runs every host test, the generic and each headset's, in
seconds; `make lint` checks formatting, lint and types with uv, ruff and ty
([docs/tool.md](docs/tool.md#development)). Adding a headset: [docs/adding-a-headset.md](docs/adding-a-headset.md).

Device data and vendor files never enter the repository: backups, bootloader images, firmware,
Valve's packages and the built archives stay private in the ignored `backup/`, `assets/`,
`cache/` and `out/` folders and in `.env` ([.env.example](.env.example)).

## License

The project's own code is released into the public domain under [the Unlicense](LICENSE). What comes from elsewhere
keeps its license, as [THIRD-PARTY.md](THIRD-PARTY.md) and the folders' `LICENSE.md` say: the kernel's sources
are under the GPL, the Monado driver under BSL-1.0. See [CONTRIBUTING.md](CONTRIBUTING.md) and
[SECURITY.md](SECURITY.md).
