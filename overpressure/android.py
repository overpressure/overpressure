# SPDX-License-Identifier: Unlicense
"""adb and fastboot: running them fail-closed, choosing the device, reading the bootloader's state.

Every command names its device's serial; a command counts as failed when it exits nonzero or
prints FAILED or ERROR, so a bootloader's refusal is never mistaken for success.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

#: The fields of a `devices` line that matter: the serial and the state.
DEVICE_ROW_FIELDS = 2


def run(command: list[str], timeout: int = 60) -> str:
    """Adb or fastboot, logged to stderr; a nonzero exit, FAILED or ERROR raises ValueError."""
    print(">> " + subprocess.list2cmdline(command), file=sys.stderr)
    result = subprocess.run(  # noqa: S603  # adb or fastboot with the step's arguments, no shell
        command, capture_output=True, text=True, timeout=timeout, check=False
    )
    output = result.stdout + "\n" + result.stderr
    if result.returncode or re.search(r"\b(?:FAILED|ERROR)\b", output, re.IGNORECASE):
        msg = f"Command failed ({result.returncode}): {output.strip()}"
        raise ValueError(msg)
    return output.strip()


def select_device(tool: str, serial: str | None, runner: Callable[[list[str]], str] | None = None) -> str:
    """Return the serial to use: @p serial when it is attached and usable, else the only attached device."""
    runner = runner or run
    rows = [line.split() for line in runner([tool, "devices"]).splitlines()]
    devices = {
        row[0]: row[1]
        for row in rows
        if len(row) >= DEVICE_ROW_FIELDS and row[1] in ("device", "fastboot", "unauthorized", "offline")
    }
    if serial is None:
        if len(devices) != 1:
            msg = "Connect exactly one headset or specify --serial"
            raise ValueError(msg)
        serial = next(iter(devices))
    if devices.get(serial) not in ("device", "fastboot"):
        msg = "Selected headset is missing, offline, or unauthorized"
        raise ValueError(msg)
    return serial


def unlocked(output: str, *, critical: bool = False) -> bool:
    """Tell whether fastboot's device-info or getvar output reports the bootloader unlocked.

    Every report of it must say so (fail-closed).
    """
    label = "Device critical unlocked" if critical else "(?:Device unlocked|unlocked)"
    values = re.findall(
        r"(?:^|\n)\s*(?:\(bootloader\)\s*)?" + label + r"\s*:\s*(yes|no|true|false)\s*(?=\n|$)", output, re.IGNORECASE
    )
    return bool(values) and all(v.lower() in ("yes", "true") for v in values)


def check_unlock(
    fastboot: str, serial: str, *, critical: bool = False, runner: Callable[[list[str]], str] | None = None
) -> None:
    """Raise unless the bootloader reports itself unlocked (and critical-unlocked when asked)."""
    runner = runner or run
    try:
        output = runner([fastboot, "-s", serial, "oem", "device-info"])
    except ValueError:
        if critical:
            raise
        output = runner([fastboot, "-s", serial, "getvar", "unlocked"])
    if not unlocked(output) or (critical and not unlocked(output, critical=True)):
        msg = "Unlock state is locked or unknown; refusing to continue"
        raise ValueError(msg)
    print(output)


def fastboot_value(output: str, name: str) -> str:
    """One getvar variable's value; missing or repeated raises ValueError."""
    values = re.findall(
        r"^[ \t]*(?:\(bootloader\)[ \t]*)?" + re.escape(name) + r":[ \t]*(\S+)[ \t]*\r?$", output, re.MULTILINE
    )
    if len(values) != 1:
        msg = f"Missing or ambiguous fastboot variable: {name}"
        raise ValueError(msg)
    return values[0]


def executable(name: str) -> str:
    """Return the absolute path for a relative tool path; bare names stay PATH lookups.

    Windows' CreateProcess rejects relative paths written with forward slashes.
    """
    if "/" not in name and "\\" not in name:
        return name
    return str(Path(name).resolve())
