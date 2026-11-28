# SPDX-License-Identifier: Unlicense
"""A headset's settings: what overpressure records, what the owner sets, and developers' overrides.

Each headset declares its settings as a dataclass derived from Settings, with its own key prefix (PICO4_ for
the PICO 4). A headset on a stock Android bootloader derives from DeviceSettings, which has what every
Qualcomm Android headset's workflow names (serials, EDL backup, loader, tools). load() builds them in layers,
each over the one before:

- the defaults: fixed places (backup/stock, assets/...) and the tools overpressure fetches or ships;
- the records (out/records.json): what the installer finds out, such as the serials, by key. update() and
  the installer's set_setting write them; nothing else does;
- the owner's settings (settings.json): what the app's Settings page sets, the same for every headset
  (OWNER: the owner's own SSH key);
- a developer's overrides: .env in the root, read only (KEY=VALUE lines; .env.example lists overpressure's),
  then the environment. A path or a program set there replaces the fetched one.

A .env that an earlier overpressure wrote in a release's or the app's folder is migrated once (retire): the
records and the owner's settings move out of it, developers' entries go, and it is kept as .env.old. A
development checkout's .env stays as it is. load() and update() given a .env path read and write that file
alone, as tests do.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from dataclasses import dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, TypeVar

from . import CODE, REPO, tools

if TYPE_CHECKING:
    from collections.abc import Mapping

ROOT = REPO
EXE = ".exe" if sys.platform == "win32" else ""
QUOTE_PAIR = 2  # a quoted value is at least its two quotes

#: What overpressure found out (the serials, where the backup is), by key, in the root.
RECORDS = Path("out") / "records.json"
#: The owner's settings (the app's Settings page), in the root.
OWNER_FILE = Path("settings.json")
#: The settings the owner sets, by name: the same for every headset (their keys any headset's).
OWNER = ("ssh_public_key",)
#: Where a program or a private file comes from: a developer's override only, never recorded.
DEVELOPER = (
    "platform_tools",
    "ssh",
    "ssh_keygen",
    "edl",
    "ncm_python",
    "loader",
    "engineering_abl",
    "engineering_abl_sha256",
)
#: What a migrated .env is kept as.
RETIRED = ".env.old"

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
            msg = (
                f"overpressure has not recorded {', '.join(missing)} yet: the installer records it when it finds "
                "the headset (a developer sets it in the environment)"
            )
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
    ncm_python: str = ""  #: empty: this Python on Windows, which has pyusb and libusb-package
    ssh_public_key: str = ""  #: the owner's own public key file, enrolled besides this PC's key

    def edl_program(self) -> Path:
        """Return the edl setting when set, else the fetched edl-ng, else edl-ng on PATH."""
        if self.edl:
            return self.path("edl")
        return tools.program("edl-ng") or Path("edl-ng" + EXE)

    def ncm(self) -> Path | None:
        """Return the Python for the Windows NCM initialization.

        The ncm_python setting when set, else this Python when it has pyusb and libusb-package
        (pyproject.toml's dependencies); None when there is neither (or not on Windows).
        """
        if self.ncm_python:
            return self.path("ncm_python")
        if sys.platform != "win32" or not all(importlib.util.find_spec(m) for m in ("usb", "libusb_package")):
            return None
        return Path(sys.executable)


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


def _read_json(path: Path) -> dict:
    """Return the object in PATH; an empty one when it is missing or unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: Mapping[str, object]) -> None:
    """Write DATA to PATH whole: a half-written file is never read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    part.replace(path)


def records(root: Path | None = None) -> dict[str, str]:
    """Return what overpressure recorded, by key."""
    found = _read_json((ROOT if root is None else root) / RECORDS)
    return {str(k): v for k, v in found.items() if isinstance(v, str)}


def owner_settings(root: Path | None = None) -> dict:
    """Return the owner's settings file as it is (the app keeps its own settings there too)."""
    return _read_json((ROOT if root is None else root) / OWNER_FILE)


def save_owner_settings(values: Mapping[str, object], root: Path | None = None) -> None:
    """Set VALUES in the owner's settings file, keeping the rest of it."""
    path = (ROOT if root is None else root) / OWNER_FILE
    _write_json(path, {**_read_json(path), **values})


def _owner_name(key: str) -> str:
    """Return the owner setting KEY is (with any headset's prefix, or none), or ""."""
    return next((name for name in OWNER if key in (name, name.upper()) or key.endswith("_" + name.upper())), "")


def _developer(key: str) -> bool:
    """Tell whether KEY names where a program or a private file comes from (a developer's override)."""
    return key.startswith("OVERPRESSURE_") or any(key.endswith("_" + name.upper()) for name in DEVELOPER)


def record(values: Mapping[str, str], root: Path | None = None) -> None:
    """Record VALUES by key: an owner setting goes to the owner's settings, the rest to the records."""
    root = ROOT if root is None else root
    for key, value in values.items():
        if "\n" in value or "\r" in value:
            msg = f"{key}: a setting is one line"
            raise ValueError(msg)
    owner = {_owner_name(k): v for k, v in values.items() if _owner_name(k)}
    found = {k: v for k, v in values.items() if not _owner_name(k)}
    if owner:
        save_owner_settings(owner, root)
    if found:
        _write_json(root / RECORDS, {**records(root), **found})


def update(values: Mapping[str, str], path: Path | None = None, example: Path | None = None) -> None:
    """Record VALUES (record()); given a .env PATH, set KEY=VALUE lines there instead, keeping the rest.

    In a .env a key's first line takes the new value and its later lines go (no duplicates); a key that
    is not there is added at the end. A missing .env starts as a copy of .env.example (@p example)
    and each headset's devices/<codename>/env.example, so its comments explain the keys. The file
    is written in place: .env may be a hard link shared by several checkouts.
    """
    if path is None:
        record(values)
        return
    # overpressure's own template comes with its code; the headsets' with the headsets, in the data root.
    examples = [CODE / ".env.example", *sorted((ROOT / "devices").glob("*/env.example"))]
    if path.exists():
        raw = path.read_bytes().decode("utf-8")
    else:
        found = [example] if example is not None else examples
        raw = "".join(p.read_bytes().decode("utf-8") for p in found if p.exists())
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
    """Return the settings of class @p cls: the defaults, the records, the owner's, then .env and the environment.

    Given a .env @p path, that file and the environment alone. An empty value keeps the setting's default,
    as .env.example's comments say ("Empty: ..."): a .env started from it must not empty a default such as
    the engineering ABL's known hash. A record never sets where a program or a private file comes from.
    """
    environ = os.environ if environ is None else environ
    layers: list[Mapping[str, str]] = []
    if path is None:
        owner = owner_settings()
        layers.append({k: v for k, v in records().items() if not _developer(k)})
        layers.append({cls.key(n): owner[n] for n in OWNER if isinstance(owner.get(n), str)})
        path = ROOT / ".env"
    layers.append(parse(path.read_text(encoding="utf-8")) if path.exists() else {})
    layers.append(environ)
    settings = cls()
    for item in fields(cls):
        key = cls.key(item.name)
        for layer in layers:
            if layer.get(key):
                setattr(settings, item.name, layer[key])
    return settings


def _checkout(root: Path) -> bool:
    """Tell whether ROOT is a development checkout (its .env is the developer's own)."""
    return (root / ".git").exists()


def migrate(env: Path, root: Path | None = None) -> list[str]:
    """Move what an earlier overpressure kept in the .env ENV into ROOT's records and owner's settings.

    The serials and the other records go to the records, the owner's settings to theirs (neither replacing
    what ROOT has), and where a program or a private file comes from goes: the fetched or fixed ones count
    now. Return the keys moved.
    """
    root = ROOT if root is None else root
    try:
        values = parse(env.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    have, mine = records(root), owner_settings(root)

    def kept(key: str) -> bool:
        return bool(mine.get(_owner_name(key)) if _owner_name(key) else have.get(key))

    moved = {k: v for k, v in values.items() if v and not _developer(k) and not kept(k)}
    record(moved, root)
    return sorted(moved)


def retire(root: Path | None = None) -> Path | None:
    """Migrate the .env an earlier overpressure wrote in ROOT, once, and keep it as .env.old; return that.

    A development checkout's .env is the developer's: it stays.
    """
    root = ROOT if root is None else root
    env = root / ".env"
    if _checkout(root) or not env.is_file():
        return None
    migrate(env, root)
    kept = root / RETIRED
    number = 1
    while kept.exists():
        number += 1
        kept = root / f"{RETIRED}.{number}"
    env.replace(kept)
    return kept
