# SPDX-License-Identifier: Unlicense
"""The boot chain's comparison between PICO OS builds, on synthetic signed images and dtbo tables."""

from __future__ import annotations

import struct
import unittest

from devices.phoenix.tooling import bootchain, device


def elf(segments: list[tuple[int, int, bytes]], *, wide: bool = True) -> bytes:
    """Return an ELF with these (type, flags, data) segments, laid out one after another."""
    count = len(segments)
    if wide:
        header_size, entry_size = 64, 56
        header = bytearray(header_size)
        header[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<Q", header, 0x20, header_size)
        struct.pack_into("<HH", header, 0x36, entry_size, count)
    else:
        header_size, entry_size = 52, 32
        header = bytearray(header_size)
        header[:6] = b"\x7fELF\x01\x01"
        struct.pack_into("<I", header, 0x1C, header_size)
        struct.pack_into("<HH", header, 0x2A, entry_size, count)
    offset = header_size + entry_size * count
    table, body = b"", b""
    for i, (p_type, flags, data) in enumerate(segments):
        vaddr = 0x80000000 + i * 0x10000
        if wide:
            table += struct.pack("<IIQQQQQQ", p_type, flags, offset, vaddr, vaddr, len(data), len(data), 4096)
        else:
            table += struct.pack("<IIIIIIII", p_type, offset, vaddr, vaddr, len(data), len(data), flags, 4096)
        body += data
        offset += len(data)
    return bytes(header) + table + body


def image(code: bytes, signature: bytes, *, wide: bool = True) -> bytes:
    """Return a signed image: the headers' copy, the hash segment and one code segment."""
    return elf([(0, 0x07000000, b"headers"), (0, bootchain.QCOM_HASH_SEGMENT, signature), (1, 0x5, code)], wide=wide)


def fdt(compatible: str, board_id: int, payload: bytes) -> bytes:
    """Return a tiny flattened device tree: a root with compatible, qcom,board-id and one more property."""
    strings = b"compatible\0qcom,board-id\0data\0"
    names = {b"compatible": 0, b"qcom,board-id": 11, b"data": 25}

    def prop(name: bytes, value: bytes) -> bytes:
        padded = value + b"\0" * (-len(value) % 4)
        return struct.pack(">III", 3, len(value), names[name]) + padded

    struct_block = struct.pack(">I", 1) + b"\0\0\0\0"
    struct_block += prop(b"compatible", compatible.encode() + b"\0")
    struct_block += prop(b"qcom,board-id", struct.pack(">II", board_id, 0))
    struct_block += prop(b"data", payload)
    struct_block += struct.pack(">II", 2, 9)
    off_struct = 40
    off_strings = off_struct + len(struct_block)
    total = off_strings + len(strings)
    header = struct.pack(
        ">10I", 0xD00DFEED, total, off_struct, off_strings, 40, 17, 16, 0, len(strings), len(struct_block)
    )
    return header + struct_block + strings


def dtbo(overlays: list[bytes]) -> bytes:
    """Return an Android dtbo image (DT table) holding these overlays in this order."""
    header_size, entry_size = 32, 32
    offset = header_size + entry_size * len(overlays)
    entries, body = b"", b""
    for overlay in overlays:
        entries += struct.pack(">IIIIIIII", len(overlay), offset, 0, 0, 0, 0, 0, 0)
        body += overlay
        offset += len(overlay)
    header = struct.pack(
        ">IIIIIIII", bootchain.DT_TABLE_MAGIC, offset, header_size, entry_size, len(overlays), header_size, 4096, 0
    )
    return header + entries + body


class ImageTests(unittest.TestCase):
    def test_a_new_signature_over_the_same_code_is_resigned(self):
        for wide in (True, False):
            with self.subTest(wide=wide):
                ours = image(b"the code", b"signed in October", wide=wide)
                self.assertEqual(bootchain.compare_image(ours, ours), bootchain.IDENTICAL)
                theirs = image(b"the code", b"signed in September, longer", wide=wide)
                self.assertEqual(bootchain.compare_image(ours, theirs), bootchain.RESIGNED)
                changed = image(b"the c0de", b"signed in October", wide=wide)
                self.assertEqual(bootchain.compare_image(ours, changed), bootchain.DIFFERS)

    def test_not_an_elf(self):
        with self.assertRaises(ValueError):
            bootchain.code_segments(b"MZ not an ELF")


class DtboTests(unittest.TestCase):
    def test_overlays_match_by_their_board_in_any_order(self):
        kona = fdt("qcom,kona-mtp", 0x10008, b"panel")
        iot = fdt("qcom,kona-iot-mtp", 0x10008, b"panel")  # the same board-id, another board
        hdk = fdt("qcom,kona-hdk", 0x1001F, b"hdk")
        ours, reordered = dtbo([kona, iot, hdk]), dtbo([hdk, iot, kona])
        self.assertNotEqual(ours, reordered)
        self.assertEqual(bootchain.compare_dtbo(ours, reordered), bootchain.IDENTICAL)
        changed = dtbo([hdk, iot, fdt("qcom,kona-mtp", 0x10008, b"PANEL")])
        self.assertEqual(bootchain.compare_dtbo(ours, changed), f"{bootchain.DIFFERS}: 1 of 3 overlays")
        self.assertEqual(
            bootchain.compare_dtbo(ours, dtbo([kona, hdk])), f"{bootchain.DIFFERS}: other boards' overlays"
        )

    def test_two_overlays_for_one_board_are_refused(self):
        kona = fdt("qcom,kona-mtp", 0x10008, b"one")
        with self.assertRaises(ValueError):
            bootchain.dtbo_overlays(dtbo([kona, fdt("qcom,kona-mtp", 0x10008, b"two")]))


class VerdictTests(unittest.TestCase):
    def members(self, *, xbl: bytes = b"xbl", signature: bytes = b"sig") -> dict[str, bytes]:
        members = {name: image(name.encode(), signature) for name in bootchain.BOOT_CHAIN}
        members["xbl.elf"] = image(xbl, signature)
        members[bootchain.DTBO] = dtbo([fdt("qcom,kona-mtp", 0x10008, b"panel")])
        return members

    def test_same_code_needs_every_member(self):
        ours = self.members()
        result = bootchain.compare(ours, self.members(signature=b"another"))
        self.assertTrue(bootchain.same_code(result))
        self.assertEqual(set(result.values()), {bootchain.RESIGNED, bootchain.IDENTICAL})
        self.assertFalse(bootchain.same_code(bootchain.compare(ours, self.members(xbl=b"other xbl"))))
        missing = self.members()
        del missing["aop.mbn"]
        self.assertEqual(bootchain.compare(ours, missing)["aop.mbn"], bootchain.MISSING)
        self.assertFalse(bootchain.same_code(bootchain.compare(ours, missing)))

    def test_the_abl_is_not_compared(self):
        # The install replaces it with the engineering ABL.
        self.assertNotIn("abl.elf", bootchain.BOOT_CHAIN)

    def test_the_accepted_builds_are_global_seko(self):
        for version, region, state in device.VERIFIED_FIRMWARES | device.SAME_CODE_FIRMWARES:
            self.assertEqual((region, state), ("global", "true"), version)


if __name__ == "__main__":
    unittest.main()
