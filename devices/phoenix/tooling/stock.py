# SPDX-License-Identifier: Unlicense
"""The stock firmware's partitions from PICO's public full OTA, in place of the user's backup.

    python -m overpressure stock [--firmware NAME] OUTPUT_DIR

The assets command (tooling/assets.py) takes the stock files it needs from these images
(docs/development.md#stock-firmware). Everything they take is the same on every PICO 4 with that
firmware; the headset's own data (persist) is read on the headset at boot instead. OUTPUT_DIR
receives:

  system.img, vendor.img   the system and vendor partitions (ext4), rebuilt from the OTA's
                           brotli-compressed block images and their transfer lists
  modem.bin                firmware-update/NON-HLOS.bin (FAT): the DSP and Wi-Fi firmware
  dsp.bin                  firmware-update/dspso.bin (ext4): the DSPs' process shells
  bluetooth.bin            firmware-update/BTFM.bin (FAT): the Bluetooth firmware and NV data
  recovery.img, boot.img   the stock recovery and boot images
  manifest.json            the firmware, the OTA's URL and the sha256 of every file

The OTA members are kept under OUTPUT_DIR/download (about 3.9 GB) and fetched again only when
missing or corrupt. The brotli package decompresses the block images (the brotli command where
this Python lacks it).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable

from overpressure import ota

from . import FIRMWARE

CATALOG = Path(__file__).with_name("firmware.json")

#: Output file -> OTA member, copied as they are.
COPIED = {
    "modem.bin": "firmware-update/NON-HLOS.bin",
    "dsp.bin": "firmware-update/dspso.bin",
    "bluetooth.bin": "firmware-update/BTFM.bin",
    "recovery.img": "recovery.img",
    "boot.img": "boot.img",
}
#: Output image -> the block image's base name in the OTA.
BLOCK_IMAGES = {"system.img": "system", "vendor.img": "vendor"}


def build(firmware: str, output: Path, log: Callable[[str], None] = print) -> dict:
    """Extract the stock files of a firmware in firmware.json into @p output; return its manifest."""
    builds = ota.load_catalog(CATALOG)
    if firmware not in builds:
        msg = f"unknown --firmware {firmware!r}; known: {', '.join(builds)}"
        raise SystemExit(msg)
    return ota.extract(firmware, builds[firmware]["url"], output, COPIED, BLOCK_IMAGES, log=log)


def main(argv: list[str] | None = None) -> int:
    """Run the stock command line."""
    ap = argparse.ArgumentParser(prog="python -m overpressure stock", description=__doc__.split("\n\n")[0])
    ap.add_argument("output", type=Path)
    ap.add_argument("--firmware", default=FIRMWARE, help=f"firmware.json entry (default {FIRMWARE})")
    args = ap.parse_args(argv)
    build(args.firmware, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
