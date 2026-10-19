"""Local settings of the headset commands (dev, cycle), read from .env at the repository root.

.env describes one particular headset (its serials and backup) and the private files its
workflow uses, so it stays out of Git; .env.example lists the keys. Environment variables
of the same names take precedence over the file.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ".exe" if sys.platform == "win32" else ""


@dataclass
class Settings:
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
    platform_tools: str = ""
    ssh: str = "ssh"
    ssh_keygen: str = "ssh-keygen"

    @staticmethod
    def key(name: str) -> str:
        return "PICO4_" + name.upper()

    def path(self, name: str) -> Path:
        """A path setting, relative to the repository root unless absolute."""
        value = Path(getattr(self, name))
        return value if value.is_absolute() else ROOT / value

    def tool(self, name: str) -> str:
        """adb or fastboot: from PICO4_PLATFORM_TOOLS when set, else from PATH."""
        return str(Path(self.platform_tools) / (name + EXE)) if self.platform_tools else name

    def require(self, *names: str) -> None:
        missing = [self.key(n) for n in names if not getattr(self, n)]
        if missing:
            raise ValueError(f"set {', '.join(missing)} in .env (see .env.example)")


def parse(text: str) -> dict:
    """KEY=VALUE lines; blank lines and # comments are skipped, quotes around a value removed."""
    values = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not key.strip():
            raise ValueError(f".env line {number}: expected KEY=VALUE")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key.strip()] = value
    return values


def load(path: Path | None = None, environ=None) -> Settings:
    path = ROOT / ".env" if path is None else path
    environ = os.environ if environ is None else environ
    values = parse(path.read_text(encoding="utf-8")) if path.exists() else {}
    settings = Settings()
    for item in fields(Settings):
        key = Settings.key(item.name)
        value = environ.get(key, values.get(key))
        if value is not None:
            setattr(settings, item.name, value)
    return settings
