# SPDX-License-Identifier: Unlicense
"""overpressure.carryover: a newer overpressure's folder takes the owner's data from the one beside it."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from overpressure import carryover, localconfig


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
        self.assertFalse((self.new / ".env").exists())  # its .env migrated: the records keep what it named
        self.assertEqual(localconfig.records(self.new), {"PICO4_BACKUP": "backup/stock"})
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

    def test_it_reports_how_far_it_is_by_bytes(self):
        (self.old / "backup" / "stock" / "image.bin").write_bytes(b"x" * 3000)
        heard: list[tuple[int, int, str]] = []
        carryover.carry(self.old, self.new, say=lambda _text: None, report=lambda *seen: heard.append(seen))
        done, total, _what = heard[-1]
        self.assertEqual(done, total)
        self.assertGreater(total, 3000)  # the backup's bytes count, moved by a rename at once
        self.assertIn("Moving the backup", {what for _d, _t, what in heard})

    def test_a_move_to_another_disk_copies_every_byte_with_progress(self):
        (self.old / "backup" / "stock" / "image.bin").write_bytes(b"x" * (3 * 1024 * 1024))
        heard: list[tuple[int, int, str]] = []
        with (
            mock.patch.object(carryover, "REPORT_EVERY", 0),
            mock.patch.object(Path, "rename", side_effect=OSError("another disk")),
        ):
            carryover.carry(self.old, self.new, say=lambda _text: None, report=lambda *seen: heard.append(seen))
        moving = [done for done, _total, what in heard if what == "Moving the backup"]
        self.assertGreater(len(set(moving)), 2)  # chunk by chunk
        self.assertEqual(heard[-1][0], heard[-1][1])
        self.assertEqual((self.new / "backup" / "stock" / "image.bin").stat().st_size, 3 * 1024 * 1024)
        self.assertFalse((self.old / "backup").exists())

    def test_an_older_env_comes_as_records_and_settings_without_its_programs(self):
        (self.old / ".env").write_text(
            "PICO4_BACKUP=backup/stock\nPICO4_EDL=cache/edl-ng/edl-ng.exe\nPICO4_EDL_SERIAL=ABC\n"
            "PICO4_NCM_PYTHON=cache/edl-venv/Scripts/python.exe\nPICO4_SSH_PUBLIC_KEY=C:/me/id.pub\n"
            "OVERPRESSURE_PRIVATE_WORDS=secret\n",
            encoding="utf-8",
        )
        localconfig.save_owner_settings({"appearance": "dark"}, self.new)  # the app's own, before the question
        carryover.carry(self.old, self.new, say=lambda _text: None)
        # Where its programs came from stays behind: the fetched ones count here.
        self.assertEqual(localconfig.records(self.new), {"PICO4_BACKUP": "backup/stock", "PICO4_EDL_SERIAL": "ABC"})
        self.assertEqual(localconfig.owner_settings(self.new), {"appearance": "dark", "ssh_public_key": "C:/me/id.pub"})

    def test_a_newer_folders_settings_and_records_merge_key_by_key(self):
        (self.old / ".env").unlink()
        localconfig.record({"PICO4_ADB_SERIAL": "OLD", "PICO4_EDL_SERIAL": "E1"}, self.old)
        localconfig.save_owner_settings({"appearance": "light", "ssh_public_key": "k.pub"}, self.old)
        localconfig.record({"PICO4_ADB_SERIAL": "NEW"}, self.new)
        localconfig.save_owner_settings({"appearance": "dark"}, self.new)
        brought = carryover.carry(self.old, self.new, say=lambda _text: None)
        self.assertIn("your settings and what overpressure knows about the headset", brought)
        self.assertEqual(localconfig.records(self.new), {"PICO4_ADB_SERIAL": "NEW", "PICO4_EDL_SERIAL": "E1"})
        self.assertEqual(localconfig.owner_settings(self.new), {"appearance": "dark", "ssh_public_key": "k.pub"})

    def test_nothing_here_is_replaced(self):
        localconfig.record({"PICO4_BACKUP": "backup/mine"}, self.new)
        carryover.carry(self.old, self.new, say=lambda _text: None)
        self.assertEqual(localconfig.records(self.new), {"PICO4_BACKUP": "backup/mine"})

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


class AppDataTests(unittest.TestCase):
    """The app's data folder has no earlier folder beside it: the portable zip is where downloads end up."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name) / "home"
        self.data = Path(temp.name) / "AppData" / "Local" / "overpressure-data"
        self.data.mkdir(parents=True)
        self.old = folder(self.home / "Downloads", "overpressure-2026.11.0b1")
        (self.old / "backup" / "stock").mkdir(parents=True)

    def test_the_portable_zip_in_downloads_is_found(self):
        self.assertIsNone(carryover.previous(self.data))  # nothing beside the data folder
        self.assertEqual(carryover.previous(self.data, carryover.portable_places(self.home)), self.old)

    def test_one_unpacked_a_folder_deeper_is_found_too(self):
        deeper = folder(self.home / "Desktop" / "VR tools", "overpressure-2026.10.0")
        (deeper / ".env").write_text("x=1\n", encoding="utf-8")
        found = carryover.previous(self.data, carryover.portable_places(self.home))
        self.assertIn(found, {self.old, deeper})  # the one used last of the two

    def test_the_home_folder_is_searched_one_level_and_hidden_folders_not_at_all(self):
        (self.old / "backup").rename(self.old.parent / "moved-away")  # only the folders below hold data
        places = carryover.portable_places(self.home)
        deep = folder(self.home / "Projects", "overpressure-checkout")  # two levels into home: not looked at
        (deep / ".env").write_text("x=1\n", encoding="utf-8")
        hidden = folder(self.home / "Downloads" / ".cache", "overpressure-hidden")
        (hidden / ".env").write_text("x=1\n", encoding="utf-8")
        self.assertIsNone(carryover.previous(self.data, places))
        top = folder(self.home, "overpressure-old")
        (top / ".env").write_text("x=1\n", encoding="utf-8")
        self.assertEqual(carryover.previous(self.data, places), top)

    def test_a_chosen_folder_is_checked_in_the_owners_words(self):
        self.assertEqual(carryover.check(self.old, self.data), "")
        self.assertIn("not an overpressure folder", carryover.check(self.home, self.data))
        self.assertIn("already", carryover.check(self.data, self.data))
        empty = folder(self.home, "overpressure-unused")
        self.assertIn("nothing to bring over", carryover.check(empty, self.data))

    def test_the_backup_moves_into_the_data_folder(self):
        (self.old / "backup" / "stock" / "manifest.json").write_text("{}", encoding="utf-8")
        brought = carryover.carry(self.old, self.data, say=lambda _text: None)
        self.assertTrue((self.data / "backup" / "stock" / "manifest.json").is_file())
        self.assertFalse((self.old / "backup").exists())
        self.assertTrue((self.old / carryover.MOVED).is_file())
        self.assertIn("the backup (backup/, moved)", brought)
        self.assertIsNone(carryover.previous(self.data, carryover.portable_places(self.home)))  # asked once


if __name__ == "__main__":
    unittest.main()
