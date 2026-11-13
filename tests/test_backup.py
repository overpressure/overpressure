# SPDX-License-Identifier: Unlicense
"""overpressure.backup: digests, verified manifests, a partition's image in a backup, backup archives."""

from __future__ import annotations

import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import Any, ClassVar

from overpressure import backup, progress
from overpressure.headset import BackupSet


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

    def test_a_partition_from_the_backup_matches_its_manifest(self):
        # The headset's own stock images, in place of a vendor's download.
        self.assertEqual(backup.verified_partition(self.root, "boot"), (self.root / "boot.bin", b"boot.bin"))
        (self.root / "boot.bin").write_bytes(b"changed!")
        with self.assertRaisesRegex(ValueError, "differs from the backup's manifest"):
            backup.verified_partition(self.root, "boot")

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


class ArchiveTests(unittest.TestCase):
    """A backup as one file: exported checked, imported checked, never over another headset's."""

    IDENTITY: ClassVar[dict[str, Any]] = {"serial": "SERIAL1", "chip_id": "0x1"}

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.here = self.computer("here")
        folder = self.here.folder
        for name in ("lun0/boot.bin", "lun4/abl.bin"):
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(name.encode() * 1000)
        files = {n: backup.digest(folder / n) for n in ("lun0/boot.bin", "lun4/abl.bin")}
        (folder / "manifest.json").write_text(json.dumps({"schema": 1, "identity": self.IDENTITY, "files": files}))
        self.here.identity.write_text(json.dumps(self.IDENTITY))
        for path in self.here.files.values():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"key of " + path.name.encode())
        self.archive = self.root / "headset.overpressure-backup.tar"
        self.lines: list[str] = []

    def computer(self, name: str) -> BackupSet:
        """Return a computer's backup places, nothing in them yet."""
        base = self.root / name
        (base / "backup").mkdir(parents=True)
        return BackupSet(
            base / "backup" / "stock-1",
            base / "backup" / "device.json",
            {"id_ed25519": base / "out" / "id_ed25519"},
            {"PICO4_ADB_SERIAL": "SERIAL1"},
        )

    def shown(self) -> list[tuple[float | None, str]]:
        """Return the last progress line of each pass: (its fraction, its words), rounded."""
        found = [progress.parse(line) for line in self.lines]
        passes: dict[str, float | None] = {}
        for item in found:
            if item is not None:
                passes[item[1]] = item[0]
        return [(round(f, 3) if f is not None else None, text) for text, f in passes.items()]

    def export(self) -> dict:
        return backup.export_archive(self.here, "phoenix", self.archive, self.lines.append)

    def test_a_round_trip_onto_another_computer(self):
        meta = self.export()
        self.assertEqual((meta["headset"], meta["folder"], meta["files"]), ("phoenix", "stock-1", ["id_ed25519"]))
        self.assertFalse(self.archive.with_name(self.archive.name + ".part").exists())
        self.assertIn("Reading the export back", self.lines)
        self.assertEqual(
            self.shown(),
            [
                (round(1 / 3, 3), "Checking the backup in " + str(self.here.folder)),
                (round(2 / 3, 3), "Writing the backup file"),
                (1.0, "Reading the export back"),
            ],
        )
        other = self.computer("other")
        self.lines.clear()
        imported = backup.import_archive(self.archive, "phoenix", other, self.lines.append)
        self.assertEqual([text for _, text in self.shown()][-1], "Checking the imported backup")
        self.assertEqual(self.shown()[-1][0], 1.0)
        self.assertEqual(imported.folder, other.folder)
        self.assertEqual(backup.verified_backup(other.folder)["identity"], self.IDENTITY)
        self.assertEqual(json.loads(other.identity.read_text()), self.IDENTITY)
        self.assertEqual(other.files["id_ed25519"].read_bytes(), b"key of id_ed25519")
        self.assertEqual(imported.settings, {"PICO4_ADB_SERIAL": "SERIAL1"})
        with self.assertRaisesRegex(ValueError, "exists already"):
            self.export()

    def test_the_same_backup_here_already_is_kept(self):
        self.export()
        imported = backup.import_archive(self.archive, "phoenix", self.here, self.lines.append)
        self.assertEqual(imported.folder, self.here.folder)
        self.assertTrue(any("on this computer already" in line for line in self.lines))

    def test_a_damaged_file_is_refused_before_anything_is_written(self):
        self.export()
        data = bytearray(self.archive.read_bytes())
        at = data.find(b"lun4/abl.bin" * 3)
        data[at] ^= 0xFF
        self.archive.write_bytes(bytes(data))
        other = self.computer("other")
        with self.assertRaisesRegex(ValueError, "damaged: lun4/abl.bin"):
            backup.import_archive(self.archive, "phoenix", other, self.lines.append)
        self.assertFalse(other.folder.exists())
        self.assertFalse(other.identity.exists())
        (self.root / "not-a-backup.tar").write_bytes(b"no tar at all" * 100)
        with self.assertRaises((ValueError, tarfile.TarError)):
            backup.import_archive(self.root / "not-a-backup.tar", "phoenix", other, self.lines.append)

    def test_another_headset_or_kind_of_headset_is_refused(self):
        self.export()
        other = self.computer("other")
        other.identity.write_text(json.dumps({"serial": "SERIAL2"}))
        with self.assertRaisesRegex(ValueError, "another headset's identity"):
            backup.import_archive(self.archive, "phoenix", other, self.lines.append)
        with self.assertRaisesRegex(ValueError, "another kind of headset"):
            backup.import_archive(self.archive, "quest", self.computer("third"), self.lines.append)

    def test_a_different_key_here_is_never_replaced(self):
        self.export()
        other = self.computer("other")
        other.files["id_ed25519"].parent.mkdir(parents=True)
        other.files["id_ed25519"].write_bytes(b"this computer's own key")
        with self.assertRaisesRegex(ValueError, "other files where the backup's go"):
            backup.import_archive(self.archive, "phoenix", other, self.lines.append)
        self.assertEqual(other.files["id_ed25519"].read_bytes(), b"this computer's own key")

    def test_names_outside_the_folder_are_refused(self):
        for name in ("../escape", "/etc/passwd", "C:/x", "a" + chr(92) + ".." + chr(92) + "b"):
            with self.assertRaisesRegex(ValueError, "outside its folder"):
                backup._inside(self.root, name)  # noqa: SLF001  # the guard every name passes
        self.assertEqual(backup._inside(self.root, "lun0/boot.bin"), self.root / "lun0" / "boot.bin")  # noqa: SLF001

    def test_an_export_of_a_changed_backup_is_refused(self):
        (self.here.folder / "lun0/boot.bin").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "lun0/boot.bin"):
            self.export()
        self.assertFalse(self.archive.exists())
