# SPDX-License-Identifier: Unlicense
"""In-memory root filesystem tree and "newc" cpio archive reader/writer.

The Linux kernel unpacks an initramfs from a newc cpio archive. Building the
archive in memory (instead of on disk) lets us create device nodes, symlinks,
setuid bits and root ownership on any host OS, including Windows.
"""
from __future__ import annotations

import posixpath
import stat
from dataclasses import dataclass, field

NEWC_MAGIC = b"070701"
NEWC_CRC_MAGIC = b"070702"
TRAILER = "TRAILER!!!"


@dataclass
class Node:
    """One filesystem object. ``mode`` holds the full st_mode (type + permissions)."""

    mode: int
    data: bytes = b""  # file contents, or the link target for symlinks
    uid: int = 0
    gid: int = 0
    rdev: tuple[int, int] = (0, 0)
    mtime: int = 0

    @property
    def is_dir(self) -> bool:
        return stat.S_ISDIR(self.mode)

    @property
    def is_file(self) -> bool:
        return stat.S_ISREG(self.mode)

    @property
    def is_symlink(self) -> bool:
        return stat.S_ISLNK(self.mode)


def _norm(path: str) -> str:
    path = posixpath.normpath("/" + path.replace("\\", "/")).lstrip("/")
    return "" if path == "." else path


@dataclass
class Tree:
    """A root filesystem held in memory, keyed by relative POSIX path."""

    nodes: dict[str, Node] = field(default_factory=dict)
    mtime: int = 0

    # -- construction -----------------------------------------------------
    def mkdir(self, path: str, mode: int = 0o755, uid: int = 0, gid: int = 0) -> str:
        """Create ``path`` (and parents). Returns the symlink-resolved path."""
        path = _norm(path)
        if not path:
            return path
        parent = posixpath.dirname(path)
        if parent:
            parent = self.resolve(parent)
            self.mkdir(parent)
            path = posixpath.join(parent, posixpath.basename(path))
        existing = self.nodes.get(path)
        if existing is not None and existing.is_dir:
            return path
        if existing is not None and existing.is_symlink:
            # e.g. a merged-/usr layout (bin -> usr/bin): keep the link.
            return self.mkdir(self.resolve(path), mode, uid, gid)
        self.nodes[path] = Node(stat.S_IFDIR | (mode & 0o7777), uid=uid, gid=gid, mtime=self.mtime)
        return path

    def _place(self, path: str) -> str:
        """Normalise ``path``, resolve symlinks in its parent and create the parent."""
        path = _norm(path)
        parent = posixpath.dirname(path)
        if not parent:
            return path
        parent = self.resolve(parent)
        self.mkdir(parent)
        return posixpath.join(parent, posixpath.basename(path))

    def add_file(self, path: str, data: bytes, mode: int = 0o644, uid: int = 0, gid: int = 0) -> None:
        path = self._place(path)
        self.nodes[path] = Node(stat.S_IFREG | (mode & 0o7777), data=data, uid=uid, gid=gid, mtime=self.mtime)

    def add_symlink(self, path: str, target: str, uid: int = 0, gid: int = 0) -> None:
        path = self._place(path)
        self.nodes[path] = Node(stat.S_IFLNK | 0o777, data=target.encode(), uid=uid, gid=gid, mtime=self.mtime)

    def add_device(self, path: str, kind: str, major: int, minor: int, mode: int = 0o600) -> None:
        path = self._place(path)
        fmt = {"c": stat.S_IFCHR, "b": stat.S_IFBLK}[kind]
        self.nodes[path] = Node(fmt | (mode & 0o7777), rdev=(major, minor), mtime=self.mtime)

    def get(self, path: str) -> Node | None:
        return self.nodes.get(_norm(path))

    def exists(self, path: str) -> bool:
        return _norm(path) in self.nodes

    def resolve(self, path: str, _depth: int = 0) -> str:
        """Resolve symlinks in ``path`` (inside the tree, never on the host)."""
        if _depth > 40:
            raise RuntimeError(f"symlink loop resolving {path}")
        parts = _norm(path).split("/") if _norm(path) else []
        out: list[str] = []
        for i, part in enumerate(parts):
            cur = "/".join(out + [part])
            node = self.nodes.get(cur)
            if node is not None and node.is_symlink:
                target = node.data.decode()
                base = "" if target.startswith("/") else "/".join(out)
                rest = "/".join(parts[i + 1:])
                return self.resolve(posixpath.join(base, target, rest), _depth + 1)
            out.append(part)
        return "/".join(out)

    # -- serialisation ----------------------------------------------------
    def to_tar(self, fileobj) -> None:
        """Write the tree as an uncompressed tar without device nodes (handy for inspecting the rootfs)."""
        import io
        import tarfile

        with tarfile.open(fileobj=fileobj, mode="w", format=tarfile.GNU_FORMAT) as tf:
            for path in sorted(self.nodes):
                node = self.nodes[path]
                info = tarfile.TarInfo(path)
                info.mode, info.uid, info.gid, info.mtime = node.mode & 0o7777, node.uid, node.gid, node.mtime
                if node.is_dir:
                    info.type = tarfile.DIRTYPE
                elif node.is_symlink:
                    info.type, info.linkname = tarfile.SYMTYPE, node.data.decode()
                elif node.is_file:
                    info.size = len(node.data)
                    tf.addfile(info, io.BytesIO(node.data))
                    continue
                else:
                    continue
                tf.addfile(info)

    def to_cpio(self) -> bytes:
        """Serialise as a newc cpio archive (parents always precede children)."""
        out = bytearray()
        ino = 300000
        for path in sorted(self.nodes):
            node = self.nodes[path]
            ino += 1
            nlink = 2 if node.is_dir else 1
            payload = node.data if (node.is_file or node.is_symlink) else b""
            out += _newc_entry(path, node.mode, node.uid, node.gid, nlink, node.mtime, payload, ino, node.rdev)
        out += _newc_entry(TRAILER, 0, 0, 0, 1, 0, b"", 0, (0, 0))
        # Pad to 512 bytes like GNU cpio does; harmless for the kernel.
        out += b"\0" * (-len(out) % 512)
        return bytes(out)


def _pad4(n: int) -> int:
    return -n % 4


def _newc_entry(name: str, mode: int, uid: int, gid: int, nlink: int, mtime: int,
                payload: bytes, ino: int, rdev: tuple[int, int]) -> bytes:
    name_b = name.encode() + b"\0"
    fields = [ino, mode, uid, gid, nlink, mtime, len(payload), 0, 0, rdev[0], rdev[1], len(name_b), 0]
    header = NEWC_MAGIC + b"".join(b"%08X" % f for f in fields)
    entry = header + name_b + b"\0" * _pad4(len(header) + len(name_b))
    entry += payload + b"\0" * _pad4(len(payload))
    return entry


def read_cpio(data: bytes) -> list[tuple[str, Node]]:
    """Parse a (possibly concatenated) newc cpio archive into (path, Node) entries."""
    entries: list[tuple[str, Node]] = []
    pos = 0
    n = len(data)
    while pos + 110 <= n:
        magic = data[pos:pos + 6]
        if magic not in (NEWC_MAGIC, NEWC_CRC_MAGIC):
            # Skip zero padding between concatenated archives.
            if data[pos:pos + 4] == b"\0\0\0\0":
                pos += 4
                continue
            raise ValueError(f"bad cpio magic {magic!r} at offset {pos}")
        f = [int(data[pos + 6 + 8 * i: pos + 14 + 8 * i], 16) for i in range(13)]
        ino, mode, uid, gid, nlink, mtime, fsize, _dmaj, _dmin, rmaj, rmin, namesize, _chk = f
        name_start = pos + 110
        name = data[name_start:name_start + namesize - 1].decode("utf-8", "replace")
        pos = name_start + namesize
        pos += _pad4(pos)
        payload = data[pos:pos + fsize]
        pos += fsize
        pos += _pad4(pos)
        if name == TRAILER:
            continue
        entries.append((_norm(name), Node(mode, payload, uid, gid, (rmaj, rmin), mtime)))
    return entries
