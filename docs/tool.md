# The tool

`python -m overpressure` builds a headset's images and works on the headset. It is one program
with two faces: a window (the installer, and every step in its Advanced mode) and a command line
with the same steps and every command of the headset. It is Python (3.9+): the standard library,
and pinned packages where they spare the owner an outside tool (brotli for the stock OTA, pyusb with
libusb-package for EDL devices and Windows' USB network link; the window adds Tk and CustomTkinter), so it runs on Windows, Linux
and macOS, and it runs
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
queries, a probe in the build container, settings, file sizes and small files' hashes; they
install, build, register and change nothing. Missing but optional (Tk and CustomTkinter for the
window, pyusb and libusb-package, and brotli while the brotli command is there) and warnings (a slower fallback, say) do not fail it.

For the builds ([Linux-root steps](#linux-root-steps)) the checks are the build container's, each
with what it is needed for and its fix in one line:

| Check | Fix when it is not there |
| --- | --- |
| Docker or Podman answers (`ENGINE info`) | install Docker or Podman and have it running; how it runs its containers (a VM, WSL, a service, rootless) is the owner's choice, and the check and its fix are the same on every host |
| the image built | `python -m overpressure container build` (the installer builds it itself) |
| the engine's memory, from its `info` (8 GiB or more) | give the engine more (Docker Desktop: Settings, Resources, Memory limit; Podman: `podman machine set --memory`); below it a *warning*: the userspace build runs one job per 1.5 GiB and takes much longer |
| the aarch64 handler (an x86-64 engine) | none needed: each build registers it; after a failure, `python -m overpressure container binfmt` (*optional* until then) |

The image holds every Linux tool of the builds by construction, so no tool is checked one by one.

## Host tools

```sh
python -m overpressure tools
```

fetches the pinned programs the headsets' workflows use into `cache/tools`, each archive checked
by its SHA-256 ([overpressure/tools.py](../overpressure/tools.py)): Android's platform-tools
(adb, fastboot) and edl-ng. The Windows USB network link needs no tool of its own: the Python
that runs overpressure has pyusb and libusb-package (pyproject.toml). A headset's settings that
name its own copies take precedence; without them the fetched ones are used, then PATH. The
window has the same as *Fetch the host tools* in Advanced mode's Setup group. Nothing is
downloaded anywhere else: the tools are fetched only by this command.

## Linux-root steps

Building a userspace mounts file systems and chroots into an aarch64 root, so those steps
(`Step.linux_root`: the userspace archive, the system image, Valve's kernel) run on Linux as root,
in one place on every host: overpressure's build container
([overpressure/linux.py](../overpressure/linux.py)), with Docker or Podman, whichever is
there; with both, the first that answers (`OVERPRESSURE_CONTAINER_ENGINE` in the environment
chooses). The builds ask nothing else of the engine: how it runs its containers (a VM, WSL, a
service, rootless) is the owner's choice. A step's command line is
`python -m overpressure container run -- COMMAND`, from the repository root.

```sh
python -m overpressure container build          # the image, overpressure-builder:HASH
python -m overpressure container run -- CMD      # CMD as root in the container (the image built first if missing)
python -m overpressure container shell           # an interactive shell there
python -m overpressure container binfmt          # the aarch64 handler: registered when the engine has none
```

- **The image.** [overpressure/container/Dockerfile](../overpressure/container/Dockerfile): Ubuntu
  24.04 pinned by digest, its packages from `snapshot.ubuntu.com` at one fixed moment (its
  `SNAPSHOT`), so every owner gets the same versions: every tool of the userspace, system image
  and kernel builds, LLVM 20's clang and lld, the kernel's cross compiler and qemu-user-static.
  Its tag is the hash of the recipe's folder, so a changed recipe is a new image.
- **What it sees.** It runs privileged (the builds mount and chroot). The repository is mounted at
  `/work`, and `/var/tmp` (the build roots) is the named volume `overpressure-var`, on a Linux file
  system and kept between runs. Nothing else of the host is visible: a step's path parameters lie
  in the repository (relative, as the steps' defaults are) or under `/var/tmp`.
- **One build at a time.** `container run` names its container `overpressure-build`, labelled with
  its client (host, process, parent process), its command and its checkout. A second run while it
  runs is refused with one line, `another build is running: COMMAND; let it finish or stop it,
  then start again`; the engine's own refusal of the name becomes the same plain line. A leftover
  container whose client is gone (its process was killed) is removed before the next run.
  Probes (the doctor's) run unnamed.
- **Stopping.** The engine keeps a container running when its client ends, so stopping a build
  removes its container first (`ENGINE rm -f overpressure-build`, only the container of the client
  being stopped or of its child), then ends the client: the installer's stop, Advanced mode's
  *Stop*, and Ctrl-C or SIGTERM in `container run` itself. On Windows a stopped client is killed
  outright (TerminateProcess), which nothing in it can catch; the removal does not depend on it.
- **Ownership.** With a rootful engine, what a run writes into `/work` (`out/`,
  `cache/`) would be root's in the owner's checkout. `container run` passes the host's user and
  group (`OVERPRESSURE_UID`, `OVERPRESSURE_GID`, wherever the host has them and is not root), and the
  entrypoint gives back what root created or changed
  under `/work` during the command (`find -uid 0 -cnewer`, links themselves), whatever its exit
  status, only when the container's root is the host's root (a rootful engine). A rootless
  engine's root is the host's user already, and an engine that maps ownership itself is left alone
  by it: nothing is given back.
- **Linked folders.** A git worktree may link its private folders (`assets/`, `backup/`,
  `cache/`, `out/`) to another checkout's, as a symlink or a Windows junction. The bind mount of
  the repository cannot follow such a link, so `container run` binds each linked folder at its
  place under `/work`.
- **A worktree's git.** A worktree's `.git` is a file naming its folder in the main checkout's
  `.git` by a host path, which leads nowhere in the container. `container run` binds the common
  `.git` at `/git` and names the worktree's folder in `OVERPRESSURE_GIT_DIR`; the scripts that ask
  git (the image's commit in `build-userspace.sh`, `run-shellcheck.py`) pass it as `--git-dir`.
  `GIT_DIR` itself would also reach the git of the builds' package recipes.
- **Parallel jobs.** The userspace build compiles one job per CPU, but at most one per 1.5 GiB of
  the container's memory (`MAKEFLAGS`, `CMAKE_BUILD_PARALLEL_LEVEL`, `PICO4_BUILD_JOBS` for meson):
  more jobs than memory swap it to a crawl.
- **The aarch64 handler.** The userspace build's chroot runs arm64 programs through the host
  kernel's binfmt_misc handler for aarch64 executables with the F flag (magic
  `7f454c460201010000000000000000000200b700`). Some engines register one, others do not; an arm64
  engine needs none. Before a build on an x86-64 engine without an
  enabled handler with F, the container registers the image's `qemu-aarch64-static` as
  `overpressure-aarch64` as Ubuntu registers it (`aarch64-binfmt-P`, flags `POF`) and says so in one line; with F the kernel keeps the
  interpreter open, so the handler serves later containers until the host restarts.
  `container binfmt` does only that and reports the handler it found or registered.
- **Shell tests.** `python -m overpressure container run -- make test` runs the host tests and every
  headset's shell tests in the container (as root, on Linux), the container's own scripts' tests
  included.

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

- **The disclaimer.** On every start the window is covered by *Before you go on*: the warranty is void,
  nobody else is to blame, and overpressure comes with no warranty of any kind. *I accept* lets the window be
  used and holds for every program it starts (the installer, a step, a backup job); *Close* closes it. It
  shows on every start, also when the environment has accepted it ([below](#the-command-line)).
- **The headset page.** The window always opens on *Which headset do you have?*, also with one headset in
  `devices/`: one card per headset with its name, SoC and summary, what choosing it opens (the installer
  and Advanced mode, or Advanced mode only for a headset without an installer) and, for a headset with an
  installer whose plan shows an earlier run, where it stopped (*Last time: stopped in Connect your
  headset*; the page reads each plan with `install --json --plan`, which runs nothing). The headset chosen
  last is listed first, marked *Last used* in a pill, with the page's one main button; one click on an entry or its
  button opens that headset's page of what to do, where its installer does more than the install, else its
  installer at its start page (or Advanced mode for a headset without one).
  Only `python -m overpressure --device CODENAME gui` skips the page; `OVERPRESSURE_DEVICE` does not.
- **What to do.** A headset whose installer has more than the install ([workflows](#the-installer))
  asks *What would you like to do?* right after the choice: one card per workflow in its order, with its
  title, an amber *erases the headset* pill where it erases, what it does and keeps, and where it stopped
  last time (each workflow's plan read with `install --workflow KEY --json --plan`), or, in amber with its
  button off, why it cannot run now (`Headset.workflow_problem`: the PICO 4's upgrade needs a system this
  computer installed, its restore a backup on this computer). Choosing one opens its stages, the stepper
  captioned with its title. Below the cards, *Your backup* says where the headset's backup is on this
  computer (`Headset.backup_set`) with *Export backup…* (off without one) and *Import backup…*: a file
  dialog, then `python -m overpressure backup export|import FILE` as a child, its progress line in a panel
  until it has ended, then its result, which reminds to keep an export private. Nothing else starts while it
  writes.
- **The last choice** is a preference of the window, not a setting: `out/window.json`
  (`{"last_headset": CODENAME}`), written when a headset is chosen. A missing or broken file, or a codename
  no longer in `devices/`, marks nothing. The window writes nothing to `.env`; `OVERPRESSURE_DEVICE` there
  stays the command line's.
- **The way back.** Every page after the headset page (the start page, the installer's stages, its done and
  failed pages, Advanced mode) has a back link in the header, also with one headset: *‹ PICO 4* (the
  headset's name) to its page of what to do where it has one, *‹ Headsets* from that page and for any other
  headset. It keeps nothing running and loses nothing: each workflow's recorded progress is its engine's
  state, its details stay for the window's session, and choosing it again shows its start page.
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
- **The end**, under a large check (or, after a failure, a red mark). After a workflow: the headset's own
  title and text for it (`Headset.finished_page(workflow)`, the install's `Headset.after_install()`; without
  them *Installed*, "Your headset is ready.") and its actions in the action bar: one opens another workflow
  at its start page, another Advanced mode at one of the headset's steps (an action the headset lacks is left
  out); *Done* closes the window. The PICO 4's after the install: *Unplug it and put it on*, signing in to
  Steam, with *Upgrade later*, *Restore to stock* and *Get help* (its `support-bundle` step); after the
  upgrade *Upgraded*; after the restore *Back on PICO OS*, with *Install again*. After a failure: the error,
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
  Linux-root step: in the build container) and whether it writes to the headset, its parameters
  (a text, a choice, a path with *Browse* where this computer's dialog can pick it, a flag; in a card) and
  the command line it will run, in a code block, which follows the parameters as they change; *Copy* puts
  it on the clipboard. A page too long for the window scrolls. Values
  are kept per headset and step while the window is open. A Linux-root step's paths must be what the
  build container sees: one in the repository is passed relative to it, one under `/var/tmp` as it is,
  and any other is refused with a plain message before the run; *Browse* is there for its paths in the
  repository.
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
python -m overpressure upgrade                  # install --workflow upgrade: a new system, the data kept
python -m overpressure restore                  # install --workflow restore: back to the headset's own system
python -m overpressure backup export FILE       # the headset's backup as one checked file
python -m overpressure backup import FILE       # such a file onto this computer, checked
```

A headset with an installer gives its plan through `Headset.install_plan()`: stages, each a list of
tasks, built on its existing commands and steps ([overpressure/install.py](../overpressure/install.py)
is the engine, `devices/<codename>/tooling/install.py` the plan). A headset without one returns no
stages, and `install` says so (exit status 2). The engine is standard library only, so a terminal does
everything the window does.

- **Workflows.** Besides the install, a headset may give other workflows (`Headset.workflows()`, each a
  `Workflow` with a key, a title, what it does and keeps, and whether it erases the headset) and their
  plans (`Headset.plan(key)`); `install --workflow KEY` runs one, `upgrade` and `restore` are short for the
  two the PICO 4 has. Before a run (not for `--plan`), `Headset.workflow_problem(key)` may refuse it with a
  reason (exit status 2). A headset without others has the install alone.
- **Order and resume.** Tasks run in order; the first failure ends the run. Each headset has its own
  state, `out/install/<codename>/state.json` for the install and `out/install/<codename>/<workflow>/state.json`
  for another workflow (`{"schema": 1, "tasks": {ID: {"status": ..., "finished": UTC}}}`), written whole
  after every change, the tasks' logs beside it. A task done in an earlier run is skipped unless its own done check says its
  result is gone; a done check also skips a task on its first run when its output is there already
  (skipped). `--restart STAGE` forgets that stage's tasks and every later stage's. A workflow that finishes
  forgets the other workflows' state: the headset is where it left it, so each of them starts afresh.
- **Child programs.** A task's programs run from the repository root (Linux-root steps in the
  build container of [Linux-root steps](#linux-root-steps), as the window's steps do); their output goes line by
  line to log events and to `out/install/<codename>/<task>.log`. What a task prints itself becomes log lines
  too, so stdout carries only events.
- **One at a time.** An OS lock on `out/install/lock` keeps a second engine out, whatever its headset (builds
  share their caches); it ends with the process that holds it.
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
- **Exit status.** 0 finished, 1 failed, 2 a usage error, no installer or a refused workflow, 3 stopped.
- **The backup as one file.** `backup export FILE` writes the headset's backup (`Headset.backup_set()`: the
  verified backup folder, the identity, the other files and the `.env` settings the way back needs on
  another computer) into one uncompressed tar ([overpressure/backup.py](../overpressure/backup.py)):
  `overpressure-backup.json` first (the headset, the folder's name, the identity, the files' names, the
  settings), then the backup under `backup/`, the other files under `files/`. The backup is verified
  against its manifest before, and the file read back against it after; the file appears only when both
  match, never over an existing one. `backup import FILE` refuses another kind of headset, a headset other
  than the identity this computer has, a damaged file, and any file that would replace a different one; a
  backup already there with the same manifest is kept, another gets a new folder beside it. The imported
  backup is verified, then `Headset.adopt_backup` names it in `.env` (the PICO 4: its backup and identity,
  and the serials `.env` has none of). An export holds what reaches the headset's maintenance system:
  keep it private.

### Events

With `--json` every line on stdout is one JSON object; anything a child program prints arrives wrapped
in a log event. Texts are for the owner: plain words, no stack traces (those go to the task's log file).

| Event | Fields | When |
| --- | --- | --- |
| `plan` | `headset` {`codename`, `name`}, `workflow`, `stages` [{`id`, `title`, `summary`, `tasks` [{`id`, `title`, `estimate`, `writes`}]}] | first, once |
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
python -m overpressure [--device CODENAME] [--accept-disclaimer] COMMAND [ARGS...]

  devices              the headsets in devices/
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
  container build|run -- CMD|shell
                       the build container
  COMMAND [ARGS...]    a command of the headset
```

**Before anything touches the headset.** A command that touches the headset shows the disclaimer first
and asks `y` or `n` ([overpressure/disclaimer.py](../overpressure/disclaimer.py)): the installer and its
`upgrade` and `restore`, `ram-boot`, `run` of a step that does, and the headset's own commands that do
(`Command(headset=...)`: the PICO 4's `dev` but its two builds, `device` probe, backup, stage, unlock and
restore-abl, `hwtest`, `cycle`). The builds, `--plan`, `--help`, `steps` and `backup` do not. `n` does
nothing (exit status 1), no answer (the end of the input) neither (2). `--accept-disclaimer`, anywhere on
the command line, accepts it without asking for unattended runs, as `OVERPRESSURE_ACCEPT_DISCLAIMER=1` in
the environment does; an accepted disclaimer holds for the programs a command starts (the installer's
tasks, a step's command). The same commands refuse to start on a computer that runs on its battery below
30 % ([overpressure/power.py](../overpressure/power.py): Windows' `GetSystemPowerStatus`, Linux's
`/sys/class/power_supply`, macOS's `pmset -g batt`); on mains, without a battery, or when its state cannot
be read, they go on. The installer asks again before each task that writes to the headset, and fails it
before its write when the computer has gone on its battery since.

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
| [overpressure/runner.py](../overpressure/runner.py) | the process a step is on this host (a Linux-root step's in the build container), its display, running and stopping it |
| [overpressure/__main__.py](../overpressure/__main__.py) | the command line |
| [overpressure/window/](../overpressure/window/__init__.py) | the window (CustomTkinter). Its models, without widgets: `installer.py` (the engine's events as stages and asks; the child engine, the demo engine), `advanced.py` (the steps, their values and command lines, one run at a time, the checks) and `headsets.py` (which headset: the headset page, its plan probes, the last choice in `out/window.json`, the demo's second headset). Its widgets: `app.py` (the header, the installer view, the panels) and `advanced_view.py`; `theme.py` holds the design tokens and the fonts' choice, `kit.py` the components built from them |
| [overpressure/localconfig.py](../overpressure/localconfig.py) | `.env`: each headset's settings with its own key prefix; `DeviceSettings`, what every Qualcomm Android headset names |
| [overpressure/fastboot.py](../overpressure/fastboot.py) | flashing an image sparse above a size, raw below; the RAM boot: `fastboot boot` with the headset's checks, never a write |
| [overpressure/doctor.py](../overpressure/doctor.py) | the dependency checks: the host's, then the headset's (`Headset.dependencies()`), built from the shared ones (settings, EDL backup and tools, adb and fastboot, Windows' USB link) and the build container's |
| [overpressure/tools.py](../overpressure/tools.py) | the pinned host tools in `cache/tools` |
| [overpressure/linux.py](../overpressure/linux.py), [container/](../overpressure/container) | the build container, where Linux-root steps run: its image, one named build at a time, stopping it, the aarch64 handler, ownership on Linux hosts |
| [overpressure/stage1.py](../overpressure/stage1.py) | a stage 1: an Alpine ramdisk in the stock Android boot image, from a headset's `Spec` |
| [overpressure/ota.py](../overpressure/ota.py) | stock boot images and partitions from a vendor OTA (block OTAs and `payload.bin`), checked against the zip, the block images decompressed by the brotli package (the brotli command without it); a pinned file out of a remote zip, checked by its hash |
| [overpressure/android.py](../overpressure/android.py), [qualcomm.py](../overpressure/qualcomm.py) | adb and fastboot, fail-closed, the wait for Android's boot, a partition's size from `getvar all`; the SoC ID, EDL devices counted through pyusb (PowerShell, ioreg or sysfs without it), EDL (edl-ng, bkerler/edl), its partition maps, a full backup and a write verified by readback |
| [overpressure/backup.py](../overpressure/backup.py), [process.py](../overpressure/process.py), [ssh.py](../overpressure/ssh.py) | verified backups and a partition's image in them; programs and bounded waits; SSH with the project's key, the keys an image enrolls |
| [overpressure/evidence.py](../overpressure/evidence.py), [storage.py](../overpressure/storage.py) | a run that records every step and command in its results folder, bounded waits and HTTP fetches; the read-only storage policy a headset's Linux stages enforce |
| `bootimg`, `avb`, `sparse`, `payload`, `cpio`, `compress`, `alpine`, `apksign`, `kernelinfo`, `elfdeps`, `windows_ncm` | the formats and helpers Android-based headsets share (`bootimg` also edits a boot image's ramdisk and command line) |
| [overpressure/assets.py](../overpressure/assets.py) | a headset's assets out of its stock folder: the folder checked against its manifest, files out of its ext4 and FAT images (debugfs's and mcopy's semantics), the assets manifest |
| [overpressure/ext4.py](../overpressure/ext4.py), [fat.py](../overpressure/fat.py) | read-only readers of stock file system images, written from the on-disk formats: ext4 (32- and 64-bit, extents, block maps, holes, links) and FAT12/16/32 with long names; `python3 -m overpressure.ext4 cat IMAGE PATH` for the Linux build scripts |
| [scripts/stock-lib.sh](../scripts/stock-lib.sh) | for a headset's Linux build scripts: a file out of a stock ext4 image (`python3 -m overpressure.ext4 cat`) |

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

The tool runs without any package: each pinned runtime package (`brotli`, `pyusb`, `libusb-package`, the
project's dependencies in `pyproject.toml`) is imported where it is used, with a fallback. The start scripts
and `uv sync` install them. Working on the tool uses [uv](https://docs.astral.sh/uv/) and Astral's
[ruff](https://docs.astral.sh/ruff/) and [ty](https://docs.astral.sh/ty/). `pyproject.toml` pins them in the
`dev` dependency group and configures them; `uv.lock` holds the exact versions.

```sh
uv sync                        # .venv with the tool (editable) and its packages, ruff and ty
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
