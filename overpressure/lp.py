# SPDX-License-Identifier: Unlicense
"""Android's dynamic partitions: the logical partitions of a super image, read-only.

A super partition starts with 4096 reserved bytes, then its geometry (and a backup copy), then
its metadata slots: a header with SHA-256 checksums, then tables of partitions, their extents,
groups and block devices. Each logical partition is a list of extents, each a run of 512-byte
sectors in super (linear) or zeros. Written from the on-disk format of AOSP's liblp
(metadata_format.h); only what reading needs is parsed, and both checksums are checked.

    with open("super.bin", "rb") as f:
        system = LogicalPartition(f, partitions(f)["system"])
        fs = Ext4(system)
"""

from __future__ import annotations

import hashlib
import io
import struct
from dataclasses import dataclass
from typing import IO

SECTOR = 512
RESERVED = 4096
GEOMETRY_SIZE = 4096
GEOMETRY_MAGIC = 0x616C4467
HEADER_MAGIC = 0x414C5030
HEADER_MAJOR = 10
#: The header's fixed part, up to its table descriptors: magic, versions, size, checksum, tables' size and checksum.
HEADER = struct.Struct("<IHHI32sI32s")
DESCRIPTOR = struct.Struct("<III")  # offset in the tables, entries, entry size
PARTITION = struct.Struct("<36sIIII")  # name, attributes, first extent, extents, group
EXTENT = struct.Struct("<QIQI")  # sectors, target type, target data (a sector of super), block device
TARGET_LINEAR, TARGET_ZERO = 0, 1


class LpError(ValueError):
    """Not a super image, or metadata this reader refuses to guess about."""


@dataclass(frozen=True)
class Extent:
    """A run of a logical partition: @p size bytes at @p start in super, or zeros."""

    start: int
    size: int
    zero: bool = False


def _metadata(file: IO[bytes]) -> bytes:
    file.seek(RESERVED)
    geometry = file.read(GEOMETRY_SIZE)
    magic, struct_size = struct.unpack_from("<II", geometry, 0)
    if magic != GEOMETRY_MAGIC:
        msg = "not a super image (no LP geometry)"
        raise LpError(msg)
    checksum = geometry[8:40]
    if hashlib.sha256(geometry[:8] + bytes(32) + geometry[40:struct_size]).digest() != checksum:
        msg = "the super image's geometry fails its checksum"
        raise LpError(msg)
    max_size = struct.unpack_from("<I", geometry, 40)[0]
    file.seek(RESERVED + 2 * GEOMETRY_SIZE)  # slot 0, after the geometry and its backup
    return file.read(max_size)


def partitions(file: IO[bytes]) -> dict[str, tuple[Extent, ...]]:
    """Return the logical partitions of the super image in @p file (slot 0) and their extents."""
    data = _metadata(file)
    magic, major, _minor, header_size, header_sum, tables_size, tables_sum = HEADER.unpack_from(data, 0)
    if magic != HEADER_MAGIC or major != HEADER_MAJOR:
        msg = f"unknown LP metadata (magic {magic:#x}, version {major})"
        raise LpError(msg)
    header = bytearray(data[:header_size])
    header[12:44] = bytes(32)
    if hashlib.sha256(header).digest() != header_sum:
        msg = "the LP metadata header fails its checksum"
        raise LpError(msg)
    tables = data[header_size : header_size + tables_size]
    if len(tables) != tables_size or hashlib.sha256(tables).digest() != tables_sum:
        msg = "the LP metadata tables fail their checksum"
        raise LpError(msg)
    descriptors = [DESCRIPTOR.unpack_from(data, HEADER.size + i * DESCRIPTOR.size) for i in range(4)]
    (p_off, p_count, p_size), (e_off, e_count, e_size) = descriptors[:2]
    extents = []
    for i in range(e_count):
        sectors, target, sector, device = EXTENT.unpack_from(tables, e_off + i * e_size)
        if target not in (TARGET_LINEAR, TARGET_ZERO) or (target == TARGET_LINEAR and device != 0):
            msg = f"extent {i} lies outside this super partition (type {target}, device {device})"
            raise LpError(msg)
        extents.append(Extent(sector * SECTOR, sectors * SECTOR, zero=target == TARGET_ZERO))
    found = {}
    for i in range(p_count):
        raw, _attributes, first, count, _group = PARTITION.unpack_from(tables, p_off + i * p_size)
        name = raw.split(b"\0", 1)[0].decode("ascii")
        if first + count > len(extents):
            msg = f"partition {name} names extents beyond the table"
            raise LpError(msg)
        found[name] = tuple(extents[first : first + count])
    return found


class LogicalPartition(io.RawIOBase):
    """A logical partition of a super image as one seekable read-only file."""

    def __init__(self, file: IO[bytes], extents: tuple[Extent, ...]) -> None:
        """Read @p extents out of the super image open in @p file."""
        super().__init__()
        self._file = file
        self._extents = extents
        self.size = sum(e.size for e in extents)
        self._pos = 0

    def readable(self) -> bool:
        """Tell io that it reads."""
        return True

    def seekable(self) -> bool:
        """Tell io that it seeks."""
        return True

    def tell(self) -> int:
        """Return the position in the logical partition."""
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        """Move to @p offset (from the start, the position or the end, as @p whence says)."""
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self.size}[whence]
        self._pos = max(0, base + offset)
        return self._pos

    def readinto(self, buffer: bytearray | memoryview) -> int:  # ty: ignore[invalid-method-override]  # io's buffer protocol
        """Read into @p buffer from the position on, across extents; 0 at the end."""
        view = memoryview(buffer).cast("B")
        done, start = 0, 0
        for extent in self._extents:
            end = start + extent.size
            while done < len(view) and start <= self._pos < end:
                count = min(len(view) - done, end - self._pos)
                if extent.zero:
                    view[done : done + count] = bytes(count)
                else:
                    self._file.seek(extent.start + self._pos - start)
                    chunk = self._file.read(count)
                    if len(chunk) != count:
                        msg = "the super image ends inside an extent"
                        raise LpError(msg)
                    view[done : done + count] = chunk
                done += count
                self._pos += count
            start = end
        return done
