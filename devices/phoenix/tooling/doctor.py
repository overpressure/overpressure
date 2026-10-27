"""The PICO 4's checks for python -m overpressure doctor: its settings and private files, the tools
that talk to it, the Windows USB link, the Linux side of its builds, and the extracted assets.

Like every doctor check they only look: version queries, `command -v`, file sizes and the
small files' hashes. The EDL backup's images are not hashed here (gigabytes); every write checks
them against the manifest anyway.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from overpressure import REPO, backup, windows_ncm
from overpressure.doctor import MISSING, OK, WARNING, Check, Host, linux_commands, version_check

from . import device, localconfig

HEADSET = "working on the headset"
EDL = "EDL (development mode, way back)"
BUILD = "userspace and system image (Linux, as root)"
ASSETS = "userspace build: extracted stock files"
FIRMWARE_VERSION = "5.13.7"
#: assets/<name>-VERSION and the extraction script that writes it.
ASSET_KINDS = {"graphics-firmware": "extract-graphics-firmware.sh", "sensor-data": "extract-sensor-data.sh",
               "wlan": "extract-wlan.sh", "audio": "extract-audio.sh", "stock-slam": "extract-stock-slam.sh"}
APT = "in WSL/Linux as root: apt install "


def settings_checks(host: Host, settings=None, root: Path = REPO) -> List[Check]:
    s = settings or localconfig.load()
    env = root / ".env"
    checks = [Check(".env", HEADSET, OK if env.exists() else MISSING, str(env) if env.exists() else "no .env",
                    "" if env.exists() else "copy .env.example to .env and fill in the headset's serials and files")]
    for name, what in (("adb_serial", "adb devices"), ("fastboot_serial", "fastboot devices"),
                       ("edl_serial", "edl-ng's Serial Number")):
        value = getattr(s, name)
        checks.append(Check(s.key(name), HEADSET if name != "edl_serial" else EDL, OK if value else MISSING,
                            "set" if value else "empty", "" if value else f"set it in .env ({what})"))
    identity = s.path("identity")
    try:
        record = device.read_identity(identity)
        checks.append(Check("identity", HEADSET, OK, f"{identity.name}: {record['fingerprint'].split('/')[3]}"))
    except (OSError, ValueError, KeyError, IndexError) as exc:
        checks.append(Check("identity", HEADSET, MISSING, str(exc)[:120],
                            "python -m overpressure device probe (docs/device-preparation.md)"))
    checks.append(backup_check(s.path("backup")))
    loader = s.path("loader")
    checks.append(Check("Firehose loader", EDL, OK if loader.is_file() else MISSING,
                        str(loader) if loader.is_file() else f"no {loader}",
                        "" if loader.is_file() else "the headset's signed Firehose loader (docs/device-preparation.md)"))
    abl = s.path("engineering_abl")
    if not abl.is_file():
        checks.append(Check("engineering ABL", EDL, MISSING, f"no {abl}", "docs/device-preparation.md"))
    elif s.engineering_abl_sha256 and backup.digest(abl)["sha256"] != s.engineering_abl_sha256.lower():
        checks.append(Check("engineering ABL", EDL, MISSING, f"{abl.name}: sha256 differs from "
                            "PICO4_ENGINEERING_ABL_SHA256", "the exact engineering ABL the hash names"))
    else:
        checks.append(Check("engineering ABL", EDL, OK if s.engineering_abl_sha256 else WARNING, abl.name +
                            ("" if s.engineering_abl_sha256 else ": no PICO4_ENGINEERING_ABL_SHA256 to check it against"),
                            "" if s.engineering_abl_sha256 else "set PICO4_ENGINEERING_ABL_SHA256 in .env"))
    edl = s.path("edl")
    checks.append(Check("edl-ng", EDL, OK if edl.is_file() else MISSING, str(edl) if edl.is_file() else f"no {edl}",
                        "" if edl.is_file() else "edl-ng from its releases, path in PICO4_EDL"))
    return checks


def backup_check(folder: Path) -> Check:
    """The EDL backup: its manifest, and every file in it present at its recorded size."""
    manifest = folder / "manifest.json"
    fix = "python -m overpressure device backup (docs/device-preparation.md)"
    try:
        files = json.loads(manifest.read_text(encoding="utf-8"))["files"]
    except (OSError, ValueError, KeyError) as exc:
        return Check("EDL backup", HEADSET, MISSING, f"{manifest}: {exc}"[:120], fix)
    short = [name for name, entry in files.items()
             if not (folder / name).is_file() or (folder / name).stat().st_size != entry.get("size")]
    if short:
        return Check("EDL backup", HEADSET, MISSING, f"{len(short)} of {len(files)} files missing or of another size: "
                     + ", ".join(short[:3]), fix)
    return Check("EDL backup", HEADSET, OK, f"{len(files)} files (sizes; every write checks their hashes)")


def tool_checks(host: Host, settings=None) -> List[Check]:
    s = settings or localconfig.load()
    fix = ("Android platform-tools (developer.android.com/tools/releases/platform-tools); "
           "PICO4_PLATFORM_TOOLS in .env when they are not on PATH")
    return [version_check(host, "adb", [s.tool("adb"), "version"], HEADSET, fix),
            version_check(host, "fastboot", [s.tool("fastboot"), "--version"], HEADSET, fix)]


def ncm_checks(host: Host, settings=None) -> List[Check]:
    """Windows only: the Python with pyusb and libusb-package, and UsbDk behind it."""
    if host.platform != "win32":
        return []
    s = settings or localconfig.load()
    needed_for = "the USB network link on Windows"
    fix = ('a Python with pip install "pyusb==1.3.1" "libusb-package==1.0.30.0" in PICO4_NCM_PYTHON, '
           "and UsbDk installed (github.com/daynix/UsbDk releases)")
    if not s.ncm_python:
        return [Check("NCM Python", needed_for, MISSING, "PICO4_NCM_PYTHON is empty", fix)]
    code, out = host.run(windows_ncm.command(s.path("ncm_python")), 30)
    try:
        state = json.loads(out.splitlines()[-1]) if code == 0 and out else {}
    except ValueError:
        state = {}
    if state.get("status") == "ready":
        versions = ", ".join(f"{k} {v}" for k, v in (state.get("dependencies") or {}).items())
        return [Check("NCM Python and UsbDk", needed_for, OK, versions)]
    return [Check("NCM Python and UsbDk", needed_for, MISSING, (out.splitlines() or ["no answer"])[-1][:120], fix)]


def build_checks(host: Host) -> List[Check]:
    """The Linux side of the builds: tools, the aarch64 binfmt handler, native LLVM."""
    commands = {"curl": "downloads", "tar": "archives", "zstd": "archives", "xz": "archives", "chroot": "the build root",
                "python3": "the build's checks", "brotli": "the stock OTA's images", "debugfs": "the extraction",
                "mkfs.ext4": "the system image", "e2fsck": "the system image", "sha256sum": "checks"}
    packages = "curl tar zstd xz-utils coreutils python3 brotli e2fsprogs"
    checks = linux_commands(host, BUILD, commands, APT + packages)
    if not host.linux("echo ok=1"):
        return [Check("Linux", BUILD, MISSING, "no Linux to build in", "WSL (wsl --install -d Ubuntu-24.04) or a "
                      "Linux host")]
    state = host.linux("echo arch=$(uname -m); echo binfmt=$(sed -n 's/^flags: //p' "
                       "/proc/sys/fs/binfmt_misc/qemu-aarch64 2>/dev/null); "
                       "for v in 21 20 19; do [ -x /usr/lib/llvm-$v/bin/clang ] && [ -x /usr/lib/llvm-$v/bin/ld.lld ] "
                       "&& { echo llvm=/usr/lib/llvm-$v; break; }; done; true")
    if state.get("arch") == "x86_64":
        flags = state.get("binfmt", "")
        checks.append(Check("aarch64 binfmt (F flag)", BUILD, OK if "F" in flags else MISSING,
                            f"flags {flags}" if flags else "no qemu-aarch64 handler",
                            "" if "F" in flags else APT + "qemu-user-static binfmt-support"))
        llvm = state.get("llvm", "")
        checks.append(Check("clang and lld", BUILD, OK if llvm else WARNING,
                            llvm or "none: packages compile under QEMU, about ten times slower",
                            "" if llvm else APT + "clang-20 lld-20"))
    return checks


def asset_checks(host: Host, root: Path = REPO, version: str = FIRMWARE_VERSION) -> List[Check]:
    checks = []
    for kind, script in ASSET_KINDS.items():
        folder = root / "assets" / f"{kind}-{version}"
        manifest = folder / "manifest.json"
        if manifest.is_file():
            checks.append(Check(f"assets/{kind}-{version}", ASSETS, OK, "manifest.json present"))
        else:
            checks.append(Check(f"assets/{kind}-{version}", ASSETS, MISSING, "not extracted",
                                f"python3 -m overpressure stock ..., then devices/phoenix/scripts/{script} "
                                f"STOCK_DIR assets/{kind}-{version} (docs/development.md#stock-firmware)"))
    return checks


def probes():
    return (settings_checks, tool_checks, ncm_checks, build_checks, asset_checks)
