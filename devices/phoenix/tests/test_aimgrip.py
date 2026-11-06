# SPDX-License-Identifier: Unlicense
"""The controllers' aim and grip transforms, read from PICO OS's library (synthetic data here)."""

from __future__ import annotations

import json
import math
import struct
import tempfile
import unittest
from pathlib import Path
from typing import Any

from devices.phoenix.tooling import aimgrip

BASE = 0x70000  # the synthetic library's one loadable segment starts here


def block(
    angle_deg: float,
    axis: tuple[float, float, float] = (1.0, 0.0, 0.0),
    position: tuple[float, float, float] = (0.01, 0.02, 0.03),
) -> list[float]:
    """Return a synthetic transform: a position and a rotation about `axis` (not the real values)."""
    half = math.radians(angle_deg) / 2
    n = math.sqrt(sum(a * a for a in axis))
    return list(position) + [math.cos(half)] + [math.sin(half) * a / n for a in axis]


def library(values: list[float] | None = None, *, mapped: bool = True) -> bytes:
    """Build a minimal ELF64 with one PT_LOAD segment holding 14 doubles at the expected address."""
    values = values if values is not None else block(20) + block(-10, position=(-0.01, 0.02, 0.03))
    data_offset, size = 0x1000, 0x10000
    data = bytearray(size)
    struct.pack_into(f"<{len(values)}d", data, aimgrip.DEFAULTS_ADDRESS - BASE, *values)
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<Q", header, 0x20, 64)
    struct.pack_into("<HH", header, 0x36, 56, 1)
    vaddr = BASE if mapped else 0x200000
    phdr = struct.pack("<IIQQQQQQ", 1, 4, data_offset, vaddr, vaddr, size, size, 0x1000)
    image = bytearray(data_offset) + data
    image[:64] = header
    image[64 : 64 + len(phdr)] = phdr
    return bytes(image)


class AimGripTests(unittest.TestCase):
    def test_reads_the_left_then_the_right_block(self):
        result = aimgrip.transforms(library())
        want = {"left": block(20), "right": block(-10, position=(-0.01, 0.02, 0.03))}
        for hand in aimgrip.HANDS:
            self.assertEqual(result[hand]["position"], want[hand][:3])
            self.assertEqual(result[hand]["orientation"], want[hand][3:])

    def test_refuses_data_that_is_not_a_transform(self):
        cases: dict[str, dict[str, Any]] = {
            "unmapped": {"mapped": False},
            "not a unit quaternion": {"values": [*block(20)[:3], 1, 1, 0, 0, *block(20)]},
            "too far": {"values": block(20, position=(0.0, 0.0, 0.25)) + block(20)},
            "not finite": {"values": [math.nan, *block(20)[1:], *block(20)]},
            "zeros": {"values": [0.0] * 14},
        }
        for name, kwargs in cases.items():
            with self.subTest(name), self.assertRaises(aimgrip.AimGripError):
                aimgrip.transforms(library(**kwargs))
        with self.assertRaises(aimgrip.AimGripError):
            aimgrip.transforms(b"not an elf file at all" * 4)

    def test_command_writes_the_transforms(self):
        with tempfile.TemporaryDirectory() as temp:
            lib, out = Path(temp) / "libpvrtrackingservice.so", Path(temp) / "controllers/aim-to-grip.json"
            lib.write_bytes(library())
            self.assertEqual(aimgrip.main([str(lib), str(out)]), 0)
            value = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual((value["schema"], value["units"], value["source"]), (1, "m", lib.name))
            self.assertEqual(value["left"]["orientation"], block(20)[3:])
            self.assertEqual(len(value["right"]["position"]), 3)
            lib.write_bytes(library(mapped=False))
            self.assertEqual(aimgrip.main([str(lib), str(Path(temp) / "other.json")]), 1)


if __name__ == "__main__":
    unittest.main()
