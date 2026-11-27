# SPDX-License-Identifier: Unlicense
"""overpressure.lp: logical partitions out of synthetic super images; assets.BackupFolder on them."""

from __future__ import annotations

import hashlib
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path

from overpressure import assets, lp
from tests.images import Ext4Image

SECTOR = lp.SECTOR


def super_image(parts: dict[str, list[tuple[int, bytes | None]]], size: int) -> bytes:
    """Return a super image of @p size bytes: each partition's extents as (sector, data or None for zeros)."""
    image = bytearray(size)
    partitions, extents = b"", b""
    for name, runs in parts.items():
        first = len(extents) // lp.EXTENT.size
        for sector, data in runs:
            if data is None:
                extents += lp.EXTENT.pack(sector, lp.TARGET_ZERO, 0, 0)  # sectors: the run's length
            else:
                extents += lp.EXTENT.pack(len(data) // SECTOR, lp.TARGET_LINEAR, sector, 0)
                image[sector * SECTOR : sector * SECTOR + len(data)] = data
        partitions += lp.PARTITION.pack(name.encode(), 0, first, len(runs), 0)
    tables = partitions + extents
    descriptors = b"".join(
        lp.DESCRIPTOR.pack(*d)
        for d in (
            (0, len(parts), lp.PARTITION.size),
            (len(partitions), len(extents) // lp.EXTENT.size, lp.EXTENT.size),
            (len(tables), 0, 48),
            (len(tables), 0, 64),
        )
    )
    header_size = lp.HEADER.size + len(descriptors)
    fixed = lp.HEADER.pack(
        lp.HEADER_MAGIC, lp.HEADER_MAJOR, 0, header_size, bytes(32), len(tables), hashlib.sha256(tables).digest()
    )
    header = bytearray(fixed + descriptors)
    header[12:44] = hashlib.sha256(header).digest()
    geometry = bytearray(struct.pack("<II32sIII", lp.GEOMETRY_MAGIC, 52, bytes(32), 65536, 2, 4096))
    geometry[8:40] = hashlib.sha256(geometry).digest()
    image[lp.RESERVED : lp.RESERVED + len(geometry)] = geometry
    start = lp.RESERVED + 2 * lp.GEOMETRY_SIZE
    image[start : start + len(header) + len(tables)] = bytes(header) + tables
    return bytes(image)


class PartitionTests(unittest.TestCase):
    def test_a_partition_reads_across_its_extents_and_zeros(self):
        first, second = bytes(range(256)) * 8, b"\xaa" * 1024  # 4 and 2 sectors
        image = super_image(
            {"system": [(200, first), (2, None), (100, second)], "vendor": [(300, b"v" * 512)]}, 400 * SECTOR
        )
        f = io.BytesIO(image)
        found = lp.partitions(f)
        self.assertEqual(found["system"][0], lp.Extent(200 * SECTOR, len(first)))
        system = lp.LogicalPartition(f, found["system"])
        self.assertEqual(system.size, len(first) + 2 * SECTOR + len(second))
        self.assertEqual(system.read(), first + bytes(2 * SECTOR) + second)
        system.seek(len(first) - 3)
        self.assertEqual(system.read(5), first[-3:] + b"\0\0")  # across the boundary
        self.assertEqual(lp.LogicalPartition(f, found["vendor"]).read(), b"v" * 512)

    def test_damaged_metadata_and_other_images_are_refused(self):
        image = bytearray(super_image({"system": [(100, b"s" * 512)]}, 200 * SECTOR))
        image[lp.RESERVED + 2 * lp.GEOMETRY_SIZE + 200] ^= 1  # inside the tables
        with self.assertRaisesRegex(lp.LpError, "checksum"):
            lp.partitions(io.BytesIO(bytes(image)))
        with self.assertRaisesRegex(lp.LpError, "not a super image"):
            lp.partitions(io.BytesIO(bytes(200 * SECTOR)))


#: A full OTA's layout as its op list writes it (made-up sizes), with a comment and a blank line.
OP_LIST = """# Remove all existing dynamic partitions and groups before applying full OTA
remove_all_groups
add_group main 1073741824

add first main
add second main
resize first 8388608
resize second 4096
"""
#: A super of 64 MiB that starts 32 KiB past a 512 KiB boundary on its disk, as the PICO 4's does.
LAYOUT = lp.Layout(size=64 << 20, alignment=512 << 10, alignment_offset=(512 - 32) << 10)


class WriterTests(unittest.TestCase):
    """A new super's metadata, laid out from a full OTA's op list, reads back with partitions()."""

    def test_the_op_list_gives_the_group_and_the_sizes_in_order(self):
        self.assertEqual(lp.op_list(OP_LIST), ("main", 1 << 30, [("first", 8 << 20), ("second", 4096)]))
        for incremental in ("move first main", "remove first", "add_group other 1\n" + OP_LIST):
            with self.subTest(incremental=incremental), self.assertRaises(lp.LpError):
                lp.op_list(OP_LIST + incremental + "\n")

    def test_the_layout_reads_back_aligned_on_the_whole_disk(self):
        group, maximum, sizes = lp.op_list(OP_LIST)
        region, extents = lp.build(LAYOUT, group, maximum, sizes)
        self.assertEqual(len(region), LAYOUT.first_logical_sector * lp.SECTOR)
        self.assertEqual(lp.partitions(io.BytesIO(region)), extents)  # both checksums hold
        starts = [e[0].start for e in extents.values()]
        # Each extent starts on a 512 KiB boundary of the disk: 32 KiB into super's own boundaries is the disk's.
        self.assertEqual([(s + (32 << 10)) % (512 << 10) for s in starts], [0, 0])
        self.assertEqual(starts[0], (1 << 20) + (480 << 10))  # the first boundary after the metadata's 1 MiB
        self.assertGreaterEqual(starts[1], starts[0] + (8 << 20))

    def test_every_slot_and_the_geometry_copy_are_the_same(self):
        region, _extents = lp.build(LAYOUT, *lp.op_list(OP_LIST))
        geometry = region[lp.RESERVED : lp.RESERVED + lp.GEOMETRY_SIZE]
        self.assertEqual(region[lp.RESERVED + lp.GEOMETRY_SIZE : lp.RESERVED + 2 * lp.GEOMETRY_SIZE], geometry)
        first = lp.RESERVED + 2 * lp.GEOMETRY_SIZE
        slots = [region[first + i * 65536 : first + (i + 1) * 65536] for i in range(4)]
        self.assertEqual(len(set(slots)), 1)
        self.assertEqual(struct.unpack_from("<III", geometry, 40), (65536, 2, 4096))

    def test_what_does_not_fit_is_refused(self):
        with self.assertRaisesRegex(lp.LpError, "more than their group"):
            lp.build(LAYOUT, "main", 4096, [("first", 8192)])
        with self.assertRaisesRegex(lp.LpError, "does not fit"):
            lp.build(LAYOUT, "main", 0, [("first", 64 << 20)])
        with self.assertRaisesRegex(lp.LpError, "logical block size"):
            lp.build(LAYOUT, "main", 0, [("first", 1000)])


class BackupFolderTests(unittest.TestCase):
    def test_images_come_out_of_the_backups_partitions_checked_against_its_manifest(self):
        img = Ext4Image()
        img.file("/firmware/a.fw", b"A" * 3000, 0o644)
        fs = img.build()
        fs += bytes(-len(fs) % SECTOR)
        half = len(fs) // 2 // SECTOR * SECTOR
        image = super_image(
            {"vendor": [(64, fs[:half]), (64 + len(fs) // SECTOR + 8, fs[half:])]}, 2 * len(fs) + 64 * SECTOR * 4
        )
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "lun0").mkdir()
            (root / "lun0/super.bin").write_bytes(image)
            (root / "lun4").mkdir()
            (root / "lun4/modem.bin").write_bytes(b"not used")
            files = {
                n: {"size": len(d), "sha256": hashlib.sha256(d).hexdigest()}
                for n, d in (("lun0/super.bin", image), ("lun4/modem.bin", b"not used"))
            }
            fingerprint = "Pico/Phoenix_ovs/PICOA8110:10/5.13.7/x:user/dev-keys"
            manifest = {"schema": 1, "identity": {"fingerprint": fingerprint}, "files": files}
            (root / "manifest.json").write_text(json.dumps(manifest))
            images = {"vendor.img": ("super", "vendor"), "dsp.bin": ("dsp", None)}
            with assets.BackupFolder(root, images) as stock:
                self.assertEqual(stock.firmware, fingerprint)
                self.assertEqual(stock.ext4("vendor.img").read("/firmware/a.fw"), b"A" * 3000)
                with self.assertRaisesRegex(assets.AssetError, "no dsp partition"):
                    stock.ext4("dsp.bin")
            (root / "lun0/super.bin").write_bytes(image[:-1] + b"x")
            with assets.BackupFolder(root, images) as stock, self.assertRaisesRegex(assets.AssetError, "sha256"):
                stock.ext4("vendor.img")


if __name__ == "__main__":
    unittest.main()
