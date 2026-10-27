"""Local settings of a headset's commands, read from .env at the repository root.

.env describes particular headsets (their serials and backups) and the private files their
workflows use, so it stays out of Git; .env.example lists the keys. Each headset declares its
settings as a dataclass derived from Settings, with its own key prefix (PICO4_ for the PICO 4),
so several headsets can share one .env. Environment variables of the same names take precedence
over the file.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, fields
from pathlib import Path
from typing import ClassVar, Type, TypeVar

from . import REPO

ROOT = REPO
EXE = ".exe" if sys.platform == "win32" else ""

S = TypeVar("S", bound="Settings")


@dataclass
class Settings:
    """The settings every headset shares; a headset adds its own fields and PREFIX."""

    PREFIX: ClassVar[str] = "OVERPRESSURE_"

    platform_tools: str = ""
    ssh: str = "ssh"
    ssh_keygen: str = "ssh-keygen"

    @classmethod
    def key(cls, name: str) -> str:
        return cls.PREFIX + name.upper()

    def path(self, name: str) -> Path:
        """A path setting, relative to the repository root unless absolute."""
        value = Path(getattr(self, name))
        return value if value.is_absolute() else ROOT / value

    def tool(self, name: str) -> str:
        """adb or fastboot: from the platform_tools setting when set, else the fetched
        platform-tools (python -m overpressure tools), else from PATH."""
        if self.platform_tools:
            return str(Path(self.platform_tools) / (name + EXE))
        from . import tools
        fetched = tools.program(name)
        return str(fetched) if fetched else name

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


def load(cls: Type[S] = Settings, path: Path | None = None, environ=None) -> S:
    """The settings of class @p cls from .env and the environment."""
    path = ROOT / ".env" if path is None else path
    environ = os.environ if environ is None else environ
    values = parse(path.read_text(encoding="utf-8")) if path.exists() else {}
    settings = cls()
    for item in fields(cls):
        key = cls.key(item.name)
        value = environ.get(key, values.get(key))
        if value is not None:
            setattr(settings, item.name, value)
    return settings
