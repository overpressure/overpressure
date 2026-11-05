"""Host tools overpressure fetches itself: pinned versions, checked by SHA-256, kept in cache/tools.

    python -m overpressure tools [NAME...]     fetch what is missing (all of this host's by default)

- platform-tools: Google's adb and fastboot (the versioned archives of dl.google.com).
- edl-ng: the EDL client (github.com/strongtz/edl-ng, MIT), its release for this host.
- ncm-venv (Windows): a Python environment with pyusb and libusb-package for the USB network
  link's initialization (overpressure/windows_ncm.py), installed by pip with every file's hash
  pinned (--require-hashes).

A setting that names a tool (PICO4_PLATFORM_TOOLS, PICO4_EDL, PICO4_NCM_PYTHON for the PICO 4)
takes precedence; without it the fetched copy is used, and the tool's name on PATH last. Nothing
is fetched behind the user's back: only this command (or the window's step) downloads.
"""
from __future__ import annotations

import hashlib
import io
import platform
import shutil
import stat
import subprocess
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import REPO

CACHE = REPO / "cache" / "tools"
EXE = ".exe" if sys.platform == "win32" else ""


@dataclass(frozen=True)
class Archive:
    url: str
    sha256: str


@dataclass(frozen=True)
class Tool:
    name: str
    version: str
    archives: Dict[str, Archive]           #: host key (see host_key) -> archive
    programs: Dict[str, str]               #: program name -> path inside the unpacked archive

    def archive(self, host: Optional[str] = None) -> Optional[Archive]:
        host = host or host_key()
        return self.archives.get(host) or self.archives.get(host.split("-")[0])

    @property
    def folder(self) -> Path:
        return CACHE / f"{self.name}-{self.version}"


def host_key() -> str:
    """windows-x86_64, linux-aarch64, darwin-arm64, ...: the archive a tool needs here."""
    system = {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux")
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "x64": "x86_64", "arm64": "arm64" if system != "linux" else "aarch64"}.get(machine,
                                                                                                         machine)
    return f"{system}-{machine}"


_GOOGLE = "https://dl.google.com/android/repository/platform-tools_r36.0.2-{}.zip"
_EDL = "https://github.com/strongtz/edl-ng/releases/download/v1.6.0/edl-ng-{}.zip"

PLATFORM_TOOLS = Tool(
    "platform-tools", "36.0.2",
    {
        # Google builds one archive per system: x86-64 (Windows on Arm runs it emulated), Linux
        # x86-64 only, and a universal macOS one.
        "windows": Archive(_GOOGLE.format("win"), "b024d4f319d6ad3004de1ba7b96a5c7c5f3512e8b14126308d598b4ab93dcead"),
        "linux-x86_64": Archive(_GOOGLE.format("linux"), "3afdea91441815ab41254193df0343d92c1b1c0d0237165c3a345c8af8891c31"),
        "darwin": Archive(_GOOGLE.format("darwin"), "106a5d31fad8c1c0c5a180d06f5779767d129d7d5edbe629005c11a85eec5b4b"),
    },
    {"adb": "platform-tools/adb" + EXE, "fastboot": "platform-tools/fastboot" + EXE})

EDL_NG = Tool(
    "edl-ng", "1.6.0",
    {
        "windows-x86_64": Archive(_EDL.format("windows-x64"), "8b935fa5525b9faaccba2640a7721053c2dfd39b02211bd7c938ccd635eeb413"),
        "windows-arm64": Archive(_EDL.format("windows-arm64"), "64d562d7fafdc0d01bd42fba073ad79bf05a463bec476f88ae520acffe7c66a7"),
        "linux-x86_64": Archive(_EDL.format("linux-x64"), "e5ebbbf291ef86d1bcb4c662461ee7065d30988ec7dcaf4cc44dbb3da227afaf"),
        "linux-aarch64": Archive(_EDL.format("linux-arm64"), "45a204b93b90b8e858c908c3954678a2aa70c379b94d1a7096d4d236955b8fab"),
        "darwin-x86_64": Archive(_EDL.format("macos-x64"), "e69d523ebb00a2ef5b7f775fca606654fb45fa21f6b4e53c74c5b5a3e237ed24"),
        "darwin-arm64": Archive(_EDL.format("macos-arm64"), "531f32420698b657e44939653a7c18c34d2196f0807f2e3ca38840c56b8449ce"),
    },
    {"edl-ng": "edl-ng" + EXE})

ARCHIVE_TOOLS = {tool.name: tool for tool in (PLATFORM_TOOLS, EDL_NG)}

#: The USB network link's Python packages for Windows, every file pinned by its hash.
NCM_REQUIREMENTS = """\
pyusb==1.3.1 \\
    --hash=sha256:bf9b754557af4717fe80c2b07cc2b923a9151f5c08d17bdb5345dac09d6a0430 \\
    --hash=sha256:3af070b607467c1c164f49d5b0caabe8ac78dbed9298d703a8dbf9df4052d17e
libusb-package==1.0.30.0 \\
    --hash=sha256:90808da724c8939a333d931d0c7226372ca5fefd98e2f2f3ef79fd918aa37522 \\
    --hash=sha256:79728146e1f01e525786900b2ccd8298a8eca53cd94b37fb0885e56aa6f9d60a \\
    --hash=sha256:2b98784bac3bedda7e95bb5039dd0eded84b02f36110e4d5d607375366c61090 \\
    --hash=sha256:4ad25f8d254bbbdd234446d5580b24d62d59bb0e690d8090457dac347f044437 \\
    --hash=sha256:c71b5f8c65b286425c02c7d624eee1fd7f08bfb1dfa492ddc20e27f06fee3829 \\
    --hash=sha256:f502ad5a0527b8c0431de817662325c88a1bba2cc334173665b04ad168d7b6d3
importlib-resources==7.1.0 ; python_version >= "3.10" \\
    --hash=sha256:1bd7b48b4088eddb2cd16382150bb515af0bd2c70128194392725f82ad2c96a1
importlib-resources==6.5.2 ; python_version < "3.10" \\
    --hash=sha256:789cfdc3ed28c78b67a06acb8126751ced69a3d5f79c095a98298cd8a760ccec
zipp==3.23.0 ; python_version < "3.10" \\
    --hash=sha256:071652d6115ed432f5ce1d34c336c0adfd6a884660d1e9712a256d3d3bd4b14e
"""
NCM_VENV = CACHE / ("ncm-venv-" + hashlib.sha256(NCM_REQUIREMENTS.encode()).hexdigest()[:12])


def _marker(tool: Tool) -> Path:
    return tool.folder / ".complete"


def installed(tool: Tool) -> bool:
    archive = tool.archive()
    return archive is not None and _marker(tool).is_file() and _marker(tool).read_text().strip() == archive.sha256


def program(name: str) -> Optional[Path]:
    """A fetched program (adb, fastboot, edl-ng), or None when it is not fetched."""
    for tool in ARCHIVE_TOOLS.values():
        if name in tool.programs and installed(tool):
            path = tool.folder / tool.programs[name]
            return path if path.is_file() else None
    return None


def ncm_python() -> Optional[Path]:
    """The fetched NCM venv's Python, or None."""
    python = NCM_VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    return python if (NCM_VENV / ".complete").is_file() and python.is_file() else None


def download(url: str, sha256: str, log: Callable[[str], None] = print,
             opener: Callable = urllib.request.urlopen) -> bytes:
    """@p url's content, which must hash to @p sha256 (else ValueError; nothing is kept)."""
    log(f">> {url}")
    with opener(url, timeout=60) as response:
        data = response.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha256:
        raise ValueError(f"{url}: sha256 {digest} is not the pinned {sha256}")
    log(f"   {len(data) / 1e6:.1f} MB, sha256 checked")
    return data


def fetch(tool: Tool, log: Callable[[str], None] = print, opener: Callable = urllib.request.urlopen) -> Path:
    """Downloads and unpacks @p tool for this host unless it is there; its folder."""
    archive = tool.archive()
    if archive is None:
        raise ValueError(f"no {tool.name} {tool.version} for {host_key()}")
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
                raise ValueError(f"{archive.url}: {info.filename} leaves the folder")
        zf.extractall(staging)
    for path in tool.programs.values():  # zip archives carry no execute bits
        exe = staging / path
        if not exe.is_file():
            raise ValueError(f"{archive.url} has no {path}")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    shutil.rmtree(tool.folder, ignore_errors=True)
    staging.rename(tool.folder)
    _marker(tool).write_text(archive.sha256 + "\n", encoding="utf-8")
    log(f"{tool.name} {tool.version}: {tool.folder}")
    return tool.folder


def fetch_ncm_venv(log: Callable[[str], None] = print, python: str = sys.executable,
                   run: Callable = subprocess.run) -> Path:
    """The NCM venv: a venv of @p python with the pinned packages, every file's hash checked."""
    if ncm_python() is not None:
        log(f"ncm-venv: already in {NCM_VENV}")
        return NCM_VENV
    shutil.rmtree(NCM_VENV, ignore_errors=True)
    log(f">> {python} -m venv {NCM_VENV}")
    run([python, "-m", "venv", str(NCM_VENV)], check=True)
    requirements = NCM_VENV / "requirements.txt"
    requirements.write_text(NCM_REQUIREMENTS, encoding="utf-8")
    venv_python = NCM_VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    log(">> pip install --require-hashes (pyusb, libusb-package)")
    run([str(venv_python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--only-binary",
         ":all:", "--require-hashes", "-r", str(requirements)], check=True)
    (NCM_VENV / ".complete").write_text("ok\n", encoding="utf-8")
    log(f"ncm-venv: {NCM_VENV}")
    return NCM_VENV


def wanted() -> List[str]:
    """This host's tools: the NCM venv only on Windows."""
    return ["platform-tools", "edl-ng"] + (["ncm-venv"] if sys.platform == "win32" else [])


def main(argv: Optional[List[str]] = None) -> int:
    names = list(argv or []) or wanted()
    unknown = [n for n in names if n not in (*ARCHIVE_TOOLS, "ncm-venv")]
    if unknown or "-h" in names or "--help" in names:
        print(__doc__.strip())
        return 0 if not unknown else 2
    CACHE.mkdir(parents=True, exist_ok=True)
    try:
        for name in names:
            if name == "ncm-venv":
                fetch_ncm_venv()
            else:
                fetch(ARCHIVE_TOOLS[name])
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
