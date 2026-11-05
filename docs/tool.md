# The tool

`python -m overpressure` builds a headset's images and works on the headset. It is one program
with two faces: a window for image creation and flashing, and a command line with the same
steps and every command of the headset. It is pure Python (3.9+, standard library only, Tk for
the window), so it runs on Windows, Linux and macOS, and it runs from the repository root:
`.env` and the private folders (`out/`, `cache/`, `assets/`, `backup/`) live there.

## Dependencies

```sh
python -m overpressure doctor
```

lists what this computer has and lacks for the headset's workflow, grouped by what each part is
needed for (the window, working on the headset, EDL, the builds, the extracted stock files),
with what was found and, for anything missing, how to get it. The exit status is 1 while
something needed is missing. The window shows the same as *Check dependencies* in its Setup
group, with the fix of the selected line to copy. The checks only look: version queries,
`command -v` in Linux (WSL as root on Windows), settings, file sizes and small files' hashes;
they install and change nothing. Missing but optional (Tk for the window) and warnings (a
slower fallback, say) do not fail it.

## Host tools

```sh
python -m overpressure tools
```

fetches the pinned programs the headsets' workflows use into `cache/tools`, each archive checked
by its SHA-256 ([overpressure/tools.py](../overpressure/tools.py)): Android's platform-tools
(adb, fastboot), edl-ng, and on Windows a Python environment with pyusb and libusb-package for
the USB network link (installed by pip with every file's hash pinned). A headset's settings that
name its own copies take precedence; without them the fetched ones are used, then PATH. The
window has the same as *Fetch the host tools* in its Setup group. Nothing is downloaded
anywhere else: the tools are fetched only by this command.

## Linux-root steps

Building a userspace mounts file systems and chroots into an aarch64 root, so those steps run
on Linux as root, in one of three places (`OVERPRESSURE_LINUX` in the environment or `.env`;
[overpressure/linux.py](../overpressure/linux.py)):

| Backend | Where | Default on |
| --- | --- | --- |
| `wsl` | Windows' Linux, as root | Windows (with WSL) |
| `sudo` | this Linux host | Linux |
| `docker` | the build container: Docker or Podman, any host; it holds every tool of the builds, the kernel's too | macOS, Windows without WSL |

The build container ([overpressure/container/Dockerfile](../overpressure/container/Dockerfile),
Ubuntu 24.04 pinned by digest) holds the tools of the userspace, system image and extraction
builds, LLVM included, so these need only Docker on the host; the kernel build needs its own cross
toolchain. `python -m overpressure container build` builds its image (`overpressure-builder:HASH`,
the recipe's hash), `container run -- CMD` runs a command from the repository root (built first
if missing), `container shell` opens a shell. The engine is Docker, else Podman;
`OVERPRESSURE_CONTAINER_ENGINE` in the environment chooses it. It runs privileged (the builds
mount and chroot); the repository is mounted at `/work` and `/var/tmp` (build roots, stock
folders) is the named volume `overpressure-var`. The aarch64 programs of the build root run
through the host kernel's binfmt handler: Docker Desktop registers one; on Linux,
`docker run --privileged --rm tonistiigi/binfmt --install arm64` does.

## The window

```sh
python -m overpressure gui
```

- **Headset**: the headsets in `devices/`; their steps fill the list on the left, below
  *Check dependencies* ([above](#dependencies)).
- **Steps**, in three groups: *Setup* (the dependency checks, the host tools), *Image* (building
  the images) and *Headset* (flashing, connecting, checking, the way back). Selecting a step shows what it does, its parameters and the command
  line it will run, which updates as the parameters change. *Copy command* puts it on the
  clipboard.
- **Run** starts the step; its output goes to the log below and the status line shows how it
  ended. One step runs at a time: builds share their caches. *Stop* ends the running step.
- A step that writes to the headset says so and asks before it starts.

Without a display (`tkinter` missing or no window system, as in WSL), the command line does
everything the window does.

## The command line

```text
python -m overpressure [--device CODENAME] COMMAND [ARGS...]

  devices              the headsets in devices/
  gui                  the window
  steps                the headset's steps and their command lines
  run STEP [NAME=VALUE...]
                       a step with parameters, as the window runs it
  ram-boot [IMAGE]     boot an image from RAM with fastboot boot, where the bootloader allows it
  doctor               what this computer has and lacks for the headset, with fixes
  tools [NAME...]      fetch the pinned host tools (platform-tools, edl-ng, the NCM venv)
  container build|run -- CMD|shell
                       the build container
  COMMAND [ARGS...]    a command of the headset
```

The headset is `--device`, else `OVERPRESSURE_DEVICE` (environment or `.env`), else the only one
in `devices/`. `python -m overpressure` without arguments lists the headset's commands;
`python -m overpressure COMMAND --help` shows a command's options. For example, with the PICO 4:

```sh
python -m overpressure run stage1 firmware=5.13.7-global-seko   # a step, as the window runs it
python -m overpressure build --firmware 5.13.7-global-seko      # the same through its command
python -m overpressure dev check
```

## How it fits together

| Module | Role |
| --- | --- |
| [overpressure/headset.py](../overpressure/headset.py) | `Headset` (its `Info`, `commands()`, `steps()`), `Step` and `Param`, the registry |
| [overpressure/runner.py](../overpressure/runner.py) | the process a step is on this host (WSL, sudo), its display, running it |
| [overpressure/__main__.py](../overpressure/__main__.py) | the command line |
| [overpressure/gui.py](../overpressure/gui.py) | the window (Tk) |
| [overpressure/localconfig.py](../overpressure/localconfig.py) | `.env`: each headset's settings with its own key prefix |
| [overpressure/fastboot.py](../overpressure/fastboot.py) | the RAM boot: `fastboot boot` with the headset's checks, never a write |
| [overpressure/doctor.py](../overpressure/doctor.py) | the dependency checks: the host's, then the headset's (`Headset.dependencies()`) |
| [overpressure/tools.py](../overpressure/tools.py) | the pinned host tools in `cache/tools` |
| [overpressure/linux.py](../overpressure/linux.py) | where Linux-root steps run: WSL, sudo or the build container |
| [overpressure/stage1.py](../overpressure/stage1.py) | a stage 1: an Alpine ramdisk in the stock Android boot image, from a headset's `Spec` |
| [overpressure/ota.py](../overpressure/ota.py) | stock boot images and partitions from a vendor OTA (block OTAs and `payload.bin`), checked against the zip |
| [overpressure/android.py](../overpressure/android.py), [qualcomm.py](../overpressure/qualcomm.py) | adb and fastboot, fail-closed; the SoC ID, EDL (edl-ng, bkerler/edl) and its partition maps |
| [overpressure/backup.py](../overpressure/backup.py), [process.py](../overpressure/process.py), [ssh.py](../overpressure/ssh.py) | verified backups; programs and bounded waits; SSH with the project's key |
| `bootimg`, `avb`, `sparse`, `payload`, `cpio`, `compress`, `alpine`, `apksign`, `kernelinfo`, `elfdeps`, `windows_ncm` | the formats and helpers Android-based headsets share |
| [scripts/stock-lib.sh](../scripts/stock-lib.sh) | for a headset's extraction scripts: a checked stock firmware folder, files out of its ext4 images |

## RAM boot

`python -m overpressure ram-boot [IMAGE]` boots an image from RAM with `fastboot boot`: nothing is
written, and the next normal boot starts whatever the partitions hold, which makes it the first
contact with a new headset. The sequence is the same for every headset: the image must be an
Android boot image; the headset is brought into fastboot if it is not there, and must be the only
device in fastboot; its bootloader must report an unlocked state; then `fastboot boot` and a
bounded wait until the image answers. The headset says how each of these looks for it
([adding-a-headset.md](adding-a-headset.md#ram-boot)), and the window shows the step when it has
a RAM boot.

A bootloader without `fastboot boot` ("unknown command" after the transfer) ends the RAM boot as
*refused* (exit status 3), apart from a failure (1): nothing was written and the headset stays in
fastboot. It never falls back to flashing.

## How a step runs

A step is a command line: either a command of the headset (`python -m overpressure --device
CODENAME ...`) or a script in the headset's folder. The window adds nothing a terminal cannot
do, so a step that works in the window works in a terminal and the other way round.
