# SPDX-License-Identifier: Unlicense
"""overpressure.ota: OTA members checked against the zip, and partition images from a full OTA's block images.

The block images are applied as AOSP's updater writes them.
"""

from __future__ import annotations

import hashlib
import io
import json
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from overpressure import ota

B = ota.BLOCK


class TransferListTests(unittest.TestCase):
    def test_rangeset(self):
        self.assertEqual(ota.parse_rangeset("4,0,2,5,6"), [(0, 2), (5, 6)])
        with self.assertRaises(ValueError):
            ota.parse_rangeset("3,0,2,5")

    def test_new_blocks_in_order_zero_and_erase_left_empty(self):
        data = b"".join(bytes([i + 1]) * B for i in range(3))
        transfer = "4\n8\n0\n0\nerase 2,0,8\nnew 2,5,7\nzero 2,0,2\nnew 2,2,3\n"
        out = io.BytesIO()
        size = ota.apply_transfer_list(transfer, io.BytesIO(data), out)
        image = out.getvalue()
        self.assertEqual(size, 8 * B)
        self.assertEqual(len(image), 8 * B)
        self.assertEqual(image[5 * B : 6 * B], b"\x01" * B)
        self.assertEqual(image[6 * B : 7 * B], b"\x02" * B)
        self.assertEqual(image[2 * B : 3 * B], b"\x03" * B)
        self.assertEqual(image[0 : 2 * B], bytes(2 * B))
        self.assertEqual(image[7 * B :], bytes(B))

    def test_short_data_and_unknown_commands_refused(self):
        with self.assertRaises(ValueError):
            ota.apply_transfer_list("4\n4\n0\n0\nnew 2,0,2\n", io.BytesIO(bytes(B)), io.BytesIO())
        with self.assertRaises(ValueError):
            ota.apply_transfer_list("4\n4\n0\n0\nmove 2,0,1\n", io.BytesIO(), io.BytesIO())


def field(number: int, value: int | bytes) -> bytes:
    """Encode one protobuf field: a varint for a number below 128, else length-delimited bytes."""
    if isinstance(value, int):
        return bytes([number << 3, value])
    return bytes([number << 3 | 2, len(value)]) + value


class BarePayloadTests(unittest.TestCase):
    def test_a_bare_payload_gives_the_partition_asked_for(self):
        def partition(name: str, offset: int) -> bytes:
            extent = field(1, 0) + field(2, 1)
            operation = field(1, 0) + field(2, offset) + field(3, 8) + field(6, extent)
            return field(1, name.encode()) + field(7, field(1, 8)) + field(8, operation)

        manifest = field(3, 8) + field(13, partition("boot", 0)) + field(13, partition("recovery", 8))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "payload.bin"
            path.write_bytes(b"CrAU" + struct.pack(">QQI", 2, len(manifest), 0) + manifest + b"bootbootrecovery")

            def log(_line: str) -> None:
                return None

            self.assertEqual(ota.fetch_boot_image(str(path), log=log), b"bootboot")
            self.assertEqual(ota.fetch_boot_image(str(path), log=log, partition="recovery"), b"recovery")


class ExtractTests(unittest.TestCase):
    def test_copied_members_land_with_a_manifest_and_are_kept(self):
        with tempfile.TemporaryDirectory() as temp:
            zip_path, out = Path(temp) / "ota.zip", Path(temp) / "stock"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("boot.img", b"ANDROID!" + bytes(5000))
                zf.writestr("firmware-update/NON-HLOS.bin", b"modem" * 1000)
                zf.writestr("unused.bin", b"x")
            copied = {"boot.img": "boot.img", "modem.bin": "firmware-update/NON-HLOS.bin"}
            manifest = ota.extract("build-1", str(zip_path), out, copied, {}, log=lambda _line: None)
            self.assertEqual(sorted(manifest["files"]), ["boot.img", "modem.bin"])
            self.assertEqual((out / "modem.bin").read_bytes(), b"modem" * 1000)
            self.assertEqual(json.loads((out / "manifest.json").read_text())["firmware"], "build-1")
            self.assertFalse((out / "download" / "unused.bin").exists())
            # A member already downloaded intact is kept; a corrupt one is fetched again.
            (out / "download" / "boot.img").write_bytes(b"corrupt")
            lines = []
            ota.extract("build-1", str(zip_path), out, copied, {}, log=lines.append)
            self.assertIn("  firmware-update/NON-HLOS.bin: already downloaded", lines)
            self.assertEqual((out / "boot.img").read_bytes()[:8], b"ANDROID!")

    def test_members_for_copies_and_block_images(self):
        members = set(ota.members_needed({"a.bin": "dir/a.bin"}, {"system.img": "system"}))
        self.assertEqual(members, {"dir/a.bin", "system.new.dat.br", "system.transfer.list"})


class PinnedMemberTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = b"the loader"
        self.item = ota.PinnedMember(
            "https://vendor.example/ota.zip", "dir/loader.elf", len(self.data), hashlib.sha256(self.data).hexdigest()
        )
        self.reads = []

    def read(self, item: ota.PinnedMember) -> bytes:
        self.reads.append(item)
        return self.data

    def test_the_member_is_hash_checked_before_it_is_written(self):
        path = self.root / "assets" / "loader.elf"
        lines = []
        self.assertEqual(ota.fetch_pinned(self.item, path, lines.append, self.read), path)
        self.assertEqual((path.read_bytes(), self.reads), (self.data, [self.item]))
        self.assertEqual(lines, [f"{path}: {len(self.data)} bytes, SHA-256 checked"])
        ota.fetch_pinned(self.item, path, lines.append, self.read)
        self.assertEqual((len(self.reads), lines[-1]), (1, f"{path}: already there"))
        other = self.root / "other.elf"
        for data in (b"another file", self.data + b"\0"):
            self.data = data
            with self.assertRaisesRegex(
                ValueError, "dir/loader.elf from https://vendor.example/ota.zip is not the known"
            ):
                ota.fetch_pinned(self.item, other, lines.append, self.read)
            self.assertFalse(other.exists())

    def test_a_pinned_member_is_read_from_its_zip(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("dir/loader.elf", self.data)
        zip_path = self.root / "ota.zip"
        zip_path.write_bytes(buffer.getvalue())
        item = ota.PinnedMember(str(zip_path), self.item.member, self.item.size, self.item.sha256)
        self.assertEqual(ota.read_pinned(item, log=lambda _line: None), self.data)


if __name__ == "__main__":
    unittest.main()
