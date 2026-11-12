# SPDX-License-Identifier: Unlicense
"""The PICO 4 (2022, codename phoenix, Snapdragon XR2) in overpressure.

Its host tooling: the stage-1 boot image (the maintenance stage 1, on the stock kernel), the stock
firmware from PICO's public OTA, EDL, fastboot and ADB, the acceptance runner, the development
loop and the acceptance cycle; the system itself runs on Valve's kernel, overpressure/linux's
release that kernel/release.json pins. Python 3.9+
with the standard library and overpressure's pinned packages (pyproject.toml); the formats it
shares with other headsets come from the overpressure package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from overpressure import qualcomm
from overpressure.headset import (
    INSTALL,
    Action,
    AfterInstall,
    BackupSet,
    Command,
    Headset,
    Info,
    Param,
    Step,
    Workflow,
    tools_step,
)
from overpressure.usbaccess import UsbMode

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from overpressure.doctor import Check, Host
    from overpressure.install import Stage

#: The PICO 4 on USB, as the host's tools open it (checked on this unit): PICO OS with USB debugging,
#: the bootloader's fastboot and EDL. Its maintenance system and the installed system are a USB network.
ADB_MODE = UsbMode("PICO OS with USB debugging", "2d40", "00b7")
FASTBOOT_MODE = UsbMode("fastboot mode", "18d1", "d00d")
USB_MODES = (ADB_MODE, FASTBOOT_MODE, qualcomm.EDL_MODE)
#: The way out of a stale EDL session: the power button ends EDL mode, and the next attempt enters it afresh.
EDL_RESTART = (
    "Hold the power button for about 10 s until the headset restarts, keep the cable in, then run the step "
    "again: the headset enters EDL mode afresh."
)
#: The installer's other workflows (overpressure/headset.py, Workflow).
UPGRADE, RESTORE = "upgrade", "restore"
#: The dev loop's subcommands that touch the headset: all but its two builds (image, boot-image).
DEV_ON_HEADSET = (
    "stage-abl",
    "flash",
    "connect",
    "check",
    "reboot",
    "restore",
    "normal",
    "recovery",
    "installed",
    "release-check",
    "support-bundle",
    "install",
    "restore-partition",
    "ssh",
)


def _relative(path: Path, root: Path) -> str:
    """Return PATH relative to ROOT in .env's spelling when it is under ROOT, else absolute."""
    for inside, base in ((path, root), (path.resolve(), root.resolve())):
        if inside == base or base in inside.parents:
            return inside.relative_to(base).as_posix()
    return str(path)


#: The firmware every default names; device.mk and .env.example repeat it (tests/test_stock.py).
FIRMWARE = "5.13.7-global-seko"
FIRMWARES = (FIRMWARE,)
_firmware = Param(
    "firmware",
    "Firmware",
    FIRMWARE,
    "choice",
    FIRMWARES,
    "the PICO OS build in firmware.json whose stock kernel the image takes",
)
#: The stock firmware folder of python -m overpressure stock and assets, relative to the repository:
#: the assets step reads it on this computer.
STOCK_FOLDER = "cache/pico4-stock/" + FIRMWARE
# The Linux build folders live in /var/tmp on purpose: the build container's volume, on a Linux file system and kept.
#: The userspace build's root, on the Linux side.
USERSPACE_BUILD = "/var/tmp/pico4-userspace-build"  # noqa: S108  # a root-owned build folder, not a temporary file
#: The kernel build's folder (kernel-build), on the Linux side.
KERNEL_BUILD = "/var/tmp/pico4-kernel-build"  # noqa: S108  # a root-owned build folder, not a temporary file


class Phoenix(Headset):
    """The PICO 4: its commands, the window's steps and its doctor checks."""

    info = Info(
        "phoenix",
        "PICO 4",
        "Qualcomm Snapdragon XR2 (SM8250)",
        "Valve's kernel 7.2 in boot; the maintenance stage 1 on its stock kernel 4.19 in recovery",
    )

    def commands(self) -> dict[str, Command]:
        """Return the command words of python -m overpressure and their modules."""
        return {
            "build": Command("image", "build the stage-1 boot image from a stock boot.img, OTA zip or URL", keep=True),
            "inspect": Command("image", "analyse a stock boot image and its kernel configuration", keep=True),
            "match": Command("image", "find the firmware.json entry for a headset's fingerprint", keep=True),
            "stock": Command("stock", "the stock firmware's partitions from PICO's public OTA"),
            "assets": Command("assets", "the stock firmware, then every file the userspace build takes from it"),
            "bootchain": Command(
                "bootchain", "each PICO OS build's boot chain against the verified one, from PICO's OTAs"
            ),
            "device": Command(
                "device",
                "identity, EDL backups, engineering ABL, unlock and restore",
                headset=("probe", "check-unlocked", "backup", "stage", "unlock", "restore-abl"),
            ),
            "hwtest": Command("hardware_test", "preflight and the bounded acceptance runner", headset=True),
            "dev": Command(
                "devloop",
                "the development loop: maintenance stage 1, install, checks",
                headset=DEV_ON_HEADSET,
            ),
            "cycle": Command("cycle", "unattended acceptance cycle that ends on stock boot and ABL", headset=True),
            "kernel": Command("kernel", "the pinned release of overpressure/linux: download, check, contracts"),
        }

    def steps(self) -> Sequence[Step]:
        """Return the window's steps, in the order a new headset goes through them."""
        build = USERSPACE_BUILD
        archive = "out/userspace/pico4-userspace.tar.zst"
        system = "out/userspace/pico4-system.img"
        return (
            tools_step(),
            Step(
                "assets",
                "Stock firmware and assets",
                "Image",
                ("assets", "--firmware", "{firmware}", "--stock", "{stock}"),
                "PICO's public OTA (about 11 GB, kept in the stock folder), then every file the userspace "
                "build takes from it into assets/*-VERSION. Python on this computer, no Linux needed.",
                (_firmware, Param("stock", "Stock folder", STOCK_FOLDER, "path")),
            ),
            Step(
                "stage1",
                "Stage-1 boot image",
                "Image",
                ("build", "--firmware", "{firmware}"),
                "The Alpine initramfs with the stock kernel: out/pico4-stage1-FIRMWARE.img.",
                (_firmware,),
            ),
            Step(
                "userspace",
                "Userspace archive",
                "Image",
                (
                    "scripts/build-userspace.sh",
                    "{build_root}",
                    "{output}",
                    "--gpu-firmware",
                    "assets/graphics-firmware-{version}",
                    "--sensor-data",
                    "assets/sensor-data-{version}",
                    "--wlan",
                    "assets/wlan-{version}",
                    "--audio",
                    "assets/audio-{version}",
                    "--stock-slam",
                    "assets/stock-slam-{version}",
                    "{refresh}",
                    "{build_packages}",
                ),
                "Holo Core and the Frame's packages, the project's from the pinned release, with this headset's "
                "adaptation; incremental.",
                (
                    Param("build_root", "Build folder (container volume)", build, "path"),
                    Param("output", "Output folder", "out/userspace", "path"),
                    Param(
                        "version",
                        "Asset version",
                        FIRMWARE.split("-", maxsplit=1)[0],
                        help="the assets/*-VERSION folders",
                    ),
                    Param("refresh", "Refresh package databases", "0", "flag", option="--refresh"),
                    Param(
                        "build_packages",
                        "Build the project's packages here",
                        "0",
                        "flag",
                        option="--build-packages",
                        help="instead of the pinned release's; slow on x86-64",
                    ),
                ),
                script=True,
                linux_root=True,
            ),
            Step(
                "system",
                "System image for super",
                "Image",
                ("scripts/build-system-image.sh", "{archive}", "{image}"),
                "The installed system's read-only ext4 image from the userspace archive.",
                (
                    Param("archive", "Userspace archive", archive, "path"),
                    Param("image", "System image", system, "path"),
                ),
                script=True,
                linux_root=True,
            ),
            Step(
                "dev-image",
                "Dev image (maintenance stage 1)",
                "Image",
                ("dev", "image"),
                "out/dev/dev-boot.img and the whole-partition recovery image from the stage-1 image.",
            ),
            Step(
                "kernel",
                "Valve's kernel",
                "Image",
                ("kernel", "fetch"),
                "The release of overpressure/linux that kernel/release.json pins (about 30 MB), checked by "
                "hash, into out/kernel.",
            ),
            Step(
                "kernel-build",
                "Valve's kernel, built here",
                "Image",
                ("kernel", "build", "{work}"),
                "Instead of the download: the pinned commit of overpressure/linux (280 MB) built in the build "
                "image the release was built with and checked against the release's hashes, into out/kernel.",
                (Param("work", "Build folder (container volume)", KERNEL_BUILD, "path"),),
            ),
            Step(
                "boot-image",
                "Boot image",
                "Image",
                ("dev", "boot-image"),
                "out/kernel/valve-boot.img: Valve's kernel, its device tree and the command line that starts "
                "the system image.",
            ),
            Step(
                "unlock-files",
                "Unlock files",
                "Headset",
                ("device", "fetch-unlock-files"),
                "The engineering ABL and Firehose loader from PICO's public downloads, checked by hash.",
            ),
            Step(
                "preflight",
                "Preflight on PICO OS",
                "Headset",
                ("hwtest", "preflight", "{serial}"),
                "Runs the stage-1 image's programs on the real kernel from adb shell, without root, before any "
                "bootloader work; PICO OS keeps running. Takes the newest rootfs tar in out/.",
                (Param("serial", "ADB serial", "", help="empty: the only authorized device"),),
            ),
            Step(
                "stage-abl",
                "Enter development mode: engineering ABL",
                "Headset",
                ("dev", "stage-abl"),
                "From PICO OS: the engineering ABL through EDL, checked against the backup.",
                writes=True,
            ),
            Step(
                "flash",
                "Flash the dev image",
                "Headset",
                ("dev", "flash"),
                "Writes the dev image to recovery (fastboot, verified by readback) and starts it.",
                writes=True,
            ),
            Step(
                "connect",
                "Connect to stage 1",
                "Headset",
                ("dev", "connect"),
                "The USB network link (Windows: NCM initialization) and the stage-1 login.",
            ),
            Step(
                "recovery",
                "Maintenance stage 1",
                "Headset",
                ("dev", "recovery"),
                'Every boot stays in the maintenance stage 1 (recovery) until "Normal boot".',
            ),
            Step(
                "install",
                "Install the system",
                "Headset",
                ("dev", "install", "{system}", "{format_data}"),
                "From the maintenance stage 1: boot, super and the data partition; then starts it.",
                (
                    Param("system", "System image", system, "path"),
                    Param("format_data", "Format the data partition", "0", "flag", option="--format-data"),
                ),
                writes=True,
            ),
            Step(
                "installed",
                "Connect to the installed system",
                "Headset",
                ("dev", "installed"),
                "Reconnects to the installed system after a reboot.",
            ),
            Step(
                "check",
                "Acceptance checks",
                "Headset",
                ("dev", "check"),
                "The acceptance runner's stage-2 checks on the installed system.",
            ),
            Step(
                "release-check",
                "Release checks",
                "Headset",
                ("dev", "release-check"),
                "Everything automated of docs/release-checklist.md, with a result file in out/release.",
            ),
            Step(
                "support-bundle",
                "Support bundle",
                "Headset",
                ("dev", "support-bundle"),
                "The logs a bug report needs, identifiers masked, into out/support.",
            ),
            Step("reboot", "Reboot", "Headset", ("dev", "reboot"), "What misc and boot select, also from fastboot."),
            Step(
                "normal",
                "Normal boot",
                "Headset",
                ("dev", "normal"),
                "Clears the maintenance request in misc: the installed system starts again.",
                writes=True,
            ),
            Step(
                "restore",
                "Back to stock: boot",
                "Headset",
                ("dev", "restore"),
                "Leaves development mode: the backup's system, boot and recovery, fresh data, then PICO OS.",
                writes=True,
            ),
            Step(
                "restore-abl",
                "Back to stock: verify and restore the ABL",
                "Headset",
                ("cycle", "--restore-only"),
                'After "Back to stock: boot": verified stock boot and ABL, PICO OS.',
                writes=True,
            ),
        )

    def dependencies(self) -> Sequence[Callable[[Host], list[Check]]]:
        """Return the PICO 4's doctor checks."""
        from .doctor import probes  # noqa: PLC0415  # doctor imports this package: a top-level import is circular

        return probes()

    def usb_modes(self) -> Sequence[UsbMode]:
        """Return the PICO 4's USB modes: adb in PICO OS, fastboot, EDL."""
        return USB_MODES

    def install_plan(self) -> Sequence[Stage]:
        """Return the installer's five stages (tooling/install.py)."""
        from .install import Plan  # noqa: PLC0415  # install imports this package's modules: a top-level import is circular

        return Plan(self).stages()

    def workflows(self) -> Sequence[Workflow]:
        """Return the installer's three workflows: install, upgrade, back to PICO OS."""
        return (
            Workflow(
                INSTALL,
                "Install",
                "Replaces PICO OS with the SteamOS-compatible system. Your headset is backed up to this computer "
                "first; its apps, games and data are erased.",
                erases=True,
            ),
            Workflow(
                UPGRADE,
                "Upgrade",
                "Builds this version of the system and installs it over the one this computer installed. Your "
                "Steam account, games, settings and Wi-Fi stay.",
            ),
            Workflow(
                RESTORE,
                "Restore to stock",
                "Brings back PICO OS and its bootloader from your headset's backup, as it came. The system and "
                "everything on it is erased.",
                erases=True,
            ),
        )

    def plan(self, workflow: str) -> Sequence[Stage]:
        """Return the stages of WORKFLOW (tooling/install.py)."""
        from .install import Plan  # noqa: PLC0415  # install imports this package's modules: a top-level import is circular

        plan = Plan(self)
        return {INSTALL: plan.stages, UPGRADE: plan.upgrade_stages, RESTORE: plan.restore_stages}[workflow]()

    def workflow_problem(self, workflow: str) -> str:
        """Return why WORKFLOW cannot run now: an upgrade needs a system this computer installed, a restore a backup."""
        from . import devloop  # noqa: PLC0415  # devloop imports this package's modules: a top-level import is circular

        if workflow == UPGRADE and not (devloop.WORK / devloop.INSTALLED).is_file():
            return "This computer has not installed the system on a headset: install it first."
        if workflow == RESTORE and not (self.backup_set().folder / "manifest.json").is_file():
            return "This computer has no backup of your headset: import the one you exported (Import backup)."
        return ""

    def after_install(self) -> AfterInstall:
        """Return the finished page: sign in to Steam; upgrade, back to PICO OS or a support bundle."""
        return AfterInstall(
            "Unplug it and put it on",
            "Your headset is ready. Unplug the cable, put the headset on and sign in to Steam.",
            (
                Action("Upgrade later", workflow=UPGRADE),
                Action("Restore to stock", workflow=RESTORE),
                Action("Get help", "support-bundle"),
            ),
        )

    def finished_page(self, workflow: str) -> AfterInstall:
        """Return the finished page of WORKFLOW: the install's, the upgrade's, or the way back's."""
        if workflow == UPGRADE:
            return AfterInstall(
                "Upgraded",
                "Unplug the cable and put the headset on: your account, games, settings and Wi-Fi are as you left "
                "them.",
                (Action("Restore to stock", workflow=RESTORE), Action("Get help", "support-bundle")),
            )
        if workflow == RESTORE:
            return AfterInstall(
                "Back on PICO OS",
                "Your headset runs PICO OS with the system and bootloader from your backup; its bootloader stays "
                "unlocked. Keep the backup: the installer can install the system again from here.",
                (Action("Install again", workflow=INSTALL),),
            )
        return self.after_install()

    def backup_set(self) -> BackupSet:
        """Return the backup .env names, with what the way back needs on another computer.

        The maintenance system in recovery takes this computer's SSH key only, the installed system's
        record says a system is installed, and the serials name the headset to adb and fastboot.
        """
        from . import devloop, localconfig  # noqa: PLC0415  # they import this package's modules: circular otherwise

        settings = localconfig.load()
        serials = {
            localconfig.Settings.key(name): getattr(settings, name)
            for name in ("adb_serial", "fastboot_serial", "edl_serial")
            if getattr(settings, name)
        }
        files = {"id_ed25519": devloop.WORK / "id_ed25519", "id_ed25519.pub": devloop.WORK / "id_ed25519.pub"}
        if (devloop.WORK / devloop.INSTALLED).is_file():
            files[devloop.INSTALLED] = devloop.WORK / devloop.INSTALLED
        return BackupSet(settings.path("backup"), settings.path("identity"), files, serials)

    def adopt_backup(self, imported: BackupSet) -> None:
        """Name the imported backup in .env, and the serials it came with where .env has none."""
        from overpressure import REPO  # noqa: PLC0415  # only here
        from overpressure import localconfig as base  # noqa: PLC0415  # only here

        from . import localconfig  # noqa: PLC0415  # it imports this package's modules: circular otherwise

        settings = localconfig.load()
        values = {
            localconfig.Settings.key("backup"): _relative(imported.folder, REPO),
            localconfig.Settings.key("identity"): _relative(imported.identity, REPO),
        }
        for key, value in imported.settings.items():
            name = key[len(localconfig.Settings.PREFIX) :].lower()
            if key.startswith(localconfig.Settings.PREFIX) and hasattr(settings, name) and not getattr(settings, name):
                values[key] = value
        base.update(values)
        print("Saved in .env: " + ", ".join(sorted(values)))


HEADSET = Phoenix()
