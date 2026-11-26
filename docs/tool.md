# The tool

`python -m overpressure` builds a headset's images and works on the headset: a window (the installer,
and every step in its Advanced mode) and a command line with the same steps and every command of the
headset.

**TL;DR**

| To | Run |
| --- | --- |
| install, as an owner | `Install.cmd` (Windows), `Install.command` (macOS) or `sh install.sh` (Linux) |
| download a headset's release into `devices/`, or its update | `python -m overpressure headset add CODENAME [--channel beta\|preview \| --version V]`, `headset update CODENAME` |
| see what this computer lacks | `python -m overpressure doctor` |
| open the window | `python -m overpressure gui [--advanced]` |
| install, upgrade or restore in a terminal | `python -m overpressure install`, `upgrade`, `restore` |
| export or import the headset's backup | `python -m overpressure backup export\|import FILE` |
| save the logs for a bug report | the window's *Save logs…*, or `python -m overpressure support-bundle FILE` |
| run a build's Linux side | `python -m overpressure container run -- CMD` |
| list a headset's steps and commands | `python -m overpressure steps`, `python -m overpressure --help` |

It is Python 3.9+ on the standard library, with pinned packages where they spare the owner an outside
tool: brotli (the stock OTA), pyusb with libusb-package (EDL devices, Windows' USB network link), truststore
(HTTPS verified by the system's own verifier, [Downloads](#downloads)), and Tk with CustomTkinter for the
window. It runs on Windows, Linux and macOS, from the repository root, where
`.env` and the private folders (`out/`, `cache/`, `assets/`, `backup/`) live.

## Headsets in devices/

overpressure holds no headset. Each is its own repository (`overpressure/<codename>`) with its own releases,
dropped into the ignored `devices/<codename>/`; [overpressure/catalogue.py](../overpressure/catalogue.py)
lists the headsets there are.

```sh
python -m overpressure headset list                 # every headset, what devices/ holds of it, its update
python -m overpressure headset releases phoenix     # its published releases, newest first
python -m overpressure headset add phoenix          # its newest stable release (until there is one, beta)
python -m overpressure headset add phoenix --channel beta      # the newest, betas included
python -m overpressure headset add phoenix --channel preview   # its main: development state, untested
python -m overpressure headset add phoenix --version 2026.12.0b0  # a release by its version, older ones too
python -m overpressure headset update phoenix       # what its recorded channel offers now, when newer
```

A release is the tag's source zip, checked against its published SHA-256 (a preview of `main` has none),
unpacked into a new folder and checked against its `headset.json` before it replaces what was there.
`devices/<codename>/.release.json` records which it is: its tag, its channel and its zip's SHA-256, or that
it was asked for by version (pinned). `headset update` and the headset page follow the recorded channel: a
newer release on it (for a preview, another commit of `main`) is an update; a pinned release has none. A
development checkout, a Git clone of the headset's repository in its folder, is never replaced: Git updates
it.

`headset.json` names the headset interface the release was made for (`api`, `MAJOR.MINOR`,
[adding-a-headset.md](adding-a-headset.md#the-headset-interface)). `headset releases` marks each release this
overpressure cannot run, with the reason, and the channels take the newest that runs. The registry loads only
a headset this overpressure runs; `python -m overpressure devices` lists the others as refused, with the
reason (update overpressure, use an older one, or download another release).

## Dependencies

```sh
python -m overpressure doctor
```

lists what this computer has and lacks for the headset's workflow, grouped by what each part is needed
for (the window, the headset, EDL, the builds, the extracted stock files), with a fix for anything
missing. The exit status is 1 while something needed is missing; optional parts (Tk and CustomTkinter,
pyusb and libusb-package, truststore, brotli while the brotli command is there) and warnings do not fail it. The
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

## Downloads

Every download of overpressure and its headsets (the host tools, the headsets' releases, Alpine's packages,
the stock OTA, a headset's kernel and packages) goes through [overpressure/web.py](../overpressure/web.py):

- **TLS** is verified by the system's own verifier through truststore, as pip does: CryptoAPI on Windows, the
  Security framework on macOS, OpenSSL with the system's roots on Linux. Python's own verifier on Windows
  trusts only the roots already in Windows' store, while Windows fetches a missing root the first time it
  needs one; GitHub's download hosts end at roots a computer may not have fetched yet (ISRG Root YR under ISRG
  Root X1, Sectigo's E46 under USERTrust ECC), and those downloads failed with "unable to get issuer
  certificate". Python 3.9 has no truststore and verifies with the standard library; `doctor` says which one
  runs. Every certificate is verified either way.
- **A short outage** is tried again for two minutes, 2 s apart at first and up to 30 s: a failed or dropped
  connection, TLS, a name that doesn't resolve, and the answers 408, 429 and 5xx. Each try again is logged with
  its reason; any other answer fails at once.
- **A failure says why**: the URL and the HTTP status, the certificate's problem, the name, the timeout or the
  connection.

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
- **Parallel jobs.** A package build in it (`build/packages.sh` of
  [overpressure/packages](https://github.com/overpressure/packages)) runs one job per CPU, but at most one per 1.5 GiB of the container's memory
  (`MAKEFLAGS`, `CMAKE_BUILD_PARALLEL_LEVEL`, `OVERPRESSURE_BUILD_JOBS` for meson).
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
900×600. The logo is its icon, from PNG renderings at the display's scaling, copied from
[overpressure/brand](https://github.com/overpressure/brand) ([docs/logo](logo/README.md)).

It needs Tk and [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) (`customtkinter==6.0.0`,
the `gui` extra in `pyproject.toml`); the start scripts bring both, `uv sync --extra gui` too. Without them
`gui` says how to get them and exits with 1; without a display, with 2.

- **The disclaimer.** On every start the window is covered by *Before you go on*: the warranty is void,
  nobody else is to blame, no warranty of any kind. *I accept* holds for every program the window starts;
  *Close* closes it. It shows even when the environment has accepted it ([below](#the-command-line)).
- **The headset page.** One card per headset of the catalogue, in plain words for owners who are not
  technical, and with two things kept apart: the headset, and what overpressure keeps for it on this computer.
  - **The headset:** its name, *Last used* on the one chosen last (listed first, with the page's main button),
    and one line on it: *Install, update or restore it with the guided installer*, *Unfinished: continue from
    Install where you left off* (read with `install --json --plan`, which runs nothing), *Installed with
    overpressure*, or *No guided installer yet: expert tools only*. Its button is *Continue* for an unfinished
    installation, else *Open*; a click on the card does the same.
  - **Its support on this computer**, an inset panel titled *PICO 4 support on this computer*
    ([`headsets.footer`](../overpressure/window/headsets.py)): the version there in the display spelling (*Version 2026.12.0 beta 2*, a preview's
    *Version 2026.12.0 preview from 3 December 2026 (4d8f977)*, *kept at
    this version* for a pinned one, *Developer copy · updated with Git* for a checkout), at most one status and
    one action, and what that action changes. *Up to date*; *New version 2026.12.0 beta 3* with *Download 2026.12.0 beta 3*
    (the recorded channel's newest; *Checking for updates* until read, nothing claimed offline, never for a
    pinned version); *Downloading 2026.12.0 beta 3* with a progress bar; *Download failed* with the reason and *Try
    again*. Every download says that only this computer changes: afterwards the headset is updated by opening
    it and choosing *Upgrade*, and after a download the card says so again (*Your headset has not changed
    yet*), or, after the first download, that the headset can be opened to install. A headset not downloaded
    yet offers *Download PICO 4 support* (*Nothing on your headset changes*); one held in a version this
    overpressure cannot run says *Needs a newer overpressure* and shows the version choice.

  *Other versions* opens the version choice (*Newest stable*, *Newest beta*, *Preview (main, untested)*, then
  each published release), with *Download* and a note that newest stable is recommended. A download runs
  [`headset add`](#headsets-in-devices) on a thread and reads `devices/` again when it ends. The choice starts on
  what the download recorded (its channel, or its pinned version), else on *Newest stable*, or *Newest beta*
  while no stable release runs here. The headset chosen last is kept in `out/window.json`
  (`{"last_headset": CODENAME}`), not in `.env`. Only `--device` skips the page; `OVERPRESSURE_DEVICE` does not.
- **What to do.** A headset with more than the install ([workflows](#the-installer)) asks *What would you
  like to do?*: one card per workflow, with an *erases the headset* pill where it erases, where it stopped
  last time, or why it cannot run now (`Headset.workflow_problem`). When the headset runs another version than
  the support on this computer (`installed_release` against `catalogue.identity`, never for a development
  checkout), the *Upgrade* card says so: a *New version* pill, *Your headset runs 2026.12.0 beta 2. Upgrade brings it to
  2026.12.0 beta 3, the version on this computer.*, and, when the upgrade can run, the page's main button; the headset
  page's line then reads *Upgrade ready: your headset runs 2026.12.0 beta 2, this computer has 2026.12.0 beta 3* (an
  unfinished run comes first). Below them, *Your backup* says where
  the headset's backup is (`Headset.backup_set`) with *Export backup…* and *Import backup…*, which run
  `python -m overpressure backup export|import FILE` as a child. Last, *Something went wrong?* with *Save logs…*
  runs `python -m overpressure support-bundle FILE`: one zip for a bug report.
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

CI starts each script on its own system up to overpressure itself: with `OVERPRESSURE_START_CHECK` set it runs
`python -m overpressure --version` instead of the window (GitHub Actions' Windows runners have administrator
rights, which `Install.cmd` then does not refuse there).

### A newer overpressure

overpressure comes in three channels, as each headset does ([Headsets in devices/](#headsets-in-devices)):

| Channel | What | Where |
| --- | --- | --- |
| stable | `YYYY.M.PATCH` (`2026.12.0`) | the release GitHub marks *Latest*, once there is one |
| beta | `YYYY.M.PATCHbN` (`2026.12.0b3`), a pre-release | the newest release marked *Pre-release* |
| preview | `main` | *Download ZIP* on the repository's page, or a clone |

`python -m overpressure --version` says which one runs, in both spellings and with its commit:
*overpressure 2026.12.0 beta 3 (2026.12.0b3, commit 0123abcd4567)*; a preview with its commit's time
(*2026.12.0 preview from 3 December 2026 (4d8f977)*, `2026.12.0.dev202612031154`). A release zip carries the commit
and its time ([overpressure/version.py](../overpressure/version.py)); a checkout asks git. Between releases `main`
carries the next one's development version (`2026.12.0.dev0`), so a preview is never taken for the release before
it ([releasing.md](releasing.md#versions)).

A newer release unpacks into a folder of its own, beside the one before. What the owner made is in the earlier
folder: the settings (`.env`), the headset's backup (`backup/`, the way back to its own system), the installer's
records (`out/install/`, which say what the headset runs), the headsets downloaded into `devices/`, and the
files a headset's way back also needs (`Headset.backup_set`: the PICO 4's SSH key for its maintenance system).
On its first start in a fresh folder the window finds the earlier one beside it and asks before it opens; *Yes*
moves the backup (a rename on the same disk) and copies the rest, replacing nothing the new folder has, and
the earlier folder gets a `MOVED.txt` that says where its backup went. *No* is remembered
(`out/carried-over.json`), and a checkout is never asked. `python -m overpressure carry-over [FOLDER]` does the
same from a terminal ([overpressure/carryover.py](../overpressure/carryover.py)).

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

A mode that carries a drive (`UsbMode.storage`) needs one rule more: udev watches every disk and processes it
again whenever one is closed after a write, and on a drive the headset changes under it that loop can keep udev
busy for good. NetworkManager waits for udev before it sets up a link,
so the headset's USB network never comes up. The doctor checks that a rule turns that watch off
(`OPTIONS+="nowatch"` for the mode's IDs and product string) and gives the rule to add to
`/etc/udev/rules.d/70-overpressure.rules`. Better still, a headset offers no such drive (the PICO 4 offers none).

## The app (in development)

`app/` holds the window that replaces the CustomTkinter one: a web front end (Svelte 5, TypeScript, Vite; Inter
bundled) in a Tauri 2 shell. Its Rust core starts [the app's server](#the-apps-server) and passes lines both
ways; the web view has no shell or file access of its own. It runs the demo engine unless
`OVERPRESSURE_APP_REAL=1`; `OVERPRESSURE_PYTHON` names the Python, `OVERPRESSURE_ROOT` the overpressure folder.

```sh
cd app && pnpm install --frozen-lockfile
pnpm dev                 # the front end in a browser at http://127.0.0.1:1420, the demo engine behind it
pnpm check && pnpm test  # types and unit tests
pnpm tauri dev           # the app's window: needs rustup (app/src-tauri/rust-toolchain.toml pins Rust) and, on
                         # Windows, the Visual Studio Build Tools' C++ workload
pnpm tauri build --bundles nsis   # Windows' per-user installer, in src-tauri/target/release/bundle/
```

Pages: the welcome page with the disclaimer (*Show this on every start*; unticked, it comes back with a new
overpressure), the headsets on this computer, a headset's workflows as tiles (each with what it does, whether it
erases the headset and why it cannot run now), and a run: a ring with a segment per stage, the active stage's
tasks, the notes, the engine's questions as dialogs, the log under *Details*, and *Stop*. Leaving a run's page
leaves the run going; the status bar leads back to it. Downloading headsets, the backup, *Save logs…*, Advanced
mode and the packaged apps are not there yet.

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
  every later one. A finished workflow forgets the other workflows' state and records `"completed":
  {"release": ..., "at": UTC}` in its own. The state records the headset's
  tooling (`"release"`: its tag and zip, or the development checkout); progress another release made starts
  over, the choices kept, so a changed plan never resumes in the middle, and each task's done check skips
  what is there already. `install.installed_release` reads what the headset runs: the release of its last
  finished install or upgrade (none after a restore).
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
  (`localconfig.update`: the key's line replaced, everything else kept; a missing `.env` starts as
  `.env.example` and each headset's `env.example`).
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
| `finished` | `status` (`done`, `failed`, `stopped`), `task` (null when done), `error`, `where`; `offer` when the failure offers more than Retry (`import-backup`: the window shows *Import backup…*) | last, once |

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

### The app's server

`python -m overpressure serve --json [--demo]` is the engine for the app in development: one long-lived
process, JSON lines both ways. A request is `{"id": N, "request": NAME, ...}` on stdin and gets one answer
on stdout, `{"id": N, "result": {...}}` or `{"id": N, "error": TEXT}`. A run's [events](#events) follow
its answer, each with `"run"`, then `{"event": "exited", "run": ID, "code": N}`; a line of the engine that is
no event arrives as `output`. `--demo` runs the window's demo engine: nothing touches a headset.

| Request | Fields | Answer |
| --- | --- | --- |
| `hello` | | `protocol` (major.minor; an app refuses a newer major), `version`, `platform`, `demo`, `disclaimer` {`title`, `text`, `accept`, `due`, `accepted`} |
| `settings`, `set_settings` | `appearance`: `system`, `light` or `dark` | the settings, kept in `out/app.json` |
| `accept_disclaimer` | `every_start` | the settings; the disclaimer is accepted for the server and every program it starts. Unticked, it is due again with a new overpressure |
| `headsets` | | `headsets` [{`codename`, `name`, `soc`, `repository`, `downloaded`, `channel`, `pinned`, `refused`, `workflows` [{`key`, `title`, `summary`, `erases`, `changes`, `problem` (why it cannot run now, or empty)}]}]: the catalogue's and those in `devices/` |
| `start` | `codename`, `workflow`, `restart` (a stage) | `run`; refused without the disclaimer, while another run is under way, or when the workflow cannot run now |
| `answer` | `run`, `answer` (the ask's id), `accepted`, `values` | {} |
| `stop` | `run` | {}; the run stops at once, or after a write under way |

The end of stdin stops a run under way and ends the server.

## The command line

```text
python -m overpressure [--device CODENAME] [--accept-disclaimer] COMMAND [ARGS...]

  --version            this overpressure: its version, channel and commit
  carry-over [FOLDER]  the settings, backup and installer records from the earlier overpressure folder
  devices              the headsets in devices/, and those this version cannot run
  headset list | releases CODENAME | add CODENAME [--channel stable|beta|preview | --version V] | update CODENAME
                       the headsets there are, their releases, one downloaded into devices/, its newer one
  gui [--advanced] [--demo | --demo-fresh] [--demo-headsets]
                       the window: the installer, and every step in its Advanced mode
  serve --json [--demo]
                       the engine as a server for the app: requests, answers and events as JSON lines
  steps                the headset's steps and their command lines
  run STEP [NAME=VALUE...]
                       a step with parameters, as the window runs it
  ram-boot [IMAGE]     boot an image from RAM with fastboot boot, where the bootloader allows it
  install [--workflow KEY] [--json] [--plan] [--restart STAGE]
                       the installer: the headset's stages in order, resumable
  upgrade | restore    the installer's other workflows, where the headset has them
  backup export FILE | backup import FILE | backup compact
                       the headset's backup as one checked file, and back
  support-bundle FILE  the logs for a bug report in one zip: the installer's records of the headset
                       and its own files (Headset.support_files), the serial settings, what the headset
                       names (Headset.support_masks) and the home folder masked
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

**Its output is UTF-8** on every host, also redirected to a file or a pipe, where Python on Windows would
write the system's code page and stop at a character it lacks (a build's `→`). A program whose lines a
command passes on (`process.run_streamed`) ends when handling a line fails, and the command raises that
error: it never waits on a program blocked on a full pipe.

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

**Never with administrator rights.** Every command, and the window, refuses to start (exit status 1)
with administrator rights: Run as administrator on Windows (an elevated token), `sudo` or root on Linux
and macOS ([overpressure/elevated.py](../overpressure/elevated.py)). The error says how to start it
instead; the window shows it in a dialog too. Nothing overpressure does needs those rights (the headset's
USB access comes from drivers or udev rules, `doctor`), and what it writes into its folder would belong
to the administrator. The start scripts (`Install.cmd`, `install.sh`, `Install.command`) refuse the same
way before they write anything. It runs where nobody starts it by hand: inside the build container, where
its builds run as root, and on GitHub Actions (`GITHUB_ACTIONS=true`), whose Windows runners always run as
an administrator.

## How it fits together

| Module | Role |
| --- | --- |
| [overpressure/catalogue.py](../overpressure/catalogue.py) | the headsets there are and their repositories; their releases on GitHub, a channel's or a version's, downloaded into `devices/<codename>/` against the checksum and `headset.json` (`headset list\|releases\|add`) |
| [overpressure/api/](../overpressure/api/__init__.py) | The headset interface by major version (`v1`): the only way a headset imports overpressure; a later major keeps the old one's modules as adapters |
| [overpressure/headset.py](../overpressure/headset.py) | `SUPPORTED`, `API` and `RETIRED`, the headset interface's versions; `headset.json` read and checked; `Headset` (`Info`, `commands()`, `steps()`, `dependencies()`, `usb_modes()`, `ram_boot()`, `install_plan()`, `workflows()`, `plan()`, `workflow_problem()`, `after_install()`, `finished_page()`, `backup_set()`, `adopt_backup()`, `touches()`), `Command`, `Step`, `Param`, `Workflow`, `BackupSet`, `AfterInstall`, `Action`, the registry |
| [overpressure/install.py](../overpressure/install.py) | the install engine: `Stage`, `Task`, `Ask`, the `Context` a task gets, the state, the lock, the events, `install`, `upgrade` and `restore` |
| [overpressure/disclaimer.py](../overpressure/disclaimer.py), [power.py](../overpressure/power.py) | the disclaimer and the gate before a command that touches the headset (`--accept-disclaimer`, `OVERPRESSURE_ACCEPT_DISCLAIMER`); this computer's power: a battery below 30 % refuses those commands (Windows, Linux and macOS readers) |
| [overpressure/elevated.py](../overpressure/elevated.py) | administrator rights (an elevated token, root outside a container; not on GitHub Actions) refuse every command and the window |
| [overpressure/runner.py](../overpressure/runner.py) | the process a step is on this host (a Linux-root step's in the build container), its display, running and stopping it |
| [overpressure/__main__.py](../overpressure/__main__.py) | the command line |
| [overpressure/serve.py](../overpressure/serve.py) | the engine as a server for the app ([The app's server](#the-apps-server)): its requests, the settings, the disclaimer, a run's events with its id |
| [overpressure/window/](../overpressure/window/__init__.py) | the window (CustomTkinter). Models without widgets: `installer.py` (the engine's events as stages and asks; the child and demo engines), `advanced.py` (the steps, their values and command lines, the checks), `headsets.py` (the headset page, its plan probes, `out/window.json`, the demo's second headset). Widgets: `app.py` (the header, the installer view, the panels), `advanced_view.py`; `theme.py` the design tokens and fonts, `kit.py` the components, `logo.py` the logo's renderings (`icons/`) |
| [overpressure/carryover.py](../overpressure/carryover.py) | A newer overpressure's folder takes the owner's data from the one beside it |
| [overpressure/lp.py](../overpressure/lp.py) | `super`'s logical partitions: the metadata Android's dynamic partitions use |
| [overpressure/progress.py](../overpressure/progress.py) | The progress lines a child program prints, which the installer and the window show as bars |
| [overpressure/version.py](../overpressure/version.py) | This overpressure's version in the tags' spelling, its channel and commit (`--version`) |
| [overpressure/localconfig.py](../overpressure/localconfig.py) | `.env`: each headset's settings with its own key prefix; `DeviceSettings`, what every Qualcomm Android headset names |
| [overpressure/fastboot.py](../overpressure/fastboot.py) | flashing an image sparse above a size, raw below; a restart into the bootloader that returns once its new session is listed; the RAM boot: `fastboot boot` with the headset's checks, never a write |
| [overpressure/doctor.py](../overpressure/doctor.py) | the dependency checks: the host's, the build container's, then the headset's (`Headset.dependencies()`) from the shared ones (settings, EDL backup and tools, adb and fastboot, Windows' USB link, Linux's USB access) |
| [overpressure/usbaccess.py](../overpressure/usbaccess.py) | Linux's USB access: what the installed udev rules grant for each USB mode, the fix per distribution, a connected device's access |
| [overpressure/tools.py](../overpressure/tools.py) | the pinned host tools in `cache/tools` |
| [overpressure/web.py](../overpressure/web.py) | every download over HTTP(S): TLS verified by the system's own verifier (truststore), a short outage tried again, a failure that says why ([Downloads](#downloads)) |
| [overpressure/linux.py](../overpressure/linux.py) | the build container: its pinned image ([overpressure/builder](https://github.com/overpressure/builder)), one named build at a time, stopping it, the aarch64 handler, ownership |
| [overpressure/stage1.py](../overpressure/stage1.py) | a stage 1: an Alpine ramdisk in the stock Android boot image, from a headset's `Spec` |
| [overpressure/runtime/](../overpressure/runtime/), [contract.py](../overpressure/contract.py) | the headset runtime ([runtime.md](runtime.md)): the maintenance system's init, PID 1, services, USB gadget, storage policy, recovery deadline and library, under a headset's stage-1 overlay; the paths, files and words the PC reads and writes on the headset |
| [overpressure/ota.py](../overpressure/ota.py) | stock boot images and partitions from a vendor OTA (block OTAs and `payload.bin`), a member's parts over eight connections at once and resumed after an interruption, checked against the zip, brotli by the package or the command; a pinned file out of a remote zip, checked by its hash |
| [overpressure/android.py](../overpressure/android.py), [qualcomm.py](../overpressure/qualcomm.py) | adb and fastboot, fail-closed, the wait for Android's boot, a partition's size from `getvar all`; the SoC ID, EDL devices counted through pyusb (PowerShell, ioreg or sysfs without it), EDL (edl-ng, bkerler/edl), partition maps, a full backup (an unfinished one of the same headset goes on from where it stopped, since interface 1.5), a write verified by readback, and a whole backup written back LUN by LUN, each write read back (since 1.10) |
| [overpressure/backup.py](../overpressure/backup.py) | verified backups and a partition's image in them; the backup archive (`backup export`, `import` and `compact`) |
| [overpressure/support.py](../overpressure/support.py) | the logs for a bug report (`support-bundle`): the installer's records and the headset's own files in one zip, this computer's identifiers masked |
| [overpressure/holes.py](../overpressure/holes.py) | a file's zero runs handed back to the file system (sparse files on NTFS, ext4, APFS), its content unchanged |
| [overpressure/process.py](../overpressure/process.py), [ssh.py](../overpressure/ssh.py) | programs and bounded waits; SSH with the project's key, the keys an image enrolls |
| [overpressure/evidence.py](../overpressure/evidence.py), [storage.py](../overpressure/storage.py) | a run that records every step and command in its results folder, bounded waits and HTTP fetches; the read-only storage policy a headset's Linux stages enforce |
| `bootimg`, `avb`, `sparse`, `payload`, `cpio`, `compress`, `alpine`, `apksign`, `kernelinfo`, `elfdeps`, `windows_ncm` | the formats and helpers Android-based headsets share (`bootimg` also edits a boot image's ramdisk and command line) |
| [overpressure/assets.py](../overpressure/assets.py) | a headset's assets out of its stock folder: the folder checked against its manifest, files out of its ext4 and FAT images, the assets manifest |
| [overpressure/ext4.py](../overpressure/ext4.py), [fat.py](../overpressure/fat.py) | read-only readers of stock file system images: ext4 (32- and 64-bit, extents, block maps, holes, links) and FAT12/16/32 with long names; `python3 -m overpressure.ext4 cat IMAGE PATH` for the Linux build scripts |
| [scripts/stock-lib.sh](../scripts/stock-lib.sh) | for a headset's Linux build scripts: a file out of a stock ext4 image |
| [overpressure/kernelpin.py](../overpressure/kernelpin.py), [kernelimage.py](../overpressure/kernelimage.py) | a headset on Valve's kernel: its pinned release of overpressure/linux (download, hashes, the build in its image, the contracts with the kernel's names), and a boot image with that kernel (the stock header's, command line rewritten by key, archives appended, checked) |
| [overpressure/pacman.py](../overpressure/pacman.py), [prefetch.py](../overpressure/prefetch.py) | pacman's repositories on any host (Valve's databases, what a package depends on, pacman.conf, packages.lock), and a headset's downloads into a cache before the install writes it, each checked by its pin |
| [overpressure/build/](../overpressure/build/) | a headset's builds, called by its own scripts with its folder: the userspace (`build-userspace.sh`, its prefix and hooks in the headset's `userspace/build.sh`), the system image, the build on the headset (`build-on-headset.sh`, a hook for what it runs beside), the kernel tree's reproducible core (`build-kernel-tree.sh`, sourced by the headset's `kernel/build-tree.sh`) and the kernel step; the downloads' retries (`fetch.sh`), the base root's databases and the package lock |

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
make lint                      # as CI, and the headsets in devices/: ruff format --check, ruff check, ty check
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
