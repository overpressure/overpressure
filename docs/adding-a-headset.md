# Adding a headset

A headset is a folder `devices/<codename>/` (the codename is the vendor's internal name, a
Python identifier) with its own sources, image recipes, scripts, tests and documents. Its Python
tooling, the package `devices/<codename>/tooling`, tells overpressure what the headset is and
what can be done with it. The PICO 4 (`devices/phoenix`) is the worked example.

## The folder

```text
devices/<codename>/
  __init__.py      makes devices.<codename> importable
  README.md        what runs on the headset, its status, its quick start
  tooling/         its host tooling: HEADSET in __init__.py, the command modules
  tests/           its host tests (with an __init__.py), run by python -m unittest
  docs/            its documents
  ...              whatever its images need (stage 1, userspace, sources, scripts)
```

How the images are built is the headset's own business: the PICO 4 boots an Alpine stage 1 on
Valve's kernel, which it builds under `kernel/`, and builds its userspace from Valve's Holo packages; another headset
may differ in every step.

## What overpressure already does

Most of what a headset on its stock Android kernel needs is generic; its tooling describes the
headset and calls these:

| Need | Module |
| --- | --- |
| Stock firmware: a boot image or partitions from the vendor's OTA, checked against the zip | `overpressure.ota` (a catalog of builds, the members the headset needs) |
| A stage 1: Alpine Linux in the stock boot image, kernel and device trees untouched | `overpressure.stage1` (a `Spec`: packages, overlay, prebuilt files) |
| adb and fastboot, fail-closed, every command naming the serial | `overpressure.android`, `overpressure.fastboot` (also the RAM boot) |
| Qualcomm: the SoC ID, EDL with edl-ng or bkerler/edl, partition sizes from the rawprogram maps | `overpressure.qualcomm` |
| Backups that every write is checked against | `overpressure.backup` |
| Waiting for states with a bound, running tools, SSH with the project's key, Windows' USB network | `overpressure.process`, `overpressure.ssh`, `overpressure.windows_ncm` |
| Extraction scripts reading the stock firmware folder | `scripts/stock-lib.sh` |

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

- **Commands** are the headset's command line: `python -m overpressure --device example build
  ...` runs `devices/example/tooling/image.py`'s `main(["--firmware", ...])` (with
  `keep=True` the command word itself is passed on, for one module behind several commands).
  Talk to the headset with whatever it needs (fastboot, ADB, EDL, SSH, a vendor protocol); the
  generic modules cover the Android formats.
- **Steps** are what the window offers. A word `{name}` takes a parameter's value; braces inside
  a word are filled the same way. `script=True` runs a program from the headset's folder,
  `linux_root=True` runs on Linux as root (WSL, sudo or the build container, [tool.md](tool.md#linux-root-steps)), and `writes=True` makes the window
  ask before a step that writes to the headset.
- **Settings** in `.env` get a dataclass derived from `overpressure.localconfig.Settings` with
  the headset's own key prefix, so headsets share one `.env`
  ([the PICO 4's](../devices/phoenix/tooling/localconfig.py)).
- Output goes to the repository's `out/`, downloads to `cache/` (`overpressure.REPO`).

## Dependencies

`Headset.dependencies()` returns the headset's checks for `python -m overpressure doctor`:
callables that take an `overpressure.doctor.Host` and return `Check` objects (name, what it is
needed for, status, what was found, the fix). `doctor.version_check` covers programs that answer
a version query and `doctor.linux_commands` the programs its Linux-side scripts call (one
`command -v` round in WSL or Linux); `Host.linux(script)` runs any other probe there. The
PICO 4's are the example ([devices/phoenix/tooling/doctor.py](../devices/phoenix/tooling/doctor.py)):
its settings and private files, adb and fastboot, the Windows USB link, the build host
(binfmt, LLVM, the tools of its scripts) and the extracted assets.

## RAM boot

A headset whose bootloader has `fastboot boot` gets `python -m overpressure ram-boot` and its
step in the window by returning a target from `Headset.ram_boot()`: a subclass of
[`overpressure.fastboot.RamBootTarget`](../overpressure/fastboot.py) that says how the generic
sequence looks for it, and `ram_boot_step(default_image)` among its steps.

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

`ram_boot` checks the image and that this headset is the only device in fastboot, calls the
target in that order, and tells a refusal (`Refused`: the bootloader has no `fastboot boot`) apart
from a failure.

## Checks

`python -m overpressure devices` lists the new headset, and `python -m overpressure --device
CODENAME steps` prints its steps with their command lines. The generic tests
([tests/test_overpressure.py](../tests/test_overpressure.py)) check that every registered
headset's commands and step scripts exist.
