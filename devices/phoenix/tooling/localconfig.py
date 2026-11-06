# SPDX-License-Identifier: Unlicense
"""The PICO 4's settings in .env (PICO4_ keys; .env.example lists them).

They describe one particular headset: its serials, its EDL backup and the private files its
workflow uses (the Firehose loader, the engineering ABL, edl-ng). What every Qualcomm Android
headset names is overpressure's DeviceSettings; the engineering ABL and the firmware are the
PICO 4's own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from overpressure import localconfig as _base

from . import FIRMWARE
from .unlockfiles import ENGINEERING_ABL_SHA256

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

#: The repository's root, for cycle.py and devloop.py.
ROOT = _base.ROOT


@dataclass
class Settings(_base.DeviceSettings):
    """One PICO 4's settings: its serials, its backup and the private files its workflow uses."""

    PREFIX = "PICO4_"

    engineering_abl: str = "assets/engineering-abl.elf"
    engineering_abl_sha256: str = ENGINEERING_ABL_SHA256
    firmware: str = FIRMWARE


def load(path: Path | None = None, environ: Mapping[str, str] | None = None) -> Settings:
    """Read the PICO 4's settings from @p path (.env by default) and @p environ (the process's)."""
    return _base.load(Settings, path, environ)
