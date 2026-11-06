# SPDX-License-Identifier: Unlicense
"""PICO OS's shutdown at a low battery (docs/cooling.md#hardware-limits)."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import unittest
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "userspace/rootfs/usr/libexec/pico4/battery-guard"
_loader = importlib.machinery.SourceFileLoader("battery_guard", str(PATH))
_spec = importlib.util.spec_from_loader("battery_guard", _loader)
assert _spec is not None
tool = importlib.util.module_from_spec(_spec)
# Dataclasses resolve their string annotations through sys.modules.
sys.modules[_spec.name] = tool
_loader.exec_module(tool)

CHARGING, DISCHARGING, NOT_CHARGING, FULL = "Charging", "Discharging", "Not charging", "Full"


class ValuesTests(unittest.TestCase):
    def test_the_stock_values(self):
        # ExtBatteryServiceImpl: batteryLevel < 1, MIN_BATTERY_LEVEL_SHUTDOWN, postDelayed 60000.
        self.assertEqual((tool.EMPTY, tool.SCREEN_OFF_LEVEL, tool.SCREEN_OFF_DELAY), (1, 4, 60.0))


class ScreenOnTests(unittest.TestCase):
    def test_powers_off_only_below_one_percent(self):
        guard = tool.Guard()
        for level in (100, 20, 5, 4, 1):
            self.assertIsNone(guard.step(level, DISCHARGING, screen_on=True, now=0.0))
        self.assertEqual(guard.step(0, DISCHARGING, screen_on=True, now=0.0), "battery at 0 %")

    def test_also_while_charging(self):
        # shouldShutdownLocked: batteryLevel < 1 whatever the charger does.
        self.assertEqual(tool.Guard().step(0, CHARGING, screen_on=True, now=0.0), "battery at 0 %, charging")
        self.assertIsNone(tool.Guard().step(1, CHARGING, screen_on=True, now=0.0))

    def test_whatever_the_cable_does(self):
        for status in (NOT_CHARGING, DISCHARGING, FULL):
            self.assertIsNotNone(tool.Guard().step(0, status, screen_on=True, now=0.0))


class ScreenOffTests(unittest.TestCase):
    def test_sixty_seconds_after_the_screen_went_off_at_four_percent(self):
        guard = tool.Guard()
        self.assertIsNone(guard.step(4, DISCHARGING, screen_on=True, now=100.0))
        self.assertIsNone(guard.step(4, DISCHARGING, screen_on=False, now=110.0))
        self.assertIsNone(guard.step(4, DISCHARGING, screen_on=False, now=169.9))
        self.assertIn("screen off for 60 s", guard.step(4, DISCHARGING, screen_on=False, now=170.0))

    def test_the_screen_on_again_cancels_it(self):
        guard = tool.Guard()
        guard.step(4, DISCHARGING, screen_on=False, now=110.0)
        self.assertIsNone(guard.step(4, DISCHARGING, screen_on=True, now=120.0))
        self.assertIsNone(guard.step(4, DISCHARGING, screen_on=True, now=200.0))

    def test_above_four_percent_nothing_is_due(self):
        guard = tool.Guard()
        guard.step(5, DISCHARGING, screen_on=False, now=110.0)
        self.assertIsNone(guard.step(5, DISCHARGING, screen_on=False, now=500.0))

    def test_a_level_that_falls_to_four_with_the_screen_off(self):
        guard = tool.Guard()
        guard.step(6, DISCHARGING, screen_on=False, now=110.0)
        self.assertIsNone(guard.step(5, DISCHARGING, screen_on=False, now=120.0))
        self.assertIn("was 6 %", guard.step(4, DISCHARGING, screen_on=False, now=130.0))

    def test_the_same_level_as_at_screen_off_waits_for_the_delay(self):
        guard = tool.Guard()
        guard.step(3, DISCHARGING, screen_on=False, now=110.0)
        self.assertIsNone(guard.step(3, DISCHARGING, screen_on=False, now=120.0))
        self.assertIn("was 3 %", guard.step(2, DISCHARGING, screen_on=False, now=130.0))

    def test_not_while_charging(self):
        guard = tool.Guard()
        self.assertIsNone(guard.step(4, CHARGING, screen_on=False, now=110.0))
        self.assertIsNone(guard.step(3, CHARGING, screen_on=False, now=500.0))

    def test_screen_off_while_charging_sets_no_delay(self):
        guard = tool.Guard()
        guard.step(4, CHARGING, screen_on=False, now=110.0)
        # Unplugged later: no delay was posted, as on PICO OS; a falling level still counts.
        self.assertIsNone(guard.step(4, DISCHARGING, screen_on=False, now=500.0))
        self.assertIsNotNone(guard.step(3, DISCHARGING, screen_on=False, now=505.0))


class CheckTests(unittest.TestCase):
    @staticmethod
    def reader(**values: str) -> Callable[[str], str | None]:
        files = {
            tool.CAPACITY: values.get("capacity"),
            tool.STATUS: values.get("status"),
            tool.BACKLIGHT: values.get("backlight"),
        }
        return files.get

    def test_reads_the_sysfs_files(self):
        self.assertIsNotNone(
            tool.check(tool.Guard(), 0.0, self.reader(capacity="0", status=DISCHARGING, backlight="0"))
        )
        self.assertIsNone(tool.check(tool.Guard(), 0.0, self.reader(capacity="1", status=CHARGING, backlight="4")))

    def test_the_backlight_is_the_screen(self):
        guard = tool.Guard()
        tool.check(guard, 0.0, self.reader(capacity="4", status=DISCHARGING, backlight="4"))
        self.assertFalse(guard.screen_on)
        tool.check(guard, 1.0, self.reader(capacity="4", status=DISCHARGING, backlight="0"))
        self.assertTrue(guard.screen_on)

    def test_no_panel_driver_is_a_screen_on(self):
        guard = tool.Guard()
        self.assertIsNone(tool.check(guard, 0.0, self.reader(capacity="3", status=DISCHARGING)))
        self.assertTrue(guard.screen_on)

    def test_an_unreadable_battery_decides_nothing(self):
        for values in ({}, {"status": DISCHARGING}, {"capacity": "0"}, {"capacity": "", "status": DISCHARGING}):
            self.assertIsNone(tool.check(tool.Guard(), 0.0, self.reader(**values)))


class UnitTests(unittest.TestCase):
    def test_unit_and_build(self):
        unit = (ROOT / "userspace/rootfs/usr/lib/systemd/system/pico4-battery-guard.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("ExecStart=/usr/libexec/pico4/battery-guard\n", unit)
        self.assertIn("WantedBy=multi-user.target\n", unit)
        build = (ROOT / "scripts/build-userspace.sh").read_text(encoding="utf-8")
        self.assertIn("pico4-battery-guard.service", build[build.index("in_t systemctl enable") :])
        data = PATH.read_bytes()
        self.assertNotIn(b"\r", data)
        self.assertIn(b"SPDX-License-Identifier: Unlicense", data)


if __name__ == "__main__":
    unittest.main()
