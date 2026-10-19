"""Inspect a stock Android kernel: version, embedded config, appended DTBs."""
from __future__ import annotations

import re
import struct
import zlib
from dataclasses import dataclass, field

from . import compress

FDT_MAGIC = b"\xd0\x0d\xfe\xed"


@dataclass
class KernelInfo:
    compression: str
    image_size: int  # decompressed size
    version: str = ""
    config: dict[str, str] = field(default_factory=dict)
    config_text: str = ""
    appended_dtbs: list[bytes] = field(default_factory=list)
    has_skip_initramfs: bool = False
    arm64_magic: bool = False

    def opt(self, name: str) -> str | None:
        """Value of CONFIG_<name> ("y", "m", a string) or None if unset/unknown."""
        return self.config.get(name if name.startswith("CONFIG_") else "CONFIG_" + name)

    def enabled(self, name: str) -> bool | None:
        """True/False if the config is known, None if no config was embedded."""
        if not self.config:
            return None
        return self.opt(name) in ("y", "m")


def analyse(kernel_blob: bytes) -> KernelInfo:
    image, trailing, fmt = compress.decompress(kernel_blob)
    info = KernelInfo(compression=fmt, image_size=len(image))
    info.arm64_magic = len(image) > 0x3C and image[0x38:0x3C] == b"ARM\x64"

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
    start = image.find(b"IKCFG_ST")
    if start < 0:
        return ""
    end = image.find(b"IKCFG_ED", start)
    blob = image[start + 8:end if end > 0 else None]
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
        if size < 40 or pos + size > len(blob):
            break
        dtbs.append(blob[pos:pos + size])
        pos = blob.find(FDT_MAGIC, pos + size)
    return dtbs


def _tail_after_image(image: bytes) -> bytes:
    # arm64 Image header: image_size (le64) at offset 16 covers the whole kernel.
    if len(image) < 24:
        return b""
    (size,) = struct.unpack_from("<Q", image, 16)
    if 0 < size < len(image):
        return image[size:]
    return b""


def dtb_summary(dtb: bytes) -> dict:
    """Return model / compatible / qcom,msm-id / qcom,board-id of a DTB root node."""
    try:
        props = _fdt_root_props(dtb)
    except Exception:  # malformed DTBs are reported, not fatal
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
    (magic, _total, off_struct, off_strings, _off_rsv, _ver, _lcv, _boot_cpu, _size_strings,
     _size_struct) = struct.unpack_from(">10I", dtb, 0)
    if magic != 0xD00DFEED:
        raise ValueError("not a DTB")
    pos = off_struct
    depth = 0
    props: dict[str, bytes] = {}
    while True:
        (token,) = struct.unpack_from(">I", dtb, pos)
        pos += 4
        if token == 1:  # BEGIN_NODE
            end = dtb.index(b"\0", pos)
            pos = (end + 4) & ~3
            depth += 1
        elif token == 2:  # END_NODE
            depth -= 1
            if depth == 0:
                break
        elif token == 3:  # PROP
            length, nameoff = struct.unpack_from(">II", dtb, pos)
            pos += 8
            value = dtb[pos:pos + length]
            pos = (pos + length + 3) & ~3
            if depth == 1:
                name_end = dtb.index(b"\0", off_strings + nameoff)
                props[dtb[off_strings + nameoff:name_end].decode()] = value
        elif token == 4:  # NOP
            continue
        elif token == 9:  # END
            break
        else:
            raise ValueError(f"bad FDT token {token}")
    return props
