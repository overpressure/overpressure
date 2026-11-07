# SPDX-License-Identifier: Unlicense
"""The PICO 4's checks for python -m overpressure doctor.

They cover its settings and private files, the tools that talk to it, the Windows USB link and
the extracted assets. Its builds' Linux side (the userspace, the system image and the kernel) runs
in the build container, which overpressure.doctor checks for every headset; the stock firmware and
the assets are Python on any system. What every Qualcomm Android headset checks is
overpressure.doctor's; the identity, the engineering ABL and the assets are the PICO 4's.

Like every doctor check they only look: version queries, file sizes and the small files' hashes.
The EDL backup's images are not hashed here (gigabytes); every write checks them against the
manifest anyway.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from overpressure import REPO, backup, doctor
from overpressure.doctor import HEADSET, MISSING, OK, WARNING, Check, Host

from . import FIRMWARE, assets, device, localconfig

if TYPE_CHECKING:
    from pathlib import Path

    from .localconfig import Settings

EDL = "EDL (development mode, way back)"
ASSETS = "userspace build: extracted stock files"
FIRMWARE_VERSION = FIRMWARE.split("-")[0]
#: assets/<name>-VERSION, which python -m overpressure assets writes.
ASSET_KINDS = tuple(assets.KINDS)
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


def asset_checks(_host: Host, root: Path = REPO, version: str = FIRMWARE_VERSION) -> list[Check]:
    """Check that every kind of assets is there (they need no host: plain files)."""
    checks = []
    for kind in ASSET_KINDS:
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
                    f"python -m overpressure assets {kind} (docs/development.md#stock-firmware)",
                )
            )
    return checks


def probes() -> tuple[Callable[[Host], list[Check]], ...]:
    """Return the PICO 4's checks, each called with the host."""
    return (settings_checks, tool_checks, ncm_checks, asset_checks)
