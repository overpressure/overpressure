# SPDX-License-Identifier: Unlicense
"""The package lock check, on fake checksum lists."""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PackageLockTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("lock", ROOT / "scripts/check-package-lock.py")
        assert spec is not None
        assert spec.loader is not None
        self.lock = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.lock)

    def test_only_the_tested_packages_pass(self):
        lock = self.lock.read("aa  ./a-1.pkg\nbb  b-1.pkg\n")
        self.assertEqual(self.lock.differences(self.lock.read("aa  ./a-1.pkg\ncc  ./pico4-x-1.pkg\n"), lock), [])
        self.assertEqual(
            self.lock.differences(self.lock.read("ff  ./a-1.pkg\n"), lock), ["a-1.pkg: differs from the lock"]
        )
        self.assertEqual(self.lock.differences(self.lock.read("aa  ./a-2.pkg\n"), lock), ["a-2.pkg: not in the lock"])


if __name__ == "__main__":
    unittest.main()
