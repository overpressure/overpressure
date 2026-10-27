"""Read Android logical partitions (vendor, system, ...) from a super image.

Only what the project needs: the primary metadata copy, with its geometry,
header and table checksums verified, and linear extents on the first block
device. Usage:

    python -m overpressure.superimg list SUPER
    python -m overpressure.superimg extract SUPER NAME OUTPUT
"""

from __future__ import annotations

import hashlib
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

GEOMETRY_OFFSET = 4096
GEOMETRY_MAGIC = 0x616C4467
METADATA_OFFSET = 4096 + 2 * 4096
HEADER_MAGIC = 0x414C5030
SECTOR = 512
TARGET_LINEAR = 0


class SuperImageError(ValueError):
    pass


@dataclass(frozen=True)
class Partition:
    name: str
    extents: tuple[tuple[int, int], ...]  # (start sector, sector count)

    @property
    def size(self) -> int:
        return sum(count for _start, count in self.extents) * SECTOR


def _check(ok: bool, what: str) -> None:
    if not ok:
        raise SuperImageError(what)


def read_partitions(f) -> list[Partition]:
    f.seek(GEOMETRY_OFFSET)
    geometry = bytearray(f.read(52))
    _check(len(geometry) == 52, "truncated geometry")
    magic, size = struct.unpack_from("<II", geometry)
    _check(magic == GEOMETRY_MAGIC and size == 52, "not a super image (geometry magic)")
    checksum = bytes(geometry[8:40])
    geometry[8:40] = bytes(32)
    _check(hashlib.sha256(geometry).digest() == checksum, "geometry checksum mismatch")
    max_size = struct.unpack_from("<I", geometry, 40)[0]

    f.seek(METADATA_OFFSET)
    head = f.read(128)
    _check(len(head) == 128, "truncated metadata header")
    magic, major, minor, header_size = struct.unpack_from("<IHHI", head)
    _check(magic == HEADER_MAGIC and major == 10 and minor <= 2, "unsupported metadata header")
    _check(128 <= header_size <= 4096, "bad metadata header size")
    f.seek(METADATA_OFFSET)
    header = bytearray(f.read(header_size))
    checksum = bytes(header[12:44])
    header[12:44] = bytes(32)
    _check(hashlib.sha256(header).digest() == checksum, "metadata header checksum mismatch")
    tables_size = struct.unpack_from("<I", header, 44)[0]
    _check(header_size + tables_size <= max_size, "metadata larger than its slot")
    tables = f.read(tables_size)
    _check(len(tables) == tables_size and hashlib.sha256(tables).digest() == bytes(header[48:80]),
           "metadata table checksum mismatch")

    p_off, p_count, p_size = struct.unpack_from("<III", header, 80)
    e_off, e_count, e_size = struct.unpack_from("<III", header, 92)
    _check(p_size == 52 and e_size == 24, "unexpected table entry sizes")
    _check(p_off + p_count * p_size <= tables_size and e_off + e_count * e_size <= tables_size,
           "tables out of range")
    partitions = []
    for i in range(p_count):
        raw_name, _attrs, first, count, _group = struct.unpack_from("<36sIIII", tables, p_off + i * p_size)
        _check(first + count <= e_count, "extent index out of range")
        extents = []
        for j in range(first, first + count):
            sectors, kind, start, device = struct.unpack_from("<QIQI", tables, e_off + j * e_size)
            _check(kind == TARGET_LINEAR and device == 0, "only linear extents on the super device are supported")
            extents.append((start, sectors))
        name = raw_name.split(b"\0")[0].decode("ascii")
        partitions.append(Partition(name, tuple(extents)))
    return partitions


def extract(super_path: Path, name: str, output: Path) -> Partition:
    """Write partition NAME to OUTPUT (which must not exist) and return it."""
    total = super_path.stat().st_size
    with super_path.open("rb") as f:
        found = [p for p in read_partitions(f) if p.name == name]
        _check(len(found) == 1, f"no partition named {name!r}")
        part = found[0]
        with output.open("xb") as out:
            for start, sectors in part.extents:
                _check((start + sectors) * SECTOR <= total, f"{name} extends past the image")
                f.seek(start * SECTOR)
                left = sectors * SECTOR
                while left:
                    chunk = f.read(min(left, 1 << 20))
                    _check(bool(chunk), "short read")
                    out.write(chunk)
                    left -= len(chunk)
    return part


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "list":
        with open(argv[1], "rb") as f:
            for p in read_partitions(f):
                print(f"{p.name}\t{p.size}")
        return 0
    if len(argv) == 4 and argv[0] == "extract":
        part = extract(Path(argv[1]), argv[2], Path(argv[3]))
        print(f"{part.name}\t{part.size}")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
