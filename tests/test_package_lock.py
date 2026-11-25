# SPDX-License-Identifier: Unlicense
"""overpressure/build/check-package-lock.py, on fake checksum lists."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PackageLockTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("lock", ROOT / "overpressure/build/check-package-lock.py")
        assert spec is not None
        assert spec.loader is not None
        self.lock = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.lock)

    def test_only_the_tested_packages_pass(self):
        lock = self.lock.read("aa  ./a-1.pkg\nbb  b-1.pkg\n")
        built = self.lock.read("aa  ./a-1.pkg\ncc  ./example-x-1.pkg\n", "example-")
        self.assertEqual(self.lock.differences(built, lock), [])
        self.assertEqual(
            self.lock.differences(self.lock.read("ff  ./a-1.pkg\n"), lock), ["a-1.pkg: differs from the lock"]
        )
        self.assertEqual(self.lock.differences(self.lock.read("aa  ./a-2.pkg\n"), lock), ["a-2.pkg: not in the lock"])

    def test_the_project_prefix_is_left_out_and_a_new_lock_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            hashes, new = Path(tmp) / "hashes.txt", Path(tmp) / "packages.lock"
            hashes.write_text("bb  ./b-1.pkg\naa  ./a-1.pkg\ncc  ./example-x-1.pkg\n")
            self.assertEqual(self.lock.main([str(hashes), "-", "--skip", "example-", "--write", str(new)]), 0)
            self.assertEqual(new.read_text(), "aa  a-1.pkg\nbb  b-1.pkg\n")


if __name__ == "__main__":
    unittest.main()
