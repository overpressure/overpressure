# Contributing

Welcome. Two things help most right now: a new headset, and reports from other units of a supported one (so far
everything has run on one PICO 4).

## A new headset

overpressure is built for more than one headset: the installer, the window, the build container and the formats
Android-based headsets share are generic, and a headset adds a folder under `devices/` with its plan, images and
drivers. [docs/adding-a-headset.md](docs/adding-a-headset.md) walks through it; open an issue early, so the
generic parts can grow where your headset needs them.

## A bug

Open an issue with the template and attach the headset's support bundle (the PICO 4's:
`python -m overpressure dev support-bundle`). Its identifiers are masked, and it is plain text: read it first.

## A change

- Work on a branch and open a pull request against `main`. CI has to pass: the host tests on Python 3.9 and 3.13
  (Linux, and on GitHub also Windows and macOS), `make lint` (ruff and ty through uv:
  [docs/tool.md](docs/tool.md#development)), the shell tests, shellcheck, the C tests, the link check and the
  private-data scan.
- [CLAUDE.md](CLAUDE.md) and each headset's CLAUDE.md hold the project's rules, for people and agents alike: hard
  rules, code style, how documents are written.
- Use conventional commits (`feat:`, `fix:`, `docs:`); the changelog is generated from them.
- New behaviour gets host tests, with fakes for hardware.
- Your contribution goes out under the license of what it changes: the Unlicense unless the folder says otherwise.

## What never enters the repository

- Serials, MAC addresses, Steam IDs, network names and other identifiers.
- Vendor files and vendor data: firmware, Valve's packages, panel commands, sensor settings. They come from the
  public downloads at build time ([devices/phoenix/docs/distribution.md](devices/phoenix/docs/distribution.md)).
- Code copied from other projects without a license that allows it.

`python scripts/check-private.py` looks for identifiers, vendor register tables and binary files.

## Safety

Anything writing to a headset needs a recoverable path, as the headset's CLAUDE.md defines it. Don't add a firmware,
panel or model to the verified list before the whole path has run on it. Never send OTA or firmware commands to a
controller, the station or the MCU: their frames come from allowlists.
