# PICO 4 (phoenix)

[overpressure](../../README.md) on the original PICO 4 (2022, Snapdragon XR2): Valve's Steam Frame kernel with the
PICO's own drivers and device tree boots a SteamOS-compatible system installed on the headset (system in `super`,
data in `userdata`, as PICO OS lays them out). It runs Valve's VR session (SteamVR in gamescope) with a Monado
driver for the headset's lenses, cameras, sensors and controllers. A small Alpine system in `recovery` installs
and rescues it; the way back to PICO OS restores everything from the backup the install makes.

## Status

| Step | Result |
| --- | --- |
| 1 Recoverable device | EDL backups, engineering bootloader, unlock, the way back to stock |
| 2 Maintenance system | Alpine in `recovery`, SSH over USB, read-only storage |
| 3 Graphics | Valve's Mesa (Turnip, OpenGL through zink); both 2160 × 2160 panels at 90 and 72 Hz |
| 4 System | Holo Core aarch64 with the Steam Frame's systemd and SteamOS services; sound, Wi-Fi, Bluetooth, sleep |
| 5 XR hardware | IMU, lens correction and IPD, four tracking cameras, 6DoF with PICO OS's tracker on the CDSP, controllers, the colour room view |
| 6 VR session | SteamVR at 90 fps on the Frame's compositor path; Steam's library through Proton and FEX, Android games through Lepton, Steam Link |

Open work and details: [docs/roadmap.md](docs/roadmap.md).

## Install

**Before you start.** Installing erases PICO OS's apps and data and ends what is left of the warranty. Everything
here has run on **one** headset, the way back to PICO OS included. The tools refuse a headset, firmware or panel
that isn't verified ([list](docs/device-preparation.md#verified-hardware)): today a PICO OS `global-seko` build
5.13.3, 5.13.7 or 5.13.8, not a `sek` or China build ([why](docs/device-preparation.md#other-pico-os-builds)).
You build your own system; the project publishes no image and no vendor file ([docs/distribution.md](docs/distribution.md)).

**You need** the headset, a USB-C data cable, a computer with Windows, macOS or Linux and its internet connection,
and about 15 GB free there (the backup is most of it). The headset builds its own system during the install,
downloading about 2.2 GB through the computer's connection; nothing is built on the computer, and it needs no
Docker or Podman. On Linux also `python3-tk`
(or your distribution's equivalent), for a smooth window, and the Android udev rules (Arch `android-udev`, Debian
`android-udev-rules`), for USB access to the headset ([how it is checked](../../docs/tool.md#usb-access-on-linux)).

**The installer.** Double-click `Install.cmd` (Windows) or `Install.command` (macOS), or run `sh install.sh`
(Linux). Accept the disclaimer, choose the PICO 4, then **Install**, **Upgrade** or **Restore to stock**. Each
goes on where it stopped when started again, and after a failure says what state the headset is in and what to
do. The install's five stages:

| Stage | What happens | Headset |
| --- | --- | --- |
| 1 Check this computer | host tools and unlock files (pinned, hash-checked), then what else is missing, with fixes | not needed |
| 2 Prepare the install | the maintenance system, Valve's kernel and the boot images, from public downloads in a few minutes; also makes this computer's SSH key | not needed |
| 3 Connect your headset | developer mode and USB debugging on, plugged in; the installer checks the headset, firmware and panel before anything is written | in PICO OS |
| 4 Prepare your headset | one consent, a verified backup to this computer (**your way back: export it**), the engineering bootloader, the unlock, the maintenance system | EDL, fastboot |
| 5 Install | Wi-Fi if you like; the headset builds the SteamOS-compatible system itself, then it goes on and is checked; then "Unplug it and put it on" | on the cable |

**Upgrade** has the headset build this version and installs it over yours, keeping your data. **Restore to stock** brings back
PICO OS and its bootloader from the backup, with a stop for PICO OS's setup and USB debugging in between (wear the
headset on the cable through that setup: its first start may update the lens and fan controller)
([docs/install.md](docs/install.md#the-upgrade-and-the-way-back)). *Export backup…* writes the backup into one
checked file to keep safe; *Import backup…* brings it to another computer.

**In a terminal**, the same: `python -m overpressure install`, `upgrade`, `restore` and `backup export|import FILE`
([the tool](../../docs/tool.md#the-installer)); `python -m overpressure steps` lists every step's command. Commands
that touch the headset ask the disclaimer `y`/`n` first (`--accept-disclaimer` for unattended runs) and refuse on a
laptop on battery below 30 %.

**Your login.** The headset takes SSH (cable and Wi-Fi) only with a key: this computer's, `out/dev/id_ed25519`, or
your own named as `PICO4_SSH_PUBLIC_KEY` in `.env` before the install. No account has a password
([docs/security.md](docs/security.md)).

**If something goes wrong.** `python -m overpressure dev support-bundle` collects the logs for a bug report, your
identifiers masked ([docs/hardware-testing.md](docs/hardware-testing.md#support-bundle)); the installer's logs are in
`out/install/phoenix/`. A step that says the headset is "not in fastboot yet" while it shows its fastboot screen:
unplug the cable, plug it in again and start the step again. One that says the headset is in EDL mode but does not
answer (it answers only right after entering EDL mode): hold the power button for about 10 s until PICO OS starts,
then start the installer again; it enters EDL mode afresh.

## Documentation

| Document | Contents |
| --- | --- |
| [docs/roadmap.md](docs/roadmap.md) | status of each step, open work |
| [docs/install.md](docs/install.md) | the installed system: layout, boot modes, install, upgrade, the way back |
| [docs/device-preparation.md](docs/device-preparation.md) | identity, EDL backups, engineering bootloader, unlock, verified hardware |
| [docs/stage1.md](docs/stage1.md) | the maintenance system |
| [docs/kernel.md](docs/kernel.md) | Valve's kernel on the PICO 4: its release, the changes in overpressure/linux, building it, the contracts |
| [docs/graphics.md](docs/graphics.md) | graphics stack and panels |
| [docs/userspace.md](docs/userspace.md) | the system: packages, build, audio |
| [docs/xr.md](docs/xr.md) | IMU, Monado driver, lenses, cameras, tracking, controllers |
| [docs/session.md](docs/session.md) | Valve's VR session: SteamVR, gamescope, Steam, games, Steam Link |
| [docs/cooling.md](docs/cooling.md) | fan control and thermal policy |
| [docs/hardware-testing.md](docs/hardware-testing.md) | acceptance runs, development loop, support bundle |
| [docs/development.md](docs/development.md) | build system, build container, tests, Windows host |
| [docs/security.md](docs/security.md) | logins, services, what is protected |
| [docs/distribution.md](docs/distribution.md), [docs/stock-components.md](docs/stock-components.md) | where every file comes from; what is taken from PICO OS |
| [docs/release-checklist.md](docs/release-checklist.md) | every check of a release image |
| [docs/research.md](docs/research.md) | background: the PICO 4, the Steam Frame, related projects |

## This folder

```text
tooling/     host tooling (Python): its commands, the window's steps, the installer's plans
kernel/      Valve's kernel: the pinned release of overpressure/linux, its build script, boot images
initramfs/   the maintenance system's files in the Alpine ramdisk
userspace/   the system: the pinned release of overpressure/packages, adaptation layer, SteamVR configuration
src/         C sources outside the packages: the tracker's host, the reboot and splash helpers
scripts/     userspace, system image and kernel builds, the upgrade check, a Windows PC's internet sharing
prebuilt/    static aarch64 helpers
tests/       host tests and the QEMU boot tests
docs/        documentation
```

Device data and vendor files stay private in `backup/`, `assets/`, `cache/`, `out/` and `.env` at the repository root.
