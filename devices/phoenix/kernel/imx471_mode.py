# SPDX-License-Identifier: Unlicense
"""The RGB camera's register table, taken from PICO OS's camera module at build time.

The sensor's mode is PICO's data, so it stays out of the repository and out of the kernel: this
takes it from `com.qti.sensormodule.*_imx471.bin` in the stock vendor image (`/lib64/camera`) and
writes the file the driver loads at its first stream (`qcom/sm8250/pico4/imx471-mode.bin` in the
system image, from the assets' sensor data), or the C header an older driver compiles in. The
module is Qualcomm's parameter data: 72-byte named nodes, each with the offset and size of its
data, and C structs whose pointers hold a node's id. The table is the `regSetting` list of the
resolution the driver runs, in the file's order. Only one thing is the project's: exposure and gain
are left out, since the driver writes them as controls, under group hold as PICO OS's module does.

    python imx471_mode.py MODULE.bin OUT.bin    (the driver's file)
    python imx471_mode.py MODULE.bin OUT.h      (the header)

The file: TABLE_MAGIC, the number of writes (32-bit little endian), then each write as its
register (16-bit big endian) and its value (one byte). Only the modules in MODULES are taken: the
driver's table was compared with theirs. Anything in the structure this does not expect is refused
as well.
"""

from __future__ import annotations

import hashlib
import struct
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

#: SHA-256 of the module files of the two sensor suppliers in firmware 5.13.7, the same table.
MODULES = {
    "7096fd4ca34540433e17d95623a121d1ad37c5eb5ef2099c6c7edad3088c60fc",
    "95b9dbe7fef237cb0ac7af4fa3799aeb6753cce06de0cdbb19a64733de6e59c3",
}
MAGIC = b"QTI Chromatix Header\0"
#: The resolution the driver runs (its IMX471_WIDTH, IMX471_HEIGHT and the 2x2-binned readout).
WIDTH, HEIGHT, BITS, LINE_LENGTH, FRAME_LENGTH, FRAME_RATE = 2328, 1748, 10, 2560, 4520, 50.0
#: Exposure and gain, the driver's controls (its IMX471_REG_EXPOSURE and IMX471_REG_GAIN).
CONTROLS = (0x0202, 0x0203, 0x0204, 0x0205)
HEADER_SIZE = 160  # the file's header, up to its section table's offset and count
SECTIONS = 2  # the symbols and the data they point into
NODE_SIZE, RESOLUTION_SIZE, RECORD_SIZE, STREAM_SIZE = 72, 304, 80, 64
MAX_REGISTER, MAX_VALUE = 0xFFFF, 0xFF  # 16-bit addresses, 8-bit data
ARGC = 2  # MODULE.bin OUT
#: The driver's file: its magic (the driver's IMX471_MODE_MAGIC), then the count and the writes.
TABLE_MAGIC = b"IMX471M1"
ADDRESS_16_BITS, DATA_8_BITS, OPERATION_WRITE = 2, 1, 0


def nodes(data: bytes) -> dict[int, tuple[str, bytes]]:
    """Return every node by id: (name, its data)."""
    if not data.startswith(MAGIC) or len(data) < HEADER_SIZE or struct.unpack_from("<I", data, 28)[0] != len(data):
        msg = "not a parameter file of the expected size"
        raise ValueError(msg)
    table, count = struct.unpack_from("<II", data, 152)
    if count < SECTIONS or table + count * 24 > len(data):
        msg = "bad section table"
        raise ValueError(msg)
    sections = [struct.unpack_from("<QQQ", data, table + i * 24) for i in range(SECTIONS)]
    if [s[0] for s in sections] != [0, 1] or any(s[1] + s[2] > len(data) for s in sections):
        msg = "bad section table"
        raise ValueError(msg)
    (_, symbols, symbols_size), (_, blob, blob_size) = sections
    if symbols_size % NODE_SIZE:
        msg = "the symbol section is no whole number of nodes"
        raise ValueError(msg)
    found = {}
    for off in range(symbols, symbols + symbols_size, NODE_SIZE):
        node_id = struct.unpack_from("<Q", data, off)[0]
        name = data[off + 8 : off + 40].split(b"\0")[0].decode("latin-1")
        _kind, _parent, offset, size = struct.unpack_from("<4Q", data, off + 40)
        if offset + size > blob_size or node_id in found:
            msg = f"bad node {name!r}"
            raise ValueError(msg)
        found[node_id] = (name, data[blob + offset : blob + offset + size])
    return found


def pointed(table: dict[int, tuple[str, bytes]], pointer: int, name: str, size: int | None = None) -> bytes:
    """Return the data of the node a struct's pointer names, which must be the one the struct means."""
    node = table.get(pointer)
    if node is None or node[0] != name or (size is not None and len(node[1]) != size):
        msg = f"a pointer that should name {name!r} names {node and node[0]!r}"
        raise ValueError(msg)
    return node[1]


def resolution(table: dict[int, tuple[str, bytes]]) -> tuple[int, ...]:
    """Return the one resolution entry of the driver's mode, as its 64-bit fields."""
    entries = [n[1] for n in table.values() if n[0] == "resolutionData"]
    if len(entries) != 1 or not entries[0] or len(entries[0]) % RESOLUTION_SIZE:
        msg = "no list of resolutions"
        raise ValueError(msg)
    matches = []
    for off in range(0, len(entries[0]), RESOLUTION_SIZE):
        fields = struct.unpack_from("<38Q", entries[0], off)
        rate = struct.unpack_from("<d", entries[0], off + 80)[0]
        if fields[1] != 1 or (fields[3], fields[4], rate) != (LINE_LENGTH, FRAME_LENGTH, FRAME_RATE):
            continue
        stream = pointed(table, fields[2], "streamConfiguration", STREAM_SIZE)
        if struct.unpack_from("<QQQ", stream, 32) == (WIDTH, HEIGHT, BITS):
            matches.append(fields)
    if len(matches) != 1:
        msg = f"{len(matches)} resolutions are the driver's mode"
        raise ValueError(msg)
    return matches[0]


def mode(data: bytes) -> list[tuple[int, int]]:
    """Return (register, value) in the file's order, the driver's controls left out."""
    table = nodes(data)
    fields = resolution(table)
    settings = pointed(table, fields[21], "regSetting", fields[20] * RECORD_SIZE)
    pairs = []
    for off in range(0, len(settings), RECORD_SIZE):
        slave, _, reg, values, value, address, width, operation, delays, delay = struct.unpack_from(
            "<10Q", settings, off
        )
        expected = (slave, values, address, width, operation, delays)
        if expected != (0, 1, ADDRESS_16_BITS, DATA_8_BITS, OPERATION_WRITE, 1) or reg > MAX_REGISTER:
            msg = f"record {off // RECORD_SIZE} is not a plain byte write"
            raise ValueError(msg)
        val = struct.unpack("<Q", pointed(table, value, "registerData", 8))[0]
        if val > MAX_VALUE or struct.unpack("<Q", pointed(table, delay, "delayUs", 8))[0]:
            msg = f"record {off // RECORD_SIZE} is not a byte without a delay"
            raise ValueError(msg)
        pairs.append((reg, val))
    regs = [reg for reg, _ in pairs]
    if any(regs.count(reg) != 1 for reg in CONTROLS):
        msg = "the driver's controls are not in the mode, once each"
        raise ValueError(msg)
    return [pair for pair in pairs if pair[0] not in CONTROLS]


def header(pairs: list[tuple[int, int]]) -> str:
    """Generate the C header with the register table."""
    lines = [
        "/* Generated by imx471_mode.py from PICO OS's camera module: the mode's registers. */",
        "static const struct imx471_reg imx471_mode[] = {",
    ]
    lines += [f"\t{{ {reg:#06x}, {val:#04x} }}," for reg, val in pairs]
    return "\n".join([*lines, "};", ""])


def table(pairs: list[tuple[int, int]]) -> bytes:
    """Generate the file the driver loads with the register table."""
    return TABLE_MAGIC + struct.pack("<I", len(pairs)) + b"".join(struct.pack(">HB", r, v) for r, v in pairs)


def known_mode(data: bytes, name: str) -> list[tuple[int, int]]:
    """Return the mode of the camera module @p data (from @p name), which must be one of MODULES."""
    digest = hashlib.sha256(data).hexdigest()
    if digest not in MODULES:
        msg = f"{name}: unknown camera module, sha256 {digest}"
        raise ValueError(msg)
    return mode(data)


def main(argv: Sequence[str] | None = None) -> int:
    """Write the driver's file (OUT.bin) or the header (OUT.h) for a known MODULE.bin."""
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != ARGC:
        print("usage: python imx471_mode.py MODULE.bin OUT.bin|OUT.h", file=sys.stderr)
        return 2
    pairs = known_mode(Path(argv[0]).read_bytes(), argv[0])
    out = Path(argv[1])
    out.write_bytes(header(pairs).encode("utf-8") if out.suffix == ".h" else table(pairs))
    print(f"RGB camera mode ({len(pairs)} registers) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
