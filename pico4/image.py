"""Build the PICO 4 Linux proof-of-concept boot image.

    python -m pico4 inspect --firmware 5.13.8-global-sek   # analyse a stock boot.img
    python -m pico4 build   --firmware 5.13.8-global-sek   # -> out/pico4-poc-5.13.8-global-sek.img
    python -m pico4 build   --stock-boot my-boot.img        # from your own dump/OTA

The output keeps the stock kernel, device tree, load addresses and OS
version/patch level of the stock image byte for byte. Only the ramdisk is
replaced (Alpine Linux aarch64 userspace + the POC init in initramfs/) and
the kernel command line is extended. Pure Python 3.9+, no dependencies.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import posixpath
import re
import stat
import sys

from . import alpine, bootimg, compress, cpio, firmware, kernelinfo, userspace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CACHE = os.path.join(ROOT, "cache")
OVERLAY = os.path.join(ROOT, "initramfs")
PREBUILT = os.path.join(ROOT, "prebuilt", "aarch64")
CATALOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "firmware.json")

ALPINE_BRANCH = "v3.24"
ALPINE_VERSION = "3.24.2"
ALPINE_REPOS = ["main", "community"]
ARCH = "aarch64"

# Userspace on top of the Alpine minirootfs (resolved with dependencies).
PACKAGES = [
    "busybox-extras",  # telnetd, udhcpd, httpd
    "dropbear", "dropbear-scp", "openssh-sftp-server",  # ssh/scp/sftp (Windows has an ssh client)
    "mtools",  # builds the read-only FAT "log drive" exposed over USB
    "kmod", "dtc", "evtest", "i2c-tools", "libgpiod", "tinyalsa", "strace", "htop",
    "drm_info", "libdrm-tests", "usbutils", "pciutils", "lsblk", "device-mapper",
    # pico4-userspace: unpack the step-4 userspace into zram, keep the SSH host key
    "tar", "zstd", "e2fsprogs", "dropbear-convert",
]

# Appended to the stock command line:
#  printk.devkmsg=on              don't rate-limit our /dev/kmsg log lines (they
#                                 also end up in pstore)
#  rng_core.default_quality=1000  let the Qualcomm hardware RNG credit entropy, so
#                                 dropbear's first host-key generation cannot block
CMDLINE_APPEND = ["printk.devkmsg=on", "rng_core.default_quality=1000"]

# Kernel options the POC relies on (checked against the stock kernel's IKCONFIG).
REQUIRED_CONFIG = {
    "BLK_DEV_INITRD": "initramfs support",
    "RD_GZIP": "gzip-compressed ramdisk",
    "USB_CONFIGFS": "USB gadget configfs",
    "CONFIGFS_FS": "configfs",
    "UNIX98_PTYS": "ptys for telnet/ssh",
}
INTERESTING_CONFIG = [
    "DEVTMPFS", "USB_CONFIGFS_NCM", "USB_CONFIGFS_RNDIS", "USB_CONFIGFS_ECM", "USB_CONFIGFS_ACM",
    "USB_CONFIGFS_SERIAL", "USB_CONFIGFS_MASS_STORAGE", "USB_CONFIGFS_F_FS", "USB_CONFIGFS_F_GSI",
    "USB_CONFIGFS_UEVENT", "BLK_DEV_LOOP", "VFAT_FS", "UEVENT_HELPER", "FW_LOADER_USER_HELPER_FALLBACK",
    "PSTORE", "PSTORE_CONSOLE", "PSTORE_PMSG", "PSTORE_RAM", "MODULES", "IKCONFIG_PROC", "DRM",
    "DRM_MSM", "FB", "FB_VIRTUAL", "VT", "LEDS_CLASS", "LEDS_QTI_TRI_LED", "LEDS_QPNP_FLASH_V2",
    "QCOM_WATCHDOG_V2", "PANIC_TIMEOUT", "CMDLINE", "SECURITY_SELINUX", "ANDROID_PARANOID_NETWORK",
    "HW_RANDOM", "NET_NS", "CGROUPS", "NAMESPACES",
]


def log(msg: str) -> None:
    print(msg, flush=True)


# -- stock image ------------------------------------------------------------------

def resolve_stock(args) -> tuple[str, str]:
    """Return (label, location) for the stock boot image."""
    if args.stock_boot:
        return os.path.basename(args.stock_boot.rstrip("/")), args.stock_boot
    builds = firmware.load_catalog(CATALOG)
    if args.firmware not in builds:
        raise SystemExit(f"unknown --firmware {args.firmware!r}; known: {', '.join(builds)}")
    return args.firmware, builds[args.firmware]["url"]


def load_stock(args) -> tuple[str, bytes]:
    label, location = resolve_stock(args)
    log(f">> stock boot image: {label}")
    data = firmware.fetch_boot_image(location, CACHE, log)
    return label, data


def analyse(label: str, data: bytes):
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
            "config": {k: kinfo.opt(k) for k in INTERESTING_CONFIG},
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


def print_report(rep: dict) -> None:
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


def check_requirements(kinfo: kernelinfo.KernelInfo) -> list[str]:
    if not kinfo.config:
        return ["kernel has no embedded config; cannot verify requirements (continuing)"]
    problems = []
    for names, what in REQUIRED_CONFIG.items():
        if not any(kinfo.enabled(n) for n in names.split("|")):
            problems.append(f"CONFIG_{names} not enabled ({what})")
    if not any(kinfo.enabled(n) for n in ("USB_CONFIGFS_NCM", "USB_CONFIGFS_RNDIS", "USB_CONFIGFS_ECM")):
        problems.append("no USB network gadget function (NCM/RNDIS/ECM) in the stock kernel")
    return problems


# -- rootfs -----------------------------------------------------------------------

def build_rootfs(args, stock_entries, build_info: dict) -> cpio.Tree:
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    tree = cpio.Tree(mtime=epoch)
    log(f">> Alpine Linux {ALPINE_VERSION} ({ARCH}) minirootfs")
    alpine.unpack_tar(tree, alpine.minirootfs(CACHE, ALPINE_BRANCH, ALPINE_VERSION, ARCH, None, log))

    wanted = [] if args.minimal else PACKAGES + args.extra_packages
    if wanted:
        index = alpine.load_index(CACHE, ALPINE_BRANCH, ALPINE_REPOS, ARCH, log)
        pkgs = alpine.resolve(index, wanted, alpine.installed_db(tree))
        log(f">> {len(pkgs)} packages: {' '.join(p.name for p in pkgs)}")
        for pkg in pkgs:
            alpine.unpack_tar(tree, alpine.fetch_package(pkg, CACHE, log))
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

    log(">> POC overlay (initramfs/)")
    add_overlay(tree, OVERLAY)
    for name in ("pico4-reboot", "pico4-splash"):
        with open(os.path.join(PREBUILT, name), "rb") as f:
            tree.add_file(f"usr/sbin/{name}", f.read(), 0o755)

    # Optional local experiments (for example a separately built graphics bundle).
    # Keep these out of the default, verified shell image.
    for extra in getattr(args, "extra_overlay", []):
        if not os.path.isdir(extra):
            raise ValueError(f"Extra overlay directory does not exist: {extra}")
        log(f">> extra overlay: {extra}")
        add_overlay(tree, extra)

    # Static device nodes: the stock kernel has no devtmpfs, and the kernel
    # opens /dev/console before /init runs.
    for name, major, minor, mode in (("console", 5, 1, 0o600), ("null", 1, 3, 0o666), ("zero", 1, 5, 0o666),
                                     ("kmsg", 1, 11, 0o644), ("tty", 5, 0, 0o666), ("ptmx", 5, 2, 0o666),
                                     ("random", 1, 8, 0o666), ("urandom", 1, 9, 0o666)):
        tree.add_device(f"dev/{name}", "c", major, minor, mode)
    for d in ("proc", "sys", "run", "tmp", "mnt", "root", "srv/www", "lib/firmware", "var/log"):
        tree.mkdir(d)
    tree.get("tmp").mode = stat.S_IFDIR | 0o1777
    tree.get("root").mode = stat.S_IFDIR | 0o700

    # Passwordless root (USB-only link-local network; see README).
    shadow = tree.get("etc/shadow")
    if shadow is not None:
        lines = shadow.data.decode().splitlines()
        lines = [re.sub(r"^root:[^:]*:", "root::", ln) for ln in lines]
        shadow.data = ("\n".join(lines) + "\n").encode()
    tree.add_symlink("srv/www/report", "/run/pico4-poc/report")  # browsable via httpd
    tree.add_file("etc/motd", b"")  # the login banner comes from pico4-info
    tree.add_file("etc/hostname", b"pico4-poc\n")
    tree.add_file("etc/hosts", b"127.0.0.1\tlocalhost pico4-poc\n::1\tlocalhost\n")
    tree.add_file("etc/pico4-poc/build-info", json.dumps(build_info, indent=2).encode() + b"\n")
    return tree


def add_overlay(tree: cpio.Tree, root: str) -> None:
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


# -- commands ---------------------------------------------------------------------

def cmd_inspect(args) -> int:
    label, data = load_stock(args)
    rep, _img, kinfo, _entries = analyse(label, data)
    print_report(rep)
    out_dir = os.path.join(args.out, "inspect", label)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "analysis.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2)
    if kinfo.config_text:
        with open(os.path.join(out_dir, "kernel.config"), "w", encoding="utf-8", newline="\n") as f:
            f.write(kinfo.config_text)
        # Roadmap step 4: what the SteamOS userspace gets from this kernel.
        compat = userspace.kernel_compat(kinfo.config, kinfo.version)
        print(userspace.format_compat(compat))
        with open(os.path.join(out_dir, "userspace-compat.json"), "w", encoding="utf-8") as f:
            json.dump(compat, f, indent=2)
    log(f">> wrote {out_dir}")
    for p in check_requirements(kinfo):
        log(f"!! {p}")
    return 0


def cmd_build(args) -> int:
    label, data = load_stock(args)
    rep, stock, kinfo, entries = analyse(label, data)
    print_report(rep)
    problems = check_requirements(kinfo)
    for p in problems:
        log(f"!! {p}")
    if problems and kinfo.config and not args.force:
        raise SystemExit("stock kernel lacks required features (use --force to build anyway)")
    if kinfo.config and not kinfo.enabled("RD_GZIP"):
        raise SystemExit("stock kernel cannot unpack a gzip ramdisk")

    # SOURCE_DATE_EPOCH makes the whole image byte-reproducible.
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    when = (datetime.datetime.fromtimestamp(int(epoch), datetime.timezone.utc) if epoch
            else datetime.datetime.now(datetime.timezone.utc))
    build_info = {
        "built": when.strftime("%Y-%m-%d %H:%M UTC"),
        "stock": label,
        "stock_sha256": rep["image_sha256"],
        "kernel": rep["kernel"]["version"],
        "alpine": ALPINE_VERSION,
    }
    tree = build_rootfs(args, entries, build_info)
    raw = tree.to_cpio()
    ramdisk = compress.gzip_compress(raw)
    log(f">> ramdisk: {len(tree.nodes)} entries, {len(raw) / 1e6:.1f} MB cpio, {len(ramdisk) / 1e6:.1f} MB gzip")

    cmdline = stock.full_cmdline.split()
    for word in CMDLINE_APPEND + args.cmdline_append:
        if word not in cmdline:
            cmdline.append(word)
    new = stock.with_cmdline(" ".join(cmdline))
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
    if rep["image_size"] and len(image) > rep["image_size"]:
        log(f"!! image ({len(image)} bytes) is larger than the stock partition image ({rep['image_size']} bytes): "
            "fine for 'fastboot boot', too big to flash")

    os.makedirs(args.out, exist_ok=True)
    name = args.name or f"pico4-poc-{label}"
    paths = {
        "image": os.path.join(args.out, f"{name}.img"),
        "ramdisk": os.path.join(args.out, f"{name}-initramfs.cpio.gz"),
        # The unmodified stock image: what to flash back if you ever had to
        # use "fastboot flash boot" instead of "fastboot boot".
        "stock": os.path.join(args.out, f"stock-boot-{label}.img"),
    }
    with open(paths["image"], "wb") as f:
        f.write(image)
    with open(paths["ramdisk"], "wb") as f:
        f.write(ramdisk)
    with open(paths["stock"], "wb") as f:
        f.write(data)
    # The rootfs as a tar (no device nodes) for inspection and for the
    # preflight check, which runs these binaries on the headset under PICO OS.
    with open(os.path.join(args.out, f"{name}-rootfs.tar"), "wb") as f:
        tree.to_tar(f, skip_devices=True)
    summary = {
        "image": os.path.basename(paths["image"]),
        "image_sha256": hashlib.sha256(image).hexdigest(),
        "image_size": len(image),
        "ramdisk_size": len(ramdisk),
        "cmdline": " ".join(cmdline),
        **build_info,
        "stock_analysis": rep,
    }
    with open(os.path.join(args.out, f"{name}.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    log(f">> wrote {paths['image']} ({len(image) / 1e6:.1f} MB)")
    log(f"   sha256 {summary['image_sha256']}")
    log("   boot it (RAM only, nothing is flashed):  fastboot boot " + os.path.relpath(paths["image"]))
    return 0


def cmd_match(args) -> int:
    """Map a headset's build fingerprint + ro.oem.state to a firmware.json entry."""
    builds = firmware.load_catalog(CATALOG)
    for key, b in builds.items():
        if b.get("fingerprint") == args.fingerprint:
            print(key)
            return 0
    # e.g. Pico/Phoenix_ovs/PICOA8110:10/5.13.8/smartcm.1788408867:user/dev-keys
    parts = args.fingerprint.split("/")
    if len(parts) < 4:
        print(f"cannot parse fingerprint {args.fingerprint!r}", file=sys.stderr)
        return 1
    region = "global" if parts[1].endswith("_ovs") else "cn"
    flavour = "seko" if args.oem_state.lower() == "true" else "sek"
    key = f"{parts[3]}-{region}-{flavour}"
    if key in builds:
        print(key)
        print("(same version, region and OEM state; build number not confirmed)", file=sys.stderr)
        return 0
    print(f"no firmware.json entry for {key}; use --stock-boot with your own boot.img or OTA URL",
          file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m pico4", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("match", help="find the firmware.json entry for a headset")
    m.add_argument("--fingerprint", required=True, help="adb shell getprop ro.build.fingerprint")
    m.add_argument("--oem-state", required=True, help="adb shell getprop ro.oem.state")
    for name in ("inspect", "build"):
        p = sub.add_parser(name)
        src = p.add_mutually_exclusive_group()
        src.add_argument("--firmware", default="5.13.8-global-sek",
                         help="stock build from firmware.json (default: %(default)s)")
        src.add_argument("--stock-boot", help="stock boot.img, OTA zip, or URL of either")
        p.add_argument("--out", default=os.path.join(ROOT, "out"), help="output directory")
        if name == "build":
            p.add_argument("--name", help="output base name (default: pico4-poc-<firmware>)")
            p.add_argument("--extra-packages", nargs="*", default=[], metavar="PKG",
                           help="additional Alpine packages to include")
            p.add_argument("--extra-overlay", action="append", default=[], metavar="DIR",
                           help="merge a local rootfs overlay after the default files (repeatable)")
            p.add_argument("--minimal", action="store_true", help="only the minirootfs + overlay")
            p.add_argument("--cmdline-append", nargs="*", default=[], metavar="ARG",
                           help="extra kernel arguments, e.g. pico4poc.autoreboot=0")
            p.add_argument("--force", action="store_true", help="build even if kernel checks fail")
    args = ap.parse_args(argv)
    return {"inspect": cmd_inspect, "build": cmd_build, "match": cmd_match}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
