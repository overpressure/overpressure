# SPDX-License-Identifier: Unlicense
"""The controllers' LED model, read from PICO OS's library (synthetic data here)."""

from __future__ import annotations

import json
import math
import struct
import tempfile
import unittest
from pathlib import Path
from typing import Any

from devices.phoenix.tooling import ledmodel

BASE = 0xB40000  # the synthetic library's one loadable segment starts here


def ring(side: str) -> list[list[float]]:
    """Return a synthetic 20-LED ring of about 6 cm, mirrored in y for the left hand (not the real model)."""
    sign = -1.0 if side == "left" else 1.0
    return [
        [0.06 * math.cos(i * math.pi / 10), sign * 0.06 * math.sin(i * math.pi / 10), 0.01 * (i % 3)]
        for i in range(ledmodel.LEDS)
    ]


def outward(scale: float = 1.0) -> list[list[float]]:
    """Return unit normals of the synthetic right ring, pointing away from its axis (times `scale`)."""
    return [[scale * math.cos(i * math.pi / 10), scale * math.sin(i * math.pi / 10), 0.0] for i in range(ledmodel.LEDS)]


def library(
    points: dict[str, list[list[float]]] | None = None,
    *,
    mapped: bool = True,
    normals: list[list[float]] | None = None,
) -> bytes:
    """Build a minimal ELF64 with one PT_LOAD segment holding the rings and normals at the expected addresses."""
    points = points or {"left": ring("left"), "right": ring("right")}
    normals = normals or outward()
    data_offset, size = 0x1000, 0x10000
    data = bytearray(size)
    for side, address in ledmodel.MODEL_ADDRESSES.items():
        flat = [v for p in points[side] for v in p]
        struct.pack_into(f"<{len(flat)}f", data, address - BASE, *flat)
    flat = [v for n in normals for v in n]
    struct.pack_into(f"<{len(flat)}d", data, ledmodel.RIGHT_NORMALS_ADDRESS - BASE, *flat)
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<Q", header, 0x20, 64)  # program headers right after the header
    struct.pack_into("<HH", header, 0x36, 56, 1)  # one entry of 56 bytes
    vaddr = BASE if mapped else 0x100000
    phdr = struct.pack("<IIQQQQQQ", 1, 4, data_offset, vaddr, vaddr, size, size, 0x1000)
    image = bytearray(data_offset) + data
    image[:64] = header
    image[64 : 64 + len(phdr)] = phdr
    return bytes(image)


class LedModelTests(unittest.TestCase):
    def test_reads_both_rings_by_virtual_address(self):
        result = ledmodel.models(library())
        for side in ("left", "right"):
            for got, want in zip(result[side], ring(side)):
                for g, w in zip(got, want):
                    self.assertAlmostEqual(g, w, places=6)

    def test_reads_the_normals_and_mirrors_them_for_the_left_hand(self):
        lib = library()
        result = ledmodel.models(lib)
        normal = ledmodel.normals(lib, result)
        for got, want in zip(normal["right"], outward()):
            self.assertEqual(got, want)
        for (lx, ly, lz), (rx, ry, rz) in zip(normal["left"], normal["right"]):
            self.assertEqual((lx, ly, lz), (rx, -ry, rz))
        with self.assertRaises(ledmodel.LedModelError):
            ledmodel.normals(library(normals=outward(2.0)), result)  # not unit vectors
        inward = [[-x, -y, z] for x, y, z in outward()]
        with self.assertRaises(ledmodel.LedModelError):
            ledmodel.normals(library(normals=inward), result)  # facing the ring's centre

    def test_refuses_data_that_is_not_a_controller_ring(self):
        cases: dict[str, dict[str, Any]] = {
            "unmapped": {"mapped": False},
            "metres too large": {
                "points": {
                    "left": [[3 * v for v in p] for p in ring("left")],
                    "right": [[3 * v for v in p] for p in ring("right")],
                }
            },
            "not mirrored": {"points": {"left": ring("right"), "right": ring("right")}},
            "not finite": {"points": {"left": [[math.nan, 0, 0], *ring("left")[1:]], "right": ring("right")}},
            "zeros": {"points": {"left": [[0, 0, 0]] * 20, "right": [[0, 0, 0]] * 20}},
        }
        for name, kwargs in cases.items():
            with self.subTest(name), self.assertRaises(ledmodel.LedModelError):
                ledmodel.models(library(**kwargs))
        with self.assertRaises(ledmodel.LedModelError):
            ledmodel.models(b"not an elf file at all" * 4)

    def test_command_writes_the_models(self):
        with tempfile.TemporaryDirectory() as temp:
            lib, out = Path(temp) / "libpico_controller_tracking.pxr.so", Path(temp) / "controllers/led-models.json"
            lib.write_bytes(library())
            self.assertEqual(ledmodel.main([str(lib), str(out)]), 0)
            value = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(
                (value["schema"], value["units"], len(value["left"]), len(value["right"])), (1, "m", 20, 20)
            )
            self.assertEqual((len(value["normals"]["left"]), len(value["normals"]["right"])), (20, 20))
            self.assertEqual(value["source"], lib.name)
            lib.write_bytes(library(mapped=False))
            self.assertEqual(ledmodel.main([str(lib), str(Path(temp) / "other.json")]), 1)


if __name__ == "__main__":
    unittest.main()
