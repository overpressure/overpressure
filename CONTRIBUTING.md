# Contributing

Reports from other headsets are the most useful thing right now: everything has run on one.

## A bug

Open an issue with the template and attach `python -m overpressure dev support-bundle`'s archive. Its identifiers
are masked, and it is plain text: read it first.

## A change

- Work on a branch and open a pull request against `main`. CI has to pass: `python -m unittest`, the shell and C
  tests, the link check and the private-data scan.
- [CLAUDE.md](CLAUDE.md) and each headset's CLAUDE.md hold the project's rules, for people and agents alike: hard
  rules, code style, how documents are written.
- Use conventional commits (`feat:`, `fix:`, `docs:`); the changelog is generated from them.
- New behaviour gets host tests, with fakes for hardware.
- Your contribution goes out under the license of what it changes: the Unlicense unless the folder says otherwise.

## What never enters the repository

Serials, MAC addresses, Steam IDs, network names and other identifiers. Vendor files and vendor data: firmware,
Valve's packages, panel commands, sensor settings. They come from the public downloads at build time
([devices/phoenix/docs/distribution.md](devices/phoenix/docs/distribution.md)). `python scripts/check-private.py` looks
for them, and no code copied from other projects without a license that allows it.

## Safety

Anything writing to a headset needs a recoverable path, as the headset's CLAUDE.md defines it. Don't add a firmware,
panel or model to the verified list before the whole path has run on it. Never send OTA or firmware commands to a
controller, the station or the MCU: their frames come from allowlists.
