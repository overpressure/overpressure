"""The stock firmware's partitions from PICO's public full OTA, in place of the user's backup.

    python -m overpressure stock [--firmware NAME] OUTPUT_DIR

The extraction scripts (scripts/extract-*.sh) take the stock files they need from these images
(docs/development.md#stock-firmware). Everything they take is the same on every PICO 4 with that
firmware; the headset's own data (persist) is read on the headset at boot instead. OUTPUT_DIR
receives:

  system.img, vendor.img   the system and vendor partitions (ext4), rebuilt from the OTA's
                           brotli-compressed block images and their transfer lists
  modem.bin                firmware-update/NON-HLOS.bin (FAT): the DSP and Wi-Fi firmware
  dsp.bin                  firmware-update/dspso.bin (ext4): the DSPs' process shells
  recovery.img, boot.img   the stock recovery and boot images
  manifest.json            the firmware, the OTA's URL and the sha256 of every file

The OTA members are kept under OUTPUT_DIR/download (about 3.9 GB) and fetched again only when
missing or corrupt. Decompressing needs the brotli command (Debian/Ubuntu: apt install brotli).
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

from overpressure import ota

CATALOG = Path(__file__).with_name("firmware.json")
DEFAULT_FIRMWARE = "5.13.7-global-seko"

#: Output file -> OTA member, copied as they are.
COPIED = {
    "modem.bin": "firmware-update/NON-HLOS.bin",
    "dsp.bin": "firmware-update/dspso.bin",
    "recovery.img": "recovery.img",
    "boot.img": "boot.img",
}
#: Output image -> the block image's base name in the OTA.
BLOCK_IMAGES = {"system.img": "system", "vendor.img": "vendor"}


def members_needed() -> Iterable[str]:
    return ota.members_needed(COPIED, BLOCK_IMAGES)


def build(firmware: str, output: Path, location: str | None = None, log=print) -> dict:
    builds = ota.load_catalog(CATALOG)
    if firmware not in builds:
        raise SystemExit(f"unknown --firmware {firmware!r}; known: {', '.join(builds)}")
    return ota.extract(firmware, location or builds[firmware]["url"], output, COPIED, BLOCK_IMAGES, log)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m overpressure stock", description=__doc__.split("\n\n")[0])
    ap.add_argument("output", type=Path)
    ap.add_argument("--firmware", default=DEFAULT_FIRMWARE, help=f"firmware.json entry (default {DEFAULT_FIRMWARE})")
    ap.add_argument("--ota", help="a local copy of the OTA zip instead of the catalog's URL")
    args = ap.parse_args(argv)
    build(args.firmware, args.output, args.ota)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
