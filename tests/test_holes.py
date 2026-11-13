# SPDX-License-Identifier: Unlicense
"""overpressure.holes: a file's zero runs handed back to the file system, its content unchanged."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path

from overpressure import holes

B = holes.BLOCK


class HoleTests(unittest.TestCase):
    def test_runs_merge_blocks_of_one_kind(self):
        data = bytes(2 * B) + b"x" * (B + 5) + bytes(3 * B)
        found = list(holes.runs(io.BytesIO(data)))
        # The data run ends inside its last block: the block counts as data, the rest of the run as zeros.
        self.assertEqual(found, [(0, 2 * B, True), (2 * B, 2 * B, False), (4 * B, len(data) - 4 * B, True)])
        self.assertEqual(list(holes.runs(io.BytesIO(b""))), [])

    def test_the_file_reads_the_same_and_takes_no_more_space(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "image.bin"
            data = bytes(32 * B) + os.urandom(3 * B) + bytes(16 * B) + b"end"
            path.write_bytes(data)
            before = holes.on_disk(path)
            self.assertEqual(holes.free_zeros(path), 48 * B)
            self.assertEqual(path.read_bytes(), data)
            # Where the file system frees the runs (NTFS, ext4, APFS), the file shrinks on disk; elsewhere it stays.
            self.assertLessEqual(holes.on_disk(path), before)
            (Path(tmp) / "data.bin").write_bytes(b"no zeros")
            self.assertEqual(holes.free_zeros(Path(tmp) / "data.bin"), 0)


if __name__ == "__main__":
    unittest.main()
