# SPDX-License-Identifier: Unlicense
"""The stage-1 image's stock boot image: the headset's own, from its EDL backup."""

from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from devices.phoenix.tooling import FIRMWARE, image
from overpressure import backup


class StockBootTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.backup = Path(temp.name)
        (self.backup / "lun4").mkdir()
        (self.backup / "lun4/boot.bin").write_bytes(b"the headset's boot")
        files = {"lun4/boot.bin": backup.digest(self.backup / "lun4/boot.bin")}
        (self.backup / "manifest.json").write_text(json.dumps({"schema": 1, "files": files}))

    def args(self, **kw: object) -> argparse.Namespace:
        return argparse.Namespace(**{"stock_boot": None, "firmware": FIRMWARE, "backup": self.backup, **kw})

    def test_the_backups_boot_partition_and_no_download(self):
        with mock.patch.object(image.ota, "fetch_boot_image", side_effect=AssertionError("no download")):
            self.assertEqual(image.load_stock(self.args()), (FIRMWARE, b"the headset's boot"))

    def test_no_backup_or_a_changed_image_stops_the_build(self):
        with self.assertRaisesRegex(SystemExit, "no EDL backup"):
            image.load_stock(self.args(backup=self.backup / "missing"))
        (self.backup / "lun4/boot.bin").write_bytes(b"changed")
        with self.assertRaisesRegex(SystemExit, "differs from the backup's manifest"):
            image.load_stock(self.args())
        with self.assertRaisesRegex(SystemExit, "unknown --firmware"):
            image.load_stock(self.args(firmware="9.9.9"))


if __name__ == "__main__":
    unittest.main()
