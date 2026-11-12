# CLAUDE.md

Guidance for working in this repository: **overpressure**, Linux and a SteamOS-compatible
userspace on standalone VR headsets, on Valve's Steam Frame kernel. The generic host tooling is at
the root and serves every headset; everything specific to one headset lives in
`devices/<codename>/`. The PICO 4 is the first; new headsets are welcome.

**Before working on a headset, read its `devices/<codename>/CLAUDE.md`**: its status, its hard
rules (what must never be written, what crashes its kernel) and its hardware workflow.

| Codename | Headset | Guidance |
| --- | --- | --- |
| phoenix | PICO 4 (2022, Snapdragon XR2) | [devices/phoenix/CLAUDE.md](devices/phoenix/CLAUDE.md) |

## Backlog

The backlog lives in the repository's GitHub issues, for the headsets and the generic tooling
alike. Every new bug, gap (something unverified or unmeasured) or missing feature found during
work gets an issue there, in the active milestone, when it is found; search the issues first so
that nothing is filed twice.

- **Kind.** A label: `story` for features and gaps, `bug` for defects, `task` for technical work.
- **Description.** A user story ("As a ... I want ... so that ..."), a "Now:" line with the
  measured state, and short, exact acceptance criteria.
- **Labels.** The area (graphics, tracking, controllers, cooling, steam, tooling, ...) and,
  except for bugs, the category: `gap`, `feature`, `limitation` or `disabled`. Link blocking
  issues as dependencies.

Keeping the issues current is mandatory whenever work touches one, not a wrap-up step:

- **Status.** "In progress" when work starts, back to "To do" when it pauses; "In review" when
  only a check by the user or on hardware is left; "Done" only when every acceptance criterion is
  verified. Check the states of all issues the work touched before ending a task.
- **Comments.** As results come in: what landed (commit), what was measured, which criteria are
  met and which are still open. A partial result keeps the issue open.

## Documentation

`docs/` documents the generic tool ([tool.md](docs/tool.md)), adding a headset and releasing; each
headset's documents are in `devices/<codename>/docs/`, its overview in its `README.md`. Documents
state facts, short, with a TL;DR first: what the system is, how it works, how to build, run and
verify it, verified results and open work. No work logs: no dated entries, no history of
attempts, no evidence file paths, no test counts, no issue keys; git history is the log. When a
result changes, update the fact in place, and keep each headset's roadmap and README current.
`python scripts/check-docs.py` checks every link and anchor.

## Layout

- `overpressure/`: the generic host package (Python 3.9+): the command line, the `Headset`
  interface, the installer's engine, the window, the build container, the shared formats. Its
  module map: [docs/tool.md](docs/tool.md#how-it-fits-together).
- `devices/<codename>/`: one headset: `tooling/` (its Python package with `HEADSET`), its images'
  sources and recipes, `scripts/`, `tests/`, `docs/`, `README.md`, `CLAUDE.md`, `device.mk`.
- `tests/`: host tests of the generic package. `scripts/`: the repository's checks
  (`check-private.py`, `check-docs.py`, `run-shellcheck.py`, `c-tests.sh`, `release.py`).
- `Install.cmd`, `Install.command`, `install.sh`: the start scripts (a pinned uv, a managed Python and
  CustomTkinter into `cache/`, then the window).
- Private and generated, ignored: `backup/`, `assets/`, `cache/`, `out/`, `.env`.

## Commands

```sh
python -m unittest                                # every host test (generic and each headset's), seconds
uv run --extra gui python -m unittest tests.test_headsets tests.test_installer tests.test_advanced
                                                  # the window's tests with real widgets (skipped without CustomTkinter)
make lint                                         # uv run: ruff format --check, ruff check, ty check
make format                                       # uv run: ruff format, ruff check --fix
python -m overpressure doctor                     # what this host lacks for the headset, with fixes
python -m overpressure container pull             # the build image (overpressure/builder), where the builds' Linux side runs
python -m overpressure gui [--advanced] [--demo]  # the window (demo: nothing runs)
python -m overpressure [--device CODENAME] steps  # a headset's steps and their command lines
python -m overpressure container run -- make test # also each headset's shell tests, on any host
```

Commands that touch the headset show the disclaimer and ask y/n first: in unattended runs pass
`--accept-disclaimer` (anywhere) or set `OVERPRESSURE_ACCEPT_DISCLAIMER=1`. They also refuse on a
laptop on battery below 30 % ([docs/tool.md](docs/tool.md#the-command-line)). Each headset's
CLAUDE.md lists its build and hardware commands.

## Hard rules (every headset)

- **Private data.** Never commit or redistribute device data or vendor files: `backup/`,
  `assets/`, `out/`, `cache/` and `.env` are ignored on purpose. Valve's packages and the built
  userspace archives are private too. Docs and code must not contain device serials or similar
  identifiers; they belong in `.env`. `python scripts/check-private.py --history` before a push.
- **Hardware actions need a recoverable path**, as each headset's CLAUDE.md defines it. Ask
  the user before the first flash or low-level (EDL, bootloader) action in a new conversation.
- **Never run two builds at once**: they share their build roots and caches.

## Environment quirks (Windows host)

- **Builds.** The builds' Linux side runs only in the build container (Docker or Podman must be
  running): `python -m overpressure container run -- ...` from the repository root, which it sees
  at `/work`; build roots live in its `/var/tmp` volume. One build at a time: a second is refused.
- **MSYS paths.** From Git Bash, export `MSYS_NO_PATHCONV=1`. Otherwise `/abs/paths` in
  arguments and environment values become `C:/Program Files/Git/...`.
- **Backslashes.** Commands sent through the Bash tool collapse `\\` to `\` (also `\n` in a
  heredoc's Python string), and some heredocs fail to parse at all. Write such files and scripts
  with the file tools, or build backslashes with `chr(92)`.
- **CRLF.** Python on Windows writes CRLF unless `newline="\n"`; files under
  `devices/*/userspace/`, `devices/*/initramfs/` and scripts must stay LF (PKGBUILD checksums
  depend on it).
- **Python on PATH** is 3.11 without CustomTkinter; the window and its tests need
  `uv run --extra gui`. A scratch folder holding a file named like a standard module (`xml.py`)
  shadows it for scripts run from there.
- **PowerShell.** Run project PowerShell scripts with `pwsh` (7): Windows PowerShell 5.1 aborts
  on native stderr.

## Conventions

- **Match the code around you.** Python: standard library, dataclasses, type hints, short
  docstrings that say why. Beyond the standard library only what `pyproject.toml` pins: `brotli`,
  `pyusb` with `libusb-package`, and CustomTkinter for the window, each imported where it is used
  with a fallback. ruff (line length 120, every rule) and `ty check` pass (`make lint`): full
  annotations, imperative docstrings, exception messages through a variable, keyword-only
  booleans; a `# ty: ignore[rule]` or `# noqa: RULE` names its rule and says why. A new
  extensionless Python program goes into both file lists in `pyproject.toml`. Shell scripts use
  `set -euo pipefail`. C sources carry SPDX headers (BSL-1.0 for Monado code).
- **Generic or headset.** Code that only one headset uses lives in its folder; a format or a
  helper a second headset needs moves into `overpressure/` with its tests.
- **Pinned sources.** Pin external sources by commit or hash. Keep patches as `.patch` files
  next to their recipe, and regenerate their `sha256sums` after any change.
- **Tests.** New behaviour gets host tests (fakes for hardware, golden data for protocols).
- **Commits.** Conventional commits (`feat:`, `docs:`, ...) on a step branch, fast-forwarded into
  `main`. Commit and push only when asked. Remote: `origin`.
