# SPDX-License-Identifier: Unlicense
"""The logs for a bug report: the installer's records and the headset's own, this computer's identifiers masked."""

from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from overpressure import support
from overpressure.headset import Command, Headset, Info

SERIAL = "PA8250FAKE0001"  # a made-up serial, the shape of a real one


class Owned(Headset):
    """A headset that adds its own files: a log, and a binary that goes in as it is."""

    info = Info("owned", "Owned headset", "SoC", "test")

    def commands(self) -> dict[str, Command]:
        """Return no commands: the bundle needs none."""
        return {}

    def support_files(self, folder: Path) -> None:
        """Add a log naming the serial and a binary."""
        (folder / "tool.log").write_text(f"adb -s {SERIAL} shell\n", encoding="utf-8")
        (folder / "blob.bin").write_bytes(b"\xff\xfe" + SERIAL.encode())


class Broken(Owned):
    def support_files(self, folder: Path) -> None:  # noqa: ARG002  # it fails before it adds anything
        """Fail as a headset's own collection may."""
        msg = "the headset did not answer"
        raise RuntimeError(msg)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.folder, ignore_errors=True))
        self.records = self.folder / "out" / "install"
        logs = self.records / "owned"
        (logs / "restore").mkdir(parents=True)
        (logs / "stage-abl.log").write_text(f"timed out: adb: device '{SERIAL}' not found\n", encoding="utf-8")
        (logs / "restore" / "state.json").write_text('{"schema": 1, "tasks": {}}', encoding="utf-8")
        (self.records / "lock").write_text("", encoding="utf-8")
        self.env = self.folder / ".env"
        self.env.write_text(
            f"PICO4_ADB_SERIAL={SERIAL}\nPICO4_FASTBOOT_SERIAL=\nPICO4_FIRMWARE=5.13.7\n", encoding="utf-8"
        )
        self.target = self.folder / "logs.zip"

    def bundle(self, headset: Headset) -> zipfile.ZipFile:
        support.bundle(headset, self.target, root=self.folder, records=self.records, env=self.env, environ={})
        return zipfile.ZipFile(self.target)

    def test_the_installer_s_records_and_the_headset_s_own_files_are_in_it(self):
        with self.bundle(Owned()) as archive:
            names = archive.namelist()
        self.assertEqual(
            names,
            [
                "about.txt",
                "headset/blob.bin",
                "headset/tool.log",
                "installer/restore/state.json",
                "installer/stage-abl.log",
            ],
        )

    def test_serials_and_the_home_folder_are_masked_in_text_and_a_binary_goes_in_as_it_is(self):
        home = str(Path.home())
        log = self.records / "owned" / "stage-abl.log"
        log.write_text(log.read_text(encoding="utf-8") + f"backup in {home}/overpressure/backup\n", encoding="utf-8")
        with self.bundle(Owned()) as archive:
            text = archive.read("installer/stage-abl.log").decode()
            own = archive.read("headset/tool.log").decode()
            blob = archive.read("headset/blob.bin")
        self.assertNotIn(SERIAL, text + own)
        self.assertIn(f"device '{support.SERIAL}' not found", text)
        self.assertIn(f"backup in {support.HOME}/overpressure/backup", text)
        self.assertIn(SERIAL.encode(), blob)  # a headset's own binary bundle masks itself

    def test_a_short_or_empty_serial_setting_masks_nothing(self):
        found = support.identifiers(self.env, {"PICO4_EDL_SERIAL": "1a2b"})
        self.assertNotIn("", found)
        self.assertNotIn("1a2b", found)
        self.assertNotIn("5.13.7", found)  # not a serial setting

    def test_the_headset_s_own_files_failing_leaves_the_installer_s(self):
        with self.bundle(Broken()) as archive:
            names = archive.namelist()
            error = archive.read("headset/error.txt").decode()
        self.assertIn("installer/stage-abl.log", names)
        self.assertIn("the headset did not answer", error)

    def test_the_command_lists_what_it_saved_and_ends_with_where(self):
        out = io.StringIO()
        with (
            mock.patch.object(support, "bundle", return_value=["about.txt", "installer/stage-abl.log"]),
            redirect_stdout(out),
        ):
            self.assertEqual(support.main(Owned(), [str(self.folder / "logs")]), 0)
        lines = out.getvalue().splitlines()
        self.assertIn("  installer/stage-abl.log", lines)
        self.assertEqual(lines[-1], f"Saved to {self.folder / 'logs.zip'}")


if __name__ == "__main__":
    unittest.main()
