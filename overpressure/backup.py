# SPDX-License-Identifier: Unlicense
"""Verified files: SHA-256 digests, manifests of backups, records that are never overwritten, backup archives.

A backup folder holds manifest.json ({"schema": 1, "files": {NAME: {"size", "sha256"}}}); every
write to a device is checked against it first, so a changed or truncated backup stops it.

    python -m overpressure backup export FILE     the headset's backup as one file, checked
    python -m overpressure backup import FILE     such a file onto this computer, checked

An export is one uncompressed tar: ARCHIVE_META first (the headset, the backup folder's name, the
headset's identity, the other files and the .env settings the way back needs, Headset.backup_set),
then the backup under backup/ and the other files under files/. The backup is verified before it
is written, and the finished file is read back against its manifest. An import refuses another
headset's backup, a file that is not one, and any file that would replace a different one here; the
imported backup is verified before the headset's settings name it (Headset.adopt_backup).
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import IO, TYPE_CHECKING, Callable

from . import progress

if TYPE_CHECKING:
    from .headset import BackupSet, Headset

#: The archive's first member: what it holds and for which headset.
ARCHIVE_META = "overpressure-backup.json"
ARCHIVE_SCHEMA = 1
#: The suffix the window suggests for an export.
ARCHIVE_SUFFIX = ".overpressure-backup.tar"
CHUNK = 1024 * 1024


def digest(path: Path, count: Callable[[int], None] | None = None) -> dict:
    """Return a file's size and SHA-256, as a manifest records them; an empty file raises.

    @p count is told the bytes as they are read.
    """
    size = path.stat().st_size
    if not size:
        msg = f"Empty backup/image: {path}"
        raise ValueError(msg)
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
            if count is not None:
                count(len(block))
    return {"size": size, "sha256": sha.hexdigest()}


def save_new(path: Path, data: dict) -> None:
    """Write DATA as JSON to PATH, which must not exist: a record is never overwritten."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")


def verified_backup(folder: Path, count: Callable[[int], None] | None = None) -> dict:
    """Return a backup's manifest once every file in it matches; a mismatch raises (@p count: as digest's)."""
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != 1 or not manifest.get("files"):
        msg = "Incomplete backup manifest"
        raise ValueError(msg)
    for name, expected in manifest["files"].items():
        path = (folder / name).resolve()
        if folder.resolve() not in path.parents or digest(path, count) != expected:
            msg = f"Backup integrity check failed: {name}"
            raise ValueError(msg)
    return manifest


def verified_backup_shown(folder: Path, out: Callable[[str], None] | None = None) -> dict:
    """Return verified_backup's manifest, printing progress lines (overpressure.progress) as it reads."""
    files = _listed(folder / "manifest.json")
    size = sum(int(e.get("size", 0)) for e in files.values() if isinstance(e, dict)) if isinstance(files, dict) else 0
    meter = progress.Meter(size, lambda done, total: f"checked {progress.mib(done, total)}", out=out)
    return verified_backup(folder, meter.add)


#: The suffixes of a partition's image in a backup, besides its GPT and rawprogram metadata.
IMAGE_SUFFIXES = (".bin", ".img")


def partition_names(manifest: dict, name: str) -> list[str]:
    """Return the files in a backup manifest that are images of partition @p name (exact, no slot)."""
    return [n for n in manifest["files"] if Path(n).stem == name and Path(n).suffix in IMAGE_SUFFIXES]


def partition_image(backup: Path, name: str) -> tuple[Path, dict]:
    """Return the one image of partition @p name in a backup and its manifest entry.

    The manifest is read, not verified: whoever writes the image checks it (digest) first.
    """
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    names = partition_names(manifest, name)
    if len(names) != 1:
        msg = f"the backup must hold exactly one {name} partition image"
        raise ValueError(msg)
    return backup / names[0], manifest["files"][names[0]]


def verified_partition(backup: Path, name: str) -> tuple[Path, bytes]:
    """Return the path and bytes of partition @p name's image in a backup, checked against its manifest.

    The headset's own copy of a stock image (its boot or recovery, say), in place of a vendor's download.
    """
    path, entry = partition_image(backup, name)
    data = path.read_bytes()
    if len(data) != entry.get("size") or hashlib.sha256(data).hexdigest() != entry.get("sha256"):
        msg = f"{path} differs from the backup's manifest"
        raise ValueError(msg)
    return path, data


# -- the archive: export and import --------------------------------------------------------------
def _member(name: str, data: bytes) -> tuple[tarfile.TarInfo, IO[bytes]]:
    info = tarfile.TarInfo(name)
    info.size, info.mode = len(data), 0o600
    return info, io.BytesIO(data)


def _digest_stream(stream: IO[bytes], count: Callable[[int], None] | None = None) -> dict:
    sha, size = hashlib.sha256(), 0
    for block in iter(lambda: stream.read(CHUNK), b""):
        sha.update(block)
        size += len(block)
        if count is not None:
            count(len(block))
    return {"size": size, "sha256": sha.hexdigest()}


class _Counted(io.RawIOBase):
    """A file read through, its bytes counted: what tarfile copies into the archive."""

    def __init__(self, stream: IO[bytes], count: Callable[[int], None]) -> None:
        super().__init__()
        self._stream, self._count = stream, count

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        data = self._stream.read(size)
        self._count(len(data))
        return data


class _Passes:
    """An export's or import's three passes over every file on one meter: check, copy, check again."""

    PASSES = 3

    def __init__(self, files: object, say: Callable[[str], None]) -> None:
        entries = files.values() if isinstance(files, dict) else ()
        size = sum(int(entry.get("size", 0)) for entry in entries if isinstance(entry, dict))
        self.phase = ""
        self.meter = progress.Meter(self.PASSES * size, lambda _done, _total: self.phase, out=say)
        self.say = say

    def begin(self, phase: str) -> None:
        """Start the next pass, @p phase in words (also a line of its own)."""
        self.phase = phase
        self.say(phase)

    def count(self, size: int) -> None:
        """Count @p size more bytes of the pass."""
        self.meter.add(size)


def _listed(manifest: Path) -> object:
    """Return the files a backup's manifest lists, unchecked: only the meter's sizes come from it."""
    try:
        return json.loads(manifest.read_text(encoding="utf-8")).get("files")
    except (OSError, ValueError, AttributeError):
        return None


def _extract(tar: tarfile.TarFile, name: str) -> IO[bytes] | None:
    """Return the archive member NAME's content, None when the archive has no such file."""
    try:
        return tar.extractfile(name)
    except KeyError:
        return None


def export_archive(bundle: BackupSet, codename: str, target: Path, say: Callable[[str], None] = print) -> dict:
    """Write BUNDLE as one archive at TARGET (never over a file there); return its description.

    The backup is verified first, the archive read back after: TARGET appears only when both match.
    """
    if target.exists():
        msg = f"{target} exists already: choose a new file"
        raise ValueError(msg)
    passes = _Passes(_listed(bundle.folder / "manifest.json"), say)
    passes.begin(f"Checking the backup in {bundle.folder}")
    manifest = verified_backup(bundle.folder, passes.count)
    identity = json.loads(bundle.identity.read_text(encoding="utf-8"))
    if manifest.get("identity", identity) != identity:
        msg = f"{bundle.identity} describes another headset than the backup's manifest"
        raise ValueError(msg)
    missing = [str(path) for path in bundle.files.values() if not path.is_file()]
    if missing:
        msg = f"missing for the way back: {', '.join(missing)}"
        raise ValueError(msg)
    meta = {
        "schema": ARCHIVE_SCHEMA,
        "headset": codename,
        "folder": bundle.folder.name,
        "identity": identity,
        "files": sorted(bundle.files),
        "settings": dict(bundle.settings),
        "exported": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    part = target.with_name(target.name + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(part, "w", format=tarfile.PAX_FORMAT) as tar:
            tar.addfile(*_member(ARCHIVE_META, (json.dumps(meta, indent=2) + "\n").encode()))
            tar.add(bundle.folder / "manifest.json", "backup/manifest.json", recursive=False)
            passes.begin("Writing the backup file")
            for n, name in enumerate(manifest["files"], 1):
                say(f"Exporting {name} ({n} of {len(manifest['files'])})")
                info = tar.gettarinfo(bundle.folder / name, f"backup/{name}")
                with (bundle.folder / name).open("rb") as stream:
                    tar.addfile(info, _Counted(stream, passes.count))
            for name, path in bundle.files.items():
                tar.add(path, f"files/{name}", recursive=False)
        passes.begin("Reading the export back")
        check_archive(part, manifest, passes.count)
        part.replace(target)
    finally:
        with contextlib.suppress(OSError):
            part.unlink()
    say(f"Exported to {target}: keep it private, it is your headset's way back.")
    return meta


def read_meta(tar: tarfile.TarFile) -> dict:
    """Return an archive's description; a file that is no backup archive raises."""
    member = _extract(tar, ARCHIVE_META)
    if member is None:
        msg = "this file is no overpressure backup"
        raise ValueError(msg)
    meta = json.loads(member.read().decode("utf-8"))
    if not isinstance(meta, dict) or meta.get("schema") != ARCHIVE_SCHEMA:
        msg = "this backup file is of another version of overpressure"
        raise ValueError(msg)
    return meta


def check_archive(path: Path, manifest: dict | None = None, count: Callable[[int], None] | None = None) -> dict:
    """Return the manifest an archive holds once every backup file in it matches it; a mismatch raises.

    @p count is told the bytes as they are read.
    """
    with tarfile.open(path, "r") as tar:
        read_meta(tar)
        stream = _extract(tar, "backup/manifest.json")
        if stream is None:
            msg = "the backup file holds no manifest"
            raise ValueError(msg)
        held = json.loads(stream.read().decode("utf-8"))
        if manifest is not None and held != manifest:
            msg = "the backup file's manifest differs from the backup's"
            raise ValueError(msg)
        if held.get("schema") != 1 or not held.get("files"):
            msg = "the backup file's manifest is incomplete"
            raise ValueError(msg)
        for name, expected in held["files"].items():
            member = _extract(tar, f"backup/{name}")
            if member is None or _digest_stream(member, count) != expected:
                msg = f"the backup file is damaged: {name}"
                raise ValueError(msg)
    return held


def _inside(root: Path, name: str) -> Path:
    """Return ROOT/NAME, refused when NAME would leave ROOT (an absolute path, ..)."""
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or ".." in path.parts or ":" in name or "\\" in name:
        msg = f"the backup file names a path outside its folder: {name}"
        raise ValueError(msg)
    return root.joinpath(*path.parts)


def _same_identity(path: Path, identity: dict) -> bool | None:
    """Tell whether the identity record at PATH is IDENTITY's headset (by serial); None when there is none."""
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("serial") == identity.get("serial")
    except (OSError, ValueError, AttributeError):
        return False


def import_archive(source: Path, codename: str, here: BackupSet, say: Callable[[str], None] = print) -> BackupSet:
    """Put the backup archive SOURCE onto this computer where HERE (the headset's settings now) says; return it.

    Refused: another kind of headset, another headset than the identity here, a damaged file, and
    any file that would replace a different one. A backup already here with the same manifest is
    kept as it is; one with another manifest gets a new folder beside it.
    """
    say(f"Checking {source}")
    with tarfile.open(source, "r") as tar:
        meta = read_meta(tar)
    if meta.get("headset") != codename:
        msg = f"this is a backup of another kind of headset ({meta.get('headset')})"
        raise ValueError(msg)
    identity = meta.get("identity") or {}
    if _same_identity(here.identity, identity) is False:
        msg = (
            f"this computer has another headset's identity in {here.identity}: this backup is of the headset "
            f"with serial {identity.get('serial')}"
        )
        raise ValueError(msg)
    with tarfile.open(source, "r") as tar:
        stream = _extract(tar, "backup/manifest.json")
        listed = json.loads(stream.read().decode("utf-8")).get("files") if stream is not None else None
    passes = _Passes(listed, say)
    passes.begin(f"Checking every file in {source.name}")
    manifest = check_archive(source, count=passes.count)
    if manifest.get("identity", identity) != identity:
        msg = "the backup file's manifest is of another headset than its description"
        raise ValueError(msg)
    folder = _inside(here.folder.parent, str(meta.get("folder") or "imported"))
    present = folder / "manifest.json"
    if present.is_file() and json.loads(present.read_text(encoding="utf-8")) == manifest:
        say(f"The backup is on this computer already: {folder}")
        passes.count(passes.meter.total // _Passes.PASSES)  # nothing to copy
        passes.begin("Checking the backup here")
        verified_backup(folder, passes.count)
    else:
        if folder.exists():
            folder = folder.with_name(f"{folder.name}-imported-{dt.datetime.now(dt.timezone.utc):%Y%m%d-%H%M%S}")
        passes.begin("Copying the backup onto this computer")
        _unpack(source, folder, manifest, here, names=meta.get("files", []), say=say, count=passes.count)
        passes.begin("Checking the imported backup")
        verified_backup(folder, passes.count)
    if not here.identity.is_file():
        save_new(here.identity, identity)
    settings = {str(k): str(v) for k, v in (meta.get("settings") or {}).items()}
    files = {n: here.files[n] for n in meta.get("files", []) if n in here.files}
    say(f"Imported: {folder}")
    return type(here)(folder, here.identity, files, settings)


def _copy(member: IO[bytes], path: Path, count: Callable[[int], None] | None) -> None:
    """Write an archive member into the new file PATH, its bytes told to COUNT."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as out:
        for block in iter(lambda: member.read(CHUNK), b""):
            out.write(block)
            if count is not None:
                count(len(block))


def _unpack(  # noqa: PLR0913  # the archive, where it goes, what it must hold
    source: Path,
    folder: Path,
    manifest: dict,
    here: BackupSet,
    *,
    names: list[str],
    say: Callable[[str], None],
    count: Callable[[int], None] | None = None,
) -> None:
    """Write the archive's backup into the new FOLDER and its other files NAMES where HERE puts them."""
    with tarfile.open(source, "r") as tar:
        others = {}
        for name in names:
            member = _extract(tar, f"files/{name}")
            if name in here.files and member is not None:
                others[name] = member.read()
        conflicts = [
            str(here.files[n])
            for n, data in others.items()
            if here.files[n].is_file() and here.files[n].read_bytes() != data
        ]
        if conflicts:
            msg = f"this computer has other files where the backup's go: {', '.join(conflicts)}"
            raise ValueError(msg)
        folder.mkdir(parents=True)
        for n, name in enumerate(["manifest.json", *manifest["files"]]):
            if n:
                say(f"Importing {name} ({n} of {len(manifest['files'])})")
            member = _extract(tar, f"backup/{name}")
            if member is None:
                msg = f"the backup file lacks {name}"
                raise ValueError(msg)
            _copy(member, _inside(folder, name), count)
    for name, data in others.items():
        path = here.files[name]
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o600)


def main(headset: Headset, argv: list[str]) -> int:
    """Run python -m overpressure backup export|import FILE."""
    ap = argparse.ArgumentParser(prog="python -m overpressure backup", description=(__doc__ or "").split("\n\n")[1])
    ap.add_argument("action", choices=("export", "import"))
    ap.add_argument("file", type=Path)
    args = ap.parse_args(argv)
    here = headset.backup_set()
    if here is None:
        print(f"error: the {headset.info.name} keeps no backup in overpressure", file=sys.stderr)
        return 2
    try:
        if args.action == "export":
            export_archive(here, headset.info.codename, args.file, lambda line: print(line, flush=True))
        else:
            imported = import_archive(args.file, headset.info.codename, here, lambda line: print(line, flush=True))
            headset.adopt_backup(imported)
    except (OSError, ValueError, tarfile.TarError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
