<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo/overpressure.svg">
    <img src="docs/logo/overpressure-light.svg" width="128" height="128" alt="The overpressure logo: a headset whose two lenses are pressure gauges reading in the red">
  </picture>
</p>

<h1 align="center">overpressure</h1>

<p align="center">
  <b>Linux and Valve's SteamOS VR session on standalone VR headsets.</b>
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

overpressure turns a PICO 4 into a SteamOS headset: Valve's Steam Frame kernel and VR session (SteamVR in
gamescope) with a driver for the headset's own lenses, cameras, sensors and controllers. Your Steam library runs
on it, Windows games through Proton and FEX, Android games through Valve's Lepton, and PC games over Steam Link.
One installer builds the system on your computer from Valve's and PICO's public downloads and puts it on the
headset; the way back to PICO OS is one click away.

> [!WARNING]
> A beta for owners who accept the risk. It replaces the headset's system, erases its data and ends what is left of
> its warranty. It has run on one headset. The installer backs your headset up first: keep that backup. Not
> affiliated with Valve or PICO.

## What works

| | On the tested PICO 4 |
| --- | --- |
| **Works** | Both lenses at 90 and 72 Hz, head and controller tracking, the room view, sound and microphone, Wi-Fi and Bluetooth, sleep, Steam with Proton and FEX, Android games, Steam Link, the way back to PICO OS |
| **Limits** | Heavy games run at what the GPU allows. Only one unit, firmware and panel have run this; battery life unplugged is not measured ([roadmap](devices/phoenix/docs/roadmap.md)) |
| **Not included** | No image and no vendor file: you build your own ([what comes from where](devices/phoenix/docs/distribution.md)) |

## Quick start

**You need** the headset and a USB-C data cable, a computer with Windows, macOS or Linux, about 40 GB free, a few
hours for the first build, and [Docker](https://docs.docker.com/get-docker/) or [Podman](https://podman.io)
running (the build's Linux side runs in a container).

1. Download the release zip and unpack it.
2. Start the installer: double-click `Install.cmd` (Windows) or `Install.command` (macOS; the first time
   right-click, *Open*), or run `sh install.sh` (Linux). It fetches its own Python into `cache/`; nothing is
   installed on the computer.
3. Accept the disclaimer, choose your headset, then what to do: **Install**, **Upgrade** or **Restore to stock**.
   Each is a few stages with one button each, and goes on where it stopped when started again.

The PICO 4's stages, what they need and do: [devices/phoenix/README.md](devices/phoenix/README.md#install).

## Headsets

| Codename | Headset | Status |
| --- | --- | --- |
| [phoenix](devices/phoenix/README.md) | PICO 4 (2022, Snapdragon XR2), PICO OS 5.13.7 | Installs and runs; see [what works](#what-works) |

## For developers

Everything the window does, the command line does too ([docs/tool.md](docs/tool.md)); Python 3.9+, from the
repository root:

```sh
python -m overpressure doctor        # what this computer lacks, with fixes
python -m overpressure gui           # the window (--advanced: every step, one click each)
python -m overpressure install       # the installer in a terminal; also upgrade, restore
python -m overpressure steps         # the headset's steps and the command lines they run
python -m unittest                   # every host test, in seconds; make lint: ruff and ty
```

`overpressure/` is the generic tool, `devices/<codename>/` one headset each ([adding a
headset](docs/adding-a-headset.md)). Device data, vendor files and builds stay in the ignored `backup/`, `assets/`,
`cache/`, `out/` and `.env`. See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## License

The project's own code is in the public domain ([the Unlicense](LICENSE)). What comes from elsewhere keeps its
license ([THIRD-PARTY.md](THIRD-PARTY.md)): the kernel's sources are under the GPL, the Monado driver under
BSL-1.0.
