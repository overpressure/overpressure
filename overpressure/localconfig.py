# SPDX-License-Identifier: Unlicense
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
from typing import TYPE_CHECKING, ClassVar, TypeVar

from . import REPO, tools

if TYPE_CHECKING:
    from collections.abc import Mapping

ROOT = REPO
EXE = ".exe" if sys.platform == "win32" else ""
QUOTE_PAIR = 2  # a quoted value is at least its two quotes

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
        """Return the .env key of setting NAME: the prefix and the name in capitals."""
        return cls.PREFIX + name.upper()

    def path(self, name: str) -> Path:
        """Return a path setting, relative to the repository root unless absolute."""
        value = Path(getattr(self, name))
        return value if value.is_absolute() else ROOT / value

    def tool(self, name: str) -> str:
        """Return adb or fastboot to run.

        From the platform_tools setting when set, else the fetched platform-tools (python -m
        overpressure tools), else from PATH.
        """
        if self.platform_tools:
            return str(Path(self.platform_tools) / (name + EXE))
        fetched = tools.program(name)
        return str(fetched) if fetched else name

    def require(self, *names: str) -> None:
        """Raise unless every setting of NAMES is set, naming the missing keys."""
        missing = [self.key(n) for n in names if not getattr(self, n)]
        if missing:
            msg = f"set {', '.join(missing)} in .env (see .env.example)"
            raise ValueError(msg)


def parse(text: str) -> dict:
    """Return the KEY=VALUE lines; blank lines and # comments are skipped, quotes around a value removed."""
    values = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not key.strip():
            msg = f".env line {number}: expected KEY=VALUE"
            raise ValueError(msg)
        value = value.strip()
        if len(value) >= QUOTE_PAIR and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key.strip()] = value
    return values


# The default is the bound of S: sound, but ty does not accept a default for a type[S] parameter.
def load(cls: type[S] = Settings, path: Path | None = None, environ: Mapping[str, str] | None = None) -> S:  # ty: ignore[invalid-parameter-default]
    """Return the settings of class @p cls from .env and the environment."""
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
