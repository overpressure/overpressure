# SPDX-License-Identifier: Unlicense
"""Android Verified Boot footers of partitions that carry their own vbmeta.

The stock recovery is a chained AVB partition: its signed vbmeta follows the image, and a
footer in the partition's last 64 bytes points to it. The ABL checks that chain on every boot,
not only on recovery boots; an image that overwrites the vbmeta leaves every boot in fastboot.
An unlocked ABL tolerates a hash mismatch but not missing metadata, so another image goes in
front of the stock vbmeta, unchanged, with a footer that points to it again.
"""
from __future__ import annotations

import struct

FOOTER = struct.Struct(">4sIIQQQ28x")  # AvbFooter: magic, version, original size, vbmeta offset, size
FOOTER_MAGIC = b"AVBf"
VBMETA_MAGIC = b"AVB0"


def footer(partition: bytes) -> tuple:
    """(original image size, vbmeta offset, vbmeta size) of a partition image with a footer."""
    magic, _major, _minor, original, offset, size = FOOTER.unpack(partition[-FOOTER.size:])
    if magic != FOOTER_MAGIC or partition[offset:offset + 4] != VBMETA_MAGIC:
        raise ValueError("no AVB footer and vbmeta")
    return original, offset, size


def with_stock_vbmeta(stock_partition: bytes, image: bytes, block: int = 4096) -> bytes:
    """A whole partition image: @p image, the stock partition's vbmeta after it (block-aligned)
    and a footer that points to it, in the stock footer's version."""
    _original, offset, size = footer(stock_partition)
    vbmeta = stock_partition[offset:offset + size]
    new_offset = -(-len(image) // block) * block
    total = len(stock_partition)
    if new_offset + size > total - FOOTER.size:
        raise ValueError("the image leaves no room for the vbmeta")
    out = bytearray(total)
    out[:len(image)] = image
    out[new_offset:new_offset + size] = vbmeta
    out[-FOOTER.size:] = stock_partition[-FOOTER.size:]
    struct.pack_into(">QQQ", out, len(out) - FOOTER.size + 12, len(image), new_offset, size)
    return bytes(out)
