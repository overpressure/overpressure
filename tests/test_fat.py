# SPDX-License-Identifier: Unlicense
"""overpressure.fat: files, long names and cluster chains out of synthetic FAT12/16/32 images."""

from __future__ import annotations

import datetime as dt
import io
import struct
import tempfile
import unittest
from pathlib import Path

from overpressure.fat import Fat, FatError
from tests.images import FatImage

WHEN = dt.datetime(2025, 10, 30, 20, 18, 42)  # noqa: DTZ001  # FAT keeps local time


def sample(bits: int) -> bytes:
    """Return an image with short and long names, a subdirectory tree and a multi-cluster file."""
    img = FatImage(bits)
    img.file("/README", b"top")
    img.file("/image/venus.mdt", b"mdt" * 100, WHEN)
    img.file("/image/venus.b00", b"b00")
    img.file("/image/venus.b19", b"b19" * 1000)  # several clusters
    img.file("/image/slpius.jsn", b"{}")
    img.file("/image/capi_v2_long_name.so", b"so")
    img.file("/image/Mixed Case.Bin", b"mixed")
    img.file("/image/empty", b"")
    img.file("/image/qca6390/bdwlanq.elf", b"elf" * 300)
    img.mkdir("/verinfo")
    return img.build()


class FatTests(unittest.TestCase):
    def test_each_fat_type(self):
        for bits in (12, 16, 32):
            with self.subTest(bits=bits):
                fs = Fat(io.BytesIO(sample(bits)))
                self.assertEqual(fs.bits, bits)
                self.assertEqual(fs.listdir("/"), ["README", "image", "verinfo"])
                self.assertEqual(fs.read("/image/venus.b19"), b"b19" * 1000)
                self.assertEqual(fs.read("/image/qca6390/bdwlanq.elf"), b"elf" * 300)
                self.assertEqual(fs.read("/image/capi_v2_long_name.so"), b"so")
                self.assertEqual(fs.read("/image/Mixed Case.Bin"), b"mixed")
                self.assertEqual(fs.read("/image/empty"), b"")
                self.assertEqual(fs.read("/README"), b"top")
                self.assertEqual(fs.listdir("/verinfo"), [])

    def test_names_short_long_and_case(self):
        fs = Fat(io.BytesIO(sample(16)))
        names = fs.listdir("/image")
        self.assertIn("venus.mdt", names)  # an 8.3 name in lower case: NT's case bits
        self.assertIn("capi_v2_long_name.so", names)  # a long name
        long_entry = fs.lookup("/image/capi_v2_long_name.so")
        assert long_entry is not None
        self.assertTrue(long_entry.short_name.startswith("CAPIV2"))
        # FAT compares names without case, by the long or the 8.3 name.
        self.assertEqual(fs.read("/IMAGE/VENUS.MDT"), b"mdt" * 100)
        self.assertEqual(fs.read("/image/" + long_entry.short_name), b"so")

    def test_glob_matches_files_not_directories(self):
        fs = Fat(io.BytesIO(sample(16)))
        self.assertEqual([e.name for e in fs.glob("/image", "venus.*")], ["venus.mdt", "venus.b00", "venus.b19"])
        self.assertEqual([e.name for e in fs.glob("/image", "SLPI*")], ["slpius.jsn"])
        self.assertEqual(fs.glob("/image", "qca*"), [])
        self.assertEqual(fs.glob("/image", "none.*"), [])

    def test_times(self):
        fs = Fat(io.BytesIO(sample(32)))
        entry = fs.lookup("/image/venus.mdt")
        assert entry is not None
        self.assertEqual(entry.mtime, WHEN.replace(second=42))

    def test_from_a_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "modem.bin"
            path.write_bytes(sample(12))
            with Fat.open(path) as fs:
                self.assertEqual(fs.read("/image/venus.b00"), b"b00")

    def test_errors(self):
        fs = Fat(io.BytesIO(sample(16)))
        with self.assertRaisesRegex(FatError, "no /image/none"):
            fs.read("/image/none")
        with self.assertRaisesRegex(FatError, "not a file"):
            fs.read("/image")
        with self.assertRaisesRegex(FatError, "not a directory"):
            fs.listdir("/README")
        with self.assertRaisesRegex(FatError, "no boot sector"):
            Fat(io.BytesIO(bytes(1024)))

    def test_a_looping_chain_is_refused(self):
        data = bytearray(sample(16))
        fs = Fat(io.BytesIO(bytes(data)))
        entry = fs.lookup("/image/venus.b19")
        assert entry is not None
        fat_at = 512  # one reserved sector
        struct.pack_into("<H", data, fat_at + 2 * (entry.cluster + 1), entry.cluster)
        broken = Fat(io.BytesIO(bytes(data)))
        with self.assertRaisesRegex(FatError, "loops"):
            broken.read("/image/venus.b19")


if __name__ == "__main__":
    unittest.main()
