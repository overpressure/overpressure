"""The PICO 4 (2022, codename phoenix, Snapdragon XR2) in overpressure.

Its host tooling: the stage-1 boot image, the stock firmware from PICO's public OTA, EDL,
fastboot and ADB, the acceptance runner, the development loop and the acceptance cycle. Pure
Python (3.9+) with the standard library only; the formats it shares with other headsets come
from the overpressure package.
"""
from __future__ import annotations

from typing import Dict, Sequence

from overpressure.headset import Command, Headset, Info, Param, Step

FIRMWARE = "5.13.7-global-seko"
FIRMWARES = ("5.13.7-global-seko", "5.13.8-global-sek")
_firmware = Param("firmware", "Firmware", FIRMWARE, "choice", FIRMWARES,
                  "the PICO OS build in firmware.json whose stock kernel the image takes")


class Phoenix(Headset):
    info = Info("phoenix", "PICO 4", "Qualcomm Snapdragon XR2 (SM8250)",
                "always on its stock Android kernel 4.19 and stock device trees")

    def commands(self) -> Dict[str, Command]:
        return {
            "build": Command("image", "build the stage-1 boot image from a stock boot.img, OTA zip or URL", True),
            "inspect": Command("image", "analyse a stock boot image and its kernel configuration", True),
            "match": Command("image", "find the firmware.json entry for a headset's fingerprint", True),
            "stock": Command("stock", "the stock firmware's partitions from PICO's public OTA"),
            "device": Command("device", "identity, EDL backups, engineering ABL, unlock and restore"),
            "hwtest": Command("hardware_test", "preflight and the bounded acceptance runner"),
            "dev": Command("devloop", "the development loop on the flashed dev image"),
            "cycle": Command("cycle", "unattended acceptance cycle that ends on stock boot and ABL"),
        }

    def steps(self) -> Sequence[Step]:
        build = "/var/tmp/pico4-userspace-build"
        archive = "out/userspace/pico4-userspace.tar.zst"
        system = "out/userspace/pico4-system.img"
        return (
            Step("stock", "Stock firmware from the OTA", "Image",
                 ("stock", "--firmware", "{firmware}", "{output}"),
                 "PICO's public OTA: the partitions the assets are extracted from (about 11 GB).",
                 (_firmware, Param("output", "Output folder", "/var/tmp/pico4-stock/" + FIRMWARE, "path")),
                 linux_root=True),
            Step("stage1", "Stage-1 boot image", "Image",
                 ("build", "--firmware", "{firmware}"),
                 "The Alpine initramfs with the stock kernel: out/pico4-poc-FIRMWARE.img.", (_firmware,)),
            Step("userspace", "Userspace archive", "Image",
                 ("scripts/build-userspace.sh", "{build_root}", "{output}",
                  "--gpu-firmware", "assets/graphics-firmware-{version}", "--sensor-data", "assets/sensor-data-{version}",
                  "--wlan", "assets/wlan-{version}", "--audio", "assets/audio-{version}",
                  "--stock-slam", "assets/stock-slam-{version}", "{refresh}"),
                 "Holo Core and the Frame's packages with this headset's adaptation; incremental.",
                 (Param("build_root", "Build folder (Linux)", build, "path"),
                  Param("output", "Output folder", "out/userspace", "path"),
                  Param("version", "Asset version", "5.13.7", help="the assets/*-VERSION folders"),
                  Param("refresh", "Refresh package databases", "0", "flag", option="--refresh")),
                 script=True, linux_root=True),
            Step("system", "System image for super", "Image",
                 ("scripts/build-system-image.sh", "{archive}", "{image}"),
                 "The installed system's read-only ext4 image from the userspace archive.",
                 (Param("archive", "Userspace archive", archive, "path"),
                  Param("image", "System image", system, "path")),
                 script=True, linux_root=True),
            Step("dev-image", "Dev image (maintenance stage 1)", "Image", ("dev", "image"),
                 "out/dev/dev-boot.img and the whole-partition recovery image from the stage-1 image."),
            Step("stage-abl", "Enter development mode: engineering ABL", "Headset", ("dev", "stage-abl"),
                 "From PICO OS: the engineering ABL through EDL, checked against the backup.", writes=True),
            Step("flash", "Flash the dev image", "Headset", ("dev", "flash", "{partition}"),
                 "Writes the dev image (fastboot, verified by readback) and starts it.",
                 (Param("partition", "Partition", "recovery", "choice", ("recovery", "boot")),), writes=True),
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
            Step("boot", "Boot a userspace archive", "Headset", ("dev", "boot", "{archive}"),
                 "From stage 1: streams the archive into RAM and switches to it (nothing is written).",
                 (Param("archive", "Userspace archive", archive, "path"),)),
            Step("check", "Acceptance checks", "Headset", ("dev", "check"),
                 "The acceptance runner's stage-2 checks on the running system."),
            Step("reboot", "Reboot into stage 1", "Headset", ("dev", "reboot"),
                 "The next attempt, also from fastboot."),
            Step("normal", "Normal boot", "Headset", ("dev", "normal"),
                 "Clears the maintenance request in misc: the installed system starts again.", writes=True),
            Step("restore", "Back to stock: boot", "Headset", ("dev", "restore"),
                 "Leaves development mode: stock boot, recovery and misc from the backup, then PICO OS.",
                 writes=True),
            Step("restore-abl", "Back to stock: verify and restore the ABL", "Headset", ("cycle", "--restore-only"),
                 "After \"Back to stock: boot\": verified stock boot and ABL, PICO OS.", writes=True),
        )


HEADSET = Phoenix()
