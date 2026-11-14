# Adding a headset

**TL;DR.** A headset is a repository of its own, `overpressure/<codename>` (the vendor's internal name, a
Python identifier), cloned into `devices/<codename>/` of an overpressure checkout while it is developed. Its
`headset.json` names it and the headset interface it was made for; its package `tooling` exports `HEADSET`,
a `Headset` subclass that names its commands and its window's steps. Add an installer plan, dependency
checks and a RAM boot where it has them, build on the generic modules below, and check it with `python -m
overpressure devices` and `--device CODENAME steps`. Once it has a release, an entry in
`overpressure/catalogue.py` makes it downloadable. The PICO 4
([overpressure/phoenix](https://github.com/overpressure/phoenix)) is the worked example.

## The repository

```text
devices/<codename>/   a clone of overpressure/<codename>
  headset.json     codename, name, version, the headset interface (api), commit and date (git archive's)
  __init__.py      makes devices.<codename> importable
  README.md        what runs on the headset, its status, its quick start
  CLAUDE.md        its rules and hardware workflow
  env.example      its keys in .env, with their comments (a new .env starts with them)
  tooling/         its host tooling: HEADSET in __init__.py, the command modules
  tests/           its host tests (with an __init__.py), run by python -m unittest
  docs/            its documents
  .github/         its CI (against overpressure's main) and its release workflow
  ...              whatever its images need (stage 1, userspace, sources, scripts)
```

`headset.json`:

```json
{
 "codename": "example",
 "name": "Example VR 1",
 "version": "1.0-beta",
 "api": 1,
 "commit": "$Format:%H$",
 "date": "$Format:%ct$"
}
```

`api` is the headset interface it was made for (`API` in
[overpressure/headset.py](../overpressure/headset.py)): `discover()` loads only a headset whose `api` this
overpressure has, and lists the others as refused with the reason (`python -m overpressure devices`).
`.gitattributes` marks `headset.json` `export-subst`, so a release zip carries its commit and time.

**Releases.** A tag `vMAJOR.MINOR[-beta[.N]]` naming `headset.json`'s version makes a GitHub release with
the source zip (`git archive`, one top folder) and its `.sha256`, as overpressure's own releases have
([the PICO 4's release workflow](https://github.com/overpressure/phoenix/blob/main/.github/workflows/release.yml)).
`python -m overpressure headset add CODENAME` and the window's Download take it from there
([tool.md](tool.md#headsets-in-devices)).

**CI.** Its CI checks out overpressure's `main`, itself into `devices/<codename>`, and runs its tests,
`ruff`/`ty` on its folder, `scripts/check-docs.py --repo devices/<codename>`,
`scripts/check-private.py --repo devices/<codename> --history` and `scripts/run-shellcheck.py --repo
devices/<codename>`. overpressure's CI runs its `main` against every change of the generic code (the
`headsets` job, its codename in the matrix).

How the images are built is the headset's own business. The PICO 4 keeps an Alpine stage 1 on its stock
kernel as the maintenance environment and boots its userspace, built from Valve's Holo packages, on
Valve's kernel, which it builds under `kernel/`.

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

## HEADSET

`devices/<codename>/tooling/__init__.py` exports `HEADSET`, an instance of a subclass of
[`overpressure.headset.Headset`](../overpressure/headset.py):

```python
from overpressure.headset import Command, Headset, Info, Param, Step


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
  under `/var/tmp`, and a tool it needs goes into the container's Dockerfile. `writes=True` makes the
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
such as adb, fastboot and EDL; a USB network needs none). Example:
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
cannot run now. The PICO 4 has an upgrade and the way back to stock.

**The backup as one file.** `Headset.backup_set()` returns the backup, the identity, the other files and
the `.env` settings the way back needs on another computer (`BackupSet`), and `Headset.adopt_backup()`
names an imported one in `.env`; with them `python -m overpressure backup export|import FILE` and the
window's backup card work.

**What the window says** comes from the headset; the generic window names no product, OS or store:

- Its `Info` (name, SoC, summary) heads its card on the headset page and Advanced mode. A headset without
  an installer opens in Advanced mode only.
- The finished page: `Headset.after_install()` returns an `AfterInstall` (title, text, `Action`s), and
  `Headset.finished_page(workflow)` another workflow's. An `Action` is a label and either a workflow
  (`workflow=`, opened at its start page) or the key of a step, opened in Advanced mode. By default the
  page says *Installed*, "Your headset is ready.", and offers nothing. The PICO 4's:

```python
from overpressure.headset import Action, AfterInstall


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
from overpressure import REPO
from overpressure.fastboot import Fastboot, RamBootTarget


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
