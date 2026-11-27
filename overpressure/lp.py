# SPDX-License-Identifier: Unlicense
"""Android's dynamic partitions: the logical partitions of a super image, read, and a new layout's metadata.

A super partition starts with 4096 reserved bytes, then its geometry (and a backup copy), then
its metadata slots: a header with SHA-256 checksums, then tables of partitions, their extents,
groups and block devices. Each logical partition is a list of extents, each a run of 512-byte
sectors in super (linear) or zeros. Written from the on-disk format of AOSP's liblp
(metadata_format.h); only what reading needs is parsed, and both checksums are checked. build()
writes the metadata of a new layout (version 10.0), from a full OTA's op list (op_list).

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


# -- writing a new super's metadata (since interface 1.12) -------------------------------------------------------
GEOMETRY = struct.Struct("<II32sIII")  # magic, struct size, checksum, metadata maximum size, slots, logical block
GROUP = struct.Struct("<36sIQ")  # name, flags, maximum size
BLOCK_DEVICE = struct.Struct("<QIIQ36sI")  # first logical sector, alignment, its offset, size, name, flags
HEADER_SIZE = HEADER.size + 4 * DESCRIPTOR.size  # version 10.0's: no flags
#: A partition's attribute: mounted read-only.
READONLY = 1


@dataclass(frozen=True)
class Layout:
    """A super partition's fixed shape: its metadata, where its logical partitions may start, its size.

    alignment_offset puts every extent at a multiple of @p alignment on the whole disk, not in super: it is
    (-super's start in bytes) mod alignment.
    """

    size: int  #: the super partition's size in bytes
    alignment: int
    alignment_offset: int
    metadata_max_size: int = 65536
    slots: int = 2
    block_size: int = 4096
    first_logical_sector: int = 2048  #: 1 MiB: the metadata's region and the first extent's lower bound
    name: str = "super"


def op_list(text: str) -> tuple[str, int, list[tuple[str, int]]]:
    """Return a full OTA's dynamic_partitions_op_list as its group, the group's maximum and its partitions' sizes.

    A full OTA removes everything and builds the layout anew: remove_all_groups, add_group, add, resize. Any
    other operation (move, remove, a second group) belongs to an incremental update and is refused.
    """
    group, maximum, order, sizes = "", 0, [], {}
    for raw in text.splitlines():
        words = raw.split("#", 1)[0].split()
        if not words or words == ["remove_all_groups"]:
            continue
        if words[0] == "add_group" and len(words) == 3 and not group:  # noqa: PLR2004  # the word, a name and a size
            group, maximum = words[1], int(words[2])
        elif words[0] == "add" and len(words) == 3 and words[2] == group and words[1] not in order:  # noqa: PLR2004
            order.append(words[1])
            sizes[words[1]] = 0
        elif words[0] == "resize" and len(words) == 3 and words[1] in sizes:  # noqa: PLR2004
            sizes[words[1]] = int(words[2])
        else:
            msg = f"dynamic_partitions_op_list: {raw.strip()!r} is not a full OTA's layout"
            raise LpError(msg)
    if not group or not order:
        msg = "dynamic_partitions_op_list names no group or no partition"
        raise LpError(msg)
    return group, maximum, [(name, sizes[name]) for name in order]


def _name(text: str) -> bytes:
    raw = text.encode("ascii")
    if len(raw) > 35:  # noqa: PLR2004  # 36 bytes, the last a zero
        msg = f"the name {text!r} is longer than 35 characters"
        raise LpError(msg)
    return raw.ljust(36, b"\0")


def build(
    layout: Layout, group: str, maximum: int, sizes: list[tuple[str, int]]
) -> tuple[bytes, dict[str, tuple[Extent, ...]]]:
    """Lay out @p sizes (name, bytes, in order) in one group and return super's metadata region and the extents.

    The region is super's first first_logical_sector sectors: the reserved bytes, the geometry and its copy, and
    every metadata slot, each with the same metadata (as a flash of an empty super writes it). Each partition gets
    one extent, the next one aligned after the last, read-only, as a full OTA's update lays them out. The
    extents' starts are byte offsets in super, as partitions() reads them back.
    """
    total = sum(size for _name, size in sizes)
    if maximum and total > maximum:
        msg = f"the partitions take {total} bytes, more than their group's {maximum}"
        raise LpError(msg)
    unit = layout.alignment // SECTOR
    offset = (layout.alignment_offset // SECTOR) % unit
    last = layout.size // SECTOR
    sector, extents = layout.first_logical_sector, {}
    entries, extent_rows = [], []
    for name, size in sizes:
        if size % layout.block_size:
            msg = f"{name}'s size {size} is not a multiple of the logical block size"
            raise LpError(msg)
        sector += (offset - sector) % unit
        count = size // SECTOR
        if sector + count > last:
            msg = f"{name} does not fit into {layout.name} ({layout.size} bytes)"
            raise LpError(msg)
        extents[name] = (Extent(sector * SECTOR, size),) if count else ()
        entries.append(PARTITION.pack(_name(name), READONLY, len(extent_rows), 1 if count else 0, 1))
        if count:
            extent_rows.append(EXTENT.pack(count, TARGET_LINEAR, sector, 0))
        sector += count
    groups = [GROUP.pack(_name("default"), 0, 0), GROUP.pack(_name(group), 0, maximum)]
    device = BLOCK_DEVICE.pack(
        layout.first_logical_sector, layout.alignment, layout.alignment_offset, layout.size, _name(layout.name), 0
    )
    tables, descriptors = b"", b""
    for rows, size in ((entries, PARTITION.size), (extent_rows, EXTENT.size), (groups, GROUP.size), ([device], 64)):
        descriptors += DESCRIPTOR.pack(len(tables), len(rows), size)
        tables += b"".join(rows)
    header = bytearray(
        HEADER.pack(HEADER_MAGIC, HEADER_MAJOR, 0, HEADER_SIZE, bytes(32), len(tables), hashlib.sha256(tables).digest())
        + descriptors
    )
    header[12:44] = hashlib.sha256(header).digest()
    metadata = bytes(header) + tables
    if len(metadata) > layout.metadata_max_size:
        msg = "the metadata is larger than its slot"
        raise LpError(msg)
    geometry = bytearray(
        GEOMETRY.pack(
            GEOMETRY_MAGIC, GEOMETRY.size, bytes(32), layout.metadata_max_size, layout.slots, layout.block_size
        )
    )
    geometry[8:40] = hashlib.sha256(geometry).digest()
    slot = metadata.ljust(layout.metadata_max_size, b"\0")
    region = bytes(RESERVED) + (bytes(geometry).ljust(GEOMETRY_SIZE, b"\0") * 2) + slot * (2 * layout.slots)
    if len(region) > layout.first_logical_sector * SECTOR:
        msg = "the metadata does not fit before the first logical sector"
        raise LpError(msg)
    return region.ljust(layout.first_logical_sector * SECTOR, b"\0"), extents
