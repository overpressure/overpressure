# SPDX-License-Identifier: Unlicense
"""This computer's power: on mains, or on its battery and how full it is.

A write to the headset cut off halfway needs its way back, so whatever touches the headset refuses
to start on a computer that runs on its battery below MIN_BATTERY percent (overpressure/disclaimer.py),
and the installer asks again before each task that writes. A computer without a battery, or one
whose state cannot be read, goes on. Windows: GetSystemPowerStatus; Linux: /sys/class/power_supply;
macOS: pmset -g batt.
"""

from __future__ import annotations

import ctypes
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

#: The least battery charge a computer off mains may have for a run that touches the headset.
MIN_BATTERY = 30
SYSFS = Path("/sys/class/power_supply")
UNKNOWN = 255  # GetSystemPowerStatus's value for unknown
NO_BATTERY = 128  # GetSystemPowerStatus's BatteryFlag bit: no system battery


@dataclass(frozen=True)
class Power:
    """What a computer runs on: a battery or not, mains or not (None: unknown), the battery's charge."""

    battery: bool = False
    mains: bool | None = None
    percent: int | None = None


def from_windows(ac_line: int, flag: int, percent: int) -> Power:
    """Return the power from GetSystemPowerStatus's ACLineStatus, BatteryFlag and BatteryLifePercent."""
    battery = flag != UNKNOWN and not flag & NO_BATTERY
    mains = None if ac_line == UNKNOWN else ac_line == 1
    return Power(battery, mains, None if percent == UNKNOWN else percent)


def _windows() -> Power:
    class Status(ctypes.Structure):
        _fields_ = (
            ("ACLineStatus", ctypes.c_ubyte),
            ("BatteryFlag", ctypes.c_ubyte),
            ("BatteryLifePercent", ctypes.c_ubyte),
            ("SystemStatusFlag", ctypes.c_ubyte),
            ("BatteryLifeTime", ctypes.c_ulong),
            ("BatteryFullLifeTime", ctypes.c_ulong),
        )

    status = Status()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):  # Windows only
        return Power()
    return from_windows(status.ACLineStatus, status.BatteryFlag, status.BatteryLifePercent)


def from_sysfs(root: Path = SYSFS) -> Power:
    """Return the power from Linux's power supplies: a system battery (not a device's) and mains or USB online."""
    battery, mains, percent = False, False, None
    for supply in sorted(root.glob("*")):

        def read(name: str, folder: Path = supply) -> str:
            try:
                return (folder / name).read_text(encoding="utf-8").strip()
            except OSError:
                return ""

        kind = read("type")
        if kind == "Battery" and read("scope") != "Device":
            battery = True
            capacity = read("capacity")
            if capacity.isdigit():
                percent = int(capacity) if percent is None else min(percent, int(capacity))
        elif kind in ("Mains", "USB", "USB_C", "USB_PD") and read("online") == "1":
            mains = True
    return Power(battery, mains if battery else None, percent)


def from_pmset(output: str) -> Power:
    """Return the power from macOS's pmset -g batt."""
    found = re.search(r"(\d+)%", output)
    if "InternalBattery" not in output:
        return Power(mains="'AC Power'" in output or None)
    return Power(battery=True, mains="'AC Power'" in output, percent=int(found.group(1)) if found else None)


def read() -> Power:
    """Return this computer's power; unknown parts stay None."""
    try:
        if sys.platform == "win32":
            return _windows()
        if sys.platform == "darwin":
            output = subprocess.run(  # a fixed command
                ["pmset", "-g", "batt"],  # noqa: S607  # macOS's own pmset from PATH
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            ).stdout
            return from_pmset(output)
        return from_sysfs()
    except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
        return Power()


def problem(found: Power, minimum: int = MIN_BATTERY) -> str:
    """Return why FOUND is too little for a write to the headset, or "" when it is enough."""
    if not found.battery or found.mains or found.percent is None or found.percent >= minimum:
        return ""
    return (
        f"This computer runs on its battery at {found.percent} %. Plug in its charger (or charge it to "
        f"{minimum} %) before anything touches the headset: a write cut off halfway needs its way back."
    )


def battery_problem() -> str:
    """Return why this computer's power is too little for a write to the headset, or ""."""
    return problem(read())
