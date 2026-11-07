# CLAUDE.md

Guidance for working in this repository: **overpressure**, Linux and a SteamOS-compatible
userspace on standalone VR headsets (the PICO 4 on Valve's kernel). The generic host tooling is at
the root; everything specific to a headset lives in `devices/<codename>/`.

**Before working on a headset, read its `devices/<codename>/CLAUDE.md`**: its status, its hard
rules (what must never be written, what crashes its kernel) and its hardware workflow.

| Codename | Headset | Guidance |
| --- | --- | --- |
| phoenix | PICO 4 (2022, Snapdragon XR2) | [devices/phoenix/CLAUDE.md](devices/phoenix/CLAUDE.md) |

## Backlog

The backlog lives in the issue tracker, project "Overpressure", key `the backlog`, for the
headsets and the generic tooling alike. Every new bug, gap (something unverified or unmeasured) or missing feature
found during work gets an issue there, in the active release, when it is found; search the
project first so that nothing is filed twice.

- **Kind.** `story` for features and gaps, `bug` for defects, `task` for technical work.
- **Description.** A user story ("As a ... I want ... so that ..."), a "Now:" line with the
  measured state, and short, exact acceptance criteria.
- **Labels.** The area (graphics, tracking, controllers, cooling, steam, tooling, ...) and,
  except for bugs, the category: `gap`, `feature`, `limitation` or `disabled`. Link blocking
  issues as dependencies.

Keeping the issues current is mandatory whenever work touches one, not a wrap-up step:

- **Status.** Move an issue to "In progress" when work on it starts, and back to "To do" when
  it is paused. "In review" means implemented with only a check left that needs the user or
  hardware; "Done" only when every acceptance criterion has been verified. Check the states of
  all issues the work touched before ending a task, including ones resolved on the side.
- **Comments.** Comment as results come in: what landed (commit), what was measured, which
  criteria are met and which are still open. A partial result keeps the issue open, with the
  open criteria named in the comment.

## Documentation

The generic tool and how to add a headset are documented in `docs/`; each headset's documents
are in `devices/<codename>/docs/`, its overview in `devices/<codename>/README.md`; the root
README is the project's overview. Documents state facts: what the system is, how it works, how
to build, run and verify it, verified results (a table of current facts) and open work. No work
logs: no dated entries, no history of attempts, no evidence file paths, no test counts. Git
history is the log. When a result changes, update the fact in place, and keep each headset's
status in its roadmap and README current.

## Layout

- `overpressure/`: the generic host package, Python (3.9+) on the standard library and three pinned
  packages (see Conventions):
  `headset.py` (the `Headset` interface: `Info`, `commands()`, `steps()`, `install_plan()`; `Step`,
  `Param`; the registry), `runner.py` (a step's process on this host: the build container for Linux-root steps),
  `__main__.py` (the command line), `install.py` (the installer's engine: stages, asks, resumable state,
  JSON events; `python -m overpressure install`), `window/` (the window, CustomTkinter, which
  only the window imports; `installer.py` and `advanced.py` are the
  installer view's and Advanced mode's models without widgets, `app.py` and `advanced_view.py` draw
  them on `kit.py`'s components, `theme.py` the design tokens: colours, spacing, type and fonts),
  `tools.py` (pinned host tools:
  platform-tools, edl-ng), `linux.py` and `container/` (the build container, where every
  Linux-root step runs: one named build at a time, its stop, the aarch64 handler), `doctor.py` (dependency checks:
  the host's, the ones every Qualcomm Android headset shares, and `Headset.dependencies()`),
  `localconfig.py` (`.env`, one key prefix per headset; `DeviceSettings`), `stage1.py` (the
  Alpine stage 1 in a stock Android boot image), `ota.py` (stock images and partitions from a
  vendor OTA, pinned files from a remote zip), `assets.py` (a headset's assets out of its stock folder:
  the checked folder, files out of its images, the assets manifest), `android.py` (adb/fastboot, fail-closed, boot wait,
  partition sizes), `qualcomm.py` (SoC ID, EDL, partition maps, backups, verified writes),
  `backup.py` (verified backups), `process.py` (programs and bounded waits), `ssh.py`,
  `evidence.py` (runs that record every step and command), `storage.py` (the read-only storage
  policy), and the shared formats: `bootimg.py` (also ramdisk edits), `ext4.py` and `fat.py` (read-only
  file system readers for stock images), `avb.py`, `sparse.py`,
  `payload.py`, `cpio.py`, `compress.py`, `alpine.py`, `apksign.py`,
  `kernelinfo.py`, `elfdeps.py`, `windows_ncm.py`, and `fastboot.py` (sparse-or-raw flashing, and
  the RAM boot every headset with `fastboot boot` gets: `python -m overpressure ram-boot`).
- `devices/<codename>/`: one headset: `tooling/` (its Python package with `HEADSET`), its
  images' sources and recipes, `scripts/`, `tests/`, `docs/`, `README.md`, `CLAUDE.md`,
  `device.mk` (its Makefile targets).
- `tests/`: host tests of the generic package. `docs/`: `tool.md`, `adding-a-headset.md`, `releasing.md`.
  `scripts/`: shell helpers for the headsets' scripts (`stock-lib.sh`: files out of a stock ext4 image), and the
  repository's checks: `check-private.py`, `check-docs.py`, `run-shellcheck.py`, `c-tests.sh`, `changelog.py`.
- `Install.cmd`, `Install.command`, `install.sh`: the start scripts; they fetch the pinned uv, a managed
  Python and CustomTkinter into `cache/` and open the window.
- `LICENSE` (Unlicense) and `THIRD-PARTY.md`; a directory under another license says so in its `LICENSE.md`.
- Private and generated, ignored, at the root: `backup/`, `assets/`, `cache/`, `out/`, `.env`.

## Commands

```sh
python -m unittest                                # every host test (generic and each headset's), seconds
uv sync                                           # the dev environment: ruff, ty (pyproject.toml, uv.lock)
make lint                                         # uv run: ruff format --check, ruff check, ty check
make format                                       # uv run: ruff format, ruff check --fix
python -m overpressure doctor                     # what this host lacks for the headset, with fixes
python -m overpressure tools                      # fetch the pinned host tools into cache/tools
python -m overpressure container build            # the build container, where the builds' Linux side runs
uv sync --extra gui                               # also CustomTkinter, for the window
python -m overpressure gui [--advanced] [--demo | --demo-fresh] [--demo-headsets]  # the window (demo: nothing runs)
python -m overpressure devices                    # the headsets
python -m overpressure [--device CODENAME] steps  # a headset's steps and their command lines
python -m overpressure [--device CODENAME] COMMAND ...   # a command of the headset
make test                                         # Linux: also each headset's shell tests
python -m overpressure container run -- make test  # the same on any host, in the build container
```

With one headset the `--device` choice is implicit; `OVERPRESSURE_DEVICE` in `.env` picks one
otherwise. Each headset's CLAUDE.md lists its build and hardware commands.

## Hard rules (every headset)

- **Private data.** Never commit or redistribute device data or vendor files: `backup/`,
  `assets/`, `out/`, `cache/` and `.env` are ignored on purpose. Valve's packages and the built
  userspace archives are private too. Docs and code must not contain device serials or similar
  identifiers; they belong in `.env`.
- **Hardware actions need a recoverable path**, as each headset's CLAUDE.md defines it. Ask
  the user before the first flash or low-level (EDL, bootloader) action in a new conversation.
- **Never run two builds at once**: they share their build roots and caches.

## Environment quirks (Windows host)

- **Builds.** The builds' Linux side runs only in the build container (Docker or Podman must be
  running): `python -m overpressure container run -- ...` from the repository root, which it sees
  at `/work`; build roots live in its `/var/tmp` volume. One build at a time: a second is refused.
- **MSYS paths.** From Git Bash, export `MSYS_NO_PATHCONV=1`. Otherwise `/abs/paths` in
  arguments and environment values become `C:/Program Files/Git/...`.
- **Backslashes.** Commands sent through the Bash tool collapse `\\` to `\`, also inside quoted
  heredocs, so file content with double backslashes arrives mangled. Write such files with the
  file tools, or build backslashes with `chr(92)` in Python.
- **CRLF.** Python on Windows writes CRLF unless `newline="\n"`; files under
  `devices/*/userspace/`, `devices/*/initramfs/` and scripts must stay LF (PKGBUILD checksums
  depend on it).
- **Tk.** The window needs Tk and CustomTkinter, which the start scripts' managed Python has (uv's
  Python 3.12 with the `gui` extra); Windows' own Python has Tk, a Linux system Python often not. The command line
  does everything the window does.
- **PowerShell.** Run project PowerShell scripts with `pwsh` (7): Windows PowerShell 5.1 aborts
  on native stderr.

## Conventions

- **Match the code around you.** Python uses the standard library, dataclasses and
  type hints, and short docstrings that explain why. Beyond the standard library it takes only the
  packages `pyproject.toml` pins where they save the owner an outside tool: `brotli` (the stock OTA's
  images; else the brotli command) and `pyusb` with `libusb-package` (counting EDL devices; else the
  system's own listing), and CustomTkinter for the window. Each is imported where it is used, with a
  fallback or a one-line fix, so plain `python -m overpressure` runs without them. It is formatted by ruff (line length 120)
  and passes every ruff rule and `ty check` (`make lint`): full annotations, docstrings with an
  imperative first line, exception messages through a variable, keyword-only booleans; a
  `# ty: ignore[rule]` or `# noqa: RULE` names its rule and says why. A new extensionless Python
  program goes into both file lists in `pyproject.toml`. Shell scripts use `set -euo pipefail`.
  C sources carry SPDX headers (BSL-1.0 for Monado code).
- **Generic or headset.** Code that only one headset uses lives in its folder; a format or a
  helper a second headset needs moves into `overpressure/` with its tests.
- **Pinned sources.** Pin external sources by commit or hash. Keep patches as `.patch` files
  next to their recipe, and regenerate their `sha256sums` after any change.
- **Tests.** New behaviour gets host tests (fakes for hardware, golden data for protocols).
- **Commits.** Use conventional commits (`feat:`, `docs:`, ...) on a step branch,
  fast-forwarded into `main`. Commit and push only when asked. Remote: `origin`.
