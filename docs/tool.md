# The tool

`python -m overpressure` builds a headset's images and works on the headset. It is one program
with two faces: a window (the installer, and every step in its Advanced mode) and a command line
with the same steps and every command of the headset. It is pure Python (3.9+, standard library
only; the window adds Tk and CustomTkinter), so it runs on Windows, Linux and macOS, and it runs
from the repository root: `.env` and the private folders (`out/`, `cache/`, `assets/`, `backup/`)
live there.

## Dependencies

```sh
python -m overpressure doctor
```

lists what this computer has and lacks for the headset's workflow, grouped by what each part is
needed for (the window, working on the headset, EDL, the builds, the extracted stock files),
with what was found and, for anything missing, how to get it. The exit status is 1 while
something needed is missing. The window shows the same as *Check dependencies* in Advanced
mode's Setup group, with the fix of the selected line to copy. The checks only look: version
queries, `command -v` in Linux (WSL as root on Windows), settings, file sizes and small files'
hashes; they install and change nothing. Missing but optional (Tk and CustomTkinter for the
window) and warnings (a slower fallback, say) do not fail it.

## Host tools

```sh
python -m overpressure tools
```

fetches the pinned programs the headsets' workflows use into `cache/tools`, each archive checked
by its SHA-256 ([overpressure/tools.py](../overpressure/tools.py)): Android's platform-tools
(adb, fastboot), edl-ng, and on Windows a Python environment with pyusb and libusb-package for
the USB network link (installed by pip with every file's hash pinned). A headset's settings that
name its own copies take precedence; without them the fetched ones are used, then PATH. The
window has the same as *Fetch the host tools* in Advanced mode's Setup group. Nothing is
downloaded anywhere else: the tools are fetched only by this command.

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
python -m overpressure gui              # the window: which headset, then its installer
python -m overpressure gui --advanced   # the same, then Advanced mode: every step of the headset, one click each
python -m overpressure --device phoenix gui  # straight to the PICO 4's installer, past the headset page
python -m overpressure gui --demo       # a fake installer engine, a fake step program and fixed checks: nothing runs
python -m overpressure gui --demo-fresh # the same, without the earlier run the demo starts from
python -m overpressure gui --demo-headsets  # the demo with a fake second headset on the headset page
```

One window ([overpressure/window/](../overpressure/window/__init__.py)) opens on the headset page, *Which
headset do you have?*, and then has two views for the chosen headset: the installer, the way in for an
owner, and Advanced mode, every step of the headset for whoever wants each one. The link at the right of
the header switches between them (*Advanced mode*, *Installer*); whatever runs keeps running.
Neither view does anything a terminal cannot: the installer is the engine's command line, and a step shows
the command line it runs. The window looks the same on Windows, macOS and Linux, light or dark as the system
is: a flat app shell (a header bar, a raised sidebar, the page on a slightly darker surface), cards and list
rows that shade under the pointer, status pills and note boxes in soft tints, and one main button in the accent
colour per view. Its look is one set of design tokens ([theme.py](../overpressure/window/theme.py): spacing,
radii, colours as light and dark pairs with body text at a contrast of at least 4.5:1, the type scale) and
the components built from them ([kit.py](../overpressure/window/kit.py)). It uses the best UI font the system
has (Segoe UI Variable or Segoe UI on Windows, the system font on macOS, the first of Inter, Cantarell, Noto
Sans, Ubuntu and DejaVu Sans on Linux) and a monospaced one for command lines and output; no font files are
bundled. A page keeps a readable width in a wide window and scrolls in a short one; the window opens at
1120×760 where the screen has room and is at least 900×600.

It needs Tk and [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) (`customtkinter==6.0.0`,
the `gui` extra in `pyproject.toml`, pinned with its dependencies in `uv.lock`); nothing else in the tool
does. The start scripts below bring both; in a development environment `uv sync --extra gui` does. Without
them `gui` says how to get them and exits with status 1, and without a display it exits with status 2; the
command line does everything the window does.

- **The headset page.** The window always opens on *Which headset do you have?*, also with one headset in
  `devices/`: one card per headset with its name, SoC and summary, what choosing it opens (the installer
  and Advanced mode, or Advanced mode only for a headset without an installer) and, for a headset with an
  installer whose plan shows an earlier run, where it stopped (*Last time: stopped in Connect your
  headset*; the page reads each plan with `install --json --plan`, which runs nothing). The headset chosen
  last is listed first, marked *Last used* in a pill, with the page's one main button; one click on an entry or its
  button opens that headset's installer at its start page (or Advanced mode for a headset without one).
  Only `python -m overpressure --device CODENAME gui` skips the page; `OVERPRESSURE_DEVICE` does not.
- **The last choice** is a preference of the window, not a setting: `out/window.json`
  (`{"last_headset": CODENAME}`), written when a headset is chosen. A missing or broken file, or a codename
  no longer in `devices/`, marks nothing. The window writes nothing to `.env`; `OVERPRESSURE_DEVICE` there
  stays the command line's.
- **The way back.** Every page after the headset page (the start page, the installer's stages, its done and
  failed pages, Advanced mode) has *‹ Headsets* in the header, also with one headset. It keeps nothing
  running and loses nothing: each headset's recorded progress is its engine's state, its details stay for
  the window's session, and choosing it again shows its start page.
- **One thing at a time.** A step and the installer never run at once (builds share their caches): starting
  one while the other runs is refused with a message that says which one runs. Going back to the headset
  page while either runs is refused the same way.
- **Closing** while the installer or a step runs asks first. The installer stops as below (the same
  question and texts as its *Stop*); a step's program is ended. The window closes once nothing runs any more.

### The installer view

The installer view shows the headset's installer stages ([below](#the-installer)) with one main button at a
time. It draws and asks but does nothing itself: it runs `python -m overpressure install --json` from the
repository root as a child, renders its [events](#events) and writes the answers to the child's stdin, so a
terminal can do all of it too.

- **Layout.** The stages down the left (done: a check; running: a dot in the accent colour; pending: an
  empty circle; failed: a mark in red), joined by a line that turns to the accent colour below a finished
  stage, the current one shaded. On the right, the current stage: its title, what it does, a card with its
  tasks and their estimates at the right (*done* once finished; the running one's progress text in the
  accent colour, a thin progress bar under it), and a note box for the engine's notes, tinted by their level
  (a warning amber, an information blue). An action bar along the bottom holds *Show details* (the child
  programs' output, in a drawer under the view) and the main button: *Start*, *Continue* after a stop,
  *Retry* after a failure. While a run goes, an outlined *Stop* button stands where the main button was.
- **Before the first run** the window reads the plan (`install --json --plan`, on the headset page or
  once the headset is chosen), which runs nothing, so the installer's first page already shows the stages and what an earlier run finished. Nothing starts until a button
  is pressed; there is none until the plan is read.
- **The start page.** When the plan shows an earlier run (a task done or skipped, a failure, or a task
  still recorded as running), the installer's first page is *Welcome back*: the stages done so far and the stage it
  stopped (or failed) in, with three choices. *Continue where you left off*, the main button, runs the
  engine as it is. *Start over from…* lists the stages with something recorded as rows of a card (a stage
  that writes to the headset says so in an amber pill); choosing one asks in the window's panel, naming what is forgotten and done again, with a warning box when a forgotten stage writes
  to the headset (done again on a headset that is already prepared, it repeats its backup and writes), and
  runs the engine with `--restart STAGE`. *Advanced mode* opens Advanced mode. Without an earlier run the
  installer's first page is the first stage with *Start*.
- **Asks** appear as a card in the middle of the view, which is dimmed behind it: a consent with its accept
  and decline, an action with *Done*, a form with its fields (secret fields masked). The values go to the engine only in the answer
  line, and the fields are emptied when the panel closes. While the engine waits for an answer, the
  header's link in Advanced mode says *Installer (waiting for you)*, shaded in the accent colour.
- **The end**, under a large check (or, after a failure, a red mark). After the install: the headset's own
  title and text (`Headset.after_install()`; without them *Installed*, "Your headset is ready.") and its
  actions in the action bar, each of which opens Advanced mode at one of
  its steps (the engine has no modes for them; an action whose step the headset lacks is left out); *Done*
  closes the window. The PICO 4's: *Unplug it and put it on*, signing in to Steam, with *Update*, *Back to
  PICO OS* and *Get help* (its `install`, `restore` and `support-bundle` steps). After a failure: the error,
  where the headset stands and what to do next, with *Retry* and *Show details*.
- **Stopping.** *Stop*, or closing the window while a run is under way, asks first in the panel: *Stop the
  installer?* For a task that does not write (or one waiting for an answer): it stops now, nothing on the
  headset changes, starting again picks up there. For a running task that writes to the headset: it finishes
  first and the installer stops right after. A stop closes the engine's stdin ([stopping](#the-installer));
  the window shows *Stopping…* until the engine's `finished` event, then the stage with what the engine
  says about the headset's state and *Continue* (or, when closing, closes).

### Advanced mode

- **Headset**: the window's headset at the top of the left side, with its SoC and summary; the list
  below it follows the choice on the headset page, the selected entry shaded and marked by an accent bar. A headset without an installer opens here, with no link to the
  installer. `gui --advanced` opens here once a headset is chosen.
- **Steps**, in three groups: *Setup* (*Check dependencies*, the host tools), *Image* (building the images)
  and *Headset* (flashing, connecting, checking, the way back). A step shows what it does, where it runs (a
  Linux-root step: WSL, sudo or the build container) and whether it writes to the headset, its parameters
  (a text, a choice, a path with *Browse* where this computer's dialog can pick it, a flag; in a card) and
  the command line it will run, in a code block, which follows the parameters as they change; *Copy* puts
  it on the clipboard. A page too long for the window scrolls. Values
  are kept per headset and step while the window is open.
- **Run** starts the selected step; its output goes to the output block below it, and the status line,
  with a dot in the state's colour, says what runs and how it ended (*done*, *failed* with its exit status, *stopped*). One step runs at a time, and its
  entry in the list says *running*. *Stop* ends the running step's program; for a step that writes to the
  headset it asks first.
- **A step that writes to the headset** asks before it starts, in the window's own panel, with its command
  line.
- **Check dependencies** runs the [checks](#dependencies) the first time it is shown and again with *Check
  again*: a table grouped by what they are needed for, each line with its status as a tinted pill (*ok*,
  *missing*, *warning*, *optional*) and what was found; the list entry counts what is missing. The first
  line that is not *ok* is selected, and the selected line's fix is shown below it in a code block with
  *Copy*.

With `--demo` the installer's engine is a fake one, a step's program prints a few lines without running
anything, and the checks are a fixed list: no headset, nothing runs. The fake engine starts as if an earlier
run had done four stages, so the window opens at the start page; `--demo-fresh` starts it with nothing done.
It stops as the engine does: at once, except in a task that writes, which finishes first. Its plan names the
chosen headset; it reads the last choice but keeps its own in the window (nothing is written).
`--demo-headsets` is the demo (`--demo`, or `--demo-fresh` with it) with a fake second headset, *Example
headset*, which has no installer and exists in that demo only.

### The start scripts

An owner starts the window from the repository's root folder, without installing anything:

| Script | Host | How |
| --- | --- | --- |
| `Install.cmd` | Windows (x64, arm64) | double-click; no administrator rights |
| `Install.command` | macOS (Intel, Apple silicon) | double-click (the first time: right-click, *Open*); it runs `install.sh` |
| `install.sh` | Linux (x86_64, aarch64) | `sh install.sh` |

Each fetches [uv](https://docs.astral.sh/uv/) 0.13.4 for the machine into `cache/uv/`, checks the
archive against its pinned SHA-256 (a mismatch deletes it and stops; an archive that matches is not
fetched again), unpacks it and runs, from the repository root:

```sh
uv run --locked --no-dev --extra gui --managed-python --python 3.12 python -m overpressure gui
```

with `UV_CACHE_DIR=cache/uv-cache`, `UV_PYTHON_INSTALL_DIR=cache/python` and
`UV_PROJECT_ENVIRONMENT=cache/venv`, so uv's Python 3.12 (which has Tk on every platform; a Linux
system Python often has not), CustomTkinter and the environment stay inside the folder, apart from a
development `.venv`. When the start fails, the terminal window stays open with the error.

## The installer

```sh
python -m overpressure install                  # in a terminal: the stages as text, asks through the keyboard
python -m overpressure install --json           # the window's engine: events on stdout, answers on stdin
python -m overpressure install --plan           # the plan and what is done already; runs nothing
python -m overpressure install --restart STAGE  # forget what was done from STAGE on, then run
```

A headset with an installer gives its plan through `Headset.install_plan()`: stages, each a list of
tasks, built on its existing commands and steps ([overpressure/install.py](../overpressure/install.py)
is the engine, `devices/<codename>/tooling/install.py` the plan). A headset without one returns no
stages, and `install` says so (exit status 2). The engine is standard library only, so a terminal does
everything the window does.

- **Order and resume.** Tasks run in order; the first failure ends the run. Each headset has its own
  state, `out/install/<codename>/state.json` (`{"schema": 1, "tasks": {ID: {"status": ..., "finished":
  UTC}}}`), written whole after every change. A task done in an earlier run is skipped unless its own done check says its
  result is gone; a done check also skips a task on its first run when its output is there already
  (skipped). `--restart STAGE` forgets that stage's tasks and every later stage's.
- **Child programs.** A task's programs run from the repository root (Linux-root steps through the
  backend of [Linux-root steps](#linux-root-steps), as the window's steps do); their output goes line by
  line to log events and to `out/install/<codename>/<task>.log`. What a task prints itself becomes log lines
  too, so stdout carries only events.
- **One at a time.** An OS lock on `out/install/lock` keeps a second engine out, whatever its headset (builds
  share their caches); it ends with the process that holds it.
- **An older state.** A state in `out/install/state.json`, from before the headsets' folders, belongs to the
  headset whose plan has every task it records. That headset's run, or its `--plan`, moves it and those
  tasks' logs into `out/install/<codename>/` when that folder has no state yet, under the lock; while
  another engine holds the lock, `--plan` reads it where it is and moves nothing.
- **Stopping.** Ctrl-C, a closed stdin or `{"stop": true}` on stdin stop the run at once wherever it
  waits: an ask, a wait of the task (`ctx.clock`, which every `until()` a task runs goes through, looks for
  the stop every 0.2 s), a program that does not write to the headset (it is ended). A program that writes
  is never cut off: it runs in its own process group, so Ctrl-C does not reach it, and the stop takes effect
  when it has ended (its result counts; the task's next wait, ask or program stops it). The stopped task is
  pending again, and `finished`'s `where` says the headset's state: for a task that does not write, nothing
  changed and starting again continues; for one that writes, whether its write was finished first or had not
  begun, and the task's own state and what to do.
- **Secrets.** The values of a form's secret fields stay in the engine's memory. They never reach a log,
  the state, `.env`, a command line (the engine refuses such a command line), an event or stderr; they
  reach a program on its stdin only. Any text that would carry one shows `***` instead.
- **Settings.** What the installer finds out (a headset's serials) it writes to `.env` itself
  (`localconfig.update`: the key's line is replaced, every other line and comment kept, a missing `.env`
  starts as a copy of `.env.example`).
- **Exit status.** 0 finished, 1 failed, 2 a usage error or no installer, 3 stopped.

### Events

With `--json` every line on stdout is one JSON object; anything a child program prints arrives wrapped
in a log event. Texts are for the owner: plain words, no stack traces (those go to the task's log file).

| Event | Fields | When |
| --- | --- | --- |
| `plan` | `headset` {`codename`, `name`}, `stages` [{`id`, `title`, `summary`, `tasks` [{`id`, `title`, `estimate`, `writes`}]}] | first, once |
| `task` | `stage`, `task`, `status`: `pending`, `running`, `done`, `skipped` (its output was there) or `failed` | each change; a task done in an earlier run shows `done` when the run reaches it |
| `progress` | `task`, `fraction` (0 to 1, or null when unknown), `text` | while a task runs |
| `log` | `task`, `line` | a line of a child program, or of the task itself: the details view |
| `note` | `task`, `level` (`info` or `warning`), `text` | something the owner should know |
| `ask` | `id`, `kind`, `title`, `text`, `fields` [{`name`, `label`, `secret`, `optional`}], `accept`, `decline` | the engine waits for the answer |
| `finished` | `status` (`done`, `failed`, `stopped`), `task` (null when done), `error`, `where` | last, once |

`ask` kinds: `confirm` (a consent: accept or decline), `action` (the owner does something, such as
plugging in; accept is "Done") and `form` (fields; secret ones are masked). The answer is one line on
stdin: `{"answer": ID, "accepted": true, "values": {NAME: VALUE}}`. A declined ask, or a closed stdin,
stops the run (`finished` with `stopped`); an empty required field is asked again with a note. After a
failure or a stop, `where` says what state the headset is in and what to do next. `--plan --json` prints the
`plan` event and a `task` event with each task's recorded status, and runs nothing.

In a terminal the same plan prints as stage headings and a line per task status, child programs'
output indented below; asks use `input()`, secret fields `getpass()`.

## The command line

```text
python -m overpressure [--device CODENAME] COMMAND [ARGS...]

  devices              the headsets in devices/
  gui [--advanced] [--demo | --demo-fresh] [--demo-headsets]
                       the window: the installer, and every step in its Advanced mode
  steps                the headset's steps and their command lines
  run STEP [NAME=VALUE...]
                       a step with parameters, as the window runs it
  ram-boot [IMAGE]     boot an image from RAM with fastboot boot, where the bootloader allows it
  install [--json] [--plan] [--restart STAGE]
                       the installer: the headset's stages in order, resumable
  doctor               what this computer has and lacks for the headset, with fixes
  tools [NAME...]      fetch the pinned host tools (platform-tools, edl-ng, the NCM venv)
  container build|run -- CMD|shell
                       the build container
  COMMAND [ARGS...]    a command of the headset
```

The headset is `--device`, else `OVERPRESSURE_DEVICE` (environment or `.env`), else the only one
in `devices/`; `gui` asks on its headset page instead, unless `--device` names one. `python -m overpressure` without arguments lists the headset's commands;
`python -m overpressure COMMAND --help` shows a command's options. For example, with the PICO 4:

```sh
python -m overpressure run stage1 firmware=5.13.7-global-seko   # a step, as the window runs it
python -m overpressure build --firmware 5.13.7-global-seko      # the same through its command
python -m overpressure dev check
```

## How it fits together

| Module | Role |
| --- | --- |
| [overpressure/headset.py](../overpressure/headset.py) | `Headset` (its `Info`, `commands()`, `steps()`, `install_plan()`, `after_install()`), `Step` and `Param`, `AfterInstall` and `Action`, the registry |
| [overpressure/install.py](../overpressure/install.py) | the install engine: `Stage`, `Task`, `Ask`, the `Context` a task gets, the state, the lock, the events, `python -m overpressure install` |
| [overpressure/runner.py](../overpressure/runner.py) | the process a step is on this host (WSL, sudo), its display, running it |
| [overpressure/__main__.py](../overpressure/__main__.py) | the command line |
| [overpressure/window/](../overpressure/window/__init__.py) | the window (CustomTkinter). Its models, without widgets: `installer.py` (the engine's events as stages and asks; the child engine, the demo engine), `advanced.py` (the steps, their values and command lines, one run at a time, the checks) and `headsets.py` (which headset: the headset page, its plan probes, the last choice in `out/window.json`, the demo's second headset). Its widgets: `app.py` (the header, the installer view, the panels) and `advanced_view.py`; `theme.py` holds the design tokens and the fonts' choice, `kit.py` the components built from them |
| [overpressure/localconfig.py](../overpressure/localconfig.py) | `.env`: each headset's settings with its own key prefix; `DeviceSettings`, what every Qualcomm Android headset names |
| [overpressure/fastboot.py](../overpressure/fastboot.py) | flashing an image sparse above a size, raw below; the RAM boot: `fastboot boot` with the headset's checks, never a write |
| [overpressure/doctor.py](../overpressure/doctor.py) | the dependency checks: the host's, then the headset's (`Headset.dependencies()`), built from the shared ones (settings, EDL backup and tools, adb and fastboot, Windows' USB link, an arm64 build host) |
| [overpressure/tools.py](../overpressure/tools.py) | the pinned host tools in `cache/tools` |
| [overpressure/linux.py](../overpressure/linux.py) | where Linux-root steps run: WSL, sudo or the build container |
| [overpressure/stage1.py](../overpressure/stage1.py) | a stage 1: an Alpine ramdisk in the stock Android boot image, from a headset's `Spec` |
| [overpressure/ota.py](../overpressure/ota.py) | stock boot images and partitions from a vendor OTA (block OTAs and `payload.bin`), checked against the zip; a pinned file out of a remote zip, checked by its hash |
| [overpressure/android.py](../overpressure/android.py), [qualcomm.py](../overpressure/qualcomm.py) | adb and fastboot, fail-closed, the wait for Android's boot, a partition's size from `getvar all`; the SoC ID, EDL (edl-ng, bkerler/edl), its partition maps, a full backup and a write verified by readback |
| [overpressure/backup.py](../overpressure/backup.py), [process.py](../overpressure/process.py), [ssh.py](../overpressure/ssh.py) | verified backups and a partition's image in them; programs and bounded waits; SSH with the project's key, the keys an image enrolls |
| [overpressure/evidence.py](../overpressure/evidence.py), [storage.py](../overpressure/storage.py) | a run that records every step and command in its results folder, bounded waits and HTTP fetches; the read-only storage policy a headset's Linux stages enforce |
| `bootimg`, `avb`, `sparse`, `payload`, `cpio`, `compress`, `alpine`, `apksign`, `kernelinfo`, `elfdeps`, `windows_ncm` | the formats and helpers Android-based headsets share (`bootimg` also edits a boot image's ramdisk and command line) |
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

## Development

The tool needs no package to run, but working on it uses [uv](https://docs.astral.sh/uv/) and Astral's
[ruff](https://docs.astral.sh/ruff/) and [ty](https://docs.astral.sh/ty/). `pyproject.toml` pins them in the
`dev` dependency group and configures them; `uv.lock` holds the exact versions.

```sh
uv sync                        # .venv with the tool (editable), ruff, ty and pyusb for the types
uv run python -m unittest      # the host tests in that environment
uv run ruff format .           # formatting (line length 120)
uv run ruff check .            # lint, every rule; --fix applies the safe fixes
uv run ty check                # types, for Python 3.9 on every platform
```

ruff runs every rule (`select = ["ALL"]`). `pyproject.toml` turns off only what conflicts: ruff's incompatible
docstring pairs, the trailing commas the formatter places, pytest's assertion style (the tests are unittest);
tests need no docstrings or return annotations on their test methods, and `print` is allowed outside the tests,
since everything else is a command-line program. Any other exception is a `# noqa: RULE` with its reason on the
line.

`make lint` runs the three checks as CI does, `make format` formats and applies the safe fixes. Both also cover
the headset's Python programs without a suffix in `devices/*/userspace/rootfs/`, which `pyproject.toml` lists
by name; `tests/test_repo_checks.py` fails when one is missing there.
