# SPDX-License-Identifier: Unlicense
"""A boot image with another kernel: a stock header's image with Valve's kernel, its device tree and command line.

A headset on Valve's kernel boots it from an image whose header comes from one of its own (the stock boot or
recovery image, or one built from it): kernel and device tree replaced, the command line rewritten (the stock
words Valve's kernel does not take dropped, the headset's words merged by their key, the bootloader's limit kept),
archives appended to the ramdisk, the signature cleared, and the packed image parsed back to check it. A headset
adds what is its own: its words, its stage 1's archives, AVB for a chained partition (avb.with_stock_vbmeta).
Since interface 1.7.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from . import bootimg, compress, cpio

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

#: --debug: every driver probe logged (the RAM console then ends with the probe a stalled boot waits for), every
#: task and CPU on a panic, msm's debug output, and a hard lockup a panic.
DEBUG = (
    "initcall_debug",
    "dd.dyndbg=+p",
    "ignore_loglevel",
    "panic_sys_info=tasks,all_bt",
    "drm.debug=0x1e",
    "nmi_watchdog=panic",
)


def trace_words(function: str) -> tuple[str, ...]:
    """Return the words that record the function graph below one kernel function and print it on a panic."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", function):
        msg = f"not a kernel function name: {function!r}"
        raise ValueError(msg)
    return ("ftrace=function_graph", f"ftrace_graph_filter={function}", "ftrace_dump_on_oops", "trace_buf_size=64K")


def keyed_words(*groups: Iterable[str]) -> tuple[str, ...]:
    """Merge command line words; a later word replaces an earlier one with its key."""
    keyed: dict[str, str] = {}
    for word in (w for group in groups for w in group):
        keyed.pop(word.split("=")[0], None)
        keyed[word.split("=")[0]] = word
    return tuple(keyed.values())


def command_line(stock: str, extra: Iterable[str], drop: tuple[str, ...], limit: int) -> str:
    """Return @p stock's words without those @p extra replaces by key or that start with @p drop, then @p extra.

    A line longer than @p limit bytes (what the bootloader passes) raises ValueError.
    """
    extra = tuple(extra)
    keys = {e.split("=")[0] for e in extra}
    words = [w for w in stock.split() if w.split("=")[0] not in keys and not w.startswith(drop)]
    line = " ".join(words + list(extra))
    if len(line) > limit:
        msg = f"the command line has {len(line)} bytes; the bootloader passes only {limit}"
        raise ValueError(msg)
    return line


def files_archive(files: dict[str, Path]) -> cpio.Tree:
    """Archive each file at its path (the tree makes the parent directories)."""
    tree = cpio.Tree()
    for path, source in files.items():
        tree.add_file(path, source.read_bytes())
    return tree


def appended(ramdisk: bytes, *trees: cpio.Tree) -> bytes:
    """Append each archive to the ramdisk; the kernel unpacks them in order."""
    # compress.gzip_compress: the same bytes on every host and Python (a pinned OS byte).
    return ramdisk + b"".join(compress.gzip_compress(tree.to_cpio()) for tree in trees)


def with_kernel(  # noqa: PLR0913  # the image and what replaces its parts
    base: bootimg.BootImage,
    kernel: bytes,
    dtb: bytes,
    cmdline: str,
    ramdisk: bytes,
    *,
    recovery_dtbo: bool = True,
) -> bytes:
    """Return @p base packed with @p kernel, @p dtb, @p cmdline and @p ramdisk, its signature cleared, checked.

    Without @p recovery_dtbo the image drops its recovery_dtbo (a boot partition's: the bootloader applies the dtbo
    partition's overlay).
    """
    image = base.with_cmdline(cmdline)
    image.kernel, image.dtb = kernel, dtb
    if not recovery_dtbo:
        image.recovery_dtbo = b""
    image.ramdisk = ramdisk
    image.boot_signature = b""
    data = image.pack()
    check = bootimg.parse(data)
    if (check.kernel, check.dtb, check.ramdisk, check.recovery_dtbo) != (kernel, dtb, ramdisk, image.recovery_dtbo):
        msg = "the image failed validation"
        raise RuntimeError(msg)
    return data
