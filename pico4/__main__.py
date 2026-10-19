"""Command line of the PICO 4 tooling: python -m pico4 COMMAND [ARGS...]

Stage-1 boot image (pico4.image):
  build      build the boot image from a stock boot.img, OTA zip or URL
  inspect    analyse a stock boot image and its kernel configuration
  match      find the firmware.json entry for a headset's fingerprint
Headset (each operates on real hardware):
  device     identity, EDL backups, engineering ABL, unlock and restore (pico4.device)
  hwtest     preflight and the bounded acceptance runner (pico4.hardware_test)
  dev        development loop on the flashed dev image (pico4.devloop)
  cycle      unattended acceptance cycle that ends on stock boot and ABL (pico4.cycle)

Run "python -m pico4 COMMAND --help" for the options of a command.
"""
from __future__ import annotations

import importlib
import sys

# Command -> (module, whether the command word is passed on as the module's subcommand).
COMMANDS = {
    "build": ("image", True),
    "inspect": ("image", True),
    "match": ("image", True),
    "device": ("device", False),
    "hwtest": ("hardware_test", False),
    "dev": ("devloop", False),
    "cycle": ("cycle", False),
}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help") or argv[0] not in COMMANDS:
        print(__doc__.strip())
        return 0 if argv and argv[0] in ("-h", "--help") else 2
    module, keep = COMMANDS[argv[0]]
    entry = importlib.import_module(f"pico4.{module}").main
    return entry(argv if keep else argv[1:])


if __name__ == "__main__":
    sys.exit(main())
