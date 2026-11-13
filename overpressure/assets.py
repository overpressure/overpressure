# SPDX-License-Identifier: Unlicense
"""A headset's assets out of its stock firmware folder, in Python on any system.

The stock folder is what a headset's `stock` command builds from its vendor's public OTA
(overpressure/ota.py): partition images and a manifest.json with each file's SHA-256. An asset
folder is a set of files taken out of those images, with its own manifest.json:

    {"schema": 2, "firmware": ..., "sources": {PREFIX: "partition path"}, "files": {PATH: sha256}}

- StockFolder checks the images against the stock manifest before anything is read from them
  and opens them as ext4 (overpressure/ext4.py) or FAT (overpressure/fat.py).
- dump() copies a file or a directory tree out of an ext4 image as debugfs's dump and rdump do: a
  file as a new file, a tree with the image's permissions and modification times.
- copy_matching() copies the files of a FAT directory that match patterns, as mcopy does.
- write_manifest() writes the asset folder's manifest.

Ownership is not copied (it would need root, and the builds that take the assets give their files
to root anyway), and permissions only on systems that have them: Windows keeps none.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath
from typing import IO, TYPE_CHECKING

from . import lp
from .ext4 import Ext4, Ext4Error, Inode
from .fat import Fat, FatError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from types import TracebackType

    from typing_extensions import Self  # typing's from Python 3.11 on

SCHEMA = 2
CHUNK = 1 << 22
#: Whether files here carry POSIX permissions (Windows keeps only a read-only flag).
POSIX_MODES = os.name == "posix"


class AssetError(RuntimeError):
    """A stock file is missing or not the one its manifest names, or an asset is not where it should be."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


class StockFolder:
    """A stock firmware folder: its manifest, its images checked against it, and readers for them."""

    def __init__(self, root: Path) -> None:
        """Read @p root's manifest.json (the folder of a headset's `stock` command)."""
        self.root = root.resolve()
        manifest = self.root / "manifest.json"
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            self.files: dict[str, str] = data["files"]
        except (OSError, ValueError, KeyError) as exc:
            msg = f"{self.root} has no stock manifest.json ({exc}): build the stock folder first"
            raise AssetError(msg) from exc
        self.firmware: str | None = data.get("firmware")
        self._checked: set[str] = set()
        self._ext4: dict[str, Ext4] = {}
        self._fat: dict[str, Fat] = {}

    def check(self, *names: str) -> None:
        """Check that each named file is there, not empty, and has the SHA-256 the manifest records.

        Each file is hashed once per folder object.
        """
        for name in names:
            if name in self._checked:
                continue
            path = self.root / name
            if not path.is_file() or not path.stat().st_size:
                msg = f"missing {path}"
                raise AssetError(msg)
            if _sha256(path) != self.files.get(name):
                msg = f"{name}: sha256 does not match the stock manifest"
                raise AssetError(msg)
            self._checked.add(name)

    def ext4(self, name: str) -> Ext4:
        """Return the checked ext4 image @p name, open for reading."""
        self.check(name)
        if name not in self._ext4:
            self._ext4[name] = Ext4.open(self.root / name)
        return self._ext4[name]

    def fat(self, name: str) -> Fat:
        """Return the checked FAT image @p name, open for reading."""
        self.check(name)
        if name not in self._fat:
            self._fat[name] = Fat.open(self.root / name)
        return self._fat[name]

    def close(self) -> None:
        """Close the open images."""
        for reader in (*self._ext4.values(), *self._fat.values()):
            reader.close()
        self._ext4.clear()
        self._fat.clear()

    def __enter__(self) -> Self:
        """Use the folder in a with statement: its images are closed at its end."""
        return self

    def __exit__(
        self, kind: type[BaseException] | None, value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the open images."""
        self.close()


class BackupFolder(StockFolder):
    """A headset's backup in a stock folder's place: the same images, read out of its partitions.

    @p images maps each image name a stock folder would hold to the backup's partition and, for a
    super partition, the logical partition in it (overpressure/lp.py). Each partition file is
    checked against the backup's manifest before anything is read from it.
    """

    def __init__(self, root: Path, images: Mapping[str, tuple[str, str | None]]) -> None:
        """Read the backup's manifest.json in @p root."""
        self.root = root.resolve()
        try:
            data = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
            backed: dict[str, dict] = data["files"]
        except (OSError, ValueError, KeyError) as exc:
            msg = f"{self.root} has no backup manifest.json ({exc})"
            raise AssetError(msg) from exc
        self.images = dict(images)
        self.files = {}
        self._paths: dict[str, str] = {}
        for name, (partition, _logical) in self.images.items():
            found = [n for n in backed if PurePosixPath(n).stem == partition and n.endswith((".bin", ".img"))]
            if len(found) == 1:
                self._paths[name] = found[0]
                self.files[found[0]] = backed[found[0]].get("sha256", "")
        self.firmware = (data.get("identity") or {}).get("fingerprint")
        self._checked = set()
        self._ext4 = {}
        self._fat = {}
        self._open: list[IO[bytes]] = []

    def check(self, *names: str) -> None:
        """Check each image's partition file against the backup's manifest (once per folder object)."""
        for name in names:
            if name not in self._paths:
                msg = f"the backup in {self.root} holds no {self.images.get(name, (name,))[0]} partition"
                raise AssetError(msg)
            super().check(self._paths[name])

    def _image(self, name: str) -> IO[bytes]:
        self.check(name)
        file = (self.root / self._paths[name]).open("rb")
        self._open.append(file)
        logical = self.images[name][1]
        if logical is None:
            return file
        try:
            return lp.LogicalPartition(file, lp.partitions(file)[logical])
        except KeyError as exc:
            msg = f"the backup's {self.images[name][0]} holds no logical partition {logical}"
            raise AssetError(msg) from exc
        except lp.LpError as exc:
            raise AssetError(str(exc)) from exc

    def ext4(self, name: str) -> Ext4:
        """Return the ext4 file system the image @p name names, open for reading."""
        if name not in self._ext4:
            self._ext4[name] = Ext4(self._image(name))
        return self._ext4[name]

    def fat(self, name: str) -> Fat:
        """Return the FAT file system the image @p name names, open for reading."""
        if name not in self._fat:
            self._fat[name] = Fat(self._image(name))
        return self._fat[name]

    def close(self) -> None:
        """Close the partition files."""
        super().close()
        for file in self._open:
            file.close()
        self._open.clear()


def _write(fs: Ext4, inode: Inode, dest: Path) -> None:
    with dest.open("wb") as out:
        for chunk in fs.chunks(inode, CHUNK):
            out.write(chunk)


def _preserve(path: Path, inode: Inode) -> None:
    """Give @p path the inode's permissions and modification time, as debugfs's rdump does."""
    if POSIX_MODES:
        path.chmod(inode.permissions)
    os.utime(path, ns=(inode.mtime * 10**9 + inode.mtime_ns, inode.mtime * 10**9 + inode.mtime_ns))


def _rdump(fs: Ext4, path: str, inode: Inode, dest: Path) -> None:
    """Copy the tree at @p path to @p dest (which it creates), with the image's modes and times."""
    if inode.is_dir:
        dest.mkdir()
        for name, number, _kind in fs.entries(inode):
            _rdump(fs, f"{path.rstrip('/')}/{name}", fs.inode(number), dest / name)
    elif inode.is_file:
        _write(fs, inode, dest)
    elif inode.is_symlink:
        try:
            dest.symlink_to(fs.link_target(inode))
        except OSError as exc:
            msg = f"{path} is a symbolic link, which this system cannot create here ({exc})"
            raise AssetError(msg) from exc
        return
    else:
        return  # devices, pipes and sockets: debugfs's rdump leaves them out too
    _preserve(dest, inode)


def dump(fs: Ext4, path: str, dest: Path) -> None:
    """Copy @p path out of an ext4 image, as the extraction scripts' debugfs did.

    A regular file becomes the new file @p dest. A directory is copied with its tree into
    @p dest/NAME (debugfs's rdump: @p dest is created when missing), with the image's permissions
    and modification times. A symbolic link is refused: name its target.
    """
    try:
        inode = fs.lookup(path)
    except Ext4Error as exc:
        msg = f"missing {path} in the image ({exc})"
        raise AssetError(msg) from exc
    if inode.is_dir:
        dest.mkdir(parents=True, exist_ok=True)
        _rdump(fs, path, inode, dest / PurePosixPath(path).name)
    elif inode.is_file:
        _write(fs, inode, dest)
    elif inode.is_symlink:
        msg = f"{path} in the image is a link; name its target"
        raise AssetError(msg)
    else:
        msg = f"{path} in the image is no file or directory"
        raise AssetError(msg)


def copy_matching(fs: Fat, directory: str, patterns: Iterable[str], dest: Path) -> list[str]:
    """Copy the files of @p directory that match each pattern into @p dest, as mcopy did; return their names.

    A pattern matches a long or an 8.3 name, ignoring case; one that matches nothing is an error.
    """
    copied = []
    for pattern in patterns:
        try:
            found = fs.glob(directory, pattern)
        except FatError as exc:
            msg = f"no {directory} in the image ({exc})"
            raise AssetError(msg) from exc
        if not found:
            msg = f"no {directory}/{pattern} in the image"
            raise AssetError(msg)
        for entry in found:
            with (dest / entry.name).open("wb") as out:
                for chunk in fs.chunks(entry):
                    out.write(chunk)
            copied.append(entry.name)
    return copied


def widen_modes(folder: Path) -> None:
    """Let everyone read @p folder's files and enter its directories, as `chmod -R u+rwX,go+rX` does.

    Nothing on Windows, whose files carry no such modes.
    """
    if not POSIX_MODES:
        return
    for path in [folder, *folder.rglob("*")]:
        if path.is_symlink():
            continue
        mode = stat.S_IMODE(path.stat().st_mode)
        executable = path.is_dir() or mode & 0o111
        path.chmod(mode | 0o644 | (0o111 if executable else 0))


def manifest_files(output: Path) -> dict[str, str]:
    """Return every file below @p output but the manifests, with its SHA-256, in path order."""
    paths = [p for p in output.rglob("*") if p.is_file() and p.name != "manifest.json"]
    paths.sort(key=lambda p: p.relative_to(output).parts)
    return {p.relative_to(output).as_posix(): _sha256(p) for p in paths}


def write_manifest(output: Path, firmware: str | None, sources: Mapping[str, str]) -> int:
    """Write @p output/manifest.json: the firmware, where each part came from, every file's SHA-256.

    Returns the number of files. The format is the one the userspace build reads (schema 2).
    """
    files = manifest_files(output)
    text = json.dumps({"schema": SCHEMA, "firmware": firmware, "sources": dict(sources), "files": files}, indent=1)
    (output / "manifest.json").write_bytes((text + "\n").encode("utf-8"))
    return len(files)
