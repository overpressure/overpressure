# SPDX-License-Identifier: Unlicense
"""Local settings of a headset's commands, read from .env at the repository root.

.env describes particular headsets (their serials and backups) and the private files their
workflows use, so it stays out of Git; .env.example lists the keys. Each headset declares its
settings as a dataclass derived from Settings, with its own key prefix (PICO4_ for the PICO 4),
so several headsets can share one .env. Environment variables of the same names take precedence
over the file. A headset on a stock Android bootloader derives from DeviceSettings, which has what
every Qualcomm Android headset's workflow names (serials, EDL backup, loader, tools). update()
sets keys in .env and keeps the rest of it: the installer writes the serials it finds there.
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


@dataclass
class DeviceSettings(Settings):
    """The settings of a headset on a stock Android bootloader: its serials, its EDL backup, the EDL tools.

    Every Qualcomm Android headset's workflow names these; a headset derives its own settings
    from this class with its PREFIX and adds what only it needs.
    """

    adb_serial: str = ""
    fastboot_serial: str = ""
    edl_serial: str = ""
    identity: str = "backup/device.json"
    backup: str = "backup/stock"
    loader: str = "assets/firehose.elf"
    edl: str = ""  #: empty: the fetched edl-ng (python -m overpressure tools)
    ncm_python: str = ""  #: empty: the fetched NCM venv on Windows
    ssh_public_key: str = ""  #: the owner's own public key file, enrolled besides this PC's key

    def edl_program(self) -> Path:
        """Return the edl setting when set, else the fetched edl-ng, else edl-ng on PATH."""
        if self.edl:
            return self.path("edl")
        return tools.program("edl-ng") or Path("edl-ng" + EXE)

    def ncm(self) -> Path | None:
        """Return the Python for the Windows NCM initialization.

        The ncm_python setting when set, else the fetched NCM venv's; None when there is neither
        (or not on Windows).
        """
        if self.ncm_python:
            return self.path("ncm_python")
        return tools.ncm_python() if sys.platform == "win32" else None


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


def _line(key: str, value: str) -> str:
    """Return KEY=VALUE as parse reads it back: quoted when the value would not survive bare."""
    if "\n" in value or "\r" in value:
        msg = f"{key}: a value in .env is one line"
        raise ValueError(msg)
    if value == value.strip() and " #" not in value and value[:1] not in ("'", '"'):
        return f"{key}={value}"
    for quote in ('"', "'"):
        if quote not in value:
            return f"{key}={quote}{value}{quote}"
    msg = f"{key}: a value in .env cannot hold both kinds of quotes"
    raise ValueError(msg)


def update(values: Mapping[str, str], path: Path | None = None, example: Path | None = None) -> None:
    """Set KEY=VALUE lines in .env, keeping every other line and comment.

    A key's first line takes the new value and its later lines go (no duplicates); a key that is
    not there is added at the end. A missing .env starts as a copy of .env.example (@p example),
    so its comments explain the keys. The file is written in place: .env may be a hard link
    shared by several checkouts.
    """
    path = ROOT / ".env" if path is None else path
    example = ROOT / ".env.example" if example is None else example
    if path.exists():
        raw = path.read_bytes().decode("utf-8")
    else:
        raw = example.read_bytes().decode("utf-8") if example.exists() else ""
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines, done = [], set()
    for line in raw.splitlines():
        key = line.partition("=")[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
        if key in values:
            if key not in done:
                lines.append(_line(key, values[key]))
                done.add(key)
            continue
        lines.append(line)
    lines += [_line(key, value) for key, value in values.items() if key not in done]
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(newline.join(lines) + newline)


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
