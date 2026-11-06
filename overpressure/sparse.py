# SPDX-License-Identifier: Unlicense
"""Android sparse images (format 1.0), as fastboot sends them.

The engineering ABL crashed while receiving the 100 MiB stock recovery image as one raw
download; as a sparse image, runs of blocks that repeat one 32-bit value go out as FILL chunks
(4 bytes each) and are still written, so the partition ends up identical to the raw image.
"""

from __future__ import annotations

import struct

MAGIC = 0xED26FF3A
RAW, FILL = 0xCAC1, 0xCAC2
HEADER = struct.Struct("<IHHHHIIII")
CHUNK = struct.Struct("<HHII")


def _fill_value(block: bytes) -> bytes | None:
    """Return the 32-bit value a block repeats, or None."""
    word = block[:4]
    return word if word * (len(block) // 4) == block else None


def encode(data: bytes, block_size: int = 4096) -> bytes:
    """Return the sparse form of @p data (a whole number of blocks)."""
    if len(data) % block_size:
        msg = "the image is not a whole number of blocks"
        raise ValueError(msg)
    blocks = len(data) // block_size
    chunks = []
    i = 0
    while i < blocks:
        block = data[i * block_size : (i + 1) * block_size]
        fill = _fill_value(block)
        j = i + 1
        if fill is not None:
            while j < blocks and data[j * block_size : (j + 1) * block_size] == block:
                j += 1
            chunks.append(CHUNK.pack(FILL, 0, j - i, CHUNK.size + 4) + fill)
        else:
            while j < blocks and _fill_value(data[j * block_size : (j + 1) * block_size]) is None:
                j += 1
            raw = data[i * block_size : j * block_size]
            chunks.append(CHUNK.pack(RAW, 0, j - i, CHUNK.size + len(raw)) + raw)
        i = j
    header = HEADER.pack(MAGIC, 1, 0, HEADER.size, CHUNK.size, block_size, blocks, len(chunks), 0)
    return header + b"".join(chunks)
