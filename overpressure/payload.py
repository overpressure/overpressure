"""Extract partitions (e.g. ``boot``) from an Android A/B OTA ``payload.bin``.

Works on a local OTA zip, a bare payload.bin, or an http(s) URL of an OTA zip.
For URLs only the bytes that are actually needed (zip directory, payload
manifest and the operations of the requested partitions) are fetched with
HTTP Range requests, so pulling a ~100 MB boot partition out of a multi-GB
OTA costs a few tens of MB of download.

Only full-OTA operation types are supported (REPLACE, REPLACE_BZ, REPLACE_XZ,
ZERO, DISCARD); incremental OTAs cannot be extracted without the old image.
"""
from __future__ import annotations

import bz2
import hashlib
import io
import lzma
import struct
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field

PAYLOAD_MAGIC = b"CrAU"
OP_NAMES = {0: "REPLACE", 1: "REPLACE_BZ", 2: "MOVE", 3: "BSDIFF", 4: "SOURCE_COPY", 5: "SOURCE_BSDIFF",
            6: "ZERO", 7: "DISCARD", 8: "REPLACE_XZ", 9: "PUFFDIFF", 10: "BROTLI_BSDIFF", 11: "ZUCCHINI",
            12: "LZ4DIFF_BSDIFF", 13: "LZ4DIFF_PUFFDIFF", 14: "REPLACE_ZSTD"}
USER_AGENT = "overpressure-build/1.0 (+payload range reader)"


# -- byte sources ------------------------------------------------------------

class Source:
    size: int

    def read_at(self, offset: int, length: int) -> bytes:
        raise NotImplementedError


class FileSource(Source):
    def __init__(self, path: str):
        self.f = open(path, "rb")
        self.f.seek(0, io.SEEK_END)
        self.size = self.f.tell()

    def read_at(self, offset: int, length: int) -> bytes:
        self.f.seek(offset)
        return self.f.read(length)


class HttpSource(Source):
    """Random access to a remote file through HTTP Range requests."""

    BLOCK = 256 * 1024

    def __init__(self, url: str, log=print):
        self.url = url
        self.log = log
        self.cache: dict[int, bytes] = {}
        self.downloaded = 0
        head = self._request(0, 0)
        content_range = head.headers.get("Content-Range", "")
        if head.status != 206 or "/" not in content_range:
            raise IOError(f"server does not support HTTP range requests (status {head.status})")
        head.read()
        self.size = int(content_range.rsplit("/", 1)[1])

    def _request(self, start: int, end: int):
        req = urllib.request.Request(self.url, headers={"Range": f"bytes={start}-{end}", "User-Agent": USER_AGENT})
        for attempt in range(5):
            try:
                return urllib.request.urlopen(req, timeout=60)
            except urllib.error.HTTPError as exc:
                if exc.code < 500 or attempt == 4:  # 4xx will not get better by retrying
                    raise
                self.log(f"  HTTP error ({exc}); retrying...")
                time.sleep(2 * (attempt + 1))
            except OSError as exc:
                if attempt == 4:
                    raise
                self.log(f"  HTTP error ({exc}); retrying...")
                time.sleep(2 * (attempt + 1))
        raise AssertionError("unreachable")

    def _fetch(self, start: int, length: int) -> bytes:
        for attempt in range(5):
            try:
                resp = self._request(start, start + length - 1)
                if resp.status != 206:
                    raise IOError(f"expected 206 Partial Content, got {resp.status}")
                data = resp.read()
                if len(data) != length:
                    raise IOError(f"short read ({len(data)} of {length} bytes)")
                self.downloaded += len(data)
                return data
            except OSError as exc:
                if attempt == 4:
                    raise
                self.log(f"  download error ({exc}); retrying...")
                time.sleep(2 * (attempt + 1))
        raise AssertionError("unreachable")

    def read_at(self, offset: int, length: int) -> bytes:
        length = max(0, min(length, self.size - offset))
        if length == 0:
            return b""
        if length > 4 * self.BLOCK:  # big blobs: fetch directly, don't cache
            return self._fetch(offset, length)
        out = bytearray()
        first, last = offset // self.BLOCK, (offset + length - 1) // self.BLOCK
        for blk in range(first, last + 1):
            if blk not in self.cache:
                start = blk * self.BLOCK
                self.cache[blk] = self._fetch(start, min(self.BLOCK, self.size - start))
            out += self.cache[blk]
        skip = offset - first * self.BLOCK
        return bytes(out[skip:skip + length])


class SourceFile(io.RawIOBase):
    """Adapter so :mod:`zipfile` can read the central directory of a Source."""

    def __init__(self, src: Source):
        self.src, self.pos = src, 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.src.size}[whence]
        self.pos = base + offset
        return self.pos

    def readinto(self, b):
        data = self.src.read_at(self.pos, len(b))
        b[:len(data)] = data
        self.pos += len(data)
        return len(data)


class SliceSource(Source):
    def __init__(self, src: Source, offset: int, size: int):
        self.src, self.offset, self.size = src, offset, size

    def read_at(self, offset: int, length: int) -> bytes:
        length = max(0, min(length, self.size - offset))
        return self.src.read_at(self.offset + offset, length)


def open_source(location: str, log=print) -> Source:
    if location.startswith(("http://", "https://")):
        return HttpSource(location, log)
    return FileSource(location)


def payload_from(location: str, log=print) -> Source:
    """Return a Source for payload.bin inside an OTA zip (or a bare payload)."""
    src = open_source(location, log)
    if src.read_at(0, 4) == PAYLOAD_MAGIC:
        return src
    with zipfile.ZipFile(io.BufferedReader(SourceFile(src), buffer_size=65536)) as zf:
        info = zf.getinfo("payload.bin")
    if info.compress_type != zipfile.ZIP_STORED:
        raise ValueError("payload.bin is compressed inside the zip; extract the zip first")
    local = src.read_at(info.header_offset, 30)
    if local[:4] != b"PK\x03\x04":
        raise ValueError("bad local file header for payload.bin")
    name_len, extra_len = struct.unpack_from("<HH", local, 26)
    data_start = info.header_offset + 30 + name_len + extra_len
    return SliceSource(src, data_start, info.file_size)


# -- protobuf (just enough for update_metadata.proto) -------------------------

def _varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def pb_decode(buf: bytes) -> dict[int, list]:
    fields: dict[int, list] = {}
    i, n = 0, len(buf)
    while i < n:
        key, i = _varint(buf, i)
        num, wire = key >> 3, key & 7
        if wire == 0:
            val, i = _varint(buf, i)
        elif wire == 1:
            val, i = buf[i:i + 8], i + 8
        elif wire == 2:
            ln, i = _varint(buf, i)
            val, i = buf[i:i + ln], i + ln
        elif wire == 5:
            val, i = buf[i:i + 4], i + 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        fields.setdefault(num, []).append(val)
    return fields


def _one(fields: dict, num: int, default=None):
    vals = fields.get(num)
    return vals[-1] if vals else default


@dataclass
class Operation:
    type: int
    data_offset: int
    data_length: int
    dst_extents: list[tuple[int, int]]
    data_sha256: bytes | None


@dataclass
class Partition:
    name: str
    size: int
    sha256: bytes | None
    operations: list[Operation] = field(default_factory=list)


@dataclass
class Payload:
    src: Source
    block_size: int
    data_start: int
    partitions: dict[str, Partition]
    security_patch_level: str = ""


def read_payload(src: Source) -> Payload:
    header = src.read_at(0, 24)
    if header[:4] != PAYLOAD_MAGIC:
        raise ValueError("not an update_engine payload (missing CrAU magic)")
    version, manifest_size = struct.unpack_from(">QQ", header, 4)
    if version != 2:
        raise ValueError(f"unsupported payload version {version}")
    (sig_size,) = struct.unpack_from(">I", header, 20)
    manifest = pb_decode(src.read_at(24, manifest_size))
    block_size = _one(manifest, 3, 4096)
    parts: dict[str, Partition] = {}
    for raw in manifest.get(13, []):
        pf = pb_decode(raw)
        name = _one(pf, 1, b"").decode()
        info = pb_decode(_one(pf, 7, b""))
        part = Partition(name, _one(info, 1, 0), _one(info, 2))
        for op_raw in pf.get(8, []):
            of = pb_decode(op_raw)
            extents = []
            for ext_raw in of.get(6, []):
                ef = pb_decode(ext_raw)
                extents.append((_one(ef, 1, 0), _one(ef, 2, 0)))
            part.operations.append(Operation(_one(of, 1, 0), _one(of, 2, 0), _one(of, 3, 0), extents, _one(of, 8)))
        parts[name] = part
    spl = _one(manifest, 18, b"")
    return Payload(src, block_size, 24 + manifest_size + sig_size, parts,
                   spl.decode() if isinstance(spl, bytes) else "")


def extract_partition(payload: Payload, name: str, log=print) -> bytes:
    part = payload.partitions.get(name)
    if part is None:
        raise KeyError(f"partition {name!r} not in payload (has: {', '.join(sorted(payload.partitions))})")
    bs = payload.block_size
    out = bytearray(part.size)
    total = len(part.operations)
    for idx, op in enumerate(part.operations, 1):
        kind = OP_NAMES.get(op.type, str(op.type))
        if op.type in (6, 7):  # ZERO / DISCARD: buffer is already zero
            continue
        if op.type not in (0, 1, 8):
            raise ValueError(f"{name}: operation {kind} needs the source image (incremental OTA?)")
        blob = payload.src.read_at(payload.data_start + op.data_offset, op.data_length)
        if op.data_sha256 and hashlib.sha256(blob).digest() != op.data_sha256:
            raise ValueError(f"{name}: operation {idx} data hash mismatch")
        if op.type == 1:
            blob = bz2.decompress(blob)
        elif op.type == 8:
            blob = lzma.decompress(blob)
        pos = 0
        for start, count in op.dst_extents:
            chunk = blob[pos:pos + count * bs]
            out[start * bs:start * bs + len(chunk)] = chunk
            pos += count * bs
        if idx % 16 == 0 or idx == total:
            log(f"  {name}: {idx}/{total} operations")
    data = bytes(out)
    if part.sha256:
        if hashlib.sha256(data).digest() != part.sha256:
            raise ValueError(f"{name}: partition hash mismatch after extraction")
        log(f"  {name}: sha256 verified against the payload manifest")
    return data


def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Extract partitions from an A/B OTA payload")
    ap.add_argument("ota", help="OTA zip, payload.bin, or http(s) URL of an OTA zip")
    ap.add_argument("partitions", nargs="*", help="partitions to extract (default: list them)")
    ap.add_argument("-o", "--out-dir", default=".")
    args = ap.parse_args(argv)
    payload = read_payload(payload_from(args.ota))
    if not args.partitions:
        for p in payload.partitions.values():
            print(f"{p.name:24s} {p.size:>12d} bytes  {len(p.operations)} ops")
        return 0
    import os
    for name in args.partitions:
        data = extract_partition(payload, name)
        path = os.path.join(args.out_dir, f"{name}.img")
        with open(path, "wb") as f:
            f.write(data)
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
