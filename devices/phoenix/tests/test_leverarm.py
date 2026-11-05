# SPDX-License-Identifier: Unlicense
from __future__ import annotations

import json
import math
import struct
import tempfile
import unittest
from pathlib import Path

from devices.phoenix.tooling import leverarm

BASE = 0x30000  # the synthetic library's one loadable segment starts here


def library(left=(-5.0, -10.0), right=(5.0, -10.0), z=4.0, mapped: bool = True) -> bytes:
    """A minimal ELF64 with one PT_LOAD segment holding synthetic levers (mm) at the expected addresses."""
    data_offset, size = 0x1000, 0x20000
    data = bytearray(size)
    struct.pack_into("<2d", data, leverarm.XY_ADDRESSES["left"] - BASE, *left)
    struct.pack_into("<2d", data, leverarm.XY_ADDRESSES["right"] - BASE, *right)
    struct.pack_into("<d", data, leverarm.Z_ADDRESS - BASE, z)
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<Q", header, 0x20, 64)
    struct.pack_into("<HH", header, 0x36, 56, 1)
    vaddr = BASE if mapped else 0x200000
    phdr = struct.pack("<IIQQQQQQ", 1, 4, data_offset, vaddr, vaddr, size, size, 0x1000)
    image = bytearray(data_offset) + data
    image[:64] = header
    image[64:64 + len(phdr)] = phdr
    return bytes(image)


class LeverArmTests(unittest.TestCase):
    def test_reads_both_hands_in_metres(self):
        result = leverarm.lever_arms(library())
        self.assertEqual(result, {"left": [-0.005, -0.01, 0.004], "right": [0.005, -0.01, 0.004]})

    def test_refuses_data_that_is_not_a_lever(self):
        cases = {
            "unmapped": dict(mapped=False),
            "zeros": dict(left=(0.0, 0.0), right=(0.0, 0.0), z=0.0),
            "too long": dict(left=(-150.0, 0.0), right=(150.0, 0.0)),
            "not mirrored": dict(right=(5.0, -11.0)),
            "not finite": dict(z=math.nan),
        }
        for name, kwargs in cases.items():
            with self.subTest(name), self.assertRaises(leverarm.LeverArmError):
                leverarm.lever_arms(library(**kwargs))
        with self.assertRaises(leverarm.LeverArmError):
            leverarm.lever_arms(b"not an elf file at all" * 4)

    def test_command_writes_the_levers(self):
        with tempfile.TemporaryDirectory() as temp:
            lib, out = Path(temp) / "libpico_controller_tracking.client.so", Path(temp) / "controllers/lever-arm.json"
            lib.write_bytes(library())
            self.assertEqual(leverarm.main([str(lib), str(out)]), 0)
            value = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual((value["schema"], value["units"], value["source"]), (1, "m", lib.name))
            self.assertEqual(value["left"], [-0.005, -0.01, 0.004])
            lib.write_bytes(library(mapped=False))
            self.assertEqual(leverarm.main([str(lib), str(Path(temp) / "other.json")]), 1)


if __name__ == "__main__":
    unittest.main()
