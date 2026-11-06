# SPDX-License-Identifier: Unlicense
"""Stock firmware from a vendor's OTA: a partition's image, or a full block OTA's partitions.

Android OTAs come in two kinds. A classic (non-A/B) block OTA is a zip whose members are the
small images (``boot.img``, ``recovery.img``, firmware files) and, per big partition, a
brotli-compressed ``NAME.new.dat.br`` with the ``NAME.transfer.list`` that places its blocks.
An A/B OTA holds a ``payload.bin`` (:mod:`overpressure.payload`). For URLs only what is needed is
downloaded (HTTP Range requests: the zip's central directory and the members), and every member
is checked against the CRC-32 the zip records. A headset keeps a catalog of its vendor's builds
(``{"builds": {NAME: {"url": ...}}}``) and says which members it needs. A PinnedMember is one
file out of a remote zip, pinned by its size and SHA-256 and checked before it is written.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import struct
import subprocess
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Callable, TypeVar, cast

from . import payload

if TYPE_CHECKING:
    import os
    from collections.abc import Iterable, Mapping

CHUNK = 8 * 1024 * 1024
BLOCK = 4096
#: The transfer list versions AOSP's block OTAs write.
TRANSFER_LIST_VERSIONS = range(1, 5)
#: From this version on, two more header lines count the stash.
TRANSFER_LIST_STASH_VERSION = 2


def load_catalog(path: str | os.PathLike[str]) -> dict:
    """Return a headset's catalog of its vendor's builds: name -> {"url": ..., ...}."""
    return json.loads(Path(path).read_text(encoding="utf-8"))["builds"]


def _member_start(src: payload.Source, info: zipfile.ZipInfo) -> int:
    """Where a zip member's data begins, after its local file header."""
    local = src.read_at(info.header_offset, 30)
    if local[:4] != b"PK\x03\x04":
        msg = f"bad local file header for {info.filename}"
        raise ValueError(msg)
    name_len, extra_len = struct.unpack_from("<HH", local, 26)
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        msg = f"{info.filename}: unsupported zip compression method {info.compress_type}"
        raise ValueError(msg)
    return info.header_offset + 30 + name_len + extra_len


def stream_member(
    src: payload.Source, info: zipfile.ZipInfo, out: IO[bytes], log: Callable[[str], None] = print
) -> None:
    """Write a zip member's data into @p out, checked against its CRC-32."""
    start = _member_start(src, info)
    inflater = zlib.decompressobj(-15) if info.compress_type == zipfile.ZIP_DEFLATED else None
    crc, done, written = 0, 0, 0
    while done < info.compress_size:
        chunk = src.read_at(start + done, min(CHUNK, info.compress_size - done))
        done += len(chunk)
        data = inflater.decompress(chunk) if inflater else chunk
        crc = zlib.crc32(data, crc)
        out.write(data)
        written += len(data)
        if done % (256 * 1024 * 1024) < CHUNK or done == info.compress_size:
            log(f"  {info.filename}: {done * 100 // max(info.compress_size, 1):3d}% ({done / 1e6:.0f} MB)")
    if inflater:
        tail = inflater.flush()
        crc = zlib.crc32(tail, crc)
        out.write(tail)
        written += len(tail)
    if written != info.file_size or crc != info.CRC:
        msg = f"{info.filename}: CRC/size mismatch after download (corrupt transfer?)"
        raise ValueError(msg)


def zip_infos(src: payload.Source) -> dict[str, zipfile.ZipInfo]:
    """Return a zip's members by name, from its central directory."""
    with zipfile.ZipFile(io.BufferedReader(payload.SourceFile(src), buffer_size=65536)) as zf:
        return {info.filename: info for info in zf.infolist()}


def read_member(src: payload.Source, name: str, log: Callable[[str], None] = print) -> bytes:
    """Return a zip member's data, checked against its CRC-32."""
    info = zip_infos(src)[name]
    log(f"  {name}: {info.compress_size / 1e6:.1f} MB compressed, {info.file_size / 1e6:.1f} MB unpacked")
    out = io.BytesIO()
    stream_member(src, info, out, log)
    log(f"  {name}: CRC-32 {info.CRC:08x} verified against the zip directory")
    return out.getvalue()


@dataclass(frozen=True)
class PinnedMember:
    """One file in a vendor's remote zip, pinned by its size and SHA-256."""

    url: str
    member: str
    size: int
    sha256: str


P = TypeVar("P", bound=PinnedMember)


def read_pinned(item: PinnedMember, log: Callable[[str], None] = print) -> bytes:
    """Return a pinned member's data, read from its zip over HTTP ranges (CRC-32 checked, not yet the hash)."""
    return read_member(payload.open_source(item.url, log), item.member, log)


def fetch_pinned(
    item: P, path: Path, log: Callable[[str], None] = print, read: Callable[[P], bytes] | None = None
) -> Path:
    """Put a pinned member at @p path unless it is there already; return the path.

    Only its member is read from the archive (@p read, by default over HTTP ranges), a few MB of
    an archive of gigabytes. A size or hash mismatch writes nothing.
    """
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == item.sha256:
        log(f"{path}: already there")
        return path
    data = read(item) if read is not None else read_pinned(item, log)
    found = hashlib.sha256(data).hexdigest()
    if found != item.sha256 or len(data) != item.size:
        msg = f"{item.member} from {item.url} is not the known file (SHA-256 {found}): nothing written"
        raise ValueError(msg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    log(f"{path}: {item.size} bytes, SHA-256 checked")
    return path


def fetch_boot_image(
    location: str, cache_dir: str | None = None, log: Callable[[str], None] = print, partition: str = "boot"
) -> bytes:
    """Return the raw stock boot.img, or another partition's image such as recovery's.

    LOCATION is an .img file, an OTA zip or payload, or the URL of one.
    """
    cached: Path | None = None
    if cache_dir and location.startswith(("http://", "https://")):
        key = location.rstrip("/").rsplit("/", 1)[-1]
        cached = Path(cache_dir, "stock", f"{key}.{partition}.img")
        if cached.exists():
            log(f"  using cached {cached}")
            return cached.read_bytes()

    src = payload.open_source(location, log)
    magic = src.read_at(0, 8)
    if magic == b"ANDROID!":
        data = src.read_at(0, src.size)
    elif magic[:4] == payload.PAYLOAD_MAGIC:
        data = payload.extract_partition(payload.read_payload(src), partition, log)
    elif magic[:4] == b"PK\x03\x04":
        names = set(zip_infos(src))
        if f"{partition}.img" in names:
            data = read_member(src, f"{partition}.img", log)
        elif "payload.bin" in names:
            data = payload.extract_partition(payload.read_payload(payload.payload_from(location, log)), partition, log)
        else:
            msg = f"zip contains neither {partition}.img nor payload.bin"
            raise ValueError(msg)
    else:
        msg = f"{location}: not a boot image, OTA zip or payload (magic {magic!r})"
        raise ValueError(msg)

    if isinstance(src, payload.HttpSource):
        log(f"  downloaded {src.downloaded / 1e6:.1f} MB of a {src.size / 1e9:.2f} GB file")
    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        part = cached.with_name(cached.name + ".part")
        part.write_bytes(data)
        part.replace(cached)
    return data


def parse_rangeset(text: str) -> list[tuple[int, int]]:
    """Return a transfer list's range set ("4,0,10,20,30": two ranges) as (first, end) block pairs."""
    values = [int(v) for v in text.split(",")]
    if values[0] != len(values) - 1 or values[0] % 2:
        msg = f"malformed range set {text!r}"
        raise ValueError(msg)
    return [(values[i], values[i + 1]) for i in range(1, len(values), 2)]


def apply_transfer_list(transfer_list: str, new_data: IO[bytes], out: IO[bytes]) -> int:
    """Rebuild a partition image from a full OTA's transfer list and its decompressed new data.

    As AOSP's block-based OTA writes it: 'new' takes the next blocks of the data in order, 'zero'
    and 'erase' leave zeros. Returns the image's size in bytes.
    """
    lines = transfer_list.splitlines()
    version, total = int(lines[0]), int(lines[1])
    if version not in TRANSFER_LIST_VERSIONS:
        msg = f"unsupported transfer list version {version}"
        raise ValueError(msg)
    commands = lines[4:] if version >= TRANSFER_LIST_STASH_VERSION else lines[2:]
    for line in commands:
        if not line.strip():
            continue
        cmd, _, arg = line.partition(" ")
        if cmd == "new":
            for first, end in parse_rangeset(arg):
                out.seek(first * BLOCK)
                left = (end - first) * BLOCK
                while left:
                    data = new_data.read(min(left, CHUNK))
                    if not data:
                        msg = "the new data ends before the transfer list"
                        raise ValueError(msg)
                    out.write(data)
                    left -= len(data)
        elif cmd in ("zero", "erase"):
            continue  # a fresh file is zero already
        else:
            msg = f"a full OTA's transfer list has no {cmd!r} command"
            raise ValueError(msg)
    size = total * BLOCK
    out.seek(0, io.SEEK_END)
    if out.tell() < size:  # trailing zero blocks: extend (truncate need not)
        out.seek(size - 1)
        out.write(b"\0")
    out.truncate(size)
    return size


def member_ok(path: Path, info: zipfile.ZipInfo) -> bool:
    """Tell whether PATH holds the member already: its size and CRC-32 match."""
    if not path.exists() or path.stat().st_size != info.file_size:
        return False
    crc = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            crc = zlib.crc32(chunk, crc)
    return crc == info.CRC


def download_member(src: payload.Source, info: zipfile.ZipInfo, path: Path, log: Callable[[str], None] = print) -> None:
    """Download one zip member into @p path, checked against its CRC-32; keep it if it is there."""
    if member_ok(path, info):
        log(f"  {info.filename}: already downloaded")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    try:
        with tmp.open("wb") as out:
            stream_member(src, info, out, log)
    except ValueError:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(path)


def rebuild_image(download: Path, name: str, out_path: Path, log: Callable[[str], None] = print) -> None:
    """Rebuild an ext4 image from NAME.new.dat.br and NAME.transfer.list, through the brotli command."""
    brotli = shutil.which("brotli")
    if brotli is None:
        msg = "the brotli command is required (Debian/Ubuntu: apt install brotli)"
        raise SystemExit(msg)
    transfer = (download / f"{name}.transfer.list").read_text(encoding="ascii")
    tmp = out_path.with_name(out_path.name + ".part")
    log(f">> {out_path.name} from {name}.new.dat.br")
    proc = subprocess.Popen(  # noqa: S603  # the brotli found on PATH, on a file of the OTA
        [brotli, "-d", "-c", str(download / f"{name}.new.dat.br")], stdout=subprocess.PIPE
    )
    stdout = cast("IO[bytes]", proc.stdout)  # stdout=PIPE
    try:
        with tmp.open("wb") as out:
            size = apply_transfer_list(transfer, stdout, out)
        rest = stdout.read(1)
    finally:
        stdout.close()
        code = proc.wait()
    if code != 0:
        msg = f"brotli failed on {name}.new.dat.br ({code})"
        raise SystemExit(msg)
    if rest:
        msg = f"{name}.new.dat.br holds more data than its transfer list places"
        raise ValueError(msg)
    with tmp.open("rb") as f:
        f.seek(0x438)  # ext4 superblock magic
        if f.read(2) != b"\x53\xef":
            msg = f"{out_path.name} is not an ext4 image"
            raise ValueError(msg)
    tmp.replace(out_path)
    log(f"  {out_path.name}: {size / 1e6:.0f} MB")


def sha256(path: Path) -> str:
    """Return a file's SHA-256, in hex."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def members_needed(copied: Mapping[str, str], block_images: Mapping[str, str]) -> Iterable[str]:
    """Yield the OTA members a set of copied files and block images needs."""
    yield from copied.values()
    for base in block_images.values():
        yield f"{base}.new.dat.br"
        yield f"{base}.transfer.list"


def extract(  # noqa: PLR0913  # the build, its OTA, the output and what to take; log keyword-only
    label: str,
    location: str,
    output: Path,
    copied: Mapping[str, str],
    block_images: Mapping[str, str],
    *,
    log: Callable[[str], None] = print,
) -> dict:
    """Extract a full block OTA's files into @p output.

    @p copied (output name -> member) as they are, @p block_images (output name -> base name)
    rebuilt from their transfer lists, and manifest.json with every file's SHA-256. The members
    stay under output/download and are fetched again only when missing or corrupt; a rebuilt image
    stays while the manifest says it is this build's.
    """
    output.mkdir(parents=True, exist_ok=True)
    download = output / "download"
    src = payload.open_source(location, log)
    everything = zip_infos(src)
    infos = {name: everything[name] for name in members_needed(copied, block_images)}
    log(f">> {label}: {sum(i.compress_size for i in infos.values()) / 1e9:.1f} GB of OTA members")
    for name, info in infos.items():
        download_member(src, info, download / name, log)
    for out_name, member in copied.items():
        shutil.copyfile(download / member, output / out_name)
    previous = {}
    if (output / "manifest.json").exists():
        old = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        previous = old["files"] if old.get("firmware") == label and old.get("source") == location else {}
    for out_name, base in block_images.items():
        path = output / out_name
        if not path.exists() or previous.get(out_name) != sha256(path):
            rebuild_image(download, base, path, log)
    files = {name: sha256(output / name) for name in (*copied, *block_images)}
    manifest = {"schema": 1, "firmware": label, "source": location, "files": files}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    log(f">> {output}: {', '.join(files)}")
    return manifest
