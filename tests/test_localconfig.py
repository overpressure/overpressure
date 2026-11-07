# SPDX-License-Identifier: Unlicense
"""overpressure.localconfig: .env, a headset's key prefix, and the settings every Qualcomm Android headset has."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from overpressure import localconfig, tools


@dataclass
class Example(localconfig.DeviceSettings):
    PREFIX = "EXAMPLE_"

    firmware: str = "1.0"


class DeviceSettingsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patcher = mock.patch.object(tools, "CACHE", self.root / "tools-cache")  # no fetched tools
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_headsets_keys_and_defaults(self):
        env = self.root / ".env"
        env.write_text("EXAMPLE_ADB_SERIAL=A1\nEXAMPLE_EDL=tools/edl-ng\nOTHER_ADB_SERIAL=B2\nEXAMPLE_FIRMWARE=2.0\n")
        s = localconfig.load(Example, env, {"EXAMPLE_FASTBOOT_SERIAL": "F1"})
        self.assertEqual((s.adb_serial, s.fastboot_serial, s.edl_serial, s.firmware), ("A1", "F1", "", "2.0"))
        self.assertEqual(s.key("edl_serial"), "EXAMPLE_EDL_SERIAL")
        self.assertEqual(
            (s.identity, s.backup, s.loader), ("backup/device.json", "backup/stock", "assets/firehose.elf")
        )
        self.assertEqual(s.edl_program(), localconfig.ROOT / "tools/edl-ng")
        with self.assertRaisesRegex(ValueError, "set EXAMPLE_EDL_SERIAL in .env"):
            s.require("adb_serial", "edl_serial")

    def test_edl_ng_and_the_ncm_python_fall_back_to_what_is_there(self):
        s = Example()
        self.assertEqual(s.edl_program(), Path("edl-ng" + localconfig.EXE))  # from PATH
        with mock.patch.object(localconfig.sys, "platform", "linux"):
            self.assertIsNone(s.ncm())  # Windows only
        with mock.patch.object(localconfig.sys, "platform", "win32"):
            with mock.patch.object(localconfig.importlib.util, "find_spec", return_value=object()):
                self.assertEqual(s.ncm(), Path(localconfig.sys.executable))  # this Python has the packages
            with mock.patch.object(localconfig.importlib.util, "find_spec", side_effect=lambda m: m != "usb" or None):
                self.assertIsNone(s.ncm())  # without pyusb
        s.ncm_python = "tools/python"
        self.assertEqual(s.ncm(), localconfig.ROOT / "tools/python")


class UpdateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.env = self.root / ".env"
        self.example = self.root / ".env.example"
        self.example.write_text("# the serials\nEXAMPLE_ADB_SERIAL=\n\n# the firmware\nEXAMPLE_FIRMWARE=1.0\n")

    def test_a_missing_env_starts_from_the_example(self):
        localconfig.update({"EXAMPLE_ADB_SERIAL": "A1"}, self.env, self.example)
        self.assertEqual(
            self.env.read_text(), "# the serials\nEXAMPLE_ADB_SERIAL=A1\n\n# the firmware\nEXAMPLE_FIRMWARE=1.0\n"
        )
        self.assertEqual(localconfig.load(Example, self.env, {}).adb_serial, "A1")

    def test_other_lines_stay_duplicates_go_and_new_keys_are_added(self):
        kept = b"OTHER=1  # kept\r\n# EXAMPLE_EDL_SERIAL=commented\r\n"
        self.env.write_bytes(b"# mine\r\nEXAMPLE_ADB_SERIAL=old\r\n" + kept + b"EXAMPLE_ADB_SERIAL=dup\r\n")
        localconfig.update({"EXAMPLE_ADB_SERIAL": "A2", "EXAMPLE_EDL_SERIAL": "E1"}, self.env, self.example)
        self.assertEqual(
            self.env.read_bytes(), b"# mine\r\nEXAMPLE_ADB_SERIAL=A2\r\n" + kept + b"EXAMPLE_EDL_SERIAL=E1\r\n"
        )

    def test_values_are_quoted_when_they_would_not_survive_bare(self):
        values = {"EXAMPLE_BACKUP": "backup/my stock #1", "EXAMPLE_EDL": " padded", "EXAMPLE_LOADER": "'quoted'"}
        localconfig.update(values, self.env, self.example)
        self.assertEqual({k: v for k, v in localconfig.parse(self.env.read_text()).items() if k in values}, values)
        with self.assertRaisesRegex(ValueError, "one line"):
            localconfig.update({"EXAMPLE_EDL": "a\nb"}, self.env, self.example)
        with self.assertRaisesRegex(ValueError, "both kinds"):
            localconfig.update({"EXAMPLE_EDL": "a \"b\" 'c' "}, self.env, self.example)

    def test_a_hard_link_stays_one_file(self):
        self.env.write_text("EXAMPLE_ADB_SERIAL=\n")
        link = self.root / "linked.env"
        try:
            link.hardlink_to(self.env)
        except (AttributeError, OSError):  # Python 3.9, or a file system without hard links
            self.skipTest("no hard links here")
        localconfig.update({"EXAMPLE_ADB_SERIAL": "A3"}, self.env, self.example)
        self.assertEqual(link.read_text(), "EXAMPLE_ADB_SERIAL=A3\n")


if __name__ == "__main__":
    unittest.main()
