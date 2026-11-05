# SPDX-License-Identifier: Unlicense
"""A headset's stage 1: an Alpine Linux ramdisk in its stock Android boot image.

The stock kernel, device trees, load addresses and OS version stay byte for byte; only the
ramdisk is replaced (the Alpine minirootfs, packages resolved and verified against Alpine's
signed indexes, the stock ramdisk's kernel modules, the headset's overlay) and the kernel
command line extended. A headset gives a Spec; its own tooling adds its files to the root and
writes the outputs. SOURCE_DATE_EPOCH makes the whole image byte-reproducible.
"""
from __future__ import annotations

import hashlib
import os
import posixpath
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence, Tuple

from . import alpine, bootimg, compress, cpio, kernelinfo

#: Kernel options every stage 1 relies on (checked against the stock kernel's IKCONFIG).
REQUIRED_CONFIG = {
    "BLK_DEV_INITRD": "initramfs support",
    "RD_GZIP": "gzip-compressed ramdisk",
    "USB_CONFIGFS": "USB gadget configfs",
    "CONFIGFS_FS": "configfs",
    "UNIX98_PTYS": "ptys for telnet/ssh",
}


@dataclass(frozen=True)
class Spec:
    """What a headset's stage 1 is made of."""

    overlay: Path                                   #: the headset's files for the root (its initramfs/)
    packages: Sequence[str] = ()                    #: Alpine packages, resolved with their dependencies
    files: Mapping[str, Path] = field(default_factory=dict)  #: root path -> prebuilt executable
    required_config: Mapping[str, str] = field(default_factory=lambda: dict(REQUIRED_CONFIG))
    alpine_branch: str = "v3.24"
    alpine_version: str = "3.24.2"
    alpine_repos: Tuple[str, ...] = ("main", "community")
    arch: str = "aarch64"


def analyse(label: str, data: bytes, interesting_config=()):
    """Return (report dict, BootImage, KernelInfo, stock ramdisk cpio entries)."""
    img = bootimg.parse(data)
    kinfo = kernelinfo.analyse(img.kernel)
    rd_raw, _, rd_fmt = compress.decompress(img.ramdisk)
    rd_entries = cpio.read_cpio(rd_raw) if rd_fmt in ("gzip", "lz4-legacy", "lz4-frame", "xz", "lzma",
                                                        "bzip2", "cpio") else []
    dtbs = kernelinfo.split_dtbs(img.dtb) or kinfo.appended_dtbs
    report = {
        "label": label,
        "image_sha256": hashlib.sha256(data).hexdigest(),
        "image_size": len(data),
        "avb_footer": img.avb_footer,
        "header_version": img.header_version,
        "page_size": img.page_size,
        "os_version": img.os_version_str,
        "board_name": img.name.decode("ascii", "replace"),
        "cmdline": img.full_cmdline,
        "addresses": {
            "kernel": hex(img.kernel_addr), "ramdisk": hex(img.ramdisk_addr), "second": hex(img.second_addr),
            "tags": hex(img.tags_addr), "dtb": hex(img.dtb_addr),
        },
        "id_matches_mkbootimg": img.header_version < 3 and img.id == img.compute_id(),
        "kernel": {
            "size": len(img.kernel), "format": kinfo.compression, "image_size": kinfo.image_size,
            "arm64_magic": kinfo.arm64_magic, "version": kinfo.version,
            "sha256": hashlib.sha256(img.kernel).hexdigest(),
            "has_ikconfig": bool(kinfo.config), "skip_initramfs_string": kinfo.has_skip_initramfs,
            "config": {k: kinfo.opt(k) for k in interesting_config},
        },
        "ramdisk": {
            "size": len(img.ramdisk), "format": rd_fmt, "entries": len(rd_entries),
            "has_init": any(p == "init" for p, _ in rd_entries),
            "first_stage": any(p.startswith("first_stage_ramdisk") for p, _ in rd_entries),
            "modules": sorted(p for p, n in rd_entries if p.startswith("lib/modules/") and n.is_file),
        },
        "dtb": {
            "size": len(img.dtb), "sha256": hashlib.sha256(img.dtb).hexdigest(), "count": len(dtbs),
            "trees": [kernelinfo.dtb_summary(d) for d in dtbs],
        },
        "second_size": len(img.second), "recovery_dtbo_size": len(img.recovery_dtbo),
    }
    return report, img, kinfo, rd_entries


def print_report(rep: dict, log=print) -> None:
    k, r, d = rep["kernel"], rep["ramdisk"], rep["dtb"]
    log(f"   image      : {rep['image_size']} bytes, header v{rep['header_version']}, page {rep['page_size']}, "
        f"AVB footer: {'yes' if rep['avb_footer'] else 'no'}")
    log(f"   os version : {rep['os_version']}")
    log(f"   kernel     : {k['size']} bytes ({k['format']}), {k['version'] or 'version string not found'}")
    log(f"   ikconfig   : {'embedded' if k['has_ikconfig'] else 'not embedded'}")
    log(f"   ramdisk    : {r['size']} bytes ({r['format']}), {r['entries']} entries, "
        f"first-stage init: {'yes' if r['first_stage'] or r['has_init'] else 'no'}, modules: {len(r['modules'])}")
    log(f"   dtb        : {d['size']} bytes, {d['count']} device trees")
    for t in d["trees"]:
        log(f"                {', '.join(t.get('model', ['?']))}  msm-id={t.get('qcom,msm-id')}  "
            f"board-id={t.get('qcom,board-id')}")
    log(f"   cmdline    : {rep['cmdline']}")
    if k["has_ikconfig"]:
        cfg = ", ".join(f"{name}={val}" for name, val in k["config"].items() if val is not None)
        log(f"   config     : {cfg}")


def check_requirements(kinfo: kernelinfo.KernelInfo, required: Mapping[str, str]) -> list[str]:
    if not kinfo.config:
        return ["kernel has no embedded config; cannot verify requirements (continuing)"]
    problems = []
    for names, what in required.items():
        if not any(kinfo.enabled(n) for n in names.split("|")):
            problems.append(f"CONFIG_{names} not enabled ({what})")
    if not any(kinfo.enabled(n) for n in ("USB_CONFIGFS_NCM", "USB_CONFIGFS_RNDIS", "USB_CONFIGFS_ECM")):
        problems.append("no USB network gadget function (NCM/RNDIS/ECM) in the stock kernel")
    return problems


def build_rootfs(spec: Spec, cache: str, stock_entries, build_info: dict, minimal: bool = False,
                 log=print) -> cpio.Tree:
    """The stage-1 root: the Alpine minirootfs, @p spec's packages (none if @p minimal), the stock
    ramdisk's kernel modules, the headset's overlay and files, device nodes and the base
    directories, root without a password (the link is USB-only). The headset adds its own files to
    the tree it gets back."""
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    tree = cpio.Tree(mtime=epoch)
    log(f">> Alpine Linux {spec.alpine_version} ({spec.arch}) minirootfs")
    alpine.unpack_tar(tree, alpine.minirootfs(cache, spec.alpine_branch, spec.alpine_version, spec.arch, log))

    wanted = [] if minimal else list(spec.packages)
    if wanted:
        index = alpine.load_index(cache, spec.alpine_branch, list(spec.alpine_repos), spec.arch, log)
        pkgs = alpine.resolve(index, wanted, alpine.installed_db(tree))
        log(f">> {len(pkgs)} packages: {' '.join(p.name for p in pkgs)}")
        for pkg in pkgs:
            alpine.unpack_tar(tree, alpine.fetch_package(pkg, cache, log))
        build_info["packages"] = sorted(f"{p.name}-{p.version}" for p in pkgs)
        # busybox-extras' applet links are normally created by its post-install script.
        if any(p.name == "busybox-extras" for p in pkgs):
            for applet in ("usr/sbin/telnetd", "usr/sbin/udhcpd", "usr/sbin/httpd", "usr/bin/telnet",
                           "usr/bin/dumpleases", "usr/bin/tftp", "usr/sbin/tftpd", "usr/sbin/ftpd",
                           "usr/bin/ftpget", "usr/bin/ftpput", "usr/sbin/inetd", "usr/bin/tcpsvd"):
                if not tree.exists(applet):
                    tree.add_symlink(applet, "/bin/busybox-extras")

    # Kernel modules from the stock first-stage ramdisk (Android loads these early).
    mods = [(p, n) for p, n in stock_entries if p.startswith("lib/modules/") and (n.is_file or n.is_symlink)]
    for path, node in mods:
        tree.nodes[path] = node
        tree.mkdir(posixpath.dirname(path))
    if mods:
        log(f">> kept {len(mods)} files from the stock ramdisk's lib/modules")

    log(f">> overlay ({spec.overlay})")
    add_overlay(tree, spec.overlay)
    for target, source in spec.files.items():
        with open(source, "rb") as f:
            tree.add_file(target, f.read(), 0o755)

    # Static device nodes: a stock Android kernel may have no devtmpfs, and the kernel opens
    # /dev/console before /init runs.
    for name, major, minor, mode in (("console", 5, 1, 0o600), ("null", 1, 3, 0o666), ("zero", 1, 5, 0o666),
                                     ("kmsg", 1, 11, 0o644), ("tty", 5, 0, 0o666), ("ptmx", 5, 2, 0o666),
                                     ("random", 1, 8, 0o666), ("urandom", 1, 9, 0o666)):
        tree.add_device(f"dev/{name}", "c", major, minor, mode)
    for d in ("proc", "sys", "run", "tmp", "mnt", "root", "srv/www", "lib/firmware", "var/log"):
        tree.mkdir(d)
    tree.get("tmp").mode = stat.S_IFDIR | 0o1777
    tree.get("root").mode = stat.S_IFDIR | 0o700

    # Passwordless root (USB-only link-local network).
    shadow = tree.get("etc/shadow")
    if shadow is not None:
        lines = shadow.data.decode().splitlines()
        lines = [re.sub(r"^root:[^:]*:", "root::", ln) for ln in lines]
        shadow.data = ("\n".join(lines) + "\n").encode()
    return tree


def kernel_cmdline(stock: bootimg.BootImage, cmdline_append) -> list:
    """The stock kernel arguments with @p cmdline_append added (each once)."""
    cmdline = stock.full_cmdline.split()
    for word in cmdline_append:
        if word not in cmdline:
            cmdline.append(word)
    return cmdline


def assemble(stock: bootimg.BootImage, ramdisk: bytes, cmdline_append) -> bytes:
    """The stock boot image with the new ramdisk and the kernel arguments appended, verified: the
    kernel, device trees, load addresses and OS version stay the stock image's byte for byte."""
    new = stock.with_cmdline(" ".join(kernel_cmdline(stock, cmdline_append)))
    new.ramdisk = ramdisk
    new.boot_signature = b""
    image = new.pack()

    # Verify the result before anyone flashes it.
    check = bootimg.parse(image)
    assert check.kernel == stock.kernel and check.dtb == stock.dtb and check.second == stock.second
    assert (check.header_version, check.page_size, check.os_version) == \
           (stock.header_version, stock.page_size, stock.os_version)
    assert (check.kernel_addr, check.ramdisk_addr, check.tags_addr, check.dtb_addr) == \
           (stock.kernel_addr, stock.ramdisk_addr, stock.tags_addr, stock.dtb_addr)
    assert check.id == check.compute_id()
    unpacked = dict(cpio.read_cpio(compress.decompress(check.ramdisk)[0]))
    init = unpacked.get("init")
    assert init is not None and init.is_file and init.mode & 0o111, "/init missing or not executable"
    return image


def add_overlay(tree: cpio.Tree, root) -> None:
    """A directory's files into the tree: CRLF fixed in text files, mode 0755 for ELF files and
    scripts, 0644 otherwise (modes of a Windows checkout mean nothing)."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        if rel_dir != ".":
            tree.mkdir(rel_dir)
        for fn in sorted(filenames):
            if fn in (".gitkeep", ".gitattributes") or fn.endswith("~"):
                continue
            with open(os.path.join(dirpath, fn), "rb") as f:
                data = f.read()
            rel = fn if rel_dir == "." else f"{rel_dir}/{fn}"
            is_elf = data[:4] == b"\x7fELF"
            if not is_elf and b"\0" not in data:
                data = data.replace(b"\r\n", b"\n")  # survive CRLF checkouts on Windows
            executable = is_elf or data.startswith(b"#!")
            tree.add_file(rel, data, 0o755 if executable else 0o644)
