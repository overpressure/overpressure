# CLAUDE.md

Guidance for working in this repository: **overpressure**, Linux and a SteamOS-compatible
userspace on standalone VR headsets, each on its own stock kernel. The generic host tooling is at
the root; everything specific to a headset lives in `devices/<codename>/`.

**Before working on a headset, read its `devices/<codename>/CLAUDE.md`**: its status, its hard
rules (what must never be written, what crashes its kernel) and its hardware workflow.

| Codename | Headset | Guidance |
| --- | --- | --- |
| phoenix | PICO 4 (2022, Snapdragon XR2) | [devices/phoenix/CLAUDE.md](devices/phoenix/CLAUDE.md) |

## Backlog

The backlog lives in the issue tracker, "Devices" workspace, one project per headset (the
PICO 4: project "PICO 4", key `PICO`); work on the generic tooling goes into the project of the
headset that needs it. Every new bug, gap (something unverified or unmeasured) or missing feature
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

- `overpressure/`: the generic host package, standard-library Python (3.9+):
  `headset.py` (the `Headset` interface: `Info`, `commands()`, `steps()`; `Step`, `Param`; the
  registry), `runner.py` (a step's process on this host: WSL or sudo for Linux-root steps),
  `__main__.py` (the command line), `gui.py` (the Tk window), `localconfig.py` (`.env`, one key
  prefix per headset), and the shared formats: `bootimg.py`, `avb.py`, `sparse.py`,
  `superimg.py`, `payload.py`, `cpio.py`, `compress.py`, `alpine.py`, `apksign.py`,
  `kernelinfo.py`, `elfdeps.py`, `windows_ncm.py`.
- `devices/<codename>/`: one headset: `tooling/` (its Python package with `HEADSET`), its
  images' sources and recipes, `scripts/`, `tests/`, `docs/`, `README.md`, `CLAUDE.md`,
  `device.mk` (its Makefile targets).
- `tests/`: host tests of the generic package. `docs/`: `tool.md`, `adding-a-headset.md`.
- Private and generated, ignored, at the root: `backup/`, `assets/`, `cache/`, `out/`, `.env`.

## Commands

```sh
python -m unittest                                # every host test (generic and each headset's), seconds
python -m overpressure gui                        # the window: image creation and flashing
python -m overpressure devices                    # the headsets
python -m overpressure [--device CODENAME] steps  # a headset's steps and their command lines
python -m overpressure [--device CODENAME] COMMAND ...   # a command of the headset
make test                                         # Linux/WSL: also each headset's shell tests
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

## Environment quirks (Windows host + WSL)

- **Variable expansion.** `wsl -u root -- bash -c '... $VAR ...'` expands `$VAR` before bash
  runs, even inside single quotes. Write the commands to a script file and run
  `wsl -u root -- bash /mnt/c/.../script.sh`.
- **MSYS paths.** From Git Bash, export `MSYS_NO_PATHCONV=1`. Otherwise `/abs/paths` in
  arguments and environment values become `C:/Program Files/Git/...`.
- **Backslashes.** Commands sent through the Bash tool collapse `\\` to `\`, also inside quoted
  heredocs, so file content with double backslashes arrives mangled. Write such files with the
  file tools, or build backslashes with `chr(92)` in Python.
- **CRLF.** Python on Windows writes CRLF unless `newline="\n"`; files under
  `devices/*/userspace/`, `devices/*/initramfs/` and scripts must stay LF (PKGBUILD checksums
  depend on it).
- **Tk.** The window needs Tk: Windows' Python has it, WSL's usually not (the command line does
  everything there).
- **PowerShell.** Run project PowerShell scripts with `pwsh` (7): Windows PowerShell 5.1 aborts
  on native stderr.

## Conventions

- **Match the code around you.** Python uses the standard library only, dataclasses and
  type hints, and short docstrings that explain why. Shell scripts use `set -euo pipefail`.
  C sources carry SPDX headers (BSL-1.0 for Monado code).
- **Generic or headset.** Code that only one headset uses lives in its folder; a format or a
  helper a second headset needs moves into `overpressure/` with its tests.
- **Pinned sources.** Pin external sources by commit or hash. Keep patches as `.patch` files
  next to their recipe, and regenerate their `sha256sums` after any change.
- **Tests.** New behaviour gets host tests (fakes for hardware, golden data for protocols).
- **Commits.** Use conventional commits (`feat:`, `docs:`, ...) on a step branch,
  fast-forwarded into `main`. Commit and push only when asked. Remote: `origin`.
