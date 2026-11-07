# SPDX-License-Identifier: Unlicense
"""A read-only ext4 reader: files, directories and symlinks out of an image, without mounting it.

Written from the on-disk format (the kernel's Documentation/filesystems/ext4): the superblock at
byte 1024, the group descriptors (32 bytes, or s_desc_size with the 64bit feature), the inode
tables, extent trees and the old block map, linear directories (an htree's index blocks read as
empty entries, so a linear scan sees every name). Nothing is written and no journal is replayed:
an image is read as its blocks are, which is what an OTA's or a backup's image holds. Holes and
uninitialized extents read as zeros. Features that change how data is found and are not handled
(inline data, encryption, compression, meta_bg) are refused by name instead of read wrongly.

    with Ext4.open("vendor.img") as fs:
        data = fs.read("/firmware/a650_sqe.fw")
        for path, inode in fs.walk("/etc/sensors"):
            ...

Its command line, for the Linux build scripts (scripts/stock-lib.sh), needs only Python 3.9+ and
this file's package:

    python3 -m overpressure.ext4 cat IMAGE PATH > DEST    a regular file of the image to stdout
"""

from __future__ import annotations

import stat
import struct
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, TYPE_CHECKING

if TYPE_CHECKING:
    import os
    from collections.abc import Iterator
    from types import TracebackType

    from typing_extensions import Self  # typing's from Python 3.11 on

MAGIC = 0xEF53
SUPERBLOCK = 1024
ROOT_INODE = 2
EXTENT_MAGIC = 0xF30A
EXTENTS_FL = 0x80000
INLINE_DATA_FL = 0x10000000
#: An extent longer than this is uninitialized: it reads as zeros.
INIT_MAX_LEN = 32768
#: The inode's i_block: 15 32-bit words, a fast symlink's target or the extent tree's root.
I_BLOCK = 60
DIRECT_BLOCKS = 12
GOOD_OLD_INODE_SIZE = 128
#: i_mtime_extra's place after the 128-byte inode: it is there when i_extra_isize reaches it.
MTIME_EXTRA_END = 12
INCOMPAT_FILETYPE = 0x2
INCOMPAT_64BIT = 0x80
#: Incompatible features this reader refuses: they change where or how file data is stored.
REFUSED = {
    0x1: "compression",
    0x8: "journal_dev",
    0x10: "meta_bg",
    0x1000: "dirdata",
    0x8000: "inline_data",
    0x10000: "encrypt",
}
CHUNK = 1 << 20
#: Directory entry file types (with the filetype feature).
FILE_TYPES = {1: "file", 2: "dir", 3: "chr", 4: "blk", 5: "fifo", 6: "sock", 7: "symlink"}


class Ext4Error(ValueError):
    """The image is not ext4, uses what this reader refuses, or lacks what was asked for."""


@dataclass(frozen=True)
class Inode:
    """One inode: its type and permissions, owner, size, times and where its data is."""

    number: int
    mode: int
    uid: int
    gid: int
    size: int
    mtime: int  #: seconds since the epoch
    mtime_ns: int  #: nanoseconds within that second (0 without the extra fields)
    links: int
    flags: int
    blocks: int  #: 512-byte sectors in use (i_blocks_lo)
    file_acl: int
    block: bytes  #: i_block: the extent tree's root, the block map or a fast symlink's target

    @property
    def is_dir(self) -> bool:
        """Tell whether this is a directory."""
        return stat.S_ISDIR(self.mode)

    @property
    def is_file(self) -> bool:
        """Tell whether this is a regular file."""
        return stat.S_ISREG(self.mode)

    @property
    def is_symlink(self) -> bool:
        """Tell whether this is a symbolic link."""
        return stat.S_ISLNK(self.mode)

    @property
    def permissions(self) -> int:
        """Return the permission bits (with setuid, setgid and sticky)."""
        return stat.S_IMODE(self.mode)


@dataclass(frozen=True)
class Run:
    """Contiguous file blocks: logical block, physical block (0: a hole), length, initialized."""

    logical: int
    physical: int
    length: int
    initialized: bool = True


class Ext4:
    """An ext4 (or ext2/ext3) image, read through a seekable binary file."""

    def __init__(self, file: IO[bytes], *, close: bool = False) -> None:
        """Read the superblock and the group descriptors of the image in @p file."""
        self._file = file
        self._close = close
        sb = self._read(SUPERBLOCK, 1024)
        if len(sb) < 1024 or struct.unpack_from("<H", sb, 0x38)[0] != MAGIC:  # noqa: PLR2004  # a superblock's size
            msg = "not an ext2/3/4 image (no superblock magic)"
            raise Ext4Error(msg)
        (blocks_lo, self.first_data_block, log_block, self.blocks_per_group) = struct.unpack_from(
            "<I12xII4xI", sb, 0x04
        )
        self.inodes_per_group = struct.unpack_from("<I", sb, 0x28)[0]
        revision = struct.unpack_from("<I", sb, 0x4C)[0]
        self.inode_size = struct.unpack_from("<H", sb, 0x58)[0] if revision else GOOD_OLD_INODE_SIZE
        self.incompat = struct.unpack_from("<I", sb, 0x60)[0]  # s_feature_incompat
        refused = [name for bit, name in REFUSED.items() if self.incompat & bit]
        if refused:
            msg = f"the image uses {', '.join(refused)}, which this reader does not handle"
            raise Ext4Error(msg)
        self.block_size = 1024 << log_block
        wide = bool(self.incompat & INCOMPAT_64BIT)
        self.desc_size = struct.unpack_from("<H", sb, 0xFE)[0] if wide else 32
        blocks_hi = struct.unpack_from("<I", sb, 0x150)[0] if wide else 0
        self.blocks_count = blocks_lo | blocks_hi << 32
        if not self.blocks_per_group or not self.inodes_per_group or self.desc_size < 32:  # noqa: PLR2004  # the smallest descriptor
            msg = "the superblock's geometry is impossible"
            raise Ext4Error(msg)
        groups = -(-(self.blocks_count - self.first_data_block) // self.blocks_per_group)
        table = self._read((self.first_data_block + 1) * self.block_size, groups * self.desc_size)
        self._inode_tables = []
        for g in range(groups):
            entry = table[g * self.desc_size : (g + 1) * self.desc_size]
            lo = struct.unpack_from("<I", entry, 0x08)[0]
            hi = struct.unpack_from("<I", entry, 0x28)[0] if wide and self.desc_size >= 64 else 0  # noqa: PLR2004  # the 64-bit descriptor
            self._inode_tables.append(lo | hi << 32)

    @classmethod
    def open(cls, path: str | os.PathLike[str]) -> Ext4:
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

    # -- blocks and inodes ------------------------------------------------------------------------
    def _read(self, offset: int, size: int) -> bytes:
        self._file.seek(offset)
        return self._file.read(size)

    def _block(self, number: int) -> bytes:
        data = self._read(number * self.block_size, self.block_size)
        if len(data) != self.block_size:
            msg = f"block {number} lies beyond the image's end"
            raise Ext4Error(msg)
        return data

    def inode(self, number: int) -> Inode:
        """Return inode @p number."""
        if number < 1 or (number - 1) // self.inodes_per_group >= len(self._inode_tables):
            msg = f"no inode {number}"
            raise Ext4Error(msg)
        group, index = divmod(number - 1, self.inodes_per_group)
        raw = self._read(self._inode_tables[group] * self.block_size + index * self.inode_size, self.inode_size)
        mode, uid, size_lo, mtime, gid, links, blocks, flags = struct.unpack_from("<HHI8xI4xHHII", raw, 0)
        size_hi, file_acl_lo = struct.unpack_from("<II", raw, 0x6C)[0], struct.unpack_from("<I", raw, 0x68)[0]
        file_acl_hi, uid_hi, gid_hi = struct.unpack_from("<HHH", raw, 0x76)
        mtime_s, mtime_ns = struct.unpack("<i", struct.pack("<I", mtime))[0], 0
        if self.inode_size > GOOD_OLD_INODE_SIZE:
            extra = struct.unpack_from("<H", raw, 0x80)[0]
            if extra >= MTIME_EXTRA_END:
                mtime_extra = struct.unpack_from("<I", raw, 0x88)[0]
                mtime_s += (mtime_extra & 3) << 32
                mtime_ns = mtime_extra >> 2
        return Inode(
            number,
            mode,
            uid | uid_hi << 16,
            gid | gid_hi << 16,
            size_lo | size_hi << 32,
            mtime_s,
            mtime_ns,
            links,
            flags,
            blocks,
            file_acl_lo | file_acl_hi << 32,
            raw[0x28 : 0x28 + I_BLOCK],
        )

    def runs(self, inode: Inode) -> list[Run]:
        """Return where @p inode's data blocks are, in logical order (holes left out)."""
        if inode.flags & INLINE_DATA_FL:
            msg = f"inode {inode.number} keeps its data inline, which this reader does not handle"
            raise Ext4Error(msg)
        if inode.flags & EXTENTS_FL:
            runs: list[Run] = []
            self._extents(inode.block, runs, depth_limit=8)
            return sorted(runs, key=lambda r: r.logical)
        return self._block_map(inode)

    def _extents(self, node: bytes, runs: list[Run], depth_limit: int) -> None:
        magic, entries, _maximum, depth = struct.unpack_from("<HHHH", node, 0)
        if magic != EXTENT_MAGIC or depth_limit < 0:
            msg = "a broken extent tree"
            raise Ext4Error(msg)
        for i in range(entries):
            at = 12 + 12 * i
            if depth == 0:
                logical, length, start_hi, start_lo = struct.unpack_from("<IHHI", node, at)
                initialized = length <= INIT_MAX_LEN
                runs.append(
                    Run(
                        logical,
                        start_lo | start_hi << 32,
                        length if initialized else length - INIT_MAX_LEN,
                        initialized,
                    )
                )
            else:
                _logical, leaf_lo, leaf_hi = struct.unpack_from("<IIH", node, at)
                self._extents(self._block(leaf_lo | leaf_hi << 32), runs, depth_limit - 1)

    def _indirect(self, block: int, level: int, limit: int) -> list[int]:
        """Return up to @p limit block numbers below an indirect block of @p level (0: a hole)."""
        per_block = self.block_size // 4
        if block == 0:
            return [0] * min(per_block**level, limit)
        entries = struct.unpack(f"<{per_block}I", self._block(block))
        if level == 1:
            return list(entries[:limit])
        out: list[int] = []
        for entry in entries:
            if len(out) >= limit:
                break
            out += self._indirect(entry, level - 1, limit - len(out))
        return out

    def _block_map(self, inode: Inode) -> list[Run]:
        """Return the runs of an inode without extents: 12 direct, then indirect blocks."""
        words = struct.unpack("<15I", inode.block)
        needed = -(-inode.size // self.block_size)
        numbers: list[int] = list(words[:DIRECT_BLOCKS])[:needed]
        for level, block in enumerate(words[DIRECT_BLOCKS:], 1):
            if len(numbers) >= needed:
                break
            numbers += self._indirect(block, level, needed - len(numbers))
        runs: list[Run] = []
        for logical, physical in enumerate(numbers):
            if not physical:
                continue
            last = runs[-1] if runs else None
            if last and last.logical + last.length == logical and last.physical + last.length == physical:
                runs[-1] = Run(last.logical, last.physical, last.length + 1)
            else:
                runs.append(Run(logical, physical, 1))
        return runs

    def chunks(self, inode: Inode, size: int = CHUNK) -> Iterator[bytes]:
        """Yield @p inode's data in pieces of at most @p size bytes; holes give zeros."""
        left, position = inode.size, 0  # position: the next logical byte
        bs = self.block_size
        for run in self.runs(inode):
            start = run.logical * bs
            if start >= inode.size:
                break
            while position < start and left:  # a hole before this run
                n = min(size, start - position, left)
                yield bytes(n)
                position, left = position + n, left - n
            end = min((run.logical + run.length) * bs, inode.size)
            while position < end:
                n = min(size, end - position)
                if run.initialized:
                    data = self._read(run.physical * bs + (position - start), n)
                    if len(data) != n:
                        msg = f"inode {inode.number}'s data lies beyond the image's end"
                        raise Ext4Error(msg)
                else:
                    data = bytes(n)
                yield data
                position, left = position + n, left - n
        while left:  # a hole at the end
            n = min(size, left)
            yield bytes(n)
            left -= n

    def data(self, inode: Inode) -> bytes:
        """Return @p inode's whole data."""
        return b"".join(self.chunks(inode))

    # -- names ----------------------------------------------------------------------------------
    def entries(self, inode: Inode) -> Iterator[tuple[str, int, str]]:
        """Yield a directory's entries as (name, inode number, type), without "." and "..".

        An htree's index blocks hold no live entries (inode 0), so they are skipped like the
        checksum tail and deleted entries. Names are UTF-8; others are decoded with escapes.
        """
        if not inode.is_dir:
            msg = f"inode {inode.number} is not a directory"
            raise Ext4Error(msg)
        typed = bool(self.incompat & INCOMPAT_FILETYPE)
        data = self.data(inode)
        bs = self.block_size
        for base in range(0, len(data), bs):
            block, at = data[base : base + bs], 0
            while at + 8 <= len(block):
                number, rec_len, name_len, kind = struct.unpack_from("<IHBB", block, at)
                # 0 or 65535 in a 64 KiB block: the whole block; else the low two bits are bits 16-17.
                rec_len = bs if rec_len in (0, 65535) else (rec_len & 65532) | (rec_len & 3) << 16
                if rec_len < 8 or at + rec_len > len(block):  # noqa: PLR2004  # an entry's fixed part
                    msg = f"a broken directory entry in inode {inode.number}"
                    raise Ext4Error(msg)
                if not typed:
                    name_len |= kind << 8
                if number:
                    name = block[at + 8 : at + 8 + name_len].decode("utf-8", "surrogateescape")
                    if name not in (".", ".."):
                        yield name, number, FILE_TYPES.get(kind, "?") if typed else "?"
                at += rec_len

    def lookup(self, path: str) -> Inode:
        """Return the inode at absolute @p path; symbolic links on the way are not followed."""
        inode = self.inode(ROOT_INODE)
        done = PurePosixPath("/")
        for part in PurePosixPath("/", path).parts[1:]:
            if not inode.is_dir:
                msg = f"{done} is not a directory"
                raise Ext4Error(msg)
            found = next((number for name, number, _kind in self.entries(inode) if name == part), None)
            if found is None:
                msg = f"no {done / part} in the image"
                raise Ext4Error(msg)
            inode, done = self.inode(found), done / part
        return inode

    def exists(self, path: str) -> bool:
        """Tell whether @p path is in the image."""
        try:
            self.lookup(path)
        except Ext4Error:
            return False
        return True

    def listdir(self, path: str) -> list[str]:
        """Return the names in the directory at @p path, in the directory's order."""
        return [name for name, _number, _kind in self.entries(self.lookup(path))]

    def read(self, path: str) -> bytes:
        """Return the regular file at @p path."""
        inode = self.lookup(path)
        if not inode.is_file:
            msg = f"{path} is not a regular file"
            raise Ext4Error(msg)
        return self.data(inode)

    def link_target(self, inode: Inode) -> str:
        """Return a symbolic link's target: in i_block when short (a fast link), else in its data."""
        if not inode.is_symlink:
            msg = f"inode {inode.number} is not a symbolic link"
            raise Ext4Error(msg)
        xattr_sectors = self.block_size // 512 if inode.file_acl else 0
        fast = inode.size < I_BLOCK and not inode.flags & EXTENTS_FL and inode.blocks in (0, xattr_sectors)
        raw = inode.block[: inode.size] if fast else self.data(inode)
        return raw.decode("utf-8", "surrogateescape")

    def readlink(self, path: str) -> str:
        """Return the target of the symbolic link at @p path."""
        return self.link_target(self.lookup(path))

    def walk(self, path: str = "/") -> Iterator[tuple[str, Inode]]:
        """Yield (path, inode) for @p path and everything below it, a directory before its contents."""
        top = PurePosixPath("/", path)
        stack = [(str(top), self.lookup(str(top)))]
        while stack:
            here, inode = stack.pop()
            yield here, inode
            if inode.is_dir:
                below = [(f"{here.rstrip('/')}/{name}", number) for name, number, _kind in self.entries(inode)]
                stack.extend((p, self.inode(n)) for p, n in reversed(below))


def main(argv: list[str] | None = None) -> int:
    """Run the command line: cat IMAGE PATH writes the image's regular file PATH to stdout."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 3 or args[0] != "cat":  # noqa: PLR2004  # cat, the image and the path
        print("usage: python3 -m overpressure.ext4 cat IMAGE PATH", file=sys.stderr)
        return 2
    try:
        with Ext4.open(args[1]) as fs:
            inode = fs.lookup(args[2])
            if inode.is_file:
                out = sys.stdout.buffer
                for chunk in fs.chunks(inode):
                    out.write(chunk)
                out.flush()
                return 0
    except (OSError, Ext4Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"error: {args[2]} in {args[1]} is not a regular file", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
