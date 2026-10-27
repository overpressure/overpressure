"""Stock firmware from a vendor's OTA: a partition's image, or a full block OTA's partitions.

Android OTAs come in two kinds. A classic (non-A/B) block OTA is a zip whose members are the
small images (``boot.img``, ``recovery.img``, firmware files) and, per big partition, a
brotli-compressed ``NAME.new.dat.br`` with the ``NAME.transfer.list`` that places its blocks.
An A/B OTA holds a ``payload.bin`` (:mod:`overpressure.payload`). For URLs only what is needed is
downloaded (HTTP Range requests: the zip's central directory and the members), and every member
is checked against the CRC-32 the zip records. A headset keeps a catalog of its vendor's builds
(``{"builds": {NAME: {"url": ...}}}``) and says which members it needs.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import zipfile
import zlib
from pathlib import Path
from typing import BinaryIO, Dict, Iterable, Mapping

from . import payload

CHUNK = 8 * 1024 * 1024
BLOCK = 4096


def load_catalog(path) -> dict:
    """A headset's catalog of its vendor's builds: name -> {"url": ..., ...}."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)["builds"]


def _member_start(src: payload.Source, info: zipfile.ZipInfo) -> int:
    """Where a zip member's data begins, after its local file header."""
    local = src.read_at(info.header_offset, 30)
    if local[:4] != b"PK\x03\x04":
        raise ValueError(f"bad local file header for {info.filename}")
    name_len, extra_len = struct.unpack_from("<HH", local, 26)
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise ValueError(f"{info.filename}: unsupported zip compression method {info.compress_type}")
    return info.header_offset + 30 + name_len + extra_len


def stream_member(src: payload.Source, info: zipfile.ZipInfo, out, log=print) -> None:
    """A zip member's data into @p out (a writable binary stream), checked against its CRC-32."""
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
        raise ValueError(f"{info.filename}: CRC/size mismatch after download (corrupt transfer?)")


def zip_infos(src: payload.Source) -> Dict[str, zipfile.ZipInfo]:
    with zipfile.ZipFile(io.BufferedReader(payload.SourceFile(src), buffer_size=65536)) as zf:
        return {info.filename: info for info in zf.infolist()}


def read_member(src: payload.Source, name: str, log=print) -> bytes:
    info = zip_infos(src)[name]
    log(f"  {name}: {info.compress_size / 1e6:.1f} MB compressed, {info.file_size / 1e6:.1f} MB unpacked")
    out = io.BytesIO()
    stream_member(src, info, out, log)
    log(f"  {name}: CRC-32 {info.CRC:08x} verified against the zip directory")
    return out.getvalue()


def fetch_boot_image(location: str, cache_dir: str | None = None, log=print, partition: str = "boot") -> bytes:
    """Return the raw stock boot.img (or another partition's image, such as recovery's) from a
    .img file, OTA zip/payload, or URL."""
    if cache_dir and location.startswith(("http://", "https://")):
        key = location.rstrip("/").rsplit("/", 1)[-1]
        cached = os.path.join(cache_dir, "stock", f"{key}.{partition}.img")
        if os.path.exists(cached):
            log(f"  using cached {cached}")
            with open(cached, "rb") as f:
                return f.read()
    else:
        cached = None

    src = payload.open_source(location, log)
    magic = src.read_at(0, 8)
    if magic == b"ANDROID!":
        data = src.read_at(0, src.size)
    elif magic[:4] == payload.PAYLOAD_MAGIC:
        data = payload.extract_partition(payload.read_payload(src), "boot", log)
    elif magic[:4] == b"PK\x03\x04":
        names = set(zip_infos(src))
        if f"{partition}.img" in names:
            data = read_member(src, f"{partition}.img", log)
        elif "payload.bin" in names:
            data = payload.extract_partition(payload.read_payload(payload.payload_from(location, log)), partition, log)
        else:
            raise ValueError(f"zip contains neither {partition}.img nor payload.bin")
    else:
        raise ValueError(f"{location}: not a boot image, OTA zip or payload (magic {magic!r})")

    if isinstance(src, payload.HttpSource):
        log(f"  downloaded {src.downloaded / 1e6:.1f} MB of a {src.size / 1e9:.2f} GB file")
    if cached:
        os.makedirs(os.path.dirname(cached), exist_ok=True)
        with open(cached + ".part", "wb") as f:
            f.write(data)
        os.replace(cached + ".part", cached)
    return data


def parse_rangeset(text: str) -> list[tuple[int, int]]:
    """A transfer list's range set ("4,0,10,20,30": two ranges) as (first, end) block pairs."""
    values = [int(v) for v in text.split(",")]
    if values[0] != len(values) - 1 or values[0] % 2:
        raise ValueError(f"malformed range set {text!r}")
    return [(values[i], values[i + 1]) for i in range(1, len(values), 2)]


def apply_transfer_list(transfer_list: str, new_data: BinaryIO, out: BinaryIO) -> int:
    """Rebuild a partition image from a full OTA's transfer list and its decompressed new data
    (as AOSP's block-based OTA writes it: 'new' takes the next blocks of the data in order,
    'zero' and 'erase' leave zeros). Returns the image's size in bytes."""
    lines = transfer_list.splitlines()
    version, total = int(lines[0]), int(lines[1])
    if version < 1 or version > 4:
        raise ValueError(f"unsupported transfer list version {version}")
    commands = lines[4:] if version >= 2 else lines[2:]
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
                        raise ValueError("the new data ends before the transfer list")
                    out.write(data)
                    left -= len(data)
        elif cmd in ("zero", "erase"):
            continue  # a fresh file is zero already
        else:
            raise ValueError(f"a full OTA's transfer list has no {cmd!r} command")
    size = total * BLOCK
    out.seek(0, io.SEEK_END)
    if out.tell() < size:  # trailing zero blocks: extend (truncate need not)
        out.seek(size - 1)
        out.write(b"\0")
    out.truncate(size)
    return size


def member_ok(path: Path, info: zipfile.ZipInfo) -> bool:
    if not path.exists() or path.stat().st_size != info.file_size:
        return False
    crc = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            crc = zlib.crc32(chunk, crc)
    return crc == info.CRC


def download_member(src: payload.Source, info: zipfile.ZipInfo, path: Path, log=print) -> None:
    """One zip member into @path, its CRC-32 checked against the zip directory; kept if it is there."""
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


def rebuild_image(download: Path, name: str, out_path: Path, log=print) -> None:
    """vendor.new.dat.br + vendor.transfer.list -> an ext4 image, through the brotli command."""
    brotli = shutil.which("brotli")
    if brotli is None:
        raise SystemExit("the brotli command is required (Debian/Ubuntu: apt install brotli)")
    transfer = (download / f"{name}.transfer.list").read_text(encoding="ascii")
    tmp = out_path.with_name(out_path.name + ".part")
    log(f">> {out_path.name} from {name}.new.dat.br")
    proc = subprocess.Popen([brotli, "-d", "-c", str(download / f"{name}.new.dat.br")], stdout=subprocess.PIPE)
    try:
        with tmp.open("wb") as out:
            size = apply_transfer_list(transfer, proc.stdout, out)
        rest = proc.stdout.read(1)
    finally:
        proc.stdout.close()
        code = proc.wait()
    if code != 0:
        raise SystemExit(f"brotli failed on {name}.new.dat.br ({code})")
    if rest:
        raise ValueError(f"{name}.new.dat.br holds more data than its transfer list places")
    with tmp.open("rb") as f:
        f.seek(0x438)  # ext4 superblock magic
        if f.read(2) != b"\x53\xef":
            raise ValueError(f"{out_path.name} is not an ext4 image")
    tmp.replace(out_path)
    log(f"  {out_path.name}: {size / 1e6:.0f} MB")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def members_needed(copied: Mapping[str, str], block_images: Mapping[str, str]) -> Iterable[str]:
    """The OTA members a set of copied files and block images needs."""
    yield from copied.values()
    for base in block_images.values():
        yield f"{base}.new.dat.br"
        yield f"{base}.transfer.list"


def extract(label: str, location: str, output: Path, copied: Mapping[str, str], block_images: Mapping[str, str],
            log=print) -> dict:
    """A full block OTA's files into @p output: @p copied (output name -> member) as they are,
    @p block_images (output name -> base name) rebuilt from their transfer lists, and
    manifest.json with every file's SHA-256. The members stay under output/download and are
    fetched again only when missing or corrupt; a rebuilt image stays while the manifest says it
    is this build's."""
    output.mkdir(parents=True, exist_ok=True)
    download = output / "download"
    src = payload.HttpSource(location, log) if location.startswith(("http://", "https://")) \
        else payload.FileSource(location)
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
