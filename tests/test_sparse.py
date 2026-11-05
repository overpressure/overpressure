# SPDX-License-Identifier: Unlicense
import os
import unittest

from overpressure import sparse


def decode(image: bytes) -> bytes:
    """The raw image a sparse image describes, as the bootloader writes it."""
    _magic, _major, _minor, hdr, chunk_hdr, block_size, _blocks, count, _crc = sparse.HEADER.unpack_from(image)
    out, pos = [], hdr
    for _ in range(count):
        kind, _res, size, total = sparse.CHUNK.unpack_from(image, pos)
        body = image[pos + chunk_hdr:pos + total]
        out.append(body if kind == sparse.RAW else body * (size * block_size // 4))
        pos += total
    return b"".join(out)


class SparseTests(unittest.TestCase):
    def test_round_trip_writes_every_block(self):
        b = 4096
        data = (os.urandom(3 * b) + bytes(5 * b) + b"\xab\xcd\xef\x01" * (2 * b // 4) + os.urandom(b)
                + bytes(b))
        image = sparse.encode(data, b)
        self.assertEqual(decode(image), data)
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


if __name__ == "__main__":
    unittest.main()
