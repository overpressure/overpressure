# Contributing

Welcome. Two things help most right now: a new headset, and reports from other units of a supported one (so far
everything has run on one PICO 4).

## A new headset

overpressure is built for more than one headset: the installer, the window, the build container and the formats
Android-based headsets share are generic, and a headset is a repository of its own with its plan, images and
drivers, dropped into `devices/`. [docs/adding-a-headset.md](docs/adding-a-headset.md) walks through it; open an
issue early, so the generic parts can grow where your headset needs them.

## A bug

Open an issue with the bug template and attach the headset's support bundle (the PICO 4's:
`python -m overpressure dev support-bundle`). Its identifiers are masked, and it is plain text: read it first.

## A change

- Work on a branch and open a pull request against `main` of the repository you change (this one, or a
  headset's such as [overpressure/phoenix](https://github.com/overpressure/phoenix)), titled as a conventional
  commit; it is merged by rebase or squash, and `main` takes no direct pushes. CI has to pass: here the host
  tests on Linux with Python 3.9 and 3.13 (a system Python may be either) and on Windows and macOS with 3.12
  (what the start scripts install), `make lint` (ruff and ty through uv:
  [docs/tool.md](docs/tool.md#development)), shellcheck, the link check, the private-data scan and each
  headset's tests against the change; a headset's CI runs its own tests, lint and checks against this
  repository's `main`.
- A change both need lands here first, keeping the headset interface compatible, then in the headset.
- [CLAUDE.md](CLAUDE.md) and each headset's CLAUDE.md hold the project's rules, for people and agents alike: hard
  rules, code style, how documents are written.
- Use conventional commits (`feat:`, `fix:`, `docs:`); the changelog is generated from them.
- New behaviour gets host tests, with fakes for hardware.
- Your contribution goes out under the license of what it changes: the Unlicense unless the folder says otherwise
  (each repository's THIRD-PARTY.md).

## What never enters the repository

- Serials, MAC addresses, Steam IDs, network names and other identifiers.
- Vendor files and vendor data: firmware, Valve's packages, panel commands, sensor settings. They come from the
  public downloads at build time (the PICO 4's:
  [docs/distribution.md](https://github.com/overpressure/phoenix/blob/main/docs/distribution.md)).
- Code copied from other projects without a license that allows it.

`python scripts/check-private.py` looks for identifiers, vendor register tables and binary files.

## Safety

Anything writing to a headset needs a recoverable path, as the headset's CLAUDE.md defines it. Don't add a firmware,
panel or model to the verified list before the whole path has run on it. Never send OTA or firmware commands to a
controller, the station or the MCU: their frames come from allowlists.
