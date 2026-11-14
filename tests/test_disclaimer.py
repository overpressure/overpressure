# SPDX-License-Identifier: Unlicense
"""overpressure.disclaimer and overpressure.power: what touches the headset asks first, on enough power."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from typing import Callable

from overpressure import disclaimer, power


def answers(*lines: str) -> Callable[[str], str]:
    """Return an input() that gives LINES, then the end of the input."""
    queue = list(lines)

    def read(_prompt: str) -> str:
        if not queue:
            raise EOFError
        return queue.pop(0)

    return read


class GateTests(unittest.TestCase):
    def gate(self, environ: dict, *lines: str, battery: str = "") -> tuple[int, str]:
        out = io.StringIO()
        code = disclaimer.gate(environ=environ, read=answers(*lines), out=out, battery=lambda: battery)
        return code, out.getvalue()

    def test_yes_accepts_for_this_program_and_its_children(self):
        environ: dict[str, str] = {}
        code, text = self.gate(environ, "maybe", "y")
        self.assertEqual(code, 0)
        self.assertIn("Your warranty is now void", text)
        self.assertEqual(environ, {disclaimer.ENV: "1"})
        code, text = self.gate(environ)  # accepted: not asked again
        self.assertEqual((code, text), (0, ""))

    def test_no_or_no_answer_does_nothing(self):
        environ: dict[str, str] = {}
        code, text = self.gate(environ, "n")
        self.assertEqual(code, 1)
        self.assertIn(disclaimer.DECLINED, text)
        code, text = self.gate(environ)
        self.assertEqual(code, 2)
        self.assertIn(disclaimer.FLAG, text)
        self.assertEqual(environ, {})

    def test_too_little_battery_refuses_before_anything_else(self):
        code, text = self.gate({disclaimer.ENV: "1"}, battery="runs on its battery at 12 %")
        self.assertEqual(code, 1)
        self.assertIn("12 %", text)
        self.assertNotIn("warranty", text)


class PowerTests(unittest.TestCase):
    def test_windows(self):
        self.assertEqual(power.from_windows(0, 1, 25), power.Power(battery=True, mains=False, percent=25))
        self.assertEqual(power.from_windows(1, 8, 25).mains, True)
        self.assertFalse(power.from_windows(1, 128, 255).battery)  # a desktop: no system battery
        self.assertEqual(power.from_windows(255, 255, 255), power.Power(battery=False, mains=None, percent=None))

    def test_linux(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)

            def supply(name: str, **files: str) -> None:
                (root / name).mkdir()
                for key, value in files.items():
                    (root / name / key).write_text(value + "\n", encoding="utf-8")

            self.assertEqual(power.from_sysfs(root), power.Power())  # no supplies: a desktop
            supply("hidpp_battery_0", type="Battery", scope="Device", capacity="5")  # a mouse's
            self.assertFalse(power.from_sysfs(root).battery)
            supply("BAT0", type="Battery", capacity="22", status="Discharging")
            supply("AC", type="Mains", online="0")
            self.assertEqual(power.from_sysfs(root), power.Power(battery=True, mains=False, percent=22))
            (root / "AC" / "online").write_text("1\n", encoding="utf-8")
            self.assertTrue(power.from_sysfs(root).mains)

    def test_macos(self):
        on_battery = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t17%; discharging; 1:02 remaining\n"
        self.assertEqual(power.from_pmset(on_battery), power.Power(battery=True, mains=False, percent=17))
        self.assertTrue(power.from_pmset(on_battery.replace("Battery Power", "AC Power")).mains)
        self.assertFalse(power.from_pmset("Now drawing from 'AC Power'\n").battery)

    def test_thirty_percent_on_battery_or_mains_go_on(self):
        self.assertIn("29 %", power.problem(power.Power(battery=True, mains=False, percent=29)))
        for fine in (
            power.Power(battery=True, mains=False, percent=30),
            power.Power(battery=True, mains=True, percent=5),
            power.Power(battery=False),
            power.Power(battery=True, mains=None, percent=None),
        ):
            self.assertEqual(power.problem(fine), "", fine)


if __name__ == "__main__":
    unittest.main()
