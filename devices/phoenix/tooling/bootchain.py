# SPDX-License-Identifier: Unlicense
"""Each PICO OS build's boot chain against the verified build's, from PICO's public full OTAs.

    python -m overpressure bootchain [--reference NAME] [NAME...]

The installed system takes everything of PICO OS's it runs from the verified build's public package
(tooling/assets.py) and the headset's own persist. What stays the headset's own PICO OS version is
what runs before the kernel and is never written by the install: the boot chain (XBL and its
configuration, AOP, TrustZone and its libraries, the hypervisor, devcfg, the serial engines'
firmware, the UEFI volume) and the board overlay in dtbo. The ABL is not among them: the install
replaces it with the engineering ABL.

Qualcomm's signed images are ELF files whose hash segment holds the segments' hashes, the signature
and the certificate chain. Two builds run the same code when every other segment is byte-identical:
"re-signed" when only the hash segment differs, "identical" when the files are. dtbo is compared
overlay by overlay. Only these members are read from each OTA, about 12 MB of an archive of 3.7 GB.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import struct
from dataclasses import dataclass
from typing import Callable

from overpressure import kernelinfo, ota, payload

from . import FIRMWARE
from .stock import CATALOG

#: The boot chain's members of a full OTA, in boot order, without the ABL.
BOOT_CHAIN = (
    "xbl.elf",
    "xbl_config.elf",
    "aop.mbn",
    "tz.mbn",
    "hyp.mbn",
    "devcfg.mbn",
    "cmnlib.mbn",
    "cmnlib64.mbn",
    "qupv3fw.elf",
    "imagefv.elf",
)
DTBO = "dtbo.img"
MEMBER_DIR = "firmware-update/"

PT_NULL = 0
ELFCLASS64 = 2
#: Qualcomm's segment type in a program header's flags: bits 24-26, 2 for the hash segment.
QCOM_SEGMENT_TYPE = 0x07000000
QCOM_HASH_SEGMENT = 0x02000000
DT_TABLE_MAGIC = 0xD7B7AB1E

IDENTICAL = "identical"
RESIGNED = "re-signed"
DIFFERS = "differs"
MISSING = "missing"


@dataclass(frozen=True)
class Segment:
    """One segment of an image's code or data: where it loads and what it holds."""

    vaddr: int
    memsz: int
    digest: str


def code_segments(elf: bytes) -> list[Segment]:
    """Return an ELF's segments without the headers' and the hash segment (signature and certificates)."""
    if elf[:4] != b"\x7fELF":
        message = "not an ELF file"
        raise ValueError(message)
    wide = elf[4] == ELFCLASS64
    endian = "<" if elf[5] == 1 else ">"
    if wide:
        (phoff,) = struct.unpack_from(endian + "Q", elf, 0x20)
        phentsize, phnum = struct.unpack_from(endian + "HH", elf, 0x36)
        layout = endian + "IIQQQQQQ"
    else:
        (phoff,) = struct.unpack_from(endian + "I", elf, 0x1C)
        phentsize, phnum = struct.unpack_from(endian + "HH", elf, 0x2A)
        layout = endian + "IIIIIIII"
    segments = []
    for i in range(phnum):
        fields = struct.unpack_from(layout, elf, phoff + i * phentsize)
        if wide:
            p_type, p_flags, p_offset, p_vaddr, _paddr, p_filesz, p_memsz, _align = fields
        else:
            p_type, p_offset, p_vaddr, _paddr, p_filesz, p_memsz, p_flags, _align = fields
        if p_type == PT_NULL or p_flags & QCOM_SEGMENT_TYPE == QCOM_HASH_SEGMENT:
            continue
        data = elf[p_offset : p_offset + p_filesz]
        segments.append(Segment(p_vaddr, p_memsz, hashlib.sha256(data).hexdigest()))
    return segments


def compare_image(reference: bytes, other: bytes) -> str:
    """Return how a signed image compares with the reference's: identical, re-signed or differs."""
    if reference == other:
        return IDENTICAL
    return RESIGNED if code_segments(reference) == code_segments(other) else DIFFERS


def dtbo_overlays(image: bytes) -> dict[str, str]:
    """Return the SHA-256 of each overlay in an Android dtbo image (DT table, big-endian), by its board.

    The board is the overlay root's compatible and qcom,board-id (kona MTP and kona-iot MTP share a
    board-id). The table's own entries are alike, and the order differs from build to build.
    """
    magic, _total, _header, entry_size, count, entries = struct.unpack_from(">IIIIII", image, 0)
    if magic != DT_TABLE_MAGIC:
        message = "not a dtbo image"
        raise ValueError(message)
    overlays = {}
    for i in range(count):
        size, offset = struct.unpack_from(">II", image, entries + i * entry_size)
        overlay = image[offset : offset + size]
        summary = kernelinfo.dtb_summary(overlay)
        board = f"{','.join(summary.get('compatible', []))} board-id {','.join(summary.get('qcom,board-id', []))}"
        if board in overlays:
            message = f"two overlays for {board}"
            raise ValueError(message)
        overlays[board] = hashlib.sha256(overlay).hexdigest()
    return overlays


def compare_dtbo(reference: bytes, other: bytes) -> str:
    """Return how a dtbo compares with the reference's: identical, or how many overlays differ."""
    if reference == other:
        return IDENTICAL
    ours, theirs = dtbo_overlays(reference), dtbo_overlays(other)
    if set(ours) != set(theirs):
        return f"{DIFFERS}: other boards' overlays"
    changed = sum(ours[board] != theirs[board] for board in ours)
    return f"{DIFFERS}: {changed} of {len(ours)} overlays" if changed else IDENTICAL


def compare(reference: dict[str, bytes], other: dict[str, bytes]) -> dict[str, str]:
    """Return each member's comparison: the boot chain's images and dtbo."""
    result = {}
    for name in (*BOOT_CHAIN, DTBO):
        if name not in other or name not in reference:
            result[name] = MISSING
        elif name == DTBO:
            result[name] = compare_dtbo(reference[name], other[name])
        else:
            result[name] = compare_image(reference[name], other[name])
    return result


def same_code(result: dict[str, str]) -> bool:
    """Whether a build runs the reference's code before the kernel: nothing differs or is missing."""
    return all(verdict in (IDENTICAL, RESIGNED) for verdict in result.values())


def read_members(url: str, log: Callable[[str], None] = print) -> dict[str, bytes]:
    """Read the boot chain and dtbo from a full OTA over HTTP ranges (CRC-32 checked)."""
    src = payload.open_source(url, log)
    infos = ota.zip_infos(src)
    members = {}
    for name in (*BOOT_CHAIN, DTBO):
        info = infos.get(MEMBER_DIR + name)
        if info is None:
            continue
        out = io.BytesIO()
        ota.stream_member(src, info, out, log)
        members[name] = out.getvalue()
    return members


def main(argv: list[str] | None = None) -> int:
    """Run the comparison and print a table: one row per build, one column per member."""
    builds = ota.load_catalog(CATALOG)
    ap = argparse.ArgumentParser(prog="python -m overpressure bootchain", description=__doc__.split("\n\n")[0])
    ap.add_argument("builds", nargs="*", metavar="NAME", help="firmware.json entries (default: all)")
    ap.add_argument("--reference", default=FIRMWARE, help=f"the build to compare with (default {FIRMWARE})")
    args = ap.parse_args(argv)
    names = args.builds or [name for name in builds if name != args.reference]
    unknown = [n for n in (args.reference, *names) if n not in builds]
    if unknown:
        ap.error(f"unknown build {', '.join(unknown)}; known: {', '.join(builds)}")

    def quiet(_line: str) -> None:
        pass

    print(f"reading {args.reference}")
    reference = read_members(builds[args.reference]["url"], quiet)
    failed = 0
    for name in names:
        result = compare(reference, read_members(builds[name]["url"], quiet))
        verdict = "same code before the kernel" if same_code(result) else "different code before the kernel"
        failed += not same_code(result)
        print(f"{name}: {verdict}")
        for member, outcome in result.items():
            print(f"  {member:16} {outcome}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
