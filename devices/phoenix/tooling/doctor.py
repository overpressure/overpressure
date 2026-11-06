# SPDX-License-Identifier: Unlicense
"""The PICO 4's checks for python -m overpressure doctor.

They cover its settings and private files, the tools that talk to it, the Windows USB link, the
Linux side of its builds, and the extracted assets. What every Qualcomm Android headset checks
is overpressure.doctor's; the identity, the engineering ABL, the build's tools and the assets
are the PICO 4's.

Like every doctor check they only look: version queries, `command -v`, file sizes and the
small files' hashes. The EDL backup's images are not hashed here (gigabytes); every write checks
them against the manifest anyway.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from overpressure import REPO, backup, doctor
from overpressure.doctor import APT, HEADSET, MISSING, OK, WARNING, Check, Host, linux_commands

from . import FIRMWARE, device, localconfig

if TYPE_CHECKING:
    from pathlib import Path

    from .localconfig import Settings

EDL = "EDL (development mode, way back)"
BUILD = "userspace and system image (Linux, as root)"
KERNEL = "Valve's kernel (Linux, as root)"
ASSETS = "userspace build: extracted stock files"
FIRMWARE_VERSION = FIRMWARE.split("-")[0]
#: assets/<name>-VERSION and the extraction script that writes it.
ASSET_KINDS = {
    "graphics-firmware": "extract-graphics-firmware.sh",
    "sensor-data": "extract-sensor-data.sh",
    "wlan": "extract-wlan.sh",
    "audio": "extract-audio.sh",
    "stock-slam": "extract-stock-slam.sh",
}
BACKUP_FIX = "python -m overpressure device backup (docs/device-preparation.md)"


def settings_checks(host: Host, settings: Settings | None = None, root: Path = REPO) -> list[Check]:
    """Check .env, the serials, the identity, the EDL backup and the private files EDL needs."""
    s = settings or localconfig.load()
    checks = [doctor.env_check(root, HEADSET), *doctor.serial_checks(s, HEADSET, EDL)]
    identity = s.path("identity")
    try:
        record = device.read_identity(identity)
        checks.append(Check("identity", HEADSET, OK, f"{identity.name}: {record['fingerprint'].split('/')[3]}"))
    except (OSError, ValueError, KeyError, IndexError) as exc:
        checks.append(
            Check(
                "identity",
                HEADSET,
                MISSING,
                str(exc)[:120],
                "python -m overpressure device probe (docs/device-preparation.md)",
            )
        )
    checks.append(backup_check(s.path("backup")))
    checks.append(doctor.loader_check(s, EDL, "the headset's signed Firehose loader (docs/device-preparation.md)"))
    abl = s.path("engineering_abl")
    if not abl.is_file():
        checks.append(Check("engineering ABL", EDL, MISSING, f"no {abl}", "docs/device-preparation.md"))
    elif s.engineering_abl_sha256 and backup.digest(abl)["sha256"] != s.engineering_abl_sha256.lower():
        checks.append(
            Check(
                "engineering ABL",
                EDL,
                MISSING,
                f"{abl.name}: sha256 differs from PICO4_ENGINEERING_ABL_SHA256",
                "the exact engineering ABL the hash names",
            )
        )
    else:
        checks.append(
            Check(
                "engineering ABL",
                EDL,
                OK if s.engineering_abl_sha256 else WARNING,
                abl.name
                + ("" if s.engineering_abl_sha256 else ": no PICO4_ENGINEERING_ABL_SHA256 to check it against"),
                "" if s.engineering_abl_sha256 else "set PICO4_ENGINEERING_ABL_SHA256 in .env",
            )
        )
    checks.append(doctor.edl_tool_check(host, s, EDL))
    return checks


def backup_check(folder: Path) -> Check:
    """Check the EDL backup: its manifest, and every file in it present at its recorded size."""
    return doctor.backup_check(folder, HEADSET, BACKUP_FIX)


def tool_checks(host: Host, settings: Settings | None = None) -> list[Check]:
    """Check adb and fastboot by their version queries."""
    return doctor.tool_checks(host, settings or localconfig.load(), HEADSET)


def ncm_checks(host: Host, settings: Settings | None = None) -> list[Check]:
    """Windows only: the Python with pyusb and libusb-package, and UsbDk behind it."""
    if host.platform != "win32":
        return []
    return doctor.ncm_checks(host, settings or localconfig.load())


def build_checks(host: Host) -> list[Check]:
    """Check the Linux side of the builds: tools, the aarch64 binfmt handler, native LLVM."""
    commands = {
        "curl": "downloads",
        "tar": "archives",
        "zstd": "archives",
        "xz": "archives",
        "chroot": "the build root",
        "python3": "the build's checks",
        "brotli": "the stock OTA's images",
        "debugfs": "the extraction",
        "mcopy": "the extraction",
        "mkfs.ext4": "the system image",
        "e2fsck": "the system image",
        "sha256sum": "checks",
    }
    packages = "curl tar zstd xz-utils coreutils python3 brotli e2fsprogs mtools"
    if host.backend == "docker" and not host.container_ready():
        return []  # the host checks name the image to build; its tools are checked once it is there
    checks = linux_commands(host, BUILD, commands, APT + packages)
    checks += linux_commands(
        host,
        KERNEL,
        {
            "aarch64-linux-gnu-gcc": "cross compiling",
            "make": "the build",
            "flex": "the build",
            "bison": "the build",
            "bc": "the build",
            "git": "the source package",
            "perl": "the build",
            "dtc": "the device tree",
        },
        APT + "gcc-aarch64-linux-gnu make flex bison bc git perl libssl-dev libelf-dev dwarves device-tree-compiler",
    )
    if not host.linux("echo ok=1"):
        return [
            Check(
                "Linux", BUILD, MISSING, "no Linux to build in", "WSL (wsl --install -d Ubuntu-24.04) or a Linux host"
            )
        ]
    return checks + doctor.arm64_build_checks(host, BUILD)


def asset_checks(_host: Host, root: Path = REPO, version: str = FIRMWARE_VERSION) -> list[Check]:
    """Check that every extraction script's assets are there (they need no host: plain files)."""
    checks = []
    for kind, script in ASSET_KINDS.items():
        folder = root / "assets" / f"{kind}-{version}"
        manifest = folder / "manifest.json"
        if manifest.is_file():
            checks.append(Check(f"assets/{kind}-{version}", ASSETS, OK, "manifest.json present"))
        else:
            checks.append(
                Check(
                    f"assets/{kind}-{version}",
                    ASSETS,
                    MISSING,
                    "not extracted",
                    f"python3 -m overpressure stock ..., then devices/phoenix/scripts/{script} "
                    f"STOCK_DIR assets/{kind}-{version} (docs/development.md#stock-firmware)",
                )
            )
    return checks


def probes() -> tuple[Callable[[Host], list[Check]], ...]:
    """Return the PICO 4's checks, each called with the host."""
    return (settings_checks, tool_checks, ncm_checks, build_checks, asset_checks)
