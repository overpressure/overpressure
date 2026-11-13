# SPDX-License-Identifier: Unlicense
"""Files without their zero runs on disk: a backup's partition images are mostly empty space.

A file's runs of zero blocks are handed back to the file system (a sparse file): the file reads the
same, byte for byte, and keeps its size, but its zeros take no space. Windows (NTFS) marks the file
sparse and zeroes the ranges (FSCTL_SET_SPARSE, FSCTL_SET_ZERO_DATA), Linux punches holes
(fallocate), macOS too (F_PUNCHHOLE). Only ranges read as zero here are touched, so a file system
that cannot do it (FAT, exFAT) keeps the file as it is.
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys
from typing import IO, TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

#: The unit zero runs are found in: NTFS frees a sparse file's space in 64 KiB units.
BLOCK = 1 << 16


def runs(stream: IO[bytes], count: Callable[[int], None] | None = None) -> Iterator[tuple[int, int, bool]]:
    """Yield (offset, length, is_zero) runs of a file read from its start, in BLOCK units, merged.

    @p count is told the bytes as they are read.
    """
    zero = bytes(BLOCK)
    offset, start, kind = 0, 0, None
    for block in iter(lambda: stream.read(BLOCK), b""):
        if count is not None:
            count(len(block))
        empty = block == zero[: len(block)]
        if kind is None:
            kind = empty
        elif empty != kind:
            yield start, offset - start, kind
            start, kind = offset, empty
        offset += len(block)
    if kind is not None:
        yield start, offset - start, kind


class _Windows:
    FSCTL_SET_SPARSE = 0x000900C4
    FSCTL_SET_ZERO_DATA = 0x000980C8

    def __init__(self, stream: IO[bytes]) -> None:
        import msvcrt  # noqa: PLC0415  # Windows only

        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # Windows only
        self.handle = msvcrt.get_osfhandle(stream.fileno())  # Windows only
        self._control(self.FSCTL_SET_SPARSE, b"")

    def _control(self, code: int, data: bytes) -> None:
        returned = ctypes.c_ulong()
        buffer = ctypes.create_string_buffer(data, len(data)) if data else None
        ok = self.kernel32.DeviceIoControl(
            ctypes.c_void_p(self.handle), code, buffer, len(data), None, 0, ctypes.byref(returned), None
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())  # Windows only

    def free(self, offset: int, length: int) -> None:
        self._control(self.FSCTL_SET_ZERO_DATA, struct.pack("<qq", offset, offset + length))


class _Linux:
    FALLOC_FL_KEEP_SIZE = 0x01
    FALLOC_FL_PUNCH_HOLE = 0x02

    def __init__(self, stream: IO[bytes]) -> None:
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.fallocate.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int64, ctypes.c_int64]
        self.fd = stream.fileno()

    def free(self, offset: int, length: int) -> None:
        mode = self.FALLOC_FL_KEEP_SIZE | self.FALLOC_FL_PUNCH_HOLE
        if self.libc.fallocate(self.fd, mode, offset, length) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))


class _MacOS:
    F_PUNCHHOLE = 99

    def __init__(self, stream: IO[bytes]) -> None:
        self.fd = stream.fileno()

    def free(self, offset: int, length: int) -> None:
        import fcntl  # noqa: PLC0415  # POSIX only

        fcntl.fcntl(self.fd, self.F_PUNCHHOLE, struct.pack("IIqq", 0, 0, offset, length))


def _freer(stream: IO[bytes]) -> _Windows | _Linux | _MacOS:
    if sys.platform == "win32":
        return _Windows(stream)
    if sys.platform == "darwin":
        return _MacOS(stream)
    return _Linux(stream)


def free_zeros(path: Path, count: Callable[[int], None] | None = None) -> int:
    """Hand @p path's zero runs back to the file system; return the bytes they hold (0: not supported here).

    The file's content and size stay as they are. @p count is told the bytes as they are read.
    """
    freed = 0
    with path.open("r+b") as stream:
        zero_runs = [(o, n) for o, n, empty in runs(stream, count) if empty]
        if not zero_runs:
            return 0
        try:
            freer = _freer(stream)
            for offset, length in zero_runs:
                freer.free(offset, length)
                freed += length
        except (OSError, AttributeError, ValueError):
            return freed  # the file system keeps the zeros: the file reads the same either way
    return freed


def on_disk(path: Path) -> int:
    """Return the bytes @p path takes on disk (its size where the file system does not tell)."""
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # Windows only
        kernel32.GetCompressedFileSizeW.restype = ctypes.c_ulong
        high = ctypes.c_ulong()
        low = kernel32.GetCompressedFileSizeW(str(path), ctypes.byref(high))
        return (high.value << 32) | low
    blocks = getattr(path.stat(), "st_blocks", None)
    return blocks * 512 if blocks is not None else path.stat().st_size
