# SPDX-License-Identifier: Unlicense
"""overpressure.backup: digests, verified manifests and a partition's image in a backup."""

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from overpressure import backup


class BackupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        names = ("boot.bin", "lun4/recovery.img", "abl_a.bin", "abl_b.bin", "rawprogram4.xml", "boot.xml")
        for name in names:
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_bytes(name.encode())
        self.manifest: dict[str, Any] = {"schema": 1, "files": {n: backup.digest(self.root / n) for n in names}}
        (self.root / "manifest.json").write_text(json.dumps(self.manifest))

    def test_the_one_image_of_a_partition(self):
        path, entry = backup.partition_image(self.root, "boot")
        self.assertEqual((path, entry), (self.root / "boot.bin", self.manifest["files"]["boot.bin"]))
        self.assertEqual(backup.partition_image(self.root, "recovery")[0], self.root / "lun4/recovery.img")
        self.assertEqual(backup.partition_names(self.manifest, "abl"), [])  # slots are not the partition
        for name in ("abl", "misc", "rawprogram4"):
            with self.assertRaisesRegex(ValueError, f"exactly one {name} partition image"):
                backup.partition_image(self.root, name)

    def test_a_verified_backup_and_a_record_that_is_never_overwritten(self):
        self.assertEqual(backup.verified_backup(self.root), self.manifest)
        (self.root / "boot.bin").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "boot.bin"):
            backup.verified_backup(self.root)
        backup.save_new(self.root / "record.json", {"a": 1})
        with self.assertRaises(FileExistsError):
            backup.save_new(self.root / "record.json", {"a": 2})


if __name__ == "__main__":
    unittest.main()
