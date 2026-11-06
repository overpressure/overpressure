# SPDX-License-Identifier: Unlicense
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
import http.client
import io
import lzma
import struct
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer

PAYLOAD_MAGIC = b"CrAU"
OP_NAMES = {
    0: "REPLACE",
    1: "REPLACE_BZ",
    2: "MOVE",
    3: "BSDIFF",
    4: "SOURCE_COPY",
    5: "SOURCE_BSDIFF",
    6: "ZERO",
    7: "DISCARD",
    8: "REPLACE_XZ",
    9: "PUFFDIFF",
    10: "BROTLI_BSDIFF",
    11: "ZUCCHINI",
    12: "LZ4DIFF_BSDIFF",
    13: "LZ4DIFF_PUFFDIFF",
    14: "REPLACE_ZSTD",
}
USER_AGENT = "overpressure-build/1.0 (+payload range reader)"


# -- byte sources ------------------------------------------------------------

HTTP_PARTIAL_CONTENT = 206
HTTP_SERVER_ERROR = 500
ATTEMPTS = 5


class Source:
    """Random access to the bytes of a file, local or remote."""

    size: int

    def read_at(self, offset: int, length: int) -> bytes:
        """Return LENGTH bytes from OFFSET (fewer at the end)."""
        raise NotImplementedError


class FileSource(Source):
    """A local file."""

    def __init__(self, path: str) -> None:
        """Open PATH for the source's lifetime, as the payload is read in pieces."""
        self.f = Path(path).open("rb")  # noqa: SIM115  # kept open for the source's lifetime
        self.f.seek(0, io.SEEK_END)
        self.size = self.f.tell()

    def read_at(self, offset: int, length: int) -> bytes:
        """Return LENGTH bytes from OFFSET (fewer at the end)."""
        self.f.seek(offset)
        return self.f.read(length)


class HttpSource(Source):
    """Random access to a remote file through HTTP Range requests."""

    BLOCK = 256 * 1024

    def __init__(self, url: str, log: Callable[[str], None] = print) -> None:
        """Ask for the first byte, which also tells whether the server does ranges and the size."""
        self.url = url
        self.log = log
        self.cache: dict[int, bytes] = {}
        self.downloaded = 0
        head = self._request(0, 0)
        content_range = head.headers.get("Content-Range", "")
        if head.status != HTTP_PARTIAL_CONTENT or "/" not in content_range:
            msg = f"server does not support HTTP range requests (status {head.status})"
            raise OSError(msg)
        head.read()
        self.size = int(content_range.rsplit("/", 1)[1])

    def _request(self, start: int, end: int) -> http.client.HTTPResponse:
        req = urllib.request.Request(  # noqa: S310  # open_source passes only http(s) URLs
            self.url, headers={"Range": f"bytes={start}-{end}", "User-Agent": USER_AGENT}
        )
        for attempt in range(ATTEMPTS):
            try:
                return urllib.request.urlopen(req, timeout=60)  # noqa: S310  # open_source passes only http(s) URLs
            except urllib.error.HTTPError as exc:  # noqa: PERF203  # a retry loop
                if exc.code < HTTP_SERVER_ERROR or attempt == ATTEMPTS - 1:  # 4xx will not get better by retrying
                    raise
                self.log(f"  HTTP error ({exc}); retrying...")
                time.sleep(2 * (attempt + 1))
            except OSError as exc:
                if attempt == ATTEMPTS - 1:
                    raise
                self.log(f"  HTTP error ({exc}); retrying...")
                time.sleep(2 * (attempt + 1))
        msg = "unreachable"
        raise AssertionError(msg)

    def _fetch_once(self, start: int, length: int) -> bytes:
        resp = self._request(start, start + length - 1)
        if resp.status != HTTP_PARTIAL_CONTENT:
            msg = f"expected 206 Partial Content, got {resp.status}"
            raise OSError(msg)
        data = resp.read()
        if len(data) != length:
            msg = f"short read ({len(data)} of {length} bytes)"
            raise OSError(msg)
        return data

    def _fetch(self, start: int, length: int) -> bytes:
        for attempt in range(ATTEMPTS):
            try:
                data = self._fetch_once(start, length)
            except (OSError, http.client.HTTPException) as exc:  # noqa: PERF203  # a retry loop
                if attempt == ATTEMPTS - 1:
                    raise
                self.log(f"  download error ({exc!r}); retrying...")
                time.sleep(2 * (attempt + 1))
            else:
                self.downloaded += len(data)
                return data
        msg = "unreachable"
        raise AssertionError(msg)

    def read_at(self, offset: int, length: int) -> bytes:
        """Return LENGTH bytes from OFFSET (fewer at the end), small reads through a block cache."""
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
        return bytes(out[skip : skip + length])


class SourceFile(io.RawIOBase):
    """Adapter so :mod:`zipfile` can read the central directory of a Source."""

    def __init__(self, src: Source) -> None:
        """Start reading SRC at its first byte."""
        self.src, self.pos = src, 0

    def readable(self) -> bool:
        """Return True: a Source is read."""
        return True

    def seekable(self) -> bool:
        """Return True: a Source has random access."""
        return True

    def tell(self) -> int:
        """Return the position."""
        return self.pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        """Move the position as file objects do."""
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.src.size}[whence]
        self.pos = base + offset
        return self.pos

    def readinto(self, b: WriteableBuffer) -> int:
        """Read into B from the position; return the number of bytes read."""
        view = memoryview(b)
        data = self.src.read_at(self.pos, len(view))
        view[: len(data)] = data
        self.pos += len(data)
        return len(data)


class SliceSource(Source):
    """A range of another Source, such as a member stored in a zip."""

    def __init__(self, src: Source, offset: int, size: int) -> None:
        """Take SIZE bytes of SRC from OFFSET."""
        self.src, self.offset, self.size = src, offset, size

    def read_at(self, offset: int, length: int) -> bytes:
        """Return LENGTH bytes from OFFSET (fewer at the end)."""
        length = max(0, min(length, self.size - offset))
        return self.src.read_at(self.offset + offset, length)


def open_source(location: str, log: Callable[[str], None] = print) -> Source:
    """Open a local file, or an http(s) URL through range requests."""
    if location.startswith(("http://", "https://")):
        return HttpSource(location, log)
    return FileSource(location)


def payload_from(location: str, log: Callable[[str], None] = print) -> Source:
    """Return a Source for payload.bin inside an OTA zip (or a bare payload)."""
    src = open_source(location, log)
    if src.read_at(0, 4) == PAYLOAD_MAGIC:
        return src
    with zipfile.ZipFile(io.BufferedReader(SourceFile(src), buffer_size=65536)) as zf:
        info = zf.getinfo("payload.bin")
    if info.compress_type != zipfile.ZIP_STORED:
        msg = "payload.bin is compressed inside the zip; extract the zip first"
        raise ValueError(msg)
    local = src.read_at(info.header_offset, 30)
    if local[:4] != b"PK\x03\x04":
        msg = "bad local file header for payload.bin"
        raise ValueError(msg)
    name_len, extra_len = struct.unpack_from("<HH", local, 26)
    data_start = info.header_offset + 30 + name_len + extra_len
    return SliceSource(src, data_start, info.file_size)


# -- protobuf (just enough for update_metadata.proto) -------------------------

WIRE_VARINT, WIRE_FIXED64, WIRE_LENGTH, WIRE_FIXED32 = 0, 1, 2, 5


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
    """Return a protobuf message's fields by number, each a list of its values in order."""
    fields: dict[int, list] = {}
    i, n = 0, len(buf)
    while i < n:
        key, i = _varint(buf, i)
        num, wire = key >> 3, key & 7
        if wire == WIRE_VARINT:
            val, i = _varint(buf, i)
        elif wire == WIRE_FIXED64:
            val, i = buf[i : i + 8], i + 8
        elif wire == WIRE_LENGTH:
            ln, i = _varint(buf, i)
            val, i = buf[i : i + ln], i + ln
        elif wire == WIRE_FIXED32:
            val, i = buf[i : i + 4], i + 4
        else:
            msg = f"unsupported protobuf wire type {wire}"
            raise ValueError(msg)
        fields.setdefault(num, []).append(val)
    return fields


def _int(fields: dict[int, list], num: int, default: int = 0) -> int:
    """Return the last value of a varint field, as protobuf keeps the last of a repeated scalar."""
    vals = fields.get(num)
    return vals[-1] if vals else default


def _bytes(fields: dict[int, list], num: int) -> bytes | None:
    """Return the last value of a length-delimited field."""
    vals = fields.get(num)
    return vals[-1] if vals else None


@dataclass
class Operation:
    """One install operation: what to do with a blob of the payload, and where it goes."""

    type: int
    data_offset: int
    data_length: int
    dst_extents: list[tuple[int, int]]
    data_sha256: bytes | None


@dataclass
class Partition:
    """A partition's new size and hash, and the operations that write it."""

    name: str
    size: int
    sha256: bytes | None
    operations: list[Operation] = field(default_factory=list)


@dataclass
class Payload:
    """A payload's manifest: where its data begins, and its partitions."""

    src: Source
    block_size: int
    data_start: int
    partitions: dict[str, Partition]


PAYLOAD_VERSION = 2
OP_REPLACE, OP_REPLACE_BZ, OP_ZERO, OP_DISCARD, OP_REPLACE_XZ = 0, 1, 6, 7, 8


def read_payload(src: Source) -> Payload:
    """Read a payload's header and manifest."""
    header = src.read_at(0, 24)
    if header[:4] != PAYLOAD_MAGIC:
        msg = "not an update_engine payload (missing CrAU magic)"
        raise ValueError(msg)
    version, manifest_size = struct.unpack_from(">QQ", header, 4)
    if version != PAYLOAD_VERSION:
        msg = f"unsupported payload version {version}"
        raise ValueError(msg)
    (sig_size,) = struct.unpack_from(">I", header, 20)
    manifest = pb_decode(src.read_at(24, manifest_size))
    block_size = _int(manifest, 3, 4096)
    parts: dict[str, Partition] = {}
    for raw in manifest.get(13, []):
        pf = pb_decode(raw)
        name = (_bytes(pf, 1) or b"").decode()
        info = pb_decode(_bytes(pf, 7) or b"")
        part = Partition(name, _int(info, 1), _bytes(info, 2))
        for op_raw in pf.get(8, []):
            of = pb_decode(op_raw)
            extents = []
            for ext_raw in of.get(6, []):
                ef = pb_decode(ext_raw)
                extents.append((_int(ef, 1), _int(ef, 2)))
            part.operations.append(Operation(_int(of, 1), _int(of, 2), _int(of, 3), extents, _bytes(of, 8)))
        parts[name] = part
    return Payload(src, block_size, 24 + manifest_size + sig_size, parts)


def _operation_data(payload: Payload, name: str, idx: int, op: Operation) -> bytes:
    """Return the data an operation writes, checked against its hash and decompressed."""
    if op.type not in (OP_REPLACE, OP_REPLACE_BZ, OP_REPLACE_XZ):
        msg = f"{name}: operation {OP_NAMES.get(op.type, str(op.type))} needs the source image (incremental OTA?)"
        raise ValueError(msg)
    blob = payload.src.read_at(payload.data_start + op.data_offset, op.data_length)
    if op.data_sha256 and hashlib.sha256(blob).digest() != op.data_sha256:
        msg = f"{name}: operation {idx} data hash mismatch"
        raise ValueError(msg)
    if op.type == OP_REPLACE_BZ:
        return bz2.decompress(blob)
    if op.type == OP_REPLACE_XZ:
        return lzma.decompress(blob)
    return blob


def extract_partition(payload: Payload, name: str, log: Callable[[str], None] = print) -> bytes:
    """Return partition NAME's image, written by its operations and checked against its hash."""
    part = payload.partitions.get(name)
    if part is None:
        msg = f"partition {name!r} not in payload (has: {', '.join(sorted(payload.partitions))})"
        raise KeyError(msg)
    bs = payload.block_size
    out = bytearray(part.size)
    total = len(part.operations)
    for idx, op in enumerate(part.operations, 1):
        if op.type in (OP_ZERO, OP_DISCARD):  # the buffer is already zero
            continue
        blob = _operation_data(payload, name, idx, op)
        pos = 0
        for start, count in op.dst_extents:
            chunk = blob[pos : pos + count * bs]
            out[start * bs : start * bs + len(chunk)] = chunk
            pos += count * bs
        if idx % 16 == 0 or idx == total:
            log(f"  {name}: {idx}/{total} operations")
    data = bytes(out)
    if part.sha256:
        if hashlib.sha256(data).digest() != part.sha256:
            msg = f"{name}: partition hash mismatch after extraction"
            raise ValueError(msg)
        log(f"  {name}: sha256 verified against the payload manifest")
    return data
