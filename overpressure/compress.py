# SPDX-License-Identifier: Unlicense
"""Detect and decompress the formats found in Android kernels and ramdisks.

gzip/xz/lzma/bzip2 come from the standard library; LZ4 (legacy and frame
format, both used by Android build systems) is implemented here because the
standard library has no LZ4 support.
"""

from __future__ import annotations

import bz2
import lzma
import struct
import zlib

LZ4_LEGACY_MAGIC = 0x184C2102
LZ4_LENGTH_MORE = 15  # a token's 4-bit length of 15 continues in the bytes after it
LZ4_BYTE_MORE = 255  # a length byte of 255 continues in the next one
LZ4_MIN_MATCH = 4
# The arm64 Image header: "ARM\x64" at 0x38.
ARM64_MAGIC_START, ARM64_MAGIC_END = 0x38, 0x3C

SIGNATURES = [
    (b"\x1f\x8b", "gzip"),
    (b"\x02\x21\x4c\x18", "lz4-legacy"),
    (b"\x04\x22\x4d\x18", "lz4-frame"),
    (b"\xfd7zXZ\x00", "xz"),
    (b"BZh", "bzip2"),
    (b"\x28\xb5\x2f\xfd", "zstd"),
    (b"\x5d\x00\x00", "lzma"),
    (b"070701", "cpio"),
    (b"070702", "cpio"),
]


def detect(data: bytes) -> str:
    """Return the format of DATA by its magic: a name of SIGNATURES, "arm64-image" or "unknown"."""
    for magic, name in SIGNATURES:
        if data.startswith(magic):
            return name
    if len(data) > ARM64_MAGIC_END and data[ARM64_MAGIC_START:ARM64_MAGIC_END] == b"ARM\x64":
        return "arm64-image"
    return "unknown"


def decompress(data: bytes) -> tuple[bytes, bytes, str]:
    """Decompress the first stream in ``data``.

    Returns (payload, trailing_bytes, format). ``trailing_bytes`` holds whatever
    follows the compressed stream, e.g. DTBs appended to an Image.gz-dtb.
    """
    fmt = detect(data)
    if fmt == "gzip":
        d = zlib.decompressobj(wbits=31)
        out = d.decompress(data)
        return out, d.unused_data, fmt
    if fmt in ("xz", "lzma"):
        d = lzma.LZMADecompressor(format=lzma.FORMAT_XZ if fmt == "xz" else lzma.FORMAT_ALONE)
        out = d.decompress(data)
        return out, d.unused_data, fmt
    if fmt == "bzip2":
        d = bz2.BZ2Decompressor()
        out = d.decompress(data)
        return out, d.unused_data, fmt
    if fmt == "lz4-legacy":
        return (*lz4_legacy_decompress(data), fmt)
    if fmt == "lz4-frame":
        return (*lz4_frame_decompress(data), fmt)
    if fmt == "zstd":
        msg = "zstd-compressed data is not supported by this tool"
        raise ValueError(msg)
    return data, b"", fmt


def _lz4_length(src: bytes, i: int, length: int) -> tuple[int, int]:
    """Return a token's length with the bytes that continue it, and the position after them."""
    if length == LZ4_LENGTH_MORE:
        while True:
            b = src[i]
            i += 1
            length += b
            if b != LZ4_BYTE_MORE:
                break
    return length, i


def lz4_block_decompress(src: bytes | memoryview, out: bytearray) -> None:
    """Append the decompressed contents of one raw LZ4 block to ``out``."""
    src = bytes(src)
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        lit, i = _lz4_length(src, i + 1, token >> 4)
        out += src[i : i + lit]
        i += lit
        if i >= n:  # the last sequence carries literals only
            break
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        if offset == 0:
            msg = "corrupt LZ4 block (zero match offset)"
            raise ValueError(msg)
        mlen, i = _lz4_length(src, i, token & LZ4_LENGTH_MORE)
        mlen += LZ4_MIN_MATCH
        start = len(out) - offset
        if start < 0:
            msg = "corrupt LZ4 block (offset before start)"
            raise ValueError(msg)
        if offset >= mlen:
            out += out[start : start + mlen]
        else:  # overlapping copy: repeat the last `offset` bytes
            pattern = bytes(out[start:])
            reps = mlen // offset + 1
            out += (pattern * reps)[:mlen]


def lz4_legacy_decompress(data: bytes) -> tuple[bytes, bytes]:
    """Return the data of a legacy LZ4 stream (the kernel's lz4 -l) and the bytes after it."""
    out = bytearray()
    pos = 0
    n = len(data)
    while pos + 4 <= n:
        (magic,) = struct.unpack_from("<I", data, pos)
        if magic == LZ4_LEGACY_MAGIC:
            pos += 4
            continue
        (size,) = struct.unpack_from("<I", data, pos)
        # Legacy streams have no end marker; anything implausible ends the stream.
        if size == 0 or size > 0x800000 + 0x10000 or pos + 4 + size > n:
            break
        lz4_block_decompress(memoryview(data)[pos + 4 : pos + 4 + size], out)
        pos += 4 + size
    return bytes(out), data[pos:]


def lz4_frame_decompress(data: bytes) -> tuple[bytes, bytes]:
    """Return the data of an LZ4 frame and the bytes after it."""
    out = bytearray()
    pos = 4
    flg = data[pos]
    pos += 2  # FLG, BD
    block_checksum = bool(flg & 0x10)
    content_size = bool(flg & 0x08)
    content_checksum = bool(flg & 0x04)
    dict_id = bool(flg & 0x01)
    if content_size:
        pos += 8
    if dict_id:
        pos += 4
    pos += 1  # header checksum
    while True:
        (bsize,) = struct.unpack_from("<I", data, pos)
        pos += 4
        if bsize == 0:
            break
        raw = bool(bsize & 0x80000000)
        bsize &= 0x7FFFFFFF
        block = memoryview(data)[pos : pos + bsize]
        if raw:
            out += block
        else:
            lz4_block_decompress(block, out)
        pos += bsize + (4 if block_checksum else 0)
    if content_checksum:
        pos += 4
    return bytes(out), data[pos:]


def gzip_compress(data: bytes, level: int = 9) -> bytes:
    """Deterministic gzip (mtime 0, no file name)."""
    co = zlib.compressobj(level, zlib.DEFLATED, 31, 9)
    blob = co.compress(data) + co.flush()
    # zlib writes mtime=0 but a host-specific OS byte; pin it to 3 (Unix) so the
    # output is identical whether the image is built on Windows or Linux.
    return blob[:9] + b"\x03" + blob[10:]
