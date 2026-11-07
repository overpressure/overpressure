# SPDX-License-Identifier: Unlicense
"""A read-only FAT12/16/32 reader with long file names: the firmware partitions' images.

Written from the FAT specification (Microsoft's "FAT: General Overview of On-Disk Format"): the
BIOS parameter block in the boot sector, the FAT type from the count of data clusters, the fixed
root directory of FAT12/16 and FAT32's root cluster, cluster chains, and VFAT's long names, whose
entries precede the short entry and carry its checksum. A file is found by its long name or its
8.3 name, case-insensitively as FAT compares names. Nothing is written.

    with Fat.open("NON-HLOS.bin") as fs:
        for entry in fs.glob("/image", "venus.*"):
            data = fs.read_entry(entry)
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import struct
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, TYPE_CHECKING

if TYPE_CHECKING:
    import os
    from collections.abc import Iterator
    from types import TracebackType

    from typing_extensions import Self  # typing's from Python 3.11 on

ENTRY = 32
ATTR_VOLUME = 0x08
ATTR_DIRECTORY = 0x10
ATTR_LFN = 0x0F
DELETED = 0xE5
#: The FAT type follows from the number of data clusters (the specification's limits).
FAT12_CLUSTERS = 4085
FAT16_CLUSTERS = 65525
#: A long name entry's sequence number: the last (first stored) one has this bit.
LFN_LAST = 0x40
#: NT's case bits for an 8.3 name without a long name (byte 12): base and extension in lower case.
LOWER_BASE, LOWER_EXT = 0x08, 0x10
#: The 13 UCS-2 characters of a long name entry, by their byte offsets.
LFN_CHARS = (1, 3, 5, 7, 9, 14, 16, 18, 20, 22, 24, 28, 30)


class FatError(ValueError):
    """The image is not FAT, is damaged, or lacks what was asked for."""


@dataclass(frozen=True)
class Entry:
    """A directory entry: its long (or 8.3) name, the 8.3 name, kind, first cluster, size and time."""

    name: str
    short_name: str
    is_dir: bool
    cluster: int
    size: int
    mtime: dt.datetime | None  #: the write time, local time as FAT keeps it; None when not valid


def _checksum(short: bytes) -> int:
    """Return the checksum of an 11-byte 8.3 name that its long name entries carry."""
    total = 0
    for byte in short:
        total = (((total & 1) << 7) | (total >> 1)) + byte & 0xFF
    return total


def _short_name(raw: bytes, case: int) -> str:
    base, ext = raw[:8].rstrip(b" "), raw[8:11].rstrip(b" ")
    if base[:1] == b"\x05":  # a name that begins with 0xE5
        base = b"\xe5" + base[1:]
    name, extension = base.decode("cp437"), ext.decode("cp437")
    if case & LOWER_BASE:
        name = name.lower()
    if case & LOWER_EXT:
        extension = extension.lower()
    return f"{name}.{extension}" if extension else name


def _time(date: int, time: int) -> dt.datetime | None:
    try:
        return dt.datetime(  # noqa: DTZ001  # FAT keeps local time without a zone
            1980 + (date >> 9), (date >> 5) & 15, date & 31, time >> 11, (time >> 5) & 63, (time & 31) * 2
        )
    except ValueError:
        return None


class Fat:
    """A FAT12, FAT16 or FAT32 image, read through a seekable binary file."""

    def __init__(self, file: IO[bytes], *, close: bool = False) -> None:
        """Read the boot sector and the first FAT of the image in @p file."""
        self._file = file
        self._close = close
        boot = self._read(0, 512)
        if len(boot) < 512 or boot[510:512] != b"\x55\xaa":  # noqa: PLR2004  # the boot sector's size and signature
            msg = "not a FAT image (no boot sector signature)"
            raise FatError(msg)
        (self.sector_size, self.cluster_sectors, reserved, fats, root_entries, total16, _media, fat16) = (
            struct.unpack_from("<HBHBHHBH", boot, 11)
        )
        total32, fat32, self._root_cluster = (
            struct.unpack_from("<I", boot, 32)[0],
            *struct.unpack_from("<I4xI", boot, 36),
        )
        if self.sector_size not in (512, 1024, 2048, 4096) or not self.cluster_sectors or not fats:
            msg = "not a FAT image (impossible boot sector values)"
            raise FatError(msg)
        fat_sectors = fat16 or fat32
        total = total16 or total32
        root_sectors = -(-root_entries * ENTRY // self.sector_size)
        self._fat_offset = reserved * self.sector_size
        self._root_offset = (reserved + fats * fat_sectors) * self.sector_size
        self._root_size = root_entries * ENTRY
        self._data_offset = self._root_offset + root_sectors * self.sector_size
        self.cluster_size = self.cluster_sectors * self.sector_size
        self.clusters = (total - (reserved + fats * fat_sectors + root_sectors)) // self.cluster_sectors
        self.bits = 12 if self.clusters < FAT12_CLUSTERS else 16 if self.clusters < FAT16_CLUSTERS else 32
        self._fat = self._read(self._fat_offset, fat_sectors * self.sector_size)

    @classmethod
    def open(cls, path: str | os.PathLike[str]) -> Fat:
        """Open the image at @p path (closed with the reader)."""
        return cls(Path(path).open("rb"), close=True)

    def close(self) -> None:
        """Close the image's file when this reader opened it."""
        if self._close:
            self._file.close()

    def __enter__(self) -> Self:
        """Use the reader in a with statement."""
        return self

    def __exit__(
        self, kind: type[BaseException] | None, value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the image's file when this reader opened it."""
        self.close()

    def _read(self, offset: int, size: int) -> bytes:
        self._file.seek(offset)
        return self._file.read(size)

    # -- clusters -------------------------------------------------------------------------------
    def next_cluster(self, cluster: int) -> int | None:
        """Return the cluster after @p cluster in its chain, None at the chain's end."""
        if self.bits == 12:  # noqa: PLR2004  # FAT12's packed entries
            pair = struct.unpack_from("<H", self._fat, cluster + cluster // 2)[0]
            value, end = (pair >> 4 if cluster & 1 else pair & 0xFFF), 0xFF8
        elif self.bits == 16:  # noqa: PLR2004  # FAT16
            value, end = struct.unpack_from("<H", self._fat, cluster * 2)[0], 0xFFF8
        else:
            value, end = struct.unpack_from("<I", self._fat, cluster * 4)[0] & 0x0FFFFFFF, 0x0FFFFFF8
        if value >= end:
            return None
        if not 2 <= value < self.clusters + 2:  # noqa: PLR2004  # the first data cluster
            msg = f"cluster {cluster} links to {value:#x}, outside the data area"
            raise FatError(msg)
        return value

    def chain(self, first: int) -> Iterator[int]:
        """Yield the clusters of the chain that starts at @p first; a chain that comes back is refused."""
        seen: set[int] = set()
        cluster: int | None = first
        while cluster is not None:
            if cluster in seen:
                msg = f"the cluster chain from {first} loops"
                raise FatError(msg)
            seen.add(cluster)
            yield cluster
            cluster = self.next_cluster(cluster)

    def _cluster(self, cluster: int) -> bytes:
        return self._read(self._data_offset + (cluster - 2) * self.cluster_size, self.cluster_size)

    def chunks(self, entry: Entry) -> Iterator[bytes]:
        """Yield a file's data, a cluster at a time."""
        left = entry.size
        if left and entry.cluster < 2:  # noqa: PLR2004  # the first data cluster
            msg = f"{entry.name} has data but no cluster"
            raise FatError(msg)
        for cluster in self.chain(entry.cluster) if left else ():
            data = self._cluster(cluster)[:left]
            left -= len(data)
            yield data
            if not left:
                return
        if left:
            msg = f"{entry.name}'s cluster chain ends before its size"
            raise FatError(msg)

    def read_entry(self, entry: Entry) -> bytes:
        """Return a file's data."""
        return b"".join(self.chunks(entry))

    # -- names ----------------------------------------------------------------------------------
    def _directory_data(self, entry: Entry | None) -> bytes:
        if entry is None and self.bits != 32:  # noqa: PLR2004  # FAT12/16: the fixed root directory
            return self._read(self._root_offset, self._root_size)
        first = self._root_cluster if entry is None else entry.cluster
        if first < 2:  # noqa: PLR2004  # ".." of a first-level directory points at the root as 0
            return self._directory_data(None)
        return b"".join(self._cluster(c) for c in self.chain(first))

    def entries(self, directory: Entry | None = None) -> Iterator[Entry]:
        """Yield the entries of @p directory (None: the root), without ".", ".." and the volume label."""
        data = self._directory_data(directory)
        long_parts: dict[int, str] = {}
        long_sum = -1
        for at in range(0, len(data) - ENTRY + 1, ENTRY):
            raw = data[at : at + ENTRY]
            if raw[0] == 0:
                return
            if raw[0] == DELETED:
                long_parts, long_sum = {}, -1
                continue
            attr = raw[11]
            if attr & 0x3F == ATTR_LFN:
                order = raw[0] & 0x1F
                if raw[0] & LFN_LAST:
                    long_parts, long_sum = {}, raw[13]
                chars = b"".join(raw[i : i + 2] for i in LFN_CHARS).decode("utf-16-le", "replace")
                long_parts[order] = chars
                continue
            if attr & ATTR_VOLUME:
                long_parts, long_sum = {}, -1
                continue
            short = _short_name(raw[:11], raw[12])
            name = short
            if long_parts and long_sum == _checksum(raw[:11]) and set(long_parts) == set(range(1, len(long_parts) + 1)):
                name = "".join(long_parts[i] for i in sorted(long_parts)).split("\0", 1)[0]
            long_parts, long_sum = {}, -1
            if short in (".", ".."):
                continue
            high, wtime, wdate, low, size = struct.unpack_from("<HHHHI", raw, 20)
            yield Entry(name, short, bool(attr & ATTR_DIRECTORY), high << 16 | low, size, _time(wdate, wtime))

    def lookup(self, path: str) -> Entry | None:
        """Return the entry at absolute @p path (None for the root directory)."""
        entry: Entry | None = None
        done = PurePosixPath("/")
        for part in PurePosixPath("/", path).parts[1:]:
            if entry is not None and not entry.is_dir:
                msg = f"{done} is not a directory"
                raise FatError(msg)
            wanted = part.lower()
            found = next(
                (e for e in self.entries(entry) if wanted in (e.name.lower(), e.short_name.lower())),
                None,
            )
            if found is None:
                msg = f"no {done / part} in the image"
                raise FatError(msg)
            entry, done = found, done / part
        return entry

    def listdir(self, path: str = "/") -> list[str]:
        """Return the names in the directory at @p path, in the directory's order."""
        return [e.name for e in self.entries(self._directory(path))]

    def _directory(self, path: str) -> Entry | None:
        entry = self.lookup(path)
        if entry is not None and not entry.is_dir:
            msg = f"{path} is not a directory"
            raise FatError(msg)
        return entry

    def read(self, path: str) -> bytes:
        """Return the file at @p path."""
        entry = self.lookup(path)
        if entry is None or entry.is_dir:
            msg = f"{path} is not a file"
            raise FatError(msg)
        return self.read_entry(entry)

    def glob(self, path: str, pattern: str) -> list[Entry]:
        """Return the files in the directory at @p path whose long or 8.3 name matches @p pattern.

        Matching ignores case, as FAT's names do; directories are left out.
        """
        wanted = pattern.lower()
        return [
            e
            for e in self.entries(self._directory(path))
            if not e.is_dir
            and (fnmatch.fnmatchcase(e.name.lower(), wanted) or fnmatch.fnmatchcase(e.short_name.lower(), wanted))
        ]
