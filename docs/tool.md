# The tool

`python -m overpressure` builds a headset's images and works on the headset: a window (the installer,
and every step in its Advanced mode) and a command line with the same steps and every command of the
headset.

**TL;DR**

| To | Run |
| --- | --- |
| install, as an owner | `Install.cmd` (Windows), `Install.command` (macOS) or `sh install.sh` (Linux) |
| download a headset's release into `devices/` | `python -m overpressure headset add CODENAME [--channel beta\|preview \| --version V]` |
| see what this computer lacks | `python -m overpressure doctor` |
| open the window | `python -m overpressure gui [--advanced]` |
| install, upgrade or restore in a terminal | `python -m overpressure install`, `upgrade`, `restore` |
| export or import the headset's backup | `python -m overpressure backup export\|import FILE` |
| run a build's Linux side | `python -m overpressure container run -- CMD` |
| list a headset's steps and commands | `python -m overpressure steps`, `python -m overpressure --help` |

It is Python 3.9+ on the standard library, with pinned packages where they spare the owner an outside
tool: brotli (the stock OTA), pyusb with libusb-package (EDL devices, Windows' USB network link), and Tk
with CustomTkinter for the window. It runs on Windows, Linux and macOS, from the repository root, where
`.env` and the private folders (`out/`, `cache/`, `assets/`, `backup/`) live.

## Headsets in devices/

overpressure holds no headset. Each is its own repository (`overpressure/<codename>`) with its own releases,
dropped into the ignored `devices/<codename>/`; [overpressure/catalogue.py](../overpressure/catalogue.py)
lists the headsets there are.

```sh
python -m overpressure headset list                 # every headset, and what devices/ holds of it
python -m overpressure headset releases phoenix     # its published releases, newest first
python -m overpressure headset add phoenix          # its newest stable release
python -m overpressure headset add phoenix --channel beta      # the newest, betas included
python -m overpressure headset add phoenix --channel preview   # its main: development state, untested
python -m overpressure headset add phoenix --version 1.0-beta  # a release by its version, older ones too
```

A release is the tag's source zip, checked against its published SHA-256 (a preview of `main` has none),
unpacked into a new folder and checked against its `headset.json` before it replaces what was there.
`devices/<codename>/.release.json` records which it is. A development checkout, a Git clone of the
headset's repository in its folder, is never replaced: Git updates it.

`headset.json` names the headset interface the release was made for (`api`; `API` in
[overpressure/headset.py](../overpressure/headset.py)). The registry loads only a headset this overpressure
runs; `python -m overpressure devices` lists the others as refused, with the reason (update overpressure, or
download another release). A new `.env` starts as `.env.example` and each headset's `env.example`.

## Dependencies

```sh
python -m overpressure doctor
```

lists what this computer has and lacks for the headset's workflow, grouped by what each part is needed
for (the window, the headset, EDL, the builds, the extracted stock files), with a fix for anything
missing. The exit status is 1 while something needed is missing; optional parts (Tk and CustomTkinter,
pyusb and libusb-package, brotli while the brotli command is there) and warnings do not fail it. The
checks only look: they install, build and change nothing. The window has the same as *Check dependencies*
in Advanced mode.

The builds' checks are the build container's ([Linux-root steps](#linux-root-steps)), for builds on this
computer: optional, never missing, since a headset can build its own system (the PICO 4's installer does, and
skips them):

| Check | Fix when it is not there |
| --- | --- |
| Docker or Podman answers (`ENGINE info`) | install Docker or Podman and have it running (how it runs its containers is the owner's choice) |
| the image is pulled | `python -m overpressure container pull` |
| the engine has 8 GiB of memory or more | give the engine more (Docker Desktop: Settings, Resources, Memory limit; Podman: `podman machine set --memory`); below it a warning: the build runs fewer jobs and takes much longer |
| the aarch64 handler (an x86-64 engine) | none: each build registers it; after a failure, `python -m overpressure container binfmt` |

The image holds every Linux tool of the builds, so no tool is checked one by one.

## Host tools

```sh
python -m overpressure tools [NAME...]
```

fetches the pinned platform-tools (adb, fastboot) and edl-ng into `cache/tools`, each archive checked by
its SHA-256 ([overpressure/tools.py](../overpressure/tools.py)). A headset's settings that name its own
copies take precedence, then the fetched ones, then PATH. Only this command downloads them; the window has
it as *Fetch the host tools*. Windows' USB network link needs no tool: it uses pyusb and libusb-package.

## Linux-root steps

Building a userspace mounts file systems and chroots into an aarch64 root, so those steps
(`Step.linux_root`: the userspace archive, the system image, Valve's kernel) run as root in overpressure's
build container ([overpressure/linux.py](../overpressure/linux.py)) on every host, with Docker or Podman,
whichever answers first (`OVERPRESSURE_CONTAINER_ENGINE` chooses). A step's command line is
`python -m overpressure container run -- COMMAND`, from the repository root.

```sh
python -m overpressure container pull           # the pinned build image, ghcr.io/overpressure/builder
python -m overpressure container run -- CMD      # CMD as root in the container (the image built first if missing)
python -m overpressure container shell           # an interactive shell there
python -m overpressure container binfmt          # register the aarch64 handler when the engine has none
python -m overpressure container run -- make test  # the host tests and every headset's shell tests, on Linux
```

- **The image.** [overpressure/builder](https://github.com/overpressure/builder)'s release, which
  `overpressure/linux.py` pins by tag and digest (`IMAGE`): Ubuntu 24.04 with every tool of the builds,
  LLVM 20's clang and lld, the kernel's cross compiler, pahole and qemu-user-static, for amd64 and arm64.
  The pin, not a package mirror, keeps a build's tools the same on every computer and later; the release
  lists every package's version. `container pull` gets it (`container run` does when it
  is missing); `OVERPRESSURE_BUILDER` names another image, such as one built from the builder's recipe.
  Every run passes the image to the command as `OVERPRESSURE_IMAGE`. Its contract (entrypoint, ownership
  hand-back, the aarch64 handler) is in that repository's README.
- **What it sees.** It runs privileged. The repository is at `/work`; `/var/tmp` (the build roots) is the
  named volume `overpressure-var`, kept between runs. Nothing else of the host is visible: a step's paths
  lie in the repository (relative) or under `/var/tmp`. Private folders linked to another checkout
  (symlink or junction) are bound at their place under `/work`, and so are links one level inside a
  private folder of the worktree's own (`cache/stock`). In a git worktree the common `.git` is
  bound at `/git` and `OVERPRESSURE_GIT_DIR` names the worktree's folder, which the scripts that ask git
  pass as `--git-dir`.
- **One build at a time.** `container run` names its container `overpressure-build`, labelled with its
  client process and command. A second run is refused with `another build is running: COMMAND; let it
  finish or stop it, then start again`. A leftover container whose client is gone is removed first. The
  doctor's probes run unnamed.
- **Stopping.** Stopping a build (the installer's stop, Advanced mode's *Stop*, Ctrl-C or SIGTERM in
  `container run`) removes its container (`ENGINE rm -f overpressure-build`), then ends the client.
- **Ownership.** With a rootful engine the entrypoint gives what root created under `/work` back to the
  host's user (`OVERPRESSURE_UID`, `OVERPRESSURE_GID`), whatever the exit status. A rootless engine, or one
  that maps ownership itself, needs nothing.
- **Parallel jobs.** The userspace build runs one job per CPU, but at most one per 1.5 GiB of the
  container's memory (`MAKEFLAGS`, `CMAKE_BUILD_PARALLEL_LEVEL`, `PICO4_BUILD_JOBS` for meson).
- **The aarch64 handler.** The chroot runs arm64 programs through binfmt_misc with the F flag. Before a
  build on an x86-64 engine without one, the container registers the image's `qemu-aarch64-static` as
  `overpressure-aarch64` (as Ubuntu does: `aarch64-binfmt-P`, flags `POF`); it serves later containers
  until the host restarts. An arm64 engine needs none.

## The window

```sh
python -m overpressure gui              # the window: which headset, then its installer
python -m overpressure gui --advanced   # the same, then Advanced mode
python -m overpressure --device phoenix gui  # straight to the PICO 4, past the headset page
python -m overpressure gui --demo       # fake engine, fake steps, fixed checks: nothing runs
python -m overpressure gui --demo-fresh # the demo without its earlier run
python -m overpressure gui --demo-headsets  # the demo with a fake second headset
```

The window ([overpressure/window/](../overpressure/window/__init__.py)) opens on *Which headset do you
have?*, then has two views for the chosen headset: the installer and Advanced mode (every step). The link
at the right of the header switches between them; whatever runs keeps running. Neither view does
anything a terminal cannot: the installer is the engine's command line, and each step shows its command
line. The look comes from design tokens in [theme.py](../overpressure/window/theme.py) (light and dark,
body text at a contrast of at least 4.5:1, the system's UI font) and components in
[kit.py](../overpressure/window/kit.py); the window opens at 1120×760 where the screen has room, at least
900×600. The logo ([docs/logo](logo/README.md)) is its icon, from PNG renderings at the display's scaling.

It needs Tk and [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) (`customtkinter==6.0.0`,
the `gui` extra in `pyproject.toml`); the start scripts bring both, `uv sync --extra gui` too. Without them
`gui` says how to get them and exits with 1; without a display, with 2.

- **The disclaimer.** On every start the window is covered by *Before you go on*: the warranty is void,
  nobody else is to blame, no warranty of any kind. *I accept* holds for every program the window starts;
  *Close* closes it. It shows even when the environment has accepted it ([below](#the-command-line)).
- **The headset page.** One card per headset of the catalogue. A headset in `devices/` shows its name,
  SoC and summary, its release and, for one with an installer, where its last run stopped (read with
  `install --json --plan`, which runs nothing). Every card but a development checkout's has a release
  choice (*Newest stable*, *Newest beta*, *Preview (main, untested)*, then each published release, read in
  the background) and *Download*, which runs [`headset add`](#headsets-in-devices) on a thread and reads
  `devices/` again when it ends. A headset not downloaded yet, or held in a release this overpressure
  cannot run (the card says why), only downloads. The headset chosen last is listed first, marked *Last
  used*. It is kept in `out/window.json`
  (`{"last_headset": CODENAME}`), not in `.env`. Only `--device` skips the page; `OVERPRESSURE_DEVICE`
  does not.
- **What to do.** A headset with more than the install ([workflows](#the-installer)) asks *What would you
  like to do?*: one card per workflow, with an *erases the headset* pill where it erases, where it stopped
  last time, or why it cannot run now (`Headset.workflow_problem`). Below them, *Your backup* says where
  the headset's backup is (`Headset.backup_set`) with *Export backup…* and *Import backup…*, which run
  `python -m overpressure backup export|import FILE` as a child.
- **The way back.** Every page after the headset page has a back link in the header (*‹ PICO 4* to the
  headset's page of what to do, *‹ Headsets* to the headset page). It stops nothing and loses nothing.
- **One thing at a time.** A step and the installer never run at once; starting one while the other runs,
  or going back to the headset page, is refused with a message naming what runs.
- **Closing** while something runs asks first: the installer stops as below, a step's program is ended.

### The installer view

The view runs `python -m overpressure install --json` as a child, renders its [events](#events) and writes
the answers to its stdin; it does nothing itself.

- **Layout.** Down the left, a ring with the share of the whole run done (each task weighed by its
  estimate, a running one by its bar), the stages done in words, and the stages with their state; on the
  right the current stage, its tasks with estimates and progress, and the engine's notes, in a column at most
  760 points wide, centred. The action bar under it, in the same column, holds *Show details* (the child
  programs' output, wrapped at the drawer's width) and one main button: *Start*, *Continue* after a stop,
  *Retry* after a failure, *Stop* while it runs.
- **Progress.** A running task has a bar and a status line under it. A task that reports its progress
  fills the bar ("45% · 2:10 · about 3 min left"); one that does not follows its estimate up to 90%, then
  the bar moves to and fro ("2:10 of about 20 min"); one without an estimate counts its time. The backup's
  export and import show the same in their card.
- **Before the first run** the window reads the plan (`install --json --plan`). After an earlier run the
  first page is *Welcome back*: *Continue where you left off*, *Start over from…* (a stage; it says what is
  forgotten and warns when a stage writes to the headset; runs `--restart STAGE`) and *Advanced mode*.
- **Asks** appear as a card over the dimmed view: a consent, an action with *Done*, or a form (secret
  fields masked, emptied when the card closes). Meanwhile Advanced mode's header link says *Installer
  (waiting for you)*.
- **The end.** After a workflow, the headset's page (`Headset.finished_page(workflow)`, for the install
  `Headset.after_install()`; by default *Installed*, "Your headset is ready.") with its actions: another
  workflow, or one of the headset's steps in Advanced mode. The PICO 4's after the install is *Unplug it
  and put it on*, with *Upgrade later*, *Restore to stock* and *Get help*. After a failure: the error, where
  the headset stands, what to do next, *Retry* and *Show details*.
- **Stopping.** *Stop* asks first. A task that does not write stops now and changes nothing; a task that
  writes to the headset finishes first. The view closes the engine's stdin and shows *Stopping…* until the
  `finished` event, then what the engine says about the headset's state.

### Advanced mode

- **Steps**, in three groups: *Setup* (*Check dependencies*, *Fetch the host tools*), *Image* (the builds)
  and *Headset* (flashing, connecting, checking, the way back). A step shows what it does, where it runs
  (a Linux-root step: the build container), whether it writes to the headset, its parameters (text,
  choice, path with *Browse*, flag) and the command line it will run, with *Copy*. Values are kept per
  headset and step while the window is open. A Linux-root step's path outside the repository and
  `/var/tmp` is refused before the run.
- **Run** starts the step; its output goes below it and the status line says how it ended (*done*,
  *failed* with its exit status, *stopped*). One step runs at a time. *Stop* ends it, asking first for a
  step that writes. A step that writes to the headset asks before it starts, with its command line.
- **Check dependencies** runs the [checks](#dependencies) when first shown and on *Check again*: a table
  with each line's status (*ok*, *missing*, *warning*, *optional*) and the selected line's fix to copy.
- A headset without an installer opens here.

With `--demo` the engine, the steps and the checks are fakes: no headset, nothing runs, nothing is
written. The fake engine starts as if an earlier run had done four stages; `--demo-fresh` starts with
nothing done. `--demo-headsets` adds *Example headset*, which has no installer.

### The start scripts

An owner starts the window from the repository's root folder, without installing anything:

| Script | Host | How |
| --- | --- | --- |
| `Install.cmd` | Windows (x64, arm64) | double-click; no administrator rights |
| `Install.command` | macOS (Intel, Apple silicon) | double-click (the first time: right-click, *Open*); it runs `install.sh` |
| `install.sh` | Linux (x86_64, aarch64) | `sh install.sh` |

Each fetches [uv](https://docs.astral.sh/uv/) 0.13.4 into `cache/uv/`, checked against its pinned SHA-256
(a mismatch deletes it and stops), and runs from the repository root

```sh
uv run --locked --no-dev --extra gui --managed-python --python 3.12 python -m overpressure gui
```

with `UV_CACHE_DIR=cache/uv-cache`, `UV_PYTHON_INSTALL_DIR=cache/python` and
`UV_PROJECT_ENVIRONMENT=cache/venv`, so uv's Python 3.12 (which has Tk everywhere), CustomTkinter and the
environment stay inside the folder. When the start fails, the terminal window stays open with the error.

**Linux.** uv's Python brings a Tk built without Xft: bitmap fonts, and CustomTkinter's rounded corners and
shapes, which it draws with a font, break. `install.sh` therefore takes the first system Python 3.9+ whose Tk
draws with Xft (Tk 9 names its font system; a Tk 8.6 with Xft has `libXft` loaded) and runs uv with
`--no-managed-python --python` it; without one it keeps uv's Python and says which package to install:
`python3-tk` (Debian, Ubuntu, openSUSE), `python3-tkinter` (Fedora) or `tk` (Arch). The window prints the same
note when its Tk draws without Xft.

### USB access on Linux

adb, fastboot and edl-ng open the headset's USB device, which Linux lets a user
open only through a udev rule. overpressure runs nothing as root: the doctor, and with it the installer's
first stage, reads the installed rules (`overpressure/usbaccess.py` follows their matches, jumps and
grants) for each of the headset's USB modes (`Headset.usb_modes()`). When one is missing it names the
distribution's Android udev rules, which grant the logged-in user their devices: `android-udev` (Arch),
`android-udev-rules` (Debian 13), `android-sdk-platform-tools-common` (Ubuntu), then `sudo udevadm trigger`.
Where the package is there but lacks a mode (Ubuntu 24.04's lacks PICO OS), or no package is known, it gives
the rule to add. Before adb, fastboot or EDL uses a connected headset, the installer checks the device itself
and stops with the same fix, before anything is sent to it.

## The installer

```sh
python -m overpressure install                  # in a terminal: the stages as text, asks through the keyboard
python -m overpressure install --json           # the window's engine: events on stdout, answers on stdin
python -m overpressure install --plan           # the plan and what is done already; runs nothing
python -m overpressure install --restart STAGE  # forget what was done from STAGE on, then run
python -m overpressure upgrade                  # install --workflow upgrade: a new system, the data kept
python -m overpressure restore                  # install --workflow restore: back to the headset's own system
python -m overpressure backup export FILE       # the headset's backup as one checked file
python -m overpressure backup import FILE       # such a file onto this computer, checked
python -m overpressure backup compact           # the backup's zero runs given back to the disk, content unchanged
```

`upgrade` and `restore` take `--json`, `--plan` and `--restart` too. A headset's plan comes from
`Headset.install_plan()`: stages, each a list of tasks built on its commands and steps
([overpressure/install.py](../overpressure/install.py) is the engine, `devices/<codename>/tooling/install.py`
the plan). A headset without one has no installer (exit status 2). The engine is standard library only.

- **Workflows.** A headset may give more than the install (`Headset.workflows()`, each a `Workflow`: key,
  title, summary, whether it erases the headset) with their plans (`Headset.plan(key)`); `install
  --workflow KEY` runs one. Before a run, `Headset.workflow_problem(key)` may refuse it (exit status 2).
- **Order and resume.** Tasks run in order; the first failure ends the run. The state is
  `out/install/<codename>/state.json` for the install, `out/install/<codename>/<workflow>/state.json` for
  another workflow (`{"schema": 1, "tasks": {ID: {"status": ..., "finished": UTC}}}`, and `"choices"`: what
  a task recorded with `ctx.remember`, never a secret, such as a skipped ask), the tasks' logs beside it. A task done earlier is skipped unless its done check says its result is gone; a done check
  also skips a task whose output is already there (`skipped`). `--restart STAGE` forgets that stage and
  every later one. A finished workflow forgets the other workflows' state.
- **Child programs** run from the repository root (Linux-root steps in the build container); their output,
  and what a task prints, goes to log events and `out/install/<codename>/<task>.log`, so stdout carries only
  events.
- **One at a time.** An OS lock on `out/install/lock` keeps a second engine out, whatever its headset.
- **Stopping.** Ctrl-C, a closed stdin or `{"stop": true}` on stdin stop the run wherever it waits: an
  ask, a wait through `ctx.clock` (checked every 0.2 s), a program that does not write (it is ended). A
  program that writes to the headset runs in its own process group and is never cut off: the stop takes
  effect when it has ended. The stopped task is pending again, and `finished`'s `where` says the headset's
  state.
- **Secrets.** A form's secret values stay in the engine's memory: never in a log, the state, `.env`, a
  command line (refused), an event or stderr; a program gets them on its stdin only. Text that would carry
  one shows `***`.
- **Settings.** What the installer finds out (a headset's serials) it writes to `.env`
  (`localconfig.update`: the key's line replaced, everything else kept; a missing `.env` starts as a copy
  of `.env.example`).
- **Power.** Before each task that writes to the headset the engine checks the computer's battery again
  and fails the task before its write when it has dropped below the limit ([the command
  line](#the-command-line)).
- **Exit status.** 0 finished, 1 failed, 2 a usage error, no installer or a refused workflow, 3 stopped.
- **The backup as one file.** `backup export FILE` writes the headset's backup (`Headset.backup_set()`: the
  verified backup folder, the identity, other files and the `.env` settings the way back needs on another
  computer) as one uncompressed tar ([overpressure/backup.py](../overpressure/backup.py)), whose partition
  images are sparse members (GNU's PAX format 1.0: their zero runs are not stored; tarfile and GNU tar read
  them back whole):
  `overpressure-backup.json` first (schema, headset, folder name, identity, file names, settings, export
  time), then the backup under `backup/` and the other files under `files/`. The backup is verified before,
  the file read back after; it appears only when both match, never over an existing file. `backup import
  FILE` refuses another kind of headset, another headset than this computer's identity, a damaged file and
  any file that would replace a different one; a backup already there with the same manifest is kept,
  another gets a new folder beside it. The imported backup is verified, then `Headset.adopt_backup` names
  it in `.env`, its zero runs handed back to the disk as a new backup's are
  ([overpressure/holes.py](../overpressure/holes.py)). An export holds what reaches the headset's maintenance
  system: keep it private.

### Events

With `--json` every line on stdout is one JSON object; whatever a child program prints arrives in a `log`
event. Texts are plain words for the owner; stack traces go to the task's log file.

| Event | Fields | When |
| --- | --- | --- |
| `plan` | `headset` {`codename`, `name`}, `workflow`, `stages` [{`id`, `title`, `summary`, `tasks` [{`id`, `title`, `estimate`, `seconds` (the estimate's upper end, or null), `writes`}]}] | first, once |
| `task` | `stage`, `task`, `status`: `pending`, `running`, `done`, `skipped` (its output was there) or `failed` | each change; a task done in an earlier run shows `done` when the run reaches it |
| `progress` | `task`, `fraction` (0 to 1, or null when unknown), `text` | while a task runs: `ctx.progress`, or a progress line of its program |
| `log` | `task`, `line` | a line of a child program, or of the task itself: the details view |
| `note` | `task`, `level` (`info` or `warning`), `text` | something the owner should know |
| `ask` | `id`, `kind`, `title`, `text`, `fields` [{`name`, `label`, `secret`, `optional`}], `accept`, `decline` | the engine waits for the answer |
| `finished` | `status` (`done`, `failed`, `stopped`), `task` (null when done), `error`, `where` | last, once |

**Progress lines.** A program a task runs reports how far it is with lines of its own on stdout
(`overpressure.progress`): `progress 42%: writing super`, `progress 3/8: package pico4-monado` (three of
eight done) or `progress: reading aop` (how far is unknown). The engine turns them into `progress` events
and writes them to the task's log file, not to the details. `progress.Meter` prints them for a byte count,
`progress.feed` counts what a child reads from a pipe, `progress.estimate_seconds` reads an estimate
("about 20 min", "1 to 3 h": its upper end).

`ask` kinds: `confirm` (accept or decline), `action` (the owner does something, such as plugging in;
accept is "Done") and `form` (fields; secret ones masked). The answer is one line on stdin:
`{"answer": ID, "accepted": true, "values": {NAME: VALUE}}`. A declined ask or a closed stdin stops the
run; an empty required field is asked again with a note. After a failure or a stop, `where` says the
headset's state and what to do next. `--plan --json` prints the `plan` event and a `task` event with each
task's recorded status, and runs nothing. In a terminal the plan prints as stage headings and task lines,
with asks through `input()` and `getpass()`.

## The command line

```text
python -m overpressure [--device CODENAME] [--accept-disclaimer] COMMAND [ARGS...]

  devices              the headsets in devices/, and those this version cannot run
  headset list | releases CODENAME | add CODENAME [--channel stable|beta|preview | --version V]
                       the headsets there are, their releases, one downloaded into devices/
  gui [--advanced] [--demo | --demo-fresh] [--demo-headsets]
                       the window: the installer, and every step in its Advanced mode
  steps                the headset's steps and their command lines
  run STEP [NAME=VALUE...]
                       a step with parameters, as the window runs it
  ram-boot [IMAGE]     boot an image from RAM with fastboot boot, where the bootloader allows it
  install [--workflow KEY] [--json] [--plan] [--restart STAGE]
                       the installer: the headset's stages in order, resumable
  upgrade | restore    the installer's other workflows, where the headset has them
  backup export FILE | backup import FILE
                       the headset's backup as one checked file, and back
  doctor               what this computer has and lacks for the headset, with fixes
  tools [NAME...]      fetch the pinned host tools (platform-tools, edl-ng)
  container pull|run -- CMD|shell|binfmt
                       the build container
  COMMAND [ARGS...]    a command of the headset
```

The headset is `--device`, else `OVERPRESSURE_DEVICE` (environment or `.env`), else the only one in
`devices/`; `gui` asks on its headset page unless `--device` names one. Without arguments it lists the
headset's commands; `COMMAND --help` shows a command's options. With the PICO 4:

```sh
python -m overpressure run stage1 firmware=5.13.7-global-seko   # a step, as the window runs it
python -m overpressure build --firmware 5.13.7-global-seko      # the same through its command
python -m overpressure dev check
```

**Before anything touches the headset.** A command that touches the headset shows the disclaimer and asks
`y` or `n` ([overpressure/disclaimer.py](../overpressure/disclaimer.py)): `install`, `upgrade`, `restore`,
`ram-boot`, `run` of a step that touches it, and the headset's own commands marked so
(`Command(headset=...)`; the PICO 4's `dev` except its two builds, `device` probe, check-unlocked, backup,
stage, unlock and restore-abl, `hwtest`, `cycle`). The builds, `--plan`, `--help`, `steps` and `backup` do
not ask. `n` does nothing (exit status 1), no answer at the end of the input neither (2).
`--accept-disclaimer`, anywhere on the command line, or `OVERPRESSURE_ACCEPT_DISCLAIMER=1` in the
environment accepts it without asking, for unattended runs; the acceptance holds for the programs the
command starts.

The same commands refuse to start (exit status 1) on a computer that runs on its battery below 30 %
([overpressure/power.py](../overpressure/power.py): Windows' `GetSystemPowerStatus`, Linux's
`/sys/class/power_supply`, macOS's `pmset -g batt`). On mains, without a battery, or when the state cannot
be read, they go on.

## How it fits together

| Module | Role |
| --- | --- |
| [overpressure/catalogue.py](../overpressure/catalogue.py) | the headsets there are and their repositories; their releases on GitHub, a channel's or a version's, downloaded into `devices/<codename>/` against the checksum and `headset.json` (`headset list\|releases\|add`) |
| [overpressure/headset.py](../overpressure/headset.py) | `API`, the headset interface; `headset.json` read and checked; `Headset` (`Info`, `commands()`, `steps()`, `dependencies()`, `usb_modes()`, `ram_boot()`, `install_plan()`, `workflows()`, `plan()`, `workflow_problem()`, `after_install()`, `finished_page()`, `backup_set()`, `adopt_backup()`, `touches()`), `Command`, `Step`, `Param`, `Workflow`, `BackupSet`, `AfterInstall`, `Action`, the registry |
| [overpressure/install.py](../overpressure/install.py) | the install engine: `Stage`, `Task`, `Ask`, the `Context` a task gets, the state, the lock, the events, `install`, `upgrade` and `restore` |
| [overpressure/disclaimer.py](../overpressure/disclaimer.py), [power.py](../overpressure/power.py) | the disclaimer and the gate before a command that touches the headset (`--accept-disclaimer`, `OVERPRESSURE_ACCEPT_DISCLAIMER`); this computer's power: a battery below 30 % refuses those commands (Windows, Linux and macOS readers) |
| [overpressure/runner.py](../overpressure/runner.py) | the process a step is on this host (a Linux-root step's in the build container), its display, running and stopping it |
| [overpressure/__main__.py](../overpressure/__main__.py) | the command line |
| [overpressure/window/](../overpressure/window/__init__.py) | the window (CustomTkinter). Models without widgets: `installer.py` (the engine's events as stages and asks; the child and demo engines), `advanced.py` (the steps, their values and command lines, the checks), `headsets.py` (the headset page, its plan probes, `out/window.json`, the demo's second headset). Widgets: `app.py` (the header, the installer view, the panels), `advanced_view.py`; `theme.py` the design tokens and fonts, `kit.py` the components, `logo.py` the logo's renderings (`icons/`) |
| [overpressure/localconfig.py](../overpressure/localconfig.py) | `.env`: each headset's settings with its own key prefix; `DeviceSettings`, what every Qualcomm Android headset names |
| [overpressure/fastboot.py](../overpressure/fastboot.py) | flashing an image sparse above a size, raw below; the RAM boot: `fastboot boot` with the headset's checks, never a write |
| [overpressure/doctor.py](../overpressure/doctor.py) | the dependency checks: the host's, the build container's, then the headset's (`Headset.dependencies()`) from the shared ones (settings, EDL backup and tools, adb and fastboot, Windows' USB link, Linux's USB access) |
| [overpressure/usbaccess.py](../overpressure/usbaccess.py) | Linux's USB access: what the installed udev rules grant for each USB mode, the fix per distribution, a connected device's access |
| [overpressure/tools.py](../overpressure/tools.py) | the pinned host tools in `cache/tools` |
| [overpressure/linux.py](../overpressure/linux.py) | the build container: its pinned image ([overpressure/builder](https://github.com/overpressure/builder)), one named build at a time, stopping it, the aarch64 handler, ownership |
| [overpressure/stage1.py](../overpressure/stage1.py) | a stage 1: an Alpine ramdisk in the stock Android boot image, from a headset's `Spec` |
| [overpressure/ota.py](../overpressure/ota.py) | stock boot images and partitions from a vendor OTA (block OTAs and `payload.bin`), checked against the zip, brotli by the package or the command; a pinned file out of a remote zip, checked by its hash |
| [overpressure/android.py](../overpressure/android.py), [qualcomm.py](../overpressure/qualcomm.py) | adb and fastboot, fail-closed, the wait for Android's boot, a partition's size from `getvar all`; the SoC ID, EDL devices counted through pyusb (PowerShell, ioreg or sysfs without it), EDL (edl-ng, bkerler/edl), partition maps, a full backup and a write verified by readback |
| [overpressure/backup.py](../overpressure/backup.py) | verified backups and a partition's image in them; the backup archive (`backup export`, `import` and `compact`) |
| [overpressure/holes.py](../overpressure/holes.py) | a file's zero runs handed back to the file system (sparse files on NTFS, ext4, APFS), its content unchanged |
| [overpressure/process.py](../overpressure/process.py), [ssh.py](../overpressure/ssh.py) | programs and bounded waits; SSH with the project's key, the keys an image enrolls |
| [overpressure/evidence.py](../overpressure/evidence.py), [storage.py](../overpressure/storage.py) | a run that records every step and command in its results folder, bounded waits and HTTP fetches; the read-only storage policy a headset's Linux stages enforce |
| `bootimg`, `avb`, `sparse`, `payload`, `cpio`, `compress`, `alpine`, `apksign`, `kernelinfo`, `elfdeps`, `windows_ncm` | the formats and helpers Android-based headsets share (`bootimg` also edits a boot image's ramdisk and command line) |
| [overpressure/assets.py](../overpressure/assets.py) | a headset's assets out of its stock folder: the folder checked against its manifest, files out of its ext4 and FAT images, the assets manifest |
| [overpressure/ext4.py](../overpressure/ext4.py), [fat.py](../overpressure/fat.py) | read-only readers of stock file system images: ext4 (32- and 64-bit, extents, block maps, holes, links) and FAT12/16/32 with long names; `python3 -m overpressure.ext4 cat IMAGE PATH` for the Linux build scripts |
| [scripts/stock-lib.sh](../scripts/stock-lib.sh) | for a headset's Linux build scripts: a file out of a stock ext4 image |

## RAM boot

`python -m overpressure ram-boot [IMAGE]` boots an image from RAM with `fastboot boot`: nothing is written,
and the next normal boot starts whatever the partitions hold, so it is the first contact with a new
headset. The image must be an Android boot image; the headset is brought into fastboot if needed and must
be the only device there; its bootloader must report unlocked; then `fastboot boot` and a bounded wait
until the image answers. The headset says how each of these looks for it
([adding-a-headset.md](adding-a-headset.md#ram-boot)), and the window shows the step where it has one.

A bootloader without `fastboot boot` ("unknown command" after the transfer) ends it as *refused* (exit
status 3), apart from a failure (1): nothing was written and the headset stays in fastboot. It never falls
back to flashing.

## How a step runs

A step is a command line: a command of the headset (`python -m overpressure --device CODENAME ...`) or a
script in the headset's folder. A step that works in the window works in a terminal, and the other way
round.

## Development

The tool runs without any package: each pinned runtime package is imported where it is used, with a
fallback. Working on it uses [uv](https://docs.astral.sh/uv/), [ruff](https://docs.astral.sh/ruff/) and
[ty](https://docs.astral.sh/ty/), pinned in `pyproject.toml`'s `dev` group (exact versions in `uv.lock`).

```sh
uv sync                        # .venv with the tool (editable) and its packages, ruff and ty
uv run python -m unittest      # the host tests
make lint                      # as CI: ruff format --check, ruff check, ty check (Python 3.9, every platform)
make format                    # ruff format, ruff check --fix
```

ruff runs every rule (`select = ["ALL"]`, line length 120). `pyproject.toml` turns off only what conflicts:
two incompatible docstring rules, the formatter's trailing commas, pytest's assertion style (the tests are
unittest) and one `with` for several contexts (Python 3.9). Tests need no docstrings or return
annotations, and `print` is allowed outside the tests. Any other exception is a `# noqa: RULE` with its
reason. The headsets' own programs are packages in overpressure/packages, whose builds run their tests.

A headset's folder is outside this repository's Git, and `ruff`/`ty` skip it when run on `.`; name it
(`uv run ruff check devices/phoenix`, `uv run ty check devices/phoenix`) and run its tests with `python -m
unittest discover -t . -s devices/phoenix/tests`. `python -m unittest` from the root runs them too.
