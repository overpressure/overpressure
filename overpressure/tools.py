# SPDX-License-Identifier: Unlicense
"""Host tools overpressure fetches itself: pinned versions, checked by SHA-256, kept in cache/tools.

    python -m overpressure tools [NAME...]     fetch what is missing (all of this host's by default)

- platform-tools: Google's adb and fastboot (the versioned archives of dl.google.com).
- edl-ng: the EDL client (github.com/strongtz/edl-ng, MIT), its release for this host.

A setting that names a tool (PICO4_PLATFORM_TOOLS, PICO4_EDL for the PICO 4)
takes precedence; without it the fetched copy is used, and the tool's name on PATH last. Nothing
is fetched behind the user's back: only this command (or the window's step) downloads.
"""

from __future__ import annotations

import hashlib
import io
import platform
import shutil
import stat
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from . import REPO

if TYPE_CHECKING:
    from pathlib import Path

CACHE = REPO / "cache" / "tools"
EXE = ".exe" if sys.platform == "win32" else ""


@dataclass(frozen=True)
class Archive:
    """A tool's archive for one host, pinned by its SHA-256."""

    url: str
    sha256: str


@dataclass(frozen=True)
class Tool:
    """A pinned host tool: its archive per host and the programs in it."""

    name: str
    version: str
    archives: dict[str, Archive]  #: host key (see host_key) -> archive
    programs: dict[str, str]  #: program name -> path inside the unpacked archive

    def archive(self, host: str | None = None) -> Archive | None:
        """Return the archive for HOST (this host by default), None if there is none."""
        host = host or host_key()
        return self.archives.get(host) or self.archives.get(host.split("-")[0])

    @property
    def folder(self) -> Path:
        """Return where the tool is unpacked."""
        return CACHE / f"{self.name}-{self.version}"


def host_key() -> str:
    """windows-x86_64, linux-aarch64, darwin-arm64, ...: the archive a tool needs here."""
    system = {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux")
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "x64": "x86_64", "arm64": "arm64" if system != "linux" else "aarch64"}.get(
        machine, machine
    )
    return f"{system}-{machine}"


_GOOGLE = "https://dl.google.com/android/repository/platform-tools_r36.0.2-{}.zip"
_EDL = "https://github.com/strongtz/edl-ng/releases/download/v1.6.0/edl-ng-{}.zip"

PLATFORM_TOOLS = Tool(
    "platform-tools",
    "36.0.2",
    {
        # Google builds one archive per system: x86-64 (Windows on Arm runs it emulated), Linux
        # x86-64 only, and a universal macOS one.
        "windows": Archive(_GOOGLE.format("win"), "b024d4f319d6ad3004de1ba7b96a5c7c5f3512e8b14126308d598b4ab93dcead"),
        "linux-x86_64": Archive(
            _GOOGLE.format("linux"), "3afdea91441815ab41254193df0343d92c1b1c0d0237165c3a345c8af8891c31"
        ),
        "darwin": Archive(_GOOGLE.format("darwin"), "106a5d31fad8c1c0c5a180d06f5779767d129d7d5edbe629005c11a85eec5b4b"),
    },
    {"adb": "platform-tools/adb" + EXE, "fastboot": "platform-tools/fastboot" + EXE},
)

EDL_NG = Tool(
    "edl-ng",
    "1.6.0",
    {
        "windows-x86_64": Archive(
            _EDL.format("windows-x64"), "8b935fa5525b9faaccba2640a7721053c2dfd39b02211bd7c938ccd635eeb413"
        ),
        "windows-arm64": Archive(
            _EDL.format("windows-arm64"), "64d562d7fafdc0d01bd42fba073ad79bf05a463bec476f88ae520acffe7c66a7"
        ),
        "linux-x86_64": Archive(
            _EDL.format("linux-x64"), "e5ebbbf291ef86d1bcb4c662461ee7065d30988ec7dcaf4cc44dbb3da227afaf"
        ),
        "linux-aarch64": Archive(
            _EDL.format("linux-arm64"), "45a204b93b90b8e858c908c3954678a2aa70c379b94d1a7096d4d236955b8fab"
        ),
        "darwin-x86_64": Archive(
            _EDL.format("macos-x64"), "e69d523ebb00a2ef5b7f775fca606654fb45fa21f6b4e53c74c5b5a3e237ed24"
        ),
        "darwin-arm64": Archive(
            _EDL.format("macos-arm64"), "531f32420698b657e44939653a7c18c34d2196f0807f2e3ca38840c56b8449ce"
        ),
    },
    {"edl-ng": "edl-ng" + EXE},
)

ARCHIVE_TOOLS = {tool.name: tool for tool in (PLATFORM_TOOLS, EDL_NG)}


def _marker(tool: Tool) -> Path:
    return tool.folder / ".complete"


def installed(tool: Tool) -> bool:
    """Tell whether TOOL is unpacked from this host's pinned archive."""
    archive = tool.archive()
    return archive is not None and _marker(tool).is_file() and _marker(tool).read_text().strip() == archive.sha256


def program(name: str) -> Path | None:
    """Return a fetched program (adb, fastboot, edl-ng), or None when it is not fetched."""
    for tool in ARCHIVE_TOOLS.values():
        if name in tool.programs and installed(tool):
            path = tool.folder / tool.programs[name]
            return path if path.is_file() else None
    return None


def download(
    url: str, sha256: str, log: Callable[[str], None] = print, opener: Callable = urllib.request.urlopen
) -> bytes:
    """@p url's content, which must hash to @p sha256 (else ValueError; nothing is kept)."""
    log(f">> {url}")
    with opener(url, timeout=60) as response:
        data = response.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha256:
        msg = f"{url}: sha256 {digest} is not the pinned {sha256}"
        raise ValueError(msg)
    log(f"   {len(data) / 1e6:.1f} MB, sha256 checked")
    return data


def fetch(tool: Tool, log: Callable[[str], None] = print, opener: Callable = urllib.request.urlopen) -> Path:
    """Download and unpack @p tool for this host unless it is there; return its folder."""
    archive = tool.archive()
    if archive is None:
        msg = f"no {tool.name} {tool.version} for {host_key()}"
        raise ValueError(msg)
    if installed(tool):
        log(f"{tool.name} {tool.version}: already in {tool.folder}")
        return tool.folder
    data = download(archive.url, archive.sha256, log, opener)
    staging = tool.folder.with_name(tool.folder.name + ".part")
    shutil.rmtree(staging, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            target = (staging / info.filename).resolve()
            if staging.resolve() not in target.parents and target != staging.resolve():
                msg = f"{archive.url}: {info.filename} leaves the folder"
                raise ValueError(msg)
        zf.extractall(staging)
    for path in tool.programs.values():  # zip archives carry no execute bits
        exe = staging / path
        if not exe.is_file():
            msg = f"{archive.url} has no {path}"
            raise ValueError(msg)
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    shutil.rmtree(tool.folder, ignore_errors=True)
    staging.rename(tool.folder)
    _marker(tool).write_text(archive.sha256 + "\n", encoding="utf-8")
    log(f"{tool.name} {tool.version}: {tool.folder}")
    return tool.folder


def main(argv: list[str] | None = None) -> int:
    """Fetch the named tools (this host's by default)."""
    names = list(argv or []) or list(ARCHIVE_TOOLS)
    unknown = [n for n in names if n not in ARCHIVE_TOOLS]
    if unknown or "-h" in names or "--help" in names:
        print(__doc__.strip())
        return 0 if not unknown else 2
    CACHE.mkdir(parents=True, exist_ok=True)
    try:
        for name in names:
            fetch(ARCHIVE_TOOLS[name])
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
