import os
import struct
import unittest

from pico4 import sparse


class SparseTests(unittest.TestCase):
    def test_round_trip_writes_every_block(self):
        b = 4096
        data = (os.urandom(3 * b) + bytes(5 * b) + b"\xab\xcd\xef\x01" * (2 * b // 4) + os.urandom(b)
                + bytes(b))
        image = sparse.encode(data, b)
        self.assertEqual(sparse.decode(image), data)
        # Zero and repeated-word runs are FILL chunks, not skipped (DONT_CARE).
        magic, _, _, hdr, _, size, blocks, count, _ = sparse.HEADER.unpack_from(image)
        self.assertEqual((magic, size, blocks, count), (sparse.MAGIC, b, 12, 5))
        kinds, pos = [], hdr
        for _ in range(count):
            kind, _, n, total = sparse.CHUNK.unpack_from(image, pos)
            kinds.append((kind, n))
            pos += total
        self.assertEqual(kinds, [(sparse.RAW, 3), (sparse.FILL, 5), (sparse.FILL, 2), (sparse.RAW, 1),
                                 (sparse.FILL, 1)])
        self.assertLess(len(image), 5 * b)

    def test_partial_block_is_refused(self):
        with self.assertRaises(ValueError):
            sparse.encode(b"x" * 4097)
        with self.assertRaises(ValueError):
            sparse.decode(struct.pack("<I", 0) * 7)


if __name__ == "__main__":
    unittest.main()
