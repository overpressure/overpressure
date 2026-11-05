# SPDX-License-Identifier: Unlicense
"""Nominal LED models of the PICO 4 controllers, from PICO OS's controller tracking library.

PICO OS tracks each controller by a ring of 20 infrared LEDs. Its tracking library
(`/system/lib64/libpico_controller_tracking.pxr.so` in the system image) carries a nominal
model of each ring as 20 points (x, y, z float32, metres) and hands the left one, then
the right one, to its pose predictor, together with each LED's unit normal (x, y, z
float64; the left normals are the right ones with y negated), which decides from where an
LED can be seen. scripts/extract-sensor-data.sh reads both from the
headset's own firmware; the values are vendor data and stay out of the repository.

    python -m devices.phoenix.tooling.ledmodel LIBRARY OUTPUT.json
"""

from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path

LEDS = 20
#: Virtual addresses of the left and right models in the PICO OS 5.13.7 library.
MODEL_ADDRESSES = {"left": 0xB47FF0, "right": 0xB480E0}
#: The right LEDs' unit normals (60 float64) in the same library; the left are mirrored in y.
RIGHT_NORMALS_ADDRESS = 0xB4CC20
#: Plausibility: every LED within this distance of the model's origin (metres).
MIN_RADIUS_M, MAX_RADIUS_M = 0.005, 0.15
#: The left ring is the right one mirrored in y, point by point, within this (metres).
MIRROR_TOLERANCE_M = 0.005


class LedModelError(ValueError):
    pass


def read_virtual(elf: bytes, address: int, size: int) -> bytes:
    """Bytes at a virtual address of a 64-bit little-endian ELF, from its file-backed PT_LOAD segments."""
    if elf[:4] != b"\x7fELF" or elf[4] != 2 or elf[5] != 1:
        raise LedModelError("not a 64-bit little-endian ELF file")
    phoff, = struct.unpack_from("<Q", elf, 0x20)
    phentsize, phnum = struct.unpack_from("<HH", elf, 0x36)
    for i in range(phnum):
        kind, _, offset, vaddr, _, filesz, _, _ = struct.unpack_from("<IIQQQQQQ", elf, phoff + i * phentsize)
        if kind == 1 and vaddr <= address and address + size <= vaddr + filesz:
            start = offset + address - vaddr
            return elf[start:start + size]
    raise LedModelError(f"address {address:#x} is not in a file-backed segment")


def models(elf: bytes) -> dict[str, list[list[float]]]:
    """Both nominal models, validated."""
    result = {}
    for side, address in MODEL_ADDRESSES.items():
        values = struct.unpack(f"<{3 * LEDS}f", read_virtual(elf, address, 3 * LEDS * 4))
        result[side] = [list(values[3 * i:3 * i + 3]) for i in range(LEDS)]
    validate(result)
    return result


def normals(elf: bytes, result: dict[str, list[list[float]]]) -> dict[str, list[list[float]]]:
    """Both sides' LED normals, checked against the models of `result`."""
    values = struct.unpack(f"<{3 * LEDS}d", read_virtual(elf, RIGHT_NORMALS_ADDRESS, 3 * LEDS * 8))
    right = [list(values[3 * i:3 * i + 3]) for i in range(LEDS)]
    out = {"right": right, "left": [[x, -y, z] for x, y, z in right]}
    for side in ("left", "right"):
        if any(not all(math.isfinite(v) for v in n) or abs(math.hypot(*n) - 1.0) > 1e-5 for n in out[side]):
            raise LedModelError(f"{side}: the LED normals are not unit vectors")
    # The LEDs face away from the ring's centre: most normals share the sign of y with their LED.
    centre_y = sum(p[1] for p in result["right"]) / LEDS
    agree = sum(1 for n, p in zip(right, result["right"]) if n[1] * (p[1] - centre_y) > 0)
    if agree < LEDS // 2:
        raise LedModelError("the LED normals do not belong to these models")
    return out


def validate(result: dict[str, list[list[float]]]) -> None:
    """A different library version puts other data at these addresses: refuse anything implausible."""
    for side in ("left", "right"):
        points = result.get(side) or []
        if len(points) != LEDS or any(len(p) != 3 or not all(math.isfinite(v) for v in p) for p in points):
            raise LedModelError(f"{side}: expected {LEDS} finite points")
        radii = [math.hypot(*p) for p in points]
        if min(radii) < MIN_RADIUS_M or max(radii) > MAX_RADIUS_M:
            raise LedModelError(f"{side}: LEDs {min(radii):.3f}-{max(radii):.3f} m from the origin, not a controller ring")
    worst = max(math.dist((x, -y, z), r) for (x, y, z), r in zip(result["left"], result["right"]))
    if worst > MIRROR_TOLERANCE_M:
        raise LedModelError(f"the left model is not the right one mirrored in y ({worst * 1000:.1f} mm off)")


def to_json(result: dict[str, list[list[float]]], normal: dict[str, list[list[float]]], library: str) -> str:
    return json.dumps({
        "schema": 1,
        "source": library,
        "units": "m",
        "frame": "the nominal model frame of PICO OS's controller tracking",
        "left": result["left"],
        "right": result["right"],
        "normals": normal,
    }, indent=1) + "\n"


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    library, output = Path(args[0]), Path(args[1])
    try:
        data = library.read_bytes()
        result = models(data)
        normal = normals(data, result)
    except (OSError, LedModelError) as exc:
        print(f"{library}: {exc}", file=sys.stderr)
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(to_json(result, normal, library.name), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
