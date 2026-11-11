# SPDX-License-Identifier: Unlicense
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
        self.assertTrue(android.unlocked("(bootloader) Device critical unlocked: true", critical=True))
        for output in (
            "",
            "FAILED (unknown command)",
            "unlocked: no",
            "unlocked: yes\nunlocked: no",
            "Device critical unlocked: true",
            "unlocked: yesterday",
        ):
            self.assertFalse(android.unlocked(output))

    def test_external_failures(self):
        for code, output in ((1, ""), (0, "FAILED (remote error)"), (0, "ERROR: missing partition")):
            with (
                patch.object(android.subprocess, "run", return_value=subprocess.CompletedProcess([], code, "", output)),
                self.assertRaises(ValueError),
            ):
                android.run(["fake"])
        with patch.object(android.subprocess, "run", side_effect=subprocess.TimeoutExpired("fake", 60)):
            with self.assertRaises(subprocess.TimeoutExpired):
                android.run(["fake"])

    def test_device_selection(self):
        def listing(_argv: list[str]) -> str:
            return "List of devices attached\na device\nb unauthorized"

        with self.assertRaises(ValueError):
            android.select_device("adb", None, listing)
        with self.assertRaises(ValueError):
            android.select_device("adb", "b", listing)
        self.assertEqual(android.select_device("adb", "a", listing), "a")

    def test_bare_edl_name_stays_a_path_lookup(self):
        self.assertEqual(android.executable("edl-ng"), "edl-ng")
        self.assertEqual(android.executable("cache/edl-ng/edl-ng.exe"), str(Path("cache/edl-ng/edl-ng.exe").resolve()))

    def test_fastboot_variables(self):
        self.assertEqual(
            android.fastboot_value("(bootloader) partition-size:boot: 0x10000\n", "partition-size:boot"), "0x10000"
        )
        for output in ("", "(bootloader) a: 1\n(bootloader) a: 2"):
            with self.assertRaises(ValueError):
                android.fastboot_value(output, "a")

    def test_a_single_non_slotted_partition_and_its_size(self):
        inventory = (
            "(bootloader) partition-size:boot: 0x6000000\n(bootloader) partition-size:recovery_a: 0x6400000\n"
            "(bootloader) partition-size:recovery_b: 0x6400000\n(bootloader) partition-size:bootloader: 0x10\nOKAY"
        )
        self.assertEqual(android.partition_names(inventory, "boot"), ["boot"])
        self.assertEqual(android.partition_names(inventory, "recovery"), ["recovery_a", "recovery_b"])
        self.assertEqual(android.single_partition_size(inventory, "boot"), 0x6000000)
        self.assertIsNone(android.single_partition_size(inventory, "recovery"))
        self.assertIsNone(android.single_partition_size(inventory, "misc"))
        with self.assertRaises(ValueError):
            android.single_partition_size("partition-size:boot: big", "boot")

    def test_waiting_for_android_to_finish_booting(self):
        answers = ["", "0\n", "1\nVendor/device/x:1/build:user/release-keys"]
        commands = []

        def runner(command: list[str], timeout: float) -> str:
            commands.append((command, timeout))
            return answers.pop(0)

        clock = FakeClock()
        self.assertEqual(
            android.wait_booted("adb", "S1", runner, clock, system="Vendor OS"),
            "Vendor/device/x:1/build:user/release-keys",
        )
        self.assertEqual(
            commands[0], (["adb", "-s", "S1", "shell", "getprop sys.boot_completed; getprop ro.build.fingerprint"], 15)
        )
        with self.assertRaisesRegex(
            RuntimeError, "timed out waiting for Vendor OS: Vendor OS has not finished booting"
        ):
            android.wait_booted("adb", "S1", lambda *_: "0", clock, seconds=10, system="Vendor OS")


class FakeClock:
    """Virtual time: a wait's sleeps pass at once."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


if __name__ == "__main__":
    unittest.main()


class ReleaseUsbTests(unittest.TestCase):
    def test_the_adb_server_is_stopped_except_on_windows(self):
        sent: list[list[str]] = []
        android.release_usb("adb", sent.append, platform="linux")
        self.assertEqual(sent, [["adb", "kill-server"]])
        android.release_usb("adb", sent.append, platform="win32")
        self.assertEqual(len(sent), 1)  # Windows' drivers keep adb and fastboot apart

    def test_no_server_is_no_problem(self):
        def refused(_argv: list[str]) -> str:
            msg = "cannot connect to daemon"
            raise RuntimeError(msg)

        android.release_usb("adb", refused, platform="linux")
