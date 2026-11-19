<!--
  The README of a headset's repository. Every headset's README follows this template, so that they all look and
  read the same. It is written for owners, not developers: plain words, no file paths or commands outside
  "Install" and "For developers", and every "doesn't work" links its issue.

  Replace each {{...}}. Keep every section and every row of "What works" in this order; a row that does not
  apply to the headset (it has no such hardware) says "Not on this headset" instead of being removed. Rows
  only this headset has go at the end of their group. The status symbols:

    ✅ Works      🟡 Works, with limits      ❌ Doesn't work yet      ❔ Not tested yet      ➖ Not on this headset

  Keep it current: when a result changes, change its row. Details belong in docs/, linked from the row.

  python scripts/check-docs.py --repo devices/<codename> checks a README against this template: its sections in
  order, each table's rows (the template's first, in order; the headset's own after them), one status symbol
  per row of "What works", an issue linked from every ❌, and no {{...}} left.
-->

# {{Headset name}}

**Linux and Valve's SteamVR on the {{headset name}} ({{year}}, {{chip}}), installed with
[overpressure](https://github.com/overpressure/overpressure).** It replaces {{stock system}} with a system built
on Valve's Steam Frame software: SteamVR, the Steam client and its games run on the headset itself, without a PC.
The way back to {{stock system}} is part of it.

| | |
| --- | --- |
| **Status** | {{Beta / Stable / In development}}: {{one sentence: who it is for today}} |
| **Newest release** | [{{version}}]({{link to the release}}) |
| **Your headset** | {{model and variants that work}}; {{the stock system versions that are needed}} |
| **Your computer** | Windows ✅ · Linux ✅ · macOS {{✅ / ❔}} |
| **Tested on** | {{number}} headset{{s}} |

> [!WARNING]
> Installing erases {{stock system}}'s apps and data. The installer makes a full backup first: it is your way
> back, so keep it safe. {{Anything else an owner must know before starting, e.g. warranty.}}

## What works

✅ Works · 🟡 Works, with limits · ❌ Doesn't work yet · ❔ Not tested yet · ➖ Not on this headset

### Display and comfort

| Feature | | Notes |
| --- | --- | --- |
| Both displays | {{}} | {{resolution, refresh rates}} |
| Lens distance (IPD) | {{}} | {{}} |
| Standby when taken off | {{}} | {{}} |
| Cooling | {{}} | {{}} |

### Tracking

| Feature | | Notes |
| --- | --- | --- |
| Head tracking (6DoF) | {{}} | {{}} |
| Controller tracking | {{}} | {{}} |
| Hand tracking | {{}} | {{}} |
| Eye tracking | {{}} | {{}} |
| Room setup and boundary | {{}} | {{}} |
| Passthrough (room view) | {{}} | {{}} |

### Controllers

| Feature | | Notes |
| --- | --- | --- |
| Buttons, sticks and triggers | {{}} | {{}} |
| Vibration | {{}} | {{}} |
| Battery level | {{}} | {{}} |
| Pairing | {{}} | {{}} |

### Sound

| Feature | | Notes |
| --- | --- | --- |
| Speakers and volume | {{}} | {{}} |
| Microphone | {{}} | {{}} |
| Headphone jack | {{}} | {{}} |

### Connections and power

| Feature | | Notes |
| --- | --- | --- |
| Wi-Fi | {{}} | {{}} |
| Bluetooth | {{}} | {{}} |
| Battery and charging | {{}} | {{}} |
| Sleep | {{}} | {{}} |
| Battery life | {{}} | {{}} |

### Games and apps

| Feature | | Notes |
| --- | --- | --- |
| Steam store and library | {{}} | {{}} |
| Linux games | {{}} | {{}} |
| Windows games (Proton) | {{}} | {{}} |
| Android apps | {{}} | {{}} |
| PC VR over Steam Link | {{}} | {{}} |
| Video playback | {{}} | {{}} |

### Installing

| Feature | | Notes |
| --- | --- | --- |
| Install | {{}} | {{}} |
| Upgrade to a new release | {{}} | {{}} |
| Back to {{stock system}} | {{}} | {{}} |

Everything that is still open: [the issues]({{link to the repository's issues}}).

## What you need

- The {{headset name}}, charged, with {{stock system}} {{versions}}.
- A USB-C cable that carries data, not only power.
- A computer with Windows, Linux or macOS, an internet connection and about {{size}} GB free.
- About {{minutes}} minutes.

## Install

1. Download [overpressure](https://github.com/overpressure/overpressure/releases) and unpack it.
2. Start it: double-click `Install.cmd` on Windows or `Install.command` on macOS, or run `sh install.sh` on Linux.
3. Accept the disclaimer, choose the {{headset name}} and click **Download**.
4. Click **Install** and follow the window. It checks your computer and the headset, backs the headset up, then
   installs. {{One sentence on what the owner does in between, e.g. switching on developer mode.}}
5. When the window says so, unplug the headset and put it on.

**Upgrade** installs a newer release and keeps your data. **Restore to stock** brings {{stock system}} back from
the backup. {{Headset-specific notes for the owner, e.g. what to do after the restore.}}

## If something goes wrong

The window says what state the headset is in and what to do next; starting it again goes on where it stopped.
{{The headset's own way out, e.g. which buttons to hold.}} To report a problem,
[open an issue]({{link to new issue}}) and attach the bundle **{{how to make the support bundle}}** makes; it
leaves out your headset's serial numbers.

Questions and quick help: the [{{headset name}} room]({{link to the headset's Matrix room}}) and the
[Help room](https://matrix.to/#/#overpressure-help:matrix.org) of the project's Matrix space.

## For developers

How it works, how it is built and how it is tested: [docs/]({{link to docs/}}), starting with
{{the development document, linked}}.
