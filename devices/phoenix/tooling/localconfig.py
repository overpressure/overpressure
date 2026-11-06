# SPDX-License-Identifier: Unlicense
"""The PICO 4's settings in .env (PICO4_ keys; .env.example lists them).

They describe one particular headset: its serials, its EDL backup and the private files its
workflow uses (the Firehose loader, the engineering ABL, edl-ng).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from overpressure import localconfig as _base
from overpressure import tools
from overpressure.localconfig import EXE

from . import FIRMWARE
from .unlockfiles import ENGINEERING_ABL_SHA256

if TYPE_CHECKING:
    from collections.abc import Mapping

#: The repository's root, for cycle.py and devloop.py.
ROOT = _base.ROOT


@dataclass
class Settings(_base.Settings):
    """One PICO 4's settings: its serials, its backup and the private files its workflow uses."""

    PREFIX = "PICO4_"

    adb_serial: str = ""
    fastboot_serial: str = ""
    edl_serial: str = ""
    identity: str = "backup/device.json"
    backup: str = "backup/stock"
    loader: str = "assets/firehose.elf"
    engineering_abl: str = "assets/engineering-abl.elf"
    engineering_abl_sha256: str = ENGINEERING_ABL_SHA256
    edl: str = ""  #: empty: the fetched edl-ng (python -m overpressure tools)
    firmware: str = FIRMWARE
    ncm_python: str = ""  #: empty: the fetched NCM venv on Windows
    ssh_public_key: str = ""  #: the owner's own public key file, enrolled besides this PC's key

    def edl_program(self) -> Path:
        """PICO4_EDL when set, else the fetched edl-ng, else edl-ng on PATH."""
        if self.edl:
            return self.path("edl")
        return tools.program("edl-ng") or Path("edl-ng" + EXE)

    def ncm(self) -> Path | None:
        """Return the Python for the Windows NCM initialization.

        PICO4_NCM_PYTHON when set, else the fetched NCM venv's; None when there is neither (or not
        on Windows).
        """
        if self.ncm_python:
            return self.path("ncm_python")
        return tools.ncm_python() if sys.platform == "win32" else None


def load(path: Path | None = None, environ: Mapping[str, str] | None = None) -> Settings:
    """Read the PICO 4's settings from @p path (.env by default) and @p environ (the process's)."""
    return _base.load(Settings, path, environ)
