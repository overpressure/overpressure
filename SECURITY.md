# Security

## Reporting a problem

Please report a vulnerability privately, through GitHub's
[private reporting](https://github.com/overpressure/overpressure/security/advisories/new) for this repository, not
in a public issue.

## What is in scope

The installed system's network exposure and logins, and anything in the tooling or the images that could harm a
headset or its owner's data, here and in each headset's repository. What the PICO 4's system protects and what
it doesn't is in [its security.md](https://github.com/overpressure/phoenix/blob/main/docs/security.md). Its
bootloader is unlocked by design: someone holding the headset can reach its data. A headset's release is checked
against its published SHA-256 when downloaded; a preview of its `main` has no checksum.

Only the latest release gets fixes.
