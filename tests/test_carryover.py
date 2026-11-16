# SPDX-License-Identifier: Unlicense
"""overpressure.carryover: a newer overpressure's folder takes the owner's data from the one beside it."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from overpressure import carryover


def folder(root: Path, name: str) -> Path:
    """Make an overpressure folder (a release's) named NAME under ROOT."""
    path = root / name
    (path / "overpressure").mkdir(parents=True)
    (path / "overpressure" / "__init__.py").write_text("", encoding="utf-8")
    (path / "pyproject.toml").write_text('[project]\nname = "overpressure"\nversion = "1.0b1"\n', encoding="utf-8")
    return path


class CarryOverTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.old = folder(self.root, "overpressure-1.0-beta")
        self.new = folder(self.root, "overpressure-1.0-beta.2")
        (self.old / ".env").write_text("PICO4_BACKUP=backup/stock\n", encoding="utf-8")
        (self.old / "backup" / "stock").mkdir(parents=True)
        (self.old / "backup" / "stock" / "manifest.json").write_text("{}", encoding="utf-8")
        (self.old / "out" / "install" / "phoenix").mkdir(parents=True)
        (self.old / "out" / "install" / "phoenix" / "state.json").write_text('{"completed": {}}', encoding="utf-8")
        (self.old / "devices" / "phoenix").mkdir(parents=True)
        (self.old / "devices" / "phoenix" / "headset.json").write_text("{}", encoding="utf-8")
        (self.old / "out" / "dev").mkdir(parents=True)
        (self.old / "out" / "dev" / "id_ed25519").write_text("key", encoding="utf-8")

    def test_a_fresh_folder_finds_the_earlier_one_and_takes_its_data(self):
        self.assertEqual(carryover.previous(self.new), self.old)
        key = self.new / "out" / "dev" / "id_ed25519"
        brought = carryover.carry(self.old, self.new, headset_files=lambda: [key], say=lambda _text: None)
        self.assertEqual(len(brought), 5, brought)
        self.assertEqual((self.new / ".env").read_text(encoding="utf-8"), "PICO4_BACKUP=backup/stock\n")
        self.assertTrue((self.new / "backup" / "stock" / "manifest.json").is_file())
        self.assertFalse((self.old / "backup").exists())  # moved, not kept twice
        self.assertIn(str(self.new / "backup"), (self.old / carryover.MOVED).read_text(encoding="utf-8"))
        self.assertTrue((self.old / ".env").is_file())  # the rest is copied
        self.assertTrue((self.new / "out" / "install" / "phoenix" / "state.json").is_file())
        self.assertTrue((self.new / "devices" / "phoenix" / "headset.json").is_file())
        self.assertEqual(key.read_text(encoding="utf-8"), "key")
        self.assertIsNone(carryover.previous(self.new))  # it has its data now: never asked again
        record = json.loads((self.new / carryover.RECORD).read_text(encoding="utf-8"))
        self.assertEqual(record["from"], str(self.old))

    def test_nothing_here_is_replaced(self):
        (self.new / ".env").write_text("OWN=1\n", encoding="utf-8")
        carryover.carry(self.old, self.new, say=lambda _text: None)
        self.assertEqual((self.new / ".env").read_text(encoding="utf-8"), "OWN=1\n")

    def test_a_declined_folder_is_not_asked_about_again(self):
        carryover.decline(self.old, self.new)
        self.assertIsNone(carryover.previous(self.new))

    def test_a_checkout_and_a_folder_with_its_own_data_are_never_asked(self):
        (self.new / ".git").mkdir()
        self.assertIsNone(carryover.previous(self.new))
        (self.new / ".git").rmdir()
        (self.new / "backup").mkdir()
        self.assertIsNone(carryover.previous(self.new))

    def test_of_several_the_one_used_last(self):
        newer = folder(self.root, "overpressure-1.0-beta.1-copy")
        time.sleep(0.05)
        (newer / ".env").write_text("X=1\n", encoding="utf-8")
        self.assertEqual(carryover.previous(self.new), newer)

    def test_a_development_checkout_of_a_headset_stays_the_developers(self):
        (self.old / "devices" / "phoenix" / ".git").mkdir()
        carryover.carry(self.old, self.new, say=lambda _text: None)
        self.assertFalse((self.new / "devices" / "phoenix").exists())

    def test_refuses_what_is_not_an_earlier_overpressure(self):
        with self.assertRaises(ValueError):
            carryover.carry(self.root, self.new)
        with self.assertRaises(ValueError):
            carryover.carry(self.new, self.new)


if __name__ == "__main__":
    unittest.main()
