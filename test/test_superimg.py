import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

from tools import superimg


def build_super(partitions: dict[str, list[tuple[int, bytes]]], size: int = 1 << 20) -> bytes:
    """A minimal super image: geometry, one metadata copy, linear extents."""
    image = bytearray(size)
    geometry = bytearray(52)
    struct.pack_into("<II", geometry, 0, superimg.GEOMETRY_MAGIC, 52)
    struct.pack_into("<III", geometry, 40, 65536, 2, 4096)
    geometry[8:40] = hashlib.sha256(geometry).digest()
    image[superimg.GEOMETRY_OFFSET:superimg.GEOMETRY_OFFSET + 52] = geometry

    entries, extents = b"", b""
    for name, chunks in partitions.items():
        first = len(extents) // 24
        for sector, data in chunks:
            assert len(data) % 512 == 0
            image[sector * 512:sector * 512 + len(data)] = data
            extents += struct.pack("<QIQI", len(data) // 512, 0, sector, 0)
        entries += struct.pack("<36sIIII", name.encode(), 0, first, len(chunks), 0)
    tables = entries + extents
    header = bytearray(128)
    struct.pack_into("<IHHI", header, 0, superimg.HEADER_MAGIC, 10, 0, 128)
    struct.pack_into("<I", header, 44, len(tables))
    header[48:80] = hashlib.sha256(tables).digest()
    struct.pack_into("<III", header, 80, 0, len(partitions), 52)
    struct.pack_into("<III", header, 92, len(entries), len(extents) // 24, 24)
    header[12:44] = hashlib.sha256(header).digest()
    image[superimg.METADATA_OFFSET:superimg.METADATA_OFFSET + 128] = header
    image[superimg.METADATA_OFFSET + 128:superimg.METADATA_OFFSET + 128 + len(tables)] = tables
    return bytes(image)


class SuperImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.vendor = [(256, b"V" * 1024), (512, b"W" * 512)]
        self.path = self.dir / "super.bin"
        self.path.write_bytes(build_super({"system": [(1024, b"S" * 512)], "vendor": self.vendor}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_lists_partitions(self):
        with self.path.open("rb") as f:
            parts = {p.name: p for p in superimg.read_partitions(f)}
        self.assertEqual(set(parts), {"system", "vendor"})
        self.assertEqual(parts["vendor"].extents, ((256, 2), (512, 1)))
        self.assertEqual(parts["vendor"].size, 1536)

    def test_extracts_extents_in_order(self):
        out = self.dir / "vendor.img"
        superimg.extract(self.path, "vendor", out)
        self.assertEqual(out.read_bytes(), b"V" * 1024 + b"W" * 512)
        with self.assertRaises(FileExistsError):
            superimg.extract(self.path, "vendor", out)

    def test_unknown_partition(self):
        with self.assertRaisesRegex(superimg.SuperImageError, "no partition"):
            superimg.extract(self.path, "odm", self.dir / "odm.img")

    def test_checksums_are_enforced(self):
        for offset, what in ((superimg.GEOMETRY_OFFSET + 44, "geometry"),
                             (superimg.METADATA_OFFSET + 80, "header"),
                             (superimg.METADATA_OFFSET + 128, "table")):
            data = bytearray(self.path.read_bytes())
            data[offset] ^= 0xFF
            bad = self.dir / f"bad-{what}.bin"
            bad.write_bytes(data)
            with self.subTest(what), bad.open("rb") as f, self.assertRaises(superimg.SuperImageError):
                superimg.read_partitions(f)

    def test_extent_past_end_is_rejected(self):
        self.path.write_bytes(build_super({"vendor": [(256, b"V" * 512)]})[:200 * 512])
        with self.assertRaisesRegex(superimg.SuperImageError, "past the image"):
            superimg.extract(self.path, "vendor", self.dir / "v.img")


if __name__ == "__main__":
    unittest.main()
