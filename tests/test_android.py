"""overpressure.android: adb and fastboot run fail-closed, the device choice, the bootloader's state."""
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from overpressure import android


class AndroidToolTests(unittest.TestCase):
    def test_unlock_is_fail_closed(self):
        self.assertTrue(android.unlocked("(bootloader) unlocked: yes"))
        self.assertTrue(android.unlocked("(bootloader) Device unlocked: true\r\n"))
        self.assertTrue(android.unlocked("(bootloader) Device critical unlocked: true", True))
        for output in ("", "FAILED (unknown command)", "unlocked: no", "unlocked: yes\nunlocked: no",
                       "Device critical unlocked: true", "unlocked: yesterday"):
            self.assertFalse(android.unlocked(output))

    def test_external_failures(self):
        for code, output in ((1, ""), (0, "FAILED (remote error)"), (0, "ERROR: missing partition")):
            with patch.object(android.subprocess, "run", return_value=subprocess.CompletedProcess([], code, "", output)):
                with self.assertRaises(ValueError):
                    android.run(["fake"])
        with patch.object(android.subprocess, "run", side_effect=subprocess.TimeoutExpired("fake", 60)):
            with self.assertRaises(subprocess.TimeoutExpired):
                android.run(["fake"])

    def test_device_selection(self):
        listing = lambda argv: "List of devices attached\na device\nb unauthorized"
        with self.assertRaises(ValueError):
            android.select_device("adb", None, listing)
        with self.assertRaises(ValueError):
            android.select_device("adb", "b", listing)
        self.assertEqual(android.select_device("adb", "a", listing), "a")

    def test_bare_edl_name_stays_a_path_lookup(self):
        self.assertEqual(android.executable("edl-ng"), "edl-ng")
        self.assertEqual(android.executable("cache/edl-ng/edl-ng.exe"), str(Path("cache/edl-ng/edl-ng.exe").resolve()))

    def test_fastboot_variables(self):
        self.assertEqual(android.fastboot_value("(bootloader) partition-size:boot: 0x10000\n", "partition-size:boot"),
                         "0x10000")
        for output in ("", "(bootloader) a: 1\n(bootloader) a: 2"):
            with self.assertRaises(ValueError):
                android.fastboot_value(output, "a")


if __name__ == "__main__":
    unittest.main()
