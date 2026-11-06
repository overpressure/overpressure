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
        s.ncm_python = "tools/python"
        self.assertEqual(s.ncm(), localconfig.ROOT / "tools/python")


if __name__ == "__main__":
    unittest.main()
