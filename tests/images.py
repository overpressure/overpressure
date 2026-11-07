# SPDX-License-Identifier: Unlicense
"""Small synthetic ext4 and FAT images and stock folders for the tests, built from the on-disk formats.

Nothing here comes from a vendor: the images hold the test's own files. The ext4 builder writes
what the reader needs (superblock, group descriptors, inode tables, directories, extents of depth 0
and 1, the old block map with indirect blocks, holes, uninitialized extents, fast and slow
symbolic links); bitmaps and checksums stay empty, which a reader does not look at.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

S_IFDIR, S_IFREG, S_IFLNK = 0o040000, 0o100000, 0o120000


@dataclass
class Node:
    """A file, directory or link of an image under construction."""

    mode: int
    data: bytes = b""
    uid: int = 0
    gid: int = 0
    mtime: int = 1_700_000_000
    mtime_ns: int = 0
    layout: str = "extents"  #: extents, tree (an index node above the leaf), map (the block map)
    holes: frozenset[int] = frozenset()  #: logical blocks left unallocated (their data must be zero)
    uninit: frozenset[int] = frozenset()  #: logical blocks in an uninitialized extent (they read as zeros)
    children: dict[str, Node] = field(default_factory=dict)
    number: int = 0


class Ext4Image:
    """An ext4 image of two block groups, built in memory."""

    def __init__(
        self, block_size: int = 1024, *, wide: bool = False, inode_size: int = 256, inodes_per_group: int = 16
    ) -> None:
        self.bs, self.wide, self.inode_size = block_size, wide, inode_size
        self.root = Node(S_IFDIR | 0o755)
        self.inodes_per_group, self.blocks_per_group, self.groups = inodes_per_group, 1024, 2

    def _parent(self, path: str) -> tuple[Node, str]:
        *dirs, name = path.strip("/").split("/")
        node = self.root
        for part in dirs:
            node = node.children.setdefault(part, Node(S_IFDIR | 0o755))
        return node, name

    def mkdir(self, path: str, mode: int = 0o755, **kw: int) -> None:
        parent, name = self._parent(path)
        parent.children.setdefault(name, Node(S_IFDIR | mode, **kw))  # ty: ignore[invalid-argument-type]  # the test's own fields

    def file(self, path: str, data: bytes, mode: int = 0o644, **kw: object) -> None:
        parent, name = self._parent(path)
        parent.children[name] = Node(S_IFREG | mode, data, **kw)  # ty: ignore[invalid-argument-type]  # the test's own fields

    def symlink(self, path: str, target: str) -> None:
        parent, name = self._parent(path)
        parent.children[name] = Node(S_IFLNK | 0o777, target.encode())

    # -- building ---------------------------------------------------------------------------
    def build(self) -> bytes:
        bs = self.bs
        first_data = 1 if bs == 1024 else 0
        blocks = first_data + self.groups * self.blocks_per_group
        self.image = bytearray(blocks * bs)
        table_blocks = -(-self.inodes_per_group * self.inode_size // bs)
        self.tables = [first_data + 2 + g * table_blocks for g in range(self.groups)]
        self.next_block = first_data + 2 + self.groups * table_blocks
        nodes: list[Node] = []

        def number(node: Node, n: int) -> int:
            node.number = n
            nodes.append(node)
            n = n + 1 if n != 2 else 11  # the root, then the first ordinary inode
            for child in node.children.values():
                n = number(child, n)
            return n

        number(self.root, 2)
        for node in nodes:
            self._inode(node, self._parent_of(node))
        sb = bytearray(1024)
        struct.pack_into("<II", sb, 0, self.groups * self.inodes_per_group, blocks)
        struct.pack_into("<III", sb, 0x14, first_data, {1024: 0, 2048: 1, 4096: 2}[bs], 0)
        struct.pack_into("<I", sb, 0x20, self.blocks_per_group)
        struct.pack_into("<I", sb, 0x28, self.inodes_per_group)
        struct.pack_into("<H", sb, 0x38, 0xEF53)
        struct.pack_into("<I", sb, 0x4C, 1)
        struct.pack_into("<IH", sb, 0x54, 11, self.inode_size)
        struct.pack_into("<III", sb, 0x5C, 0, 0x2 | 0x40 | (0x80 if self.wide else 0), 0)
        struct.pack_into("<H", sb, 0xFE, 64 if self.wide else 0)
        self.image[1024:2048] = sb
        desc = 64 if self.wide else 32
        for g, table in enumerate(self.tables):
            struct.pack_into("<I", self.image, (first_data + 1) * bs + g * desc + 8, table)
        return bytes(self.image)

    def _parent_of(self, target: Node) -> Node:
        stack = [self.root]
        while stack:
            node = stack.pop()
            if target in node.children.values():
                return node
            stack.extend(node.children.values())
        return self.root

    def _alloc(self, count: int = 1) -> int:
        start = self.next_block
        self.next_block += count
        return start

    def _put(self, block: int, data: bytes) -> None:
        self.image[block * self.bs : block * self.bs + len(data)] = data

    def _dir_data(self, node: Node, parent: Node) -> bytes:
        bs = self.bs
        entries = [(".", node.number, 2), ("..", parent.number, 2)]
        kinds = {S_IFDIR: 2, S_IFREG: 1, S_IFLNK: 7}
        entries += [(name, c.number, kinds[c.mode & 0o170000]) for name, c in node.children.items()]
        # An htree-like index block first in a big directory: one empty entry over the whole block.
        out = (
            bytearray(struct.pack("<IHBB", 0, bs, 0, 0).ljust(bs, b"\0")) if len(node.children) > 3 else bytearray()
        )  # the test's choice
        block = bytearray()
        for i, (name, num, kind) in enumerate(entries):
            raw = name.encode()
            size = (8 + len(raw) + 3) & ~3
            if len(block) + size + 12 > bs:  # close the block: the last entry spans it
                out += self._close(block)
                block = bytearray()
            last = i == len(entries) - 1
            block += struct.pack("<IHBB", num, size, len(raw), kind) + raw.ljust(size - 8, b"\0")
            if i == 2:  # a deleted entry after the first name
                block += struct.pack("<IHBB", 0, 12, 4, 1) + b"gone"
            if last:
                out += self._close(block)
        return bytes(out)

    def _close(self, block: bytearray) -> bytes:
        at, last = 0, 0
        while at < len(block):
            last = at
            at += struct.unpack_from("<H", block, at + 4)[0]
        struct.pack_into("<H", block, last + 4, self.bs - last)
        return bytes(block.ljust(self.bs, b"\0"))

    def _inode(self, node: Node, parent: Node) -> None:
        bs = self.bs
        kind = node.mode & 0o170000
        data = self._dir_data(node, parent) if kind == S_IFDIR else node.data
        flags, i_block, size = 0, b"", len(data)
        if kind == S_IFLNK and len(data) < 60:  # a fast link's target in i_block
            i_block = data
        elif node.layout == "map":
            i_block = self._block_map(data, node.holes)
        else:
            flags = 0x80000
            i_block = self._extents(data, node, tree=node.layout == "tree")
        sectors = 0 if kind == S_IFLNK and len(data) < 60 else -(-size // bs) * (bs // 512)  # i_block's size
        raw = bytearray(self.inode_size)
        struct.pack_into("<HHI", raw, 0, node.mode, node.uid & 0xFFFF, size & 0xFFFFFFFF)
        struct.pack_into("<I", raw, 0x10, node.mtime & 0xFFFFFFFF)
        struct.pack_into("<HHII", raw, 0x18, node.gid & 0xFFFF, 1, sectors, flags)
        raw[0x28 : 0x28 + len(i_block)] = i_block
        struct.pack_into("<I", raw, 0x6C, size >> 32)
        struct.pack_into("<HH", raw, 0x78, node.uid >> 16, node.gid >> 16)
        if self.inode_size > 128:  # the extra fields
            struct.pack_into("<H", raw, 0x80, 32)
            struct.pack_into("<I", raw, 0x88, node.mtime_ns << 2 | (node.mtime >> 32) & 3)
        group, index = divmod(node.number - 1, self.inodes_per_group)
        at = self.tables[group] * bs + index * self.inode_size
        self.image[at : at + self.inode_size] = raw

    def _runs(self, data: bytes, node: Node) -> list[tuple[int, int, int, bool]]:
        """Allocate the data's blocks; return (logical, physical, length, initialized) runs."""
        bs = self.bs
        count = -(-len(data) // bs)
        runs: list[tuple[int, int, int, bool]] = []
        for logical in range(count):
            if logical in node.holes:
                continue
            init = logical not in node.uninit
            physical = self._alloc()
            chunk = data[logical * bs : (logical + 1) * bs]
            self._put(physical, chunk if init else b"\xaa" * bs)  # uninitialized: garbage on disk
            if (
                runs
                and runs[-1][0] + runs[-1][2] == logical
                and runs[-1][1] + runs[-1][2] == physical
                and runs[-1][3] == init
            ):
                runs[-1] = (runs[-1][0], runs[-1][1], runs[-1][2] + 1, init)
            else:
                runs.append((logical, physical, 1, init))
        return runs

    def _extents(self, data: bytes, node: Node, *, tree: bool) -> bytes:
        runs = self._runs(data, node)
        leaves = b"".join(
            struct.pack("<IHHI", logical, length if init else length + 32768, physical >> 32, physical & 0xFFFFFFFF)
            for logical, physical, length, init in runs
        )
        if not tree:
            return struct.pack("<HHHHI", 0xF30A, len(runs), 4, 0, 0) + leaves
        leaf = self._alloc()
        self._put(leaf, struct.pack("<HHHHI", 0xF30A, len(runs), (self.bs - 12) // 12, 0, 0) + leaves)
        return struct.pack("<HHHHI", 0xF30A, 1, 4, 1, 0) + struct.pack("<IIHH", 0, leaf, 0, 0)

    def _block_map(self, data: bytes, holes: frozenset[int]) -> bytes:
        bs, per = self.bs, self.bs // 4
        count = -(-len(data) // bs)
        numbers = []
        for logical in range(count):
            if logical in holes:
                numbers.append(0)
                continue
            block = self._alloc()
            self._put(block, data[logical * bs : (logical + 1) * bs])
            numbers.append(block)
        direct, rest = numbers[:12], numbers[12:]
        words = direct + [0] * (12 - len(direct))
        single, rest = rest[:per], rest[per:]
        words.append(self._indirect(single) if single else 0)
        if rest:
            children = [self._indirect(rest[i : i + per]) for i in range(0, len(rest), per)]
            words.append(self._indirect(children))
        else:
            words.append(0)
        words.append(0)
        return struct.pack("<15I", *words)

    def _indirect(self, numbers: list[int]) -> int:
        block = self._alloc()
        self._put(block, struct.pack(f"<{len(numbers)}I", *numbers))
        return block


@dataclass
class FatNode:
    """A file or directory of a FAT image under construction."""

    data: bytes = b""
    is_dir: bool = False
    mtime: dt.datetime = dt.datetime(2024, 5, 6, 7, 8, 10)  # noqa: DTZ001  # FAT keeps local time
    children: dict[str, FatNode] = field(default_factory=dict)
    cluster: int = 0


class FatImage:
    """A FAT12, FAT16 or FAT32 image with long names, built in memory."""

    def __init__(self, bits: int = 16) -> None:
        self.bits = bits
        self.sector, self.cluster_sectors = 512, 1
        # Enough data clusters for the FAT type (its limits: 4085 and 65525).
        self.clusters = {12: 200, 16: 5000, 32: 66000}[bits]
        self.root = FatNode(is_dir=True)
        self.serial = 0

    def _parent(self, path: str) -> tuple[FatNode, str]:
        *dirs, name = path.strip("/").split("/")
        node = self.root
        for part in dirs:
            node = node.children.setdefault(part, FatNode(is_dir=True))
        return node, name

    def mkdir(self, path: str) -> None:
        parent, name = self._parent(path)
        parent.children.setdefault(name, FatNode(is_dir=True))

    def file(self, path: str, data: bytes, mtime: dt.datetime | None = None) -> None:
        parent, name = self._parent(path)
        parent.children[name] = FatNode(data, mtime=mtime or FatNode.mtime)

    def build(self) -> bytes:
        bs = self.sector * self.cluster_sectors
        entry_bytes = {12: 1.5, 16: 2, 32: 4}[self.bits]
        self.fat_sectors = -(-int((self.clusters + 2) * entry_bytes + 1) // self.sector)
        reserved = 32 if self.bits == 32 else 1  # FAT32
        root_entries = 0 if self.bits == 32 else 64  # FAT32's root is a cluster chain
        root_sectors = root_entries * 32 // self.sector
        self.data_start = (reserved + 2 * self.fat_sectors + root_sectors) * self.sector
        total = reserved + 2 * self.fat_sectors + root_sectors + self.clusters * self.cluster_sectors
        self.image = bytearray(total * self.sector)
        self.fat = [0] * (self.clusters + 2)
        self.fat[0], self.fat[1] = 0xFFFFFF8, 0xFFFFFFF
        self.next = 2
        self.cluster_size = bs
        if self.bits == 32:  # FAT32
            self.root.cluster = self._alloc_chain(1)
        self._layout(self.root, None)
        root_data = self._dir_entries(self.root, None)
        if self.bits == 32:  # FAT32
            self._write_chain(self.root.cluster, root_data)
        else:
            at = (reserved + 2 * self.fat_sectors) * self.sector
            self.image[at : at + len(root_data)] = root_data
        boot = bytearray(self.sector)
        boot[0:3], boot[3:11] = b"\xeb\x3c\x90", b"TESTFAT "
        small = total if total < 65536 and self.bits != 32 else 0  # the 16-bit count's limit
        struct.pack_into(
            "<HBHBHHBH",
            boot,
            11,
            self.sector,
            self.cluster_sectors,
            reserved,
            2,
            root_entries,
            small,
            0xF8,
            0 if self.bits == 32 else self.fat_sectors,  # FAT32
        )
        struct.pack_into("<I", boot, 32, 0 if small else total)
        if self.bits == 32:  # FAT32
            struct.pack_into("<I4xI", boot, 36, self.fat_sectors, self.root.cluster)
        boot[510:512] = b"\x55\xaa"
        self.image[0 : self.sector] = boot
        fat = self._fat_bytes()
        for copy in range(2):
            at = (reserved + copy * self.fat_sectors) * self.sector
            self.image[at : at + len(fat)] = fat
        return bytes(self.image)

    def _fat_bytes(self) -> bytes:
        if self.bits == 32:  # FAT32
            return struct.pack(f"<{len(self.fat)}I", *(v & 0x0FFFFFFF for v in self.fat))
        if self.bits == 16:  # FAT16
            return struct.pack(f"<{len(self.fat)}H", *(v & 0xFFFF for v in self.fat))
        out = bytearray()
        values = [v & 0xFFF for v in self.fat] + [0]
        for i in range(0, len(values) - 1, 2):
            a, b = values[i], values[i + 1]
            out += bytes((a & 0xFF, (a >> 8) | ((b & 0xF) << 4), b >> 4))
        return bytes(out)

    def _alloc_chain(self, count: int) -> int:
        first = self.next
        for i in range(count):
            self.fat[first + i] = first + i + 1 if i < count - 1 else 0xFFFFFFF
        self.next += count
        return first

    def _write_chain(self, first: int, data: bytes) -> None:
        cluster, at = first, 0
        while True:
            off = self.data_start + (cluster - 2) * self.cluster_size
            self.image[off : off + self.cluster_size] = data[at : at + self.cluster_size].ljust(
                self.cluster_size, b"\0"
            )
            at += self.cluster_size
            nxt = self.fat[cluster]
            if nxt >= 0xFFFFFF8 or at >= len(data):  # the chain's end
                return
            cluster = nxt

    def _layout(self, node: FatNode, parent: FatNode | None) -> None:
        for child in node.children.values():
            if child.is_dir:
                size = (len(child.children) * 4 + 4) * 32  # room for the long names
                child.cluster = self._alloc_chain(-(-size // self.cluster_size))
                self._layout(child, node)
            elif child.data:
                child.cluster = self._alloc_chain(-(-len(child.data) // self.cluster_size))
                self._write_chain(child.cluster, child.data)
        if node is not self.root:
            self._write_chain(node.cluster, self._dir_entries(node, parent))

    @staticmethod
    def _stamp(when: dt.datetime) -> tuple[int, int]:
        date = (when.year - 1980) << 9 | when.month << 5 | when.day
        return date, when.hour << 11 | when.minute << 5 | when.second // 2

    def _short(self, name: str) -> tuple[bytes, int, bool]:
        """Return the 8.3 name, the NT case bits and whether a long name is needed."""
        base, _, ext = name.rpartition(".") if "." in name else (name, "", "")
        fits = 0 < len(base) <= 8 and len(ext) <= 3 and all(c.isalnum() or c in "_-" for c in base + ext)  # 8.3
        if (
            fits
            and (base.isupper() or base.isdigit() or base.islower())
            and (not ext or ext.isupper() or ext.islower())
        ):
            case = (0x08 if base.islower() else 0) | (0x10 if ext.islower() else 0)
            return (base.upper().ljust(8) + ext.upper().ljust(3)).encode(), case, False
        self.serial += 1
        tail = f"~{self.serial}"
        stem = "".join(c for c in base.upper() if c.isalnum())[: 8 - len(tail)] + tail
        return (stem.ljust(8) + "".join(c for c in ext.upper() if c.isalnum())[:3].ljust(3)).encode(), 0, True

    def _entry(  # noqa: PLR0913, PLR0917  # an entry's fields, in their on-disk order
        self, short: bytes, case: int, attr: int, node: FatNode | None, cluster: int, size: int
    ) -> bytes:
        date, time = self._stamp(node.mtime if node else FatNode.mtime)
        return short + struct.pack(
            "<BBBHHHHHHHI", attr, case, 0, 0, 0, 0, cluster >> 16, time, date, cluster & 0xFFFF, size
        )

    def _dir_entries(self, node: FatNode, parent: FatNode | None) -> bytes:
        out = bytearray()
        if node is not self.root:
            out += self._entry(b".          ", 0, 0x10, node, node.cluster, 0)
            out += self._entry(
                b"..         ", 0, 0x10, parent, 0 if parent is self.root else (parent.cluster if parent else 0), 0
            )
        else:
            out += b"TESTVOLUME " + bytes([0x08]) + bytes(20)  # a volume label, left out by readers
        for name, child in node.children.items():
            short, case, long_name = self._short(name)
            if long_name:
                out += self._long_entries(name, short)
            out += self._entry(
                short,
                case,
                0x10 if child.is_dir else 0x20,
                child,
                child.cluster,
                0 if child.is_dir else len(child.data),
            )
        out += bytes(32)
        return bytes(out)

    @staticmethod
    def _long_entries(name: str, short: bytes) -> bytes:
        total = 0
        for byte in short:
            total = (((total & 1) << 7) | (total >> 1)) + byte & 0xFF
        units = name.encode("utf-16-le")
        chars = [units[i : i + 2] for i in range(0, len(units), 2)]
        if len(chars) % 13:
            chars.append(b"\0\0")
        while len(chars) % 13:
            chars.append(b"\xff\xff")
        parts = [chars[i : i + 13] for i in range(0, len(chars), 13)]
        out = bytearray()
        for n in range(len(parts), 0, -1):
            part = parts[n - 1]
            entry = bytearray(32)
            entry[0] = n | (0x40 if n == len(parts) else 0)
            entry[1:11] = b"".join(part[0:5])
            entry[11], entry[13] = 0x0F, total
            entry[14:26] = b"".join(part[5:11])
            entry[28:32] = b"".join(part[11:13])
            out += entry
        return bytes(out)


def stock_folder(root: Path, files: dict[str, bytes], *, firmware: str = "1.0-test") -> Path:
    """Write @p files and a stock manifest.json with their SHA-256 into @p root."""
    root.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (root / name).write_bytes(data)
    digests = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    manifest = {"schema": 1, "firmware": firmware, "source": "test", "files": digests}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root
