"""Obtain the stock PICO 4 boot.img from a file, an OTA zip, or an OTA URL.

PICO 4 OTAs are classic (non-A/B) block OTAs: ``boot.img`` is a plain deflated
member of a ~3.7 GB zip. For URLs only the zip's central directory and the
~16 MB compressed boot.img member are downloaded (HTTP Range requests); the
result is checked against the CRC-32 recorded in the zip. A/B OTAs with a
``payload.bin`` are handled by :mod:`overpressure.payload`.
"""
from __future__ import annotations

import io
import json
import os
import struct
import zipfile
import zlib

from overpressure import payload

CHUNK = 4 * 1024 * 1024


def load_catalog(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["builds"]


def _zip_member(src: payload.Source, name: str, log=print) -> bytes:
    with zipfile.ZipFile(io.BufferedReader(payload.SourceFile(src), buffer_size=65536)) as zf:
        info = zf.getinfo(name)
    local = src.read_at(info.header_offset, 30)
    if local[:4] != b"PK\x03\x04":
        raise ValueError(f"bad local file header for {name}")
    name_len, extra_len = struct.unpack_from("<HH", local, 26)
    start = info.header_offset + 30 + name_len + extra_len
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise ValueError(f"{name}: unsupported zip compression method {info.compress_type}")
    log(f"  {name}: {info.compress_size / 1e6:.1f} MB compressed, {info.file_size / 1e6:.1f} MB unpacked")
    inflater = zlib.decompressobj(-15) if info.compress_type == zipfile.ZIP_DEFLATED else None
    out = bytearray()
    crc = 0
    done = 0
    while done < info.compress_size:
        n = min(CHUNK, info.compress_size - done)
        chunk = src.read_at(start + done, n)
        done += len(chunk)
        data = inflater.decompress(chunk) if inflater else chunk
        crc = zlib.crc32(data, crc)
        out += data
        log(f"  {name}: {done * 100 // info.compress_size:3d}% ({done / 1e6:.1f} MB)")
    if inflater:
        tail = inflater.flush()
        crc = zlib.crc32(tail, crc)
        out += tail
    if len(out) != info.file_size or crc != info.CRC:
        raise ValueError(f"{name}: CRC/size mismatch after download (corrupt transfer?)")
    log(f"  {name}: CRC-32 {crc:08x} verified against the zip directory")
    return bytes(out)


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
        with zipfile.ZipFile(io.BufferedReader(payload.SourceFile(src), buffer_size=65536)) as zf:
            names = set(zf.namelist())
        if f"{partition}.img" in names:
            data = _zip_member(src, f"{partition}.img", log)
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
