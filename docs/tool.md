# The tool

`python -m overpressure` builds a headset's images and works on the headset. It is one program
with two faces: a window for image creation and flashing, and a command line with the same
steps and every command of the headset. It is pure Python (3.9+, standard library only, Tk for
the window), so it runs on Windows, Linux and macOS, and it runs from the repository root:
`.env` and the private folders (`out/`, `cache/`, `assets/`, `backup/`) live there.

## The window

```sh
python -m overpressure gui
```

- **Headset**: the headsets in `devices/`; their steps fill the list on the left.
- **Steps**, in two groups: *Image* (building the images) and *Headset* (flashing, connecting,
  checking, the way back). Selecting a step shows what it does, its parameters and the command
  line it will run, which updates as the parameters change. *Copy command* puts it on the
  clipboard.
- **Run** starts the step; its output goes to the log below and the status line shows how it
  ended. One step runs at a time: builds share their caches. *Stop* ends the running step.
- A step that writes to the headset says so and asks before it starts.
- Steps that need Linux as root (the userspace build mounts and chroots) run through WSL as root
  on Windows and through `sudo` on Linux.

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
| `bootimg`, `avb`, `sparse`, `payload`, `cpio`, `compress`, `alpine`, `apksign`, `kernelinfo`, `elfdeps`, `windows_ncm` | the formats and helpers Android-based headsets share |

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
