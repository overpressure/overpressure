# SPDX-License-Identifier: Unlicense
"""The PICO 4 (2022, codename phoenix, Snapdragon XR2) in overpressure.

Its host tooling: the stage-1 boot image (the maintenance stage 1, on the stock kernel), the stock
firmware from PICO's public OTA, EDL, fastboot and ADB, the acceptance runner, the development
loop and the acceptance cycle; the system itself runs on Valve's kernel (kernel/). Pure Python
(3.9+) with the standard library only; the formats it shares with other headsets come from the
overpressure package.
"""
from __future__ import annotations

from typing import Dict, Sequence

from overpressure.headset import Command, Headset, Info, Param, Step, tools_step

#: The firmware every default names; device.mk and .env.example repeat it (tests/test_stock.py).
FIRMWARE = "5.13.7-global-seko"
FIRMWARES = (FIRMWARE,)
_firmware = Param("firmware", "Firmware", FIRMWARE, "choice", FIRMWARES,
                  "the PICO OS build in firmware.json whose stock kernel the image takes")


class Phoenix(Headset):
    info = Info("phoenix", "PICO 4", "Qualcomm Snapdragon XR2 (SM8250)",
                "Valve's kernel 7.2 in boot; the maintenance stage 1 on its stock kernel 4.19 in recovery")

    def commands(self) -> Dict[str, Command]:
        return {
            "build": Command("image", "build the stage-1 boot image from a stock boot.img, OTA zip or URL", True),
            "inspect": Command("image", "analyse a stock boot image and its kernel configuration", True),
            "match": Command("image", "find the firmware.json entry for a headset's fingerprint", True),
            "stock": Command("stock", "the stock firmware's partitions from PICO's public OTA"),
            "device": Command("device", "identity, EDL backups, engineering ABL, unlock and restore"),
            "hwtest": Command("hardware_test", "preflight and the bounded acceptance runner"),
            "dev": Command("devloop", "the development loop: maintenance stage 1, install, checks"),
            "cycle": Command("cycle", "unattended acceptance cycle that ends on stock boot and ABL"),
        }

    def steps(self) -> Sequence[Step]:
        build = "/var/tmp/pico4-userspace-build"
        archive = "out/userspace/pico4-userspace.tar.zst"
        system = "out/userspace/pico4-system.img"
        return (
            tools_step(),
            Step("assets", "Stock firmware and assets", "Image",
                 ("scripts/extract-assets.sh", "{firmware}", "{stock}"),
                 "PICO's public OTA (about 11 GB, kept in the stock folder), then every file the userspace "
                 "build takes from it into assets/*-VERSION.",
                 (_firmware, Param("stock", "Stock folder (Linux)", "/var/tmp/pico4-stock/" + FIRMWARE, "path")),
                 script=True, linux_root=True),
            Step("stage1", "Stage-1 boot image", "Image",
                 ("build", "--firmware", "{firmware}"),
                 "The Alpine initramfs with the stock kernel: out/pico4-stage1-FIRMWARE.img.", (_firmware,)),
            Step("userspace", "Userspace archive", "Image",
                 ("scripts/build-userspace.sh", "{build_root}", "{output}",
                  "--gpu-firmware", "assets/graphics-firmware-{version}", "--sensor-data", "assets/sensor-data-{version}",
                  "--wlan", "assets/wlan-{version}", "--audio", "assets/audio-{version}",
                  "--stock-slam", "assets/stock-slam-{version}", "{refresh}"),
                 "Holo Core and the Frame's packages with this headset's adaptation; incremental.",
                 (Param("build_root", "Build folder (Linux)", build, "path"),
                  Param("output", "Output folder", "out/userspace", "path"),
                  Param("version", "Asset version", FIRMWARE.split("-")[0], help="the assets/*-VERSION folders"),
                  Param("refresh", "Refresh package databases", "0", "flag", option="--refresh")),
                 script=True, linux_root=True),
            Step("system", "System image for super", "Image",
                 ("scripts/build-system-image.sh", "{archive}", "{image}"),
                 "The installed system's read-only ext4 image from the userspace archive.",
                 (Param("archive", "Userspace archive", archive, "path"),
                  Param("image", "System image", system, "path")),
                 script=True, linux_root=True),
            Step("dev-image", "Dev image (maintenance stage 1)", "Image", ("dev", "image"),
                 "out/dev/dev-boot.img, the whole-partition recovery image and install-boot.img from the "
                 "stage-1 image."),
            Step("kernel", "Valve's kernel", "Image",
                 ("scripts/build-kernel-step.sh", "{recovery}", "{stock}", "{work}"),
                 "Valve's public source package (2.5 GB), the PICO 4's patches and drivers, into out/kernel. "
                 "Needs the dev image's step first: it caches the stock recovery.",
                 (Param("recovery", "Stock recovery image (or its folder)", "cache/stock", "path"),
                  Param("stock", "Stock folder (Linux)", "/var/tmp/pico4-stock/" + FIRMWARE, "path"),
                  Param("work", "Build folder (Linux)", "/var/tmp/pico4-kernel-build", "path")),
                 script=True, linux_root=True),
            Step("boot-image", "Boot image", "Image", ("dev", "boot-image"),
                 "out/kernel/valve-boot.img: Valve's kernel with stage 1, the keys enrolled by the dev image."),
            Step("unlock-files", "Unlock files", "Headset", ("device", "fetch-unlock-files"),
                 "The engineering ABL and Firehose loader from PICO's public downloads, checked by hash."),
            Step("preflight", "Preflight on PICO OS", "Headset", ("hwtest", "preflight", "{serial}"),
                 "Runs the stage-1 image's programs on the real kernel from adb shell, without root, before any "
                 "bootloader work; PICO OS keeps running. Takes the newest rootfs tar in out/.",
                 (Param("serial", "ADB serial", "", help="empty: the only authorized device"),)),
            Step("stage-abl", "Enter development mode: engineering ABL", "Headset", ("dev", "stage-abl"),
                 "From PICO OS: the engineering ABL through EDL, checked against the backup.", writes=True),
            Step("flash", "Flash the dev image", "Headset", ("dev", "flash"),
                 "Writes the dev image to recovery (fastboot, verified by readback) and starts it.", writes=True),
            Step("connect", "Connect to stage 1", "Headset", ("dev", "connect"),
                 "The USB network link (Windows: NCM initialization) and the stage-1 login."),
            Step("recovery", "Maintenance stage 1", "Headset", ("dev", "recovery"),
                 "Every boot stays in the maintenance stage 1 (recovery) until \"Normal boot\"."),
            Step("install", "Install the system", "Headset", ("dev", "install", "{system}", "{format_data}"),
                 "From the maintenance stage 1: boot, super and the data partition; then starts it.",
                 (Param("system", "System image", system, "path"),
                  Param("format_data", "Format the data partition", "0", "flag", option="--format-data")),
                 writes=True),
            Step("installed", "Connect to the installed system", "Headset", ("dev", "installed"),
                 "Reconnects to the installed system after a reboot."),
            Step("check", "Acceptance checks", "Headset", ("dev", "check"),
                 "The acceptance runner's stage-2 checks on the installed system."),
            Step("release-check", "Release checks", "Headset", ("dev", "release-check"),
                 "Everything automated of docs/release-checklist.md, with a result file in out/release."),
            Step("support-bundle", "Support bundle", "Headset", ("dev", "support-bundle"),
                 "The logs a bug report needs, identifiers masked, into out/support."),
            Step("reboot", "Reboot", "Headset", ("dev", "reboot"),
                 "What misc and boot select, also from fastboot."),
            Step("normal", "Normal boot", "Headset", ("dev", "normal"),
                 "Clears the maintenance request in misc: the installed system starts again.", writes=True),
            Step("restore", "Back to stock: boot", "Headset", ("dev", "restore"),
                 "Leaves development mode: the backup's system, boot and recovery, fresh data, then PICO OS.",
                 writes=True),
            Step("restore-abl", "Back to stock: verify and restore the ABL", "Headset", ("cycle", "--restore-only"),
                 "After \"Back to stock: boot\": verified stock boot and ABL, PICO OS.", writes=True),
        )

    def dependencies(self):
        from .doctor import probes
        return probes()


HEADSET = Phoenix()
