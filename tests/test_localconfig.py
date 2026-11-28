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
        with self.assertRaisesRegex(ValueError, "not recorded EXAMPLE_EDL_SERIAL yet"):
            s.require("adb_serial", "edl_serial")

    def test_an_empty_value_keeps_the_default(self):
        env = self.root / ".env"
        env.write_text("EXAMPLE_FIRMWARE=\nEXAMPLE_BACKUP=\n")
        s = localconfig.load(Example, env, {})
        self.assertEqual((s.firmware, s.backup), ("1.0", "backup/stock"))
        self.assertEqual(localconfig.load(Example, env, {"EXAMPLE_FIRMWARE": "2.0"}).firmware, "2.0")

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


class StoreTests(unittest.TestCase):
    """The records, the owner's settings and a developer's overrides, each over the one before."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patcher = mock.patch.object(localconfig, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_layers(self):
        self.assertEqual(localconfig.load(Example, environ={}).adb_serial, "")
        localconfig.update({"EXAMPLE_ADB_SERIAL": "A1", "EXAMPLE_SSH_PUBLIC_KEY": "me.pub"})  # as the installer
        self.assertEqual(localconfig.records(), {"EXAMPLE_ADB_SERIAL": "A1"})
        self.assertEqual(localconfig.owner_settings(), {"ssh_public_key": "me.pub"})  # the owner's, any headset's
        s = localconfig.load(Example, environ={})
        self.assertEqual((s.adb_serial, s.ssh_public_key), ("A1", "me.pub"))
        self.assertFalse((self.root / ".env").exists())  # nothing writes a .env
        # A developer's .env, then the environment, over both.
        (self.root / ".env").write_text("EXAMPLE_ADB_SERIAL=DEV\n", encoding="utf-8")
        self.assertEqual(localconfig.load(Example, environ={}).adb_serial, "DEV")
        self.assertEqual(localconfig.load(Example, environ={"EXAMPLE_ADB_SERIAL": "ENV"}).adb_serial, "ENV")

    def test_a_record_never_says_where_a_program_comes_from(self):
        localconfig.record({"EXAMPLE_EDL": "cache/edl-ng/edl-ng.exe", "EXAMPLE_FIRMWARE": "2.0"})
        s = localconfig.load(Example, environ={})
        self.assertEqual((s.edl, s.firmware), ("", "2.0"))
        with self.assertRaisesRegex(ValueError, "one line"):
            localconfig.record({"EXAMPLE_ADB_SERIAL": "a\nb"})

    def test_an_earlier_env_is_migrated_once_and_kept(self):
        env = self.root / ".env"
        env.write_text(
            "# mine\nEXAMPLE_ADB_SERIAL=A1\nEXAMPLE_EDL=cache/edl-ng/edl-ng.exe\nEXAMPLE_FIRMWARE=\n"
            "EXAMPLE_SSH_PUBLIC_KEY=me.pub\nOVERPRESSURE_PRIVATE_WORDS=x\n",
            encoding="utf-8",
        )
        localconfig.record({"EXAMPLE_EDL_SERIAL": "E1"})
        kept = localconfig.retire()
        self.assertEqual(kept, self.root / ".env.old")
        self.assertFalse(env.exists())
        self.assertEqual(localconfig.records(), {"EXAMPLE_ADB_SERIAL": "A1", "EXAMPLE_EDL_SERIAL": "E1"})
        self.assertEqual(localconfig.owner_settings(), {"ssh_public_key": "me.pub"})
        self.assertIsNone(localconfig.retire())  # once
        env.write_text("EXAMPLE_ADB_SERIAL=A2\n", encoding="utf-8")
        self.assertEqual(localconfig.retire(), self.root / ".env.old.2")
        self.assertEqual(localconfig.records()["EXAMPLE_ADB_SERIAL"], "A1")  # what is recorded stays

    def test_a_checkouts_env_is_the_developers(self):
        (self.root / ".git").mkdir()
        (self.root / ".env").write_text("EXAMPLE_ADB_SERIAL=DEV\n", encoding="utf-8")
        self.assertIsNone(localconfig.retire())
        self.assertTrue((self.root / ".env").exists())
        self.assertEqual(localconfig.records(), {})


if __name__ == "__main__":
    unittest.main()
