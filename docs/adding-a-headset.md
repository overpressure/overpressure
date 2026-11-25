# Adding a headset

**TL;DR.** A headset is a repository of its own, `overpressure/<codename>` (the vendor's internal name, a
Python identifier), cloned into `devices/<codename>/` of an overpressure checkout while it is developed.
It starts from [overpressure/headset-template](https://github.com/overpressure/headset-template), which loads,
passes its CI and offers one step from the first commit. Its
`headset.json` names it and the headset interface it was made for; its package `tooling` exports `HEADSET`,
a `Headset` subclass that names its commands and its window's steps. Add an installer plan, dependency
checks and a RAM boot where it has them, build on the generic modules below, and check it with `python -m
overpressure devices` and `--device CODENAME steps`. Once it has a release, an entry in
`overpressure/catalogue.py` makes it downloadable. The PICO 4
([overpressure/phoenix](https://github.com/overpressure/phoenix)) is the worked example.

## The repository

An owner of the organization creates `overpressure/<codename>` from
[the headset template](https://github.com/overpressure/headset-template) (*Use this template*), together with the
headset's team and its rulesets ([GOVERNANCE.md](https://github.com/overpressure/.github/blob/main/GOVERNANCE.md),
[the organization's part](#the-organizations-part)).
The team clones it into the overpressure checkout and gives it its names, once:

```sh
git clone https://github.com/overpressure/<codename> devices/<codename>
python devices/<codename>/scripts/new-headset.py <codename> "<Name>" "<SoC>"
python -m overpressure devices
```

That commit is a headset with everything below except its images: `headset.json`, `HEADSET` with one command
(`dev status`) and two steps, its `.env` settings and doctor checks, host tests, a README in the template's
form, `CLAUDE.md`, `docs/`, CI, the release workflow, issue forms and CODEOWNERS.

```text
devices/<codename>/   a clone of overpressure/<codename>
  headset.json     codename, name, version, the headset interface (api), commit and date (git archive's)
  __init__.py      makes devices.<codename> importable
  README.md        what works and what doesn't, and how to install it: headset-readme-template.md's form
  CLAUDE.md        its rules and hardware workflow
  env.example      its keys in .env, with their comments (a new .env starts with them)
  tooling/         its host tooling: HEADSET in __init__.py, the command modules
  tests/           its host tests (with an __init__.py), run by python -m unittest
  docs/            its documents
  .github/         its CI (against overpressure's main) and its release workflow
  ...              whatever its images need (stage 1, userspace, sources, scripts)
```

Its `README.md` is for owners, not developers, and follows [the template](headset-readme-template.md): the same
sections and rows of what works for every headset, so that they read alike. `python scripts/check-docs.py --repo
devices/<codename>`, which the headset's CI runs, checks it against the template.

`headset.json`:

```json
{
 "codename": "example",
 "name": "Example VR 1",
 "version": "2026.12.0.dev0",
 "api": "1.6",
 "commit": "$Format:%H$",
 "date": "$Format:%ct$"
}
```

`api` is the headset interface it was made for, `MAJOR.MINOR` ([the headset
interface](#the-headset-interface)): `discover()` loads only a headset this overpressure runs, and lists the
others as refused with the reason (`python -m overpressure devices`).
`.gitattributes` marks `headset.json` `export-subst`, so a release zip carries its commit and time.

`version` is the headset's calendar version ([releasing.md](releasing.md#versions)): `main` carries the next
release as a development version, which a release's version pull request turns into the release
(`python .overpressure/scripts/release.py bump beta --version-file headset.json`, from an overpressure checkout in
`.overpressure`).

**Releases.** An annotated tag that is `headset.json`'s version (`2026.12.0b0`) makes a GitHub release with
the source zip (`git archive`, one top folder) and its `.sha256`, as overpressure's own releases have. A headset's
`release.yml` runs its CI on the tag, then calls overpressure's
[headset-release.yml](../.github/workflows/headset-release.yml), which takes the codename and the name from
`headset.json`, checks the tag, writes the notes, makes the release and closes the issues it brings.
`python -m overpressure headset add CODENAME` and the window's Download take it from there
([tool.md](tool.md#headsets-in-devices)).

**CI.** A headset's `ci.yml` calls overpressure's [headset-ci.yml](../.github/workflows/headset-ci.yml) with its
codename (and `kernel: true` when it pins a release of overpressure/linux), and keeps one job of its own, `ci`,
the check its rulesets require, which passes when that run did. The shared run checks out overpressure's `main`,
the headset into `devices/<codename>`, and runs its tests on Linux, Windows and macOS, `ruff`/`ty` on its folder,
its shell tests (`tests/test_*.sh`, in dash) and `scripts/c-tests.sh` where it has them, shellcheck, the documents'
links, the private-data scan over its history, actionlint, and with `kernel` the kernel's pin and contracts; a
tree that passed against the same `main` is not tested again. overpressure's CI runs its `main`, and the oldest of
its releases that overpressure still runs, against every change of the generic code (the `headsets` job, its
codename in the matrix and a required check of each). For a change both sides need, the headset's pull request
passes `overpressure:` with the branch of overpressure's pull request while both are in review, and drops it
before it merges, once overpressure's has. The workflows' pins agree: uv is the start scripts' version, and
setup-uv and actionlint are pinned the same in each ([tests/test_workflows.py](../tests/test_workflows.py)).

How the images are built is the headset's own business. The PICO 4 keeps an Alpine stage 1 on its stock
kernel as the maintenance environment and boots its userspace, built from Valve's Holo packages, on
Valve's kernel, which it builds under `kernel/`.

## The organization's part

What the owners and `core` set up for a new headset, so that its team can start with its first pull request
([GOVERNANCE.md](https://github.com/overpressure/.github/blob/main/GOVERNANCE.md), *A new headset joins*). The
PICO 4's (`phoenix`) is the model for every setting; `<codename>` is the headset's codename, `<soc>` its SoC's layer.

**The owners**, in the organization's settings, in this order:

| Step | What |
| --- | --- |
| The tracking issue | `[Headset] <Name>` in overpressure's issues: the headset, its SoC, the people, where their work is today |
| The repository | `overpressure/<codename>` from the headset template, public; its settings as phoenix's: squash and rebase merges only, auto-merge, branches deleted after merging, no wiki, projects or discussions, overpressure as its homepage, topics for the headset and its SoC |
| Its rulesets | `main` only by pull request with a green `ci` (no deletion, no force push, linear history, review threads resolved); the shared files, which its CODEOWNERS gives `core` (`.github/`, `headset.json`), by a code owner's review; release tags (`refs/tags/v*`, `refs/tags/20*`) never move, and only the team `<codename>` creates them |
| The team `<codename>` | closed; *maintain* on `overpressure/<codename>`, *write* on overpressure/linux and overpressure/packages; on the new repository `core` gets *write* and `triage` *triage*. A new SoC also gets its team `soc-<soc>`; maintainers of a headset on an existing SoC join that team only once they maintain its layer |
| The social preview | brand's `github/social/<codename>.png`, uploaded in the repository's settings (*Social preview*) |
| The invitation | last, with the welcome, since GitHub mails it at once: into the organization (two-factor authentication) and into the team `<codename>` |

**`core`**, by pull request (branches and rulesets through the API), as soon as the repository exists:

| Repository | What |
| --- | --- |
| overpressure/`<codename>` | the naming commit (`scripts/new-headset.py`), unless the team makes it |
| [overpressure/brand](https://github.com/overpressure/brand) | before the room and the social preview, the first: the room's icon `matrix/<codename>.svg`, the headset seen from the front as it really looks (its shape, cameras and straps), in the visor's blue on the dark tile and without the maker's branding, rendered to its `.png` (its name in `scripts/render.py`, its entry in `mockups/matrix.html`); the repository's social card `github/social/<codename>.png` (1280 × 640): its name, description and topics an entry in `github/social/cards.json`, its name in `scripts/render.py`, which renders it |
| [overpressure/matrix](https://github.com/overpressure/matrix) | the headset's room `#overpressure-<codename>:matrix.org`, which the README's chat badge links: `config/rooms/<codename>.yaml` (a headset's room copied: its name, address, topic, icon `<codename>`, the names it may have had) and its key in `config/space.yaml`'s `rooms`. Merged, the *Space* workflow run by hand with *create* ticked (after a dry run without) creates it with phoenix's permissions, its icon and its place in the space. Goggles, the bot, announces the headset once its repository is public, and each of its releases |
| [overpressure/linux](https://github.com/overpressure/linux) | the branch `<codename>/7.2` from `<soc>/7.2`; its line in `.github/layers` and its row in `.github/README.md`, by a pull request into `common/7.2` that moves up the layers; rulesets as phoenix's: the branch in *Layers change by pull request*, *`<codename>/7.2` is merged by `<codename>`*, *`<codename>`'s kernels are tagged by `<codename>`* (`refs/tags/<codename>/*`), and those tags left out of *Other tags are made by core*. Until the headset's repository has `kernel/build-tree.sh`, its branch builds nothing and passes |
| [overpressure/packages](https://github.com/overpressure/packages) | the folders `<codename>/` (its layer) and `headsets/<codename>/` (its image definition), each with a `.gitkeep` until its first packages; their CODEOWNERS entries (`@overpressure/<codename> @overpressure/core`); its row in the README |
| [overpressure/.github](https://github.com/overpressure/.github) | its row in the organization's profile, [`profile/README.md`](https://github.com/overpressure/.github/blob/main/profile/README.md), the page GitHub shows for the organization: the repository, and the headset as in development |
| [overpressure](https://github.com/overpressure/overpressure) | its row in the README's *Headsets* table (in development, until its first release) and in [CLAUDE.md](../CLAUDE.md)'s table of headsets |

**Later**, when the headset gets there:

| When | What |
| --- | --- |
| Its first packages | its image definition in `headsets/<codename>/` (the `.gitkeep`s go), its codename in packages.yml's `HEADSETS`, its packages' name prefix in the layout tests |
| Its `kernel/build-tree.sh` | its branch in overpressure/linux builds. Its first build starts by itself from the ccache of another headset on its SoC's layer, as long as its `build-tree.sh` compiles in the same paths and with the same compiler as theirs (a copy of phoenix's does); the build's ccache statistics show how much the two configurations share |
| Its first release | its entry in `overpressure/catalogue.py` and its codename in the `headsets` job of [ci.yml](../.github/workflows/ci.yml), whose checks become required; the profile names it among the supported headsets |

## What overpressure already does

Most of what a headset on its stock Android kernel needs is generic:

| Need | Module |
| --- | --- |
| Its settings in `.env`: serials, the EDL backup, the Firehose loader, edl-ng, the NCM Python, the owner's SSH key | `overpressure.localconfig.DeviceSettings` |
| Its dependency checks: settings, EDL backup and tools, adb and fastboot, Windows' USB link (the build container is checked for every headset) | `overpressure.doctor` |
| Stock firmware: a boot image or partitions from the vendor's OTA, checked against the zip | `overpressure.ota` |
| A file pinned by size and SHA-256 out of a vendor's remote zip (a loader, say), over HTTP ranges | `overpressure.ota.PinnedMember`, `fetch_pinned` |
| A stage 1: Alpine Linux in the stock boot image, kernel and device trees untouched | `overpressure.stage1` (a `Spec`: packages, overlay, prebuilt files) |
| A boot image's ramdisk and command line changed, the result checked | `overpressure.bootimg.ramdisk_tree`, `with_ramdisk` |
| adb and fastboot, fail-closed, every command naming the serial; the wait for Android's boot; a partition's size | `overpressure.android` |
| Flashing through fastboot, big images sparse; the RAM boot | `overpressure.fastboot` (`flash`, `ram_boot`) |
| Qualcomm: the SoC ID, EDL with edl-ng or bkerler/edl, into EDL from Android, partition sizes from the rawprogram maps | `overpressure.qualcomm` |
| A full EDL backup with its manifest, an EDL write verified by readback | `overpressure.qualcomm.capture_backup`, `write_verified` |
| Counting a vendor's USB devices (EDL) on every system, no device opened | `overpressure.qualcomm.usb_count`, `edl_devices` |
| Backups that every write is checked against, a partition's image in them | `overpressure.backup` |
| A run on the headset that records every step and command, with bounded waits and HTTP fetches | `overpressure.evidence.Runner` |
| The read-only storage policy a Linux stage enforces | `overpressure.storage` |
| Bounded waits, running tools, SSH with the project's key, Windows' USB network | `overpressure.process`, `overpressure.ssh`, `overpressure.windows_ncm` |
| Its assets out of the stock folder: the folder checked, files out of its ext4 images (`dump`) and FAT images (`copy_matching`), the assets manifest | `overpressure.assets` |
| Reading ext4 and FAT12/16/32 images without mounting them | `overpressure.ext4`, `overpressure.fat` |
| A Linux build script taking a file out of a stock ext4 image | `scripts/stock-lib.sh` |

## The headset interface

A headset imports overpressure only through `overpressure.api.vMAJOR`, the interface's module for its major
version: `from overpressure.api.v1 import REPO, qualcomm` and `from overpressure.api.v1.headset import
Step`. Its `headset.json` names the version it was made for, `"api": "MAJOR.MINOR"`. overpressure stays
backwards compatible, so an older headset release keeps installing:

- **A minor version only adds.** A new name, a new optional argument or a new field raises the minor of its
  major in `SUPPORTED` ([overpressure/headset.py](../overpressure/headset.py)). A headset made for `1.2` runs
  on every overpressure that runs `1.2` or a later `1.x`.
- **A change that breaks anything makes a new major.** `overpressure.api.v2` gets the new interface, and
  `v1`'s modules become adapters that keep interface 1's names and behaviour on top of it. Both stay in
  `SUPPORTED` until the old major is retired, at the earliest in the release after its headsets moved on.
- **A retired major** leaves `SUPPORTED` for `RETIRED`, with the last overpressure version that ran it: a
  release made for it is refused with that version to use.
- **CI keeps it true.** overpressure's `headsets` job runs each headset's `main` and the oldest of its
  releases this overpressure still runs (`python -m overpressure headset releases CODENAME
  --oldest-supported`): their tests against the change.
- **Downloads take what runs.** `headset releases` marks each release this overpressure cannot run, with the
  reason; `stable` and `beta` take the newest that runs; the window lists only those.

Today `v1`'s modules are overpressure's own, the same objects at run time, so a patch in a headset's test
reaches every caller.

### What a headset may use

A headset declares; overpressure draws and runs. A headset has no UI code: it never imports
`overpressure.window`, Tk or CustomTkinter. The window builds every page from what the headset declares:
its steps, its plan's stages and tasks, the questions it asks, the notes it writes and its finished page.
Interface 1 is exactly what this list names, each in `overpressure.api.v1`; a headset's tests use the same
(the command line and the window are overpressure's to test).

| What | Where |
| --- | --- |
| The headset and its steps: `Headset`, `Info`, `Command`, `Step`, `Param`, `Workflow`, `Action`, `AfterInstall`, `BackupSet`, `INSTALL`, `tools_step`, `ram_boot_step`, `manifest` | `overpressure.headset` |
| The installer: `Stage`, `Task`, `Ask`, `Field`, `Failed`, `NoBackup` and the `Context` a task runs in (`run`, `output`, `progress`, `note`, `log`, `ask`, `settings`, `set_setting`, `remember`, `recall`, `clock`, `memory`, `completed`) | `overpressure.install` |
| Words for the window's backup controls: `HOW_TO_IMPORT`, `HOW_TO_EXPORT`, `missing(folder, needed_for)` | `overpressure.backup` |
| Settings and checks: `DeviceSettings`, `load`, `update`; `Check`, `Host` and the check states; `UsbMode` | `overpressure.localconfig`, `overpressure.doctor`, `overpressure.usbaccess` |
| Waits for the headset in a new USB mode, since 1.6: `replug_until` (a wait that runs out asks for a replug, or for the link's fix), `replug`, `link_ask`; what the USB bus holds (`seen`) and a listing that leaves a device out (`unusable`); adb's and fastboot's device lists (`device_rows`) | `overpressure.install`, `overpressure.usbaccess`, `overpressure.android` |
| The headset's USB network link on Linux, since 1.6: its `Gadget` (IDs, product, network, lease, NetworkManager profile), `links`, `problem` and its fix; the doctor's `link_check` | `overpressure.usblink`, `overpressure.doctor` |
| The owner's answers, since 1.6: Wi-Fi (`ask`, `remember`, `held`, `problem`, `keyfile`, `COMMAND`: a NetworkManager keyfile over SSH's stdin), the owner's SSH key (`ask_public_key`); the headset's own battery before writes (`headset_refusal`) | `overpressure.wifi`, `overpressure.ssh`, `overpressure.power` |
| Paths and programs: `REPO`, bounded waits and running tools (a failed one raises `CommandFailed`, a `RuntimeError` with the whole `output`; since 1.5), progress lines, a step's command line, the pinned host tools, HTTPS downloads (`read`, `urlopen`, `answers`, `DownloadError`; since 1.5) | `overpressure.api.v1` itself, `.process`, `.progress`, `.runner`, `.tools`, `.web` |
| Formats and devices: everything in the table above, and `avb`, `compress`, `cpio`, `kernelinfo`, `payload` | the modules named there |

Two rules keep the window the core's own:

- **A headset's texts never name the window's controls.** Where a message has to point at one, it takes
  the core's words: `backup.HOW_TO_IMPORT` and `backup.HOW_TO_EXPORT`, or `backup.missing(folder)` for a
  backup that is not on this computer. A task fails for a missing backup with `NoBackup(folder)`; the
  window then offers *Import backup…* beside *Retry*.
- **What a second headset needs moves into overpressure**, with its tests, and joins this list.

## HEADSET

`devices/<codename>/tooling/__init__.py` exports `HEADSET`, an instance of a subclass of
[`overpressure.headset.Headset`](../overpressure/headset.py):

```python
from overpressure.api.v1.headset import Command, Headset, Info, Param, Step


class Example(Headset):
    info = Info("example", "Example VR 1", "SoC name", "one line about the approach")

    def commands(self):
        # command word -> module of this package whose main(argv) runs it
        return {"build": Command("image", "build the boot image"), "dev": Command("devloop", "the development loop")}

    def steps(self):
        # the window's steps, in order: building the images, then working on the headset
        return (
            Step(
                "boot-image",
                "Boot image",
                "Image",
                ("build", "--firmware", "{firmware}"),
                "The stage-1 boot image.",
                (Param("firmware", "Firmware", "1.0", "choice", ("1.0", "1.1")),),
            ),
            Step(
                "userspace",
                "Userspace",
                "Image",
                ("scripts/build-userspace.sh", "{output}"),
                params=(Param("output", "Output folder", "out/userspace", "path"),),
                script=True,
                linux_root=True,
            ),
            Step("flash", "Flash", "Headset", ("dev", "flash"), "Writes the boot image.", writes=True),
        )


HEADSET = Example()
```

- **Commands** are the headset's command line: `python -m overpressure --device example build ...` runs
  `devices/example/tooling/image.py`'s `main(["--firmware", ...])`. With `keep=True` the command word is
  passed on too (one module behind several commands). `headset=True`, or a tuple of subcommands, marks
  what touches the headset: the disclaimer and the power check come first ([tool.md](tool.md#the-command-line)).
- **Steps** are what the window offers, in the groups `Setup`, `Image` and `Headset`. `{name}` in a word
  takes a parameter's value. `script=True` runs a program from the headset's folder; `linux_root=True` runs
  as root in the build container ([tool.md](tool.md#linux-root-steps)): its paths lie in the repository or
  under `/var/tmp`, and a tool it needs goes into the build image, by pull request to
  [overpressure/builder](https://github.com/overpressure/builder), then a new pin in `overpressure/linux.py`. `writes=True` makes the
  window ask first.
- **Settings** in `.env` are a dataclass derived from `overpressure.localconfig.Settings` with the
  headset's own key prefix, so headsets share one `.env`. A headset on a stock Android bootloader derives
  from `DeviceSettings`: `adb_serial`, `fastboot_serial`, `edl_serial`, `identity` (default
  `backup/device.json`), `backup` (`backup/stock`), `loader` (`assets/firehose.elf`), `edl`, `ncm_python`
  and `ssh_public_key`, with `edl_program()` and `ncm()` resolving the tools. The headset adds only its own
  fields ([the PICO 4's](https://github.com/overpressure/phoenix/blob/main/tooling/localconfig.py)).
- Output goes to the repository's `out/`, downloads to `cache/` (`overpressure.REPO`).

## Dependencies

`Headset.dependencies()` returns the headset's checks for `python -m overpressure doctor`: callables that
take an `overpressure.doctor.Host` and return `Check` objects (name, what it is needed for, status, what
was found, the fix). `doctor.version_check` covers programs that answer a version query;
`Host.linux(script)` runs a probe in the build container. The build container itself is checked for every
headset, and its image holds every Linux tool of the builds. A headset with `DeviceSettings` builds the
rest from the shared checks: `env_check`, `serial_checks`, `backup_check`, `loader_check`,
`edl_tool_check`, `tool_checks` (adb, fastboot), `ncm_checks` (Windows' USB link) and `usb_check`
(Linux's USB access to each of `Headset.usb_modes()`: the `UsbMode`s, vendor and product ID, its tools open,
such as adb, fastboot and EDL; a USB network needs none; since interface 1.1 a mode that carries a drive is
`storage=True`, whose udev watch is turned off, `label` its USB product string, and `access=False` when no tool
opens it: a headset that uses them declares `"api": "1.1"`). Example:
[devices/phoenix/tooling/doctor.py](https://github.com/overpressure/phoenix/blob/main/tooling/doctor.py).

## The installer

`Headset.install_plan()` returns the installer's stages (`overpressure.install.Stage`, each a list of
`Task`s); empty, the default, means no installer. A task runs in an `overpressure.install.Context`: it runs
programs with their output streamed, reports progress (`ctx.progress`, or the programs' own
[progress lines](tool.md#events); a task without them is shown against its `estimate`) and notes, asks the owner (`Ask`: a consent, an
action, a form; secret fields never leave the engine) and writes settings to `.env`; a `done` check skips
work whose output is already there. The engine keeps the state in `out/install/<codename>/`, resumes after
a failure and drives the window through JSON events ([tool.md](tool.md#the-installer)). Reuse the
headset's steps' command lines. Example:
[devices/phoenix/tooling/install.py](https://github.com/overpressure/phoenix/blob/main/tooling/install.py).

**More than the install.** `Headset.workflows()` returns the installer's workflows (`Workflow`: key,
title, summary, whether it erases the headset), the install alone by default, and `Headset.plan(key)`
each one's stages; with more than one, the window asks which. `Headset.workflow_problem(key)` says why one
cannot run now. Since interface 1.3, `Workflow.changes=False` marks one that only looks, such as a check: it
starts afresh on every run and leaves the other workflows' progress and the record of what was installed as
they are (a headset that marks one declares `"api": "1.3"`, or offers it only where `Workflow` has the
field). The PICO 4 has an upgrade, the way back to stock and a check of the headset.

**The backup as one file.** `Headset.backup_set()` returns the backup, the identity, the other files and
the `.env` settings the way back needs on another computer (`BackupSet`), and `Headset.adopt_backup()`
names an imported one in `.env`; with them `python -m overpressure backup export|import FILE` and the
window's backup card work.

**Logs for a bug report.** The window's *Save logs…* (`python -m overpressure support-bundle FILE`) writes one
zip with the installer's records of the headset (each step's log, every workflow's state) and this computer's
system, the serial settings' values and the home folder masked in every text file. Since interface 1.2,
`Headset.support_files(folder)` adds the headset's own files there: its tools' logs, its device's own bundle
(a headset that adds them declares `"api": "1.2"`). Since interface 1.4, `Headset.support_masks()` names what else
the zip must not show, each value with what it becomes, such as a code derived from the headset's chip that its
tools log: masked in every text file as the serials are (an older overpressure never asks, so a headset can
offer it on `"api": "1.2"`).

**What the window says** comes from the headset; the generic window names no product, OS or store:

- Its `Info` names its card on the headset page (the name: the card speaks to owners in plain words) and heads
  Advanced mode (name, SoC, summary). A headset without an installer opens in Advanced mode only.
- The finished page: `Headset.after_install()` returns an `AfterInstall` (title, text, `Action`s), and
  `Headset.finished_page(workflow)` another workflow's. An `Action` is a label and either a workflow
  (`workflow=`, opened at its start page) or the key of a step, opened in Advanced mode. By default the
  page says *Installed*, "Your headset is ready.", and offers nothing. The PICO 4's:

```python
from overpressure.api.v1.headset import Action, AfterInstall


def after_install(self):
    return AfterInstall(
        "Unplug it and put it on",
        "Your headset is ready. Unplug the cable, put the headset on and sign in to Steam.",
        (
            Action("Upgrade later", workflow="upgrade"),
            Action("Restore to stock", workflow="restore"),
            Action("Get help", "support-bundle"),
        ),
    )
```

- Everything else (stages, tasks, asks, notes, the headset's state after a failure) comes from the plan's
  texts.

## RAM boot

A headset whose bootloader has `fastboot boot` gets `python -m overpressure ram-boot` and its window step
by returning a target from `Headset.ram_boot()`, a subclass of
[`overpressure.fastboot.RamBootTarget`](../overpressure/fastboot.py), and putting
`ram_boot_step(default_image)` among its steps.

```python
from overpressure.api.v1 import REPO
from overpressure.api.v1.fastboot import Fastboot, RamBootTarget


class ExampleRamBoot(RamBootTarget):
    up_timeout = 120
    refused_hint = "Boot stage 1 from a partition instead: python -m overpressure dev flash."

    def __init__(self, settings):  # the headset's .env settings
        self.settings = settings

    @property
    def fastboot(self):  # fastboot with the headset's serial
        return Fastboot("fastboot", self.settings.fastboot_serial)

    def image(self):  # the image to boot when none is given
        return REPO / "out" / "example-stage1.img"

    def enter(self):  # into fastboot, e.g. adb reboot bootloader
        ...

    def check_unlocked(self):  # raise unless the bootloader may boot an unsigned image
        ...

    def reached(self):  # raise until the booted image answers (network, ADB, ...)
        ...
```

`ram_boot` checks the image and that this headset is the only device in fastboot, calls the target in
that order, and tells a refusal (`Refused`: no `fastboot boot`) apart from a failure.

## Checks

`python -m overpressure devices` lists the new headset, and `python -m overpressure --device CODENAME
steps` prints its steps with their command lines. The generic tests
([tests/test_overpressure.py](../tests/test_overpressure.py)) check that every registered headset's
command modules and step scripts exist; its own tests run with `python -m unittest discover -t . -s
devices/<codename>/tests`.
