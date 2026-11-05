# SPDX-License-Identifier: Unlicense
"""Android boot image (boot.img) parser and packer, header versions 0-4.

Layout follows AOSP system/tools/mkbootimg/include/bootimg/bootimg.h. The aim
is to take a *stock* image apart, swap the ramdisk (and optionally extend the
kernel command line) and put it back together with every other field (load
addresses, page size, OS version/patch level, DTB, ...) exactly as the stock
bootloader expects it.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, replace

from .avb import FOOTER, FOOTER_MAGIC

BOOT_MAGIC = b"ANDROID!"
BOOT_NAME_SIZE = 16
BOOT_ARGS_SIZE = 512
BOOT_EXTRA_ARGS_SIZE = 1024
BOOT_ID_SIZE = 32
V3_ARGS_SIZE = BOOT_ARGS_SIZE + BOOT_EXTRA_ARGS_SIZE
V3_PAGE_SIZE = 4096

# Sizes of the header structs (packed).
HDR_SIZE = {0: 1632, 1: 1648, 2: 1660, 3: 1580, 4: 1584}


@dataclass
class BootImage:
    header_version: int
    page_size: int
    kernel: bytes
    ramdisk: bytes
    second: bytes = b""
    recovery_dtbo: bytes = b""
    dtb: bytes = b""
    boot_signature: bytes = b""  # v4 only
    cmdline: bytes = b""  # v0-2: the 512-byte field; v3/4: the 1536-byte field
    extra_cmdline: bytes = b""  # v0-2 only
    name: bytes = b""
    os_version: int = 0
    kernel_addr: int = 0
    ramdisk_addr: int = 0
    second_addr: int = 0
    tags_addr: int = 0
    dtb_addr: int = 0
    id: bytes = b""
    # Information about the source image (not used when packing).
    avb_footer: bool = False

    # -- helpers -----------------------------------------------------------
    @property
    def full_cmdline(self) -> str:
        return (self.cmdline + self.extra_cmdline).decode("utf-8", "replace")

    @property
    def os_version_str(self) -> str:
        return decode_os_version(self.os_version)

    def with_cmdline(self, cmdline: str) -> "BootImage":
        raw = cmdline.encode()
        if self.header_version >= 3:
            if len(raw) >= V3_ARGS_SIZE:
                raise ValueError(f"kernel command line too long ({len(raw)} >= {V3_ARGS_SIZE})")
            return replace(self, cmdline=raw, extra_cmdline=b"")
        if len(raw) >= BOOT_ARGS_SIZE + BOOT_EXTRA_ARGS_SIZE - 1:
            raise ValueError("kernel command line too long for a v0-v2 boot image")
        # Same split as AOSP mkbootimg: first 511 bytes + NUL, remainder in extra.
        return replace(self, cmdline=raw[:BOOT_ARGS_SIZE - 1], extra_cmdline=raw[BOOT_ARGS_SIZE - 1:])

    def compute_id(self) -> bytes:
        """SHA-1 image id exactly as AOSP mkbootimg computes it (v0-v2)."""
        sha = hashlib.sha1()
        parts = [self.kernel, self.ramdisk, self.second]
        if self.header_version >= 1:
            parts.append(self.recovery_dtbo)
        if self.header_version >= 2:
            parts.append(self.dtb)
        for blob in parts:
            sha.update(blob)
            sha.update(struct.pack("<I", len(blob)))
        return sha.digest().ljust(BOOT_ID_SIZE, b"\0")

    # -- packing -------------------------------------------------------------
    def pack(self) -> bytes:
        if self.header_version >= 3:
            return self._pack_v3()
        return self._pack_v0_2()

    def _pack_v0_2(self) -> bytes:
        v = self.header_version
        page = self.page_size
        hdr = bytearray()
        hdr += struct.pack(
            "<8s10I", BOOT_MAGIC,
            len(self.kernel), self.kernel_addr,
            len(self.ramdisk), self.ramdisk_addr,
            len(self.second), self.second_addr,
            self.tags_addr, page, v, self.os_version,
        )
        hdr += _fixed(self.name, BOOT_NAME_SIZE, "board name")
        hdr += _fixed(self.cmdline, BOOT_ARGS_SIZE, "cmdline", nul=True)
        hdr += self.compute_id()
        hdr += _fixed(self.extra_cmdline, BOOT_EXTRA_ARGS_SIZE, "extra cmdline", nul=True)
        if v >= 1:
            # recovery_dtbo_offset is absolute within the image.
            offset = 0
            if self.recovery_dtbo:
                offset = page * (1 + _pages(len(self.kernel), page) + _pages(len(self.ramdisk), page)
                                 + _pages(len(self.second), page))
            hdr += struct.pack("<IQI", len(self.recovery_dtbo), offset, HDR_SIZE[v])
        if v >= 2:
            hdr += struct.pack("<IQ", len(self.dtb), self.dtb_addr)
        assert len(hdr) == HDR_SIZE[v], (len(hdr), v)
        out = bytearray(_pad(bytes(hdr), page))
        for blob in (self.kernel, self.ramdisk, self.second):
            out += _pad(blob, page)
        if v >= 1:
            out += _pad(self.recovery_dtbo, page)
        if v >= 2:
            out += _pad(self.dtb, page)
        return bytes(out)

    def _pack_v3(self) -> bytes:
        v = self.header_version
        hdr = bytearray()
        hdr += struct.pack("<8s4I", BOOT_MAGIC, len(self.kernel), len(self.ramdisk), self.os_version, HDR_SIZE[v])
        hdr += b"\0" * 16  # reserved[4]
        hdr += struct.pack("<I", v)
        hdr += _fixed(self.cmdline, V3_ARGS_SIZE, "cmdline", nul=True)
        if v >= 4:
            hdr += struct.pack("<I", len(self.boot_signature))
        assert len(hdr) == HDR_SIZE[v], (len(hdr), v)
        out = bytearray(_pad(bytes(hdr), V3_PAGE_SIZE))
        out += _pad(self.kernel, V3_PAGE_SIZE)
        out += _pad(self.ramdisk, V3_PAGE_SIZE)
        if v >= 4:
            out += _pad(self.boot_signature, V3_PAGE_SIZE)
        return bytes(out)


# -- parsing -------------------------------------------------------------------

def parse(data: bytes) -> BootImage:
    if data[:8] != BOOT_MAGIC:
        raise ValueError("not an Android boot image (missing ANDROID! magic)")
    # header_version sits at offset 40 in every header version.
    version = struct.unpack_from("<I", data, 40)[0]
    if version > 4:
        # v0 images with a garbage "unused" field exist; treat large values as v0.
        version = 0
    img = _parse_v3(data, version) if version >= 3 else _parse_v0_2(data, version)
    img.avb_footer = data[-FOOTER.size:-FOOTER.size + 4] == FOOTER_MAGIC
    return img


def _parse_v0_2(data: bytes, version: int) -> BootImage:
    (_magic, kernel_size, kernel_addr, ramdisk_size, ramdisk_addr, second_size, second_addr,
     tags_addr, page_size, _hv, os_version) = struct.unpack_from("<8s10I", data, 0)
    if page_size not in (2048, 4096, 8192, 16384):
        raise ValueError(f"implausible page size {page_size}")
    name = data[48:64]
    cmdline = _cstr(data[64:576])
    img_id = data[576:608]
    extra_cmdline = _cstr(data[608:1632])
    recovery_dtbo_size = dtb_size = dtb_addr = 0
    if version >= 1:
        recovery_dtbo_size, _rec_off, _hsize = struct.unpack_from("<IQI", data, 1632)
    if version >= 2:
        dtb_size, dtb_addr = struct.unpack_from("<IQ", data, 1648)

    pos = page_size  # header occupies exactly one page

    def take(size: int) -> bytes:
        nonlocal pos
        blob = data[pos:pos + size]
        if len(blob) != size:
            raise ValueError("boot image is truncated")
        pos += _pages(size, page_size) * page_size
        return blob

    kernel = take(kernel_size)
    ramdisk = take(ramdisk_size)
    second = take(second_size)
    recovery_dtbo = take(recovery_dtbo_size) if version >= 1 else b""
    dtb = take(dtb_size) if version >= 2 else b""
    return BootImage(
        header_version=version, page_size=page_size, kernel=kernel, ramdisk=ramdisk,
        second=second, recovery_dtbo=recovery_dtbo, dtb=dtb, cmdline=cmdline,
        extra_cmdline=extra_cmdline, name=name.rstrip(b"\0"), os_version=os_version,
        kernel_addr=kernel_addr, ramdisk_addr=ramdisk_addr, second_addr=second_addr,
        tags_addr=tags_addr, dtb_addr=dtb_addr, id=img_id,
    )


def _parse_v3(data: bytes, version: int) -> BootImage:
    _magic, kernel_size, ramdisk_size, os_version, _hsize = struct.unpack_from("<8s4I", data, 0)
    cmdline = _cstr(data[44:44 + V3_ARGS_SIZE])
    sig_size = struct.unpack_from("<I", data, 1580)[0] if version >= 4 else 0
    pos = V3_PAGE_SIZE

    def take(size: int) -> bytes:
        nonlocal pos
        blob = data[pos:pos + size]
        if len(blob) != size:
            raise ValueError("boot image is truncated")
        pos += _pages(size, V3_PAGE_SIZE) * V3_PAGE_SIZE
        return blob

    kernel = take(kernel_size)
    ramdisk = take(ramdisk_size)
    sig = take(sig_size) if version >= 4 else b""
    return BootImage(
        header_version=version, page_size=V3_PAGE_SIZE, kernel=kernel, ramdisk=ramdisk,
        boot_signature=sig, cmdline=cmdline, os_version=os_version,
    )


# -- misc ------------------------------------------------------------------------

def decode_os_version(value: int) -> str:
    if not value:
        return "unset"
    ver, patch = value >> 11, value & 0x7FF
    a, b, c = (ver >> 14) & 0x7F, (ver >> 7) & 0x7F, ver & 0x7F
    year, month = (patch >> 4) + 2000, patch & 0xF
    return f"Android {a}.{b}.{c}, security patch {year}-{month:02d}"


def _cstr(raw: bytes) -> bytes:
    return raw.split(b"\0", 1)[0]


def _fixed(raw: bytes, size: int, what: str, nul: bool = False) -> bytes:
    limit = size - 1 if nul else size
    if len(raw) > limit:
        raise ValueError(f"{what} too long ({len(raw)} > {limit})")
    return raw.ljust(size, b"\0")


def _pages(size: int, page: int) -> int:
    return (size + page - 1) // page


def _pad(blob: bytes, page: int) -> bytes:
    return blob + b"\0" * (-len(blob) % page)
