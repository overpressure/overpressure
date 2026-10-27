"""The PICO 4's settings in .env (PICO4_ keys; .env.example lists them).

They describe one particular headset: its serials, its EDL backup and the private files its
workflow uses (the Firehose loader, the engineering ABL, edl-ng).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from overpressure import localconfig as _base
from overpressure.localconfig import EXE, ROOT, parse

__all__ = ["EXE", "ROOT", "Settings", "load", "parse"]


@dataclass
class Settings(_base.Settings):
    PREFIX = "PICO4_"

    adb_serial: str = ""
    fastboot_serial: str = ""
    edl_serial: str = ""
    identity: str = "backup/device.json"
    backup: str = "backup/stock"
    loader: str = "assets/firehose.elf"
    engineering_abl: str = "assets/engineering-abl.elf"
    engineering_abl_sha256: str = ""
    edl: str = "cache/edl-ng/edl-ng" + EXE
    firmware: str = "5.13.8-global-sek"
    ncm_python: str = ""


def load(path: Path | None = None, environ=None) -> Settings:
    return _base.load(Settings, path, environ)
