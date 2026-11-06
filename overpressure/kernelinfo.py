# SPDX-License-Identifier: Unlicense
"""Inspect a stock Android kernel: version, embedded config, appended DTBs."""

from __future__ import annotations

import re
import struct
import zlib
from dataclasses import dataclass, field

from . import compress

FDT_MAGIC = b"\xd0\x0d\xfe\xed"
FDT_HEADER_SIZE = 40
FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9
# The arm64 Image header: the image size (le64) at 16.
ARM64_IMAGE_SIZE_START, ARM64_IMAGE_SIZE_END = 16, 24


@dataclass
class KernelInfo:
    """What a kernel image tells about itself."""

    compression: str
    image_size: int  # decompressed size
    version: str = ""
    config: dict[str, str] = field(default_factory=dict)
    config_text: str = ""
    appended_dtbs: list[bytes] = field(default_factory=list)
    has_skip_initramfs: bool = False
    arm64_magic: bool = False

    def opt(self, name: str) -> str | None:
        """Return the value of CONFIG_<name> ("y", "m", a string) or None if unset/unknown."""
        return self.config.get(name if name.startswith("CONFIG_") else "CONFIG_" + name)

    def enabled(self, name: str) -> bool | None:
        """Tell whether an option is on: True/False if the config is known, None if no config was embedded."""
        if not self.config:
            return None
        return self.opt(name) in ("y", "m")


def analyse(kernel_blob: bytes) -> KernelInfo:
    """Return what a (possibly compressed) kernel image tells about itself."""
    image, trailing, fmt = compress.decompress(kernel_blob)
    info = KernelInfo(compression=fmt, image_size=len(image))
    info.arm64_magic = (
        len(image) > compress.ARM64_MAGIC_END
        and image[compress.ARM64_MAGIC_START : compress.ARM64_MAGIC_END] == b"ARM\x64"
    )

    m = re.search(rb"Linux version \d+\.\d+[^\n\x00]*", image)
    if m:
        info.version = m.group(0).decode("utf-8", "replace").strip()

    info.config_text = extract_ikconfig(image)
    for line in info.config_text.splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, _, value = line.partition("=")
            info.config[key] = value.strip().strip('"')

    info.has_skip_initramfs = b"skip_initramfs\x00" in image
    # DTBs may follow the compressed kernel (Image.gz-dtb) or the raw image.
    info.appended_dtbs = split_dtbs(trailing if fmt != "arm64-image" else _tail_after_image(image))
    return info


def extract_ikconfig(image: bytes) -> str:
    """Return the kernel's embedded config (CONFIG_IKCONFIG), or "" without one."""
    start = image.find(b"IKCFG_ST")
    if start < 0:
        return ""
    end = image.find(b"IKCFG_ED", start)
    blob = image[start + 8 : end if end > 0 else None]
    try:
        d = zlib.decompressobj(wbits=31)
        return d.decompress(blob).decode("utf-8", "replace")
    except zlib.error:
        return ""


def split_dtbs(blob: bytes) -> list[bytes]:
    """Split concatenated flattened device trees."""
    dtbs = []
    pos = blob.find(FDT_MAGIC)
    while 0 <= pos < len(blob) - 8:
        (size,) = struct.unpack_from(">I", blob, pos + 4)
        if size < FDT_HEADER_SIZE or pos + size > len(blob):
            break
        dtbs.append(blob[pos : pos + size])
        pos = blob.find(FDT_MAGIC, pos + size)
    return dtbs


def _tail_after_image(image: bytes) -> bytes:
    # arm64 Image header: image_size (le64) at offset 16 covers the whole kernel.
    if len(image) < ARM64_IMAGE_SIZE_END:
        return b""
    (size,) = struct.unpack_from("<Q", image, ARM64_IMAGE_SIZE_START)
    if 0 < size < len(image):
        return image[size:]
    return b""


def dtb_summary(dtb: bytes) -> dict:
    """Return model / compatible / qcom,msm-id / qcom,board-id of a DTB root node."""
    try:
        props = _fdt_root_props(dtb)
    except (ValueError, struct.error):  # malformed DTBs are reported, not fatal
        return {}
    out = {}
    for key in ("model", "compatible"):
        if key in props:
            out[key] = [s.decode("utf-8", "replace") for s in props[key].split(b"\0") if s]
    for key in ("qcom,msm-id", "qcom,board-id", "qcom,pmic-id"):
        if key in props:
            raw = props[key]
            out[key] = [hex(x) for x in struct.unpack(f">{len(raw) // 4}I", raw)]
    return out


def _fdt_root_props(dtb: bytes) -> dict[str, bytes]:
    (magic, _total, off_struct, off_strings, _off_rsv, _ver, _lcv, _boot_cpu, _size_strings, _size_struct) = (
        struct.unpack_from(">10I", dtb, 0)
    )
    if magic != int.from_bytes(FDT_MAGIC, "big"):
        msg = "not a DTB"
        raise ValueError(msg)
    pos = off_struct
    depth = 0
    props: dict[str, bytes] = {}
    while True:
        (token,) = struct.unpack_from(">I", dtb, pos)
        pos += 4
        if token == FDT_BEGIN_NODE:
            end = dtb.index(b"\0", pos)
            pos = (end + 4) & ~3
            depth += 1
        elif token == FDT_END_NODE:
            depth -= 1
            if depth == 0:
                break
        elif token == FDT_PROP:
            length, nameoff = struct.unpack_from(">II", dtb, pos)
            pos += 8
            value = dtb[pos : pos + length]
            pos = (pos + length + 3) & ~3
            if depth == 1:
                name_end = dtb.index(b"\0", off_strings + nameoff)
                props[dtb[off_strings + nameoff : name_end].decode()] = value
        elif token == FDT_NOP:
            continue
        elif token == FDT_END:
            break
        else:
            msg = f"bad FDT token {token}"
            raise ValueError(msg)
    return props
