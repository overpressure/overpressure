# SPDX-License-Identifier: Unlicense
"""Build the PICO 4 Linux proof-of-concept boot image.

    python -m overpressure inspect --firmware 5.13.7-global-seko  # analyse a stock boot.img
    python -m overpressure build   --firmware 5.13.7-global-seko  # -> out/pico4-stage1-5.13.7-global-seko.img
    python -m overpressure build   --stock-boot my-boot.img        # from your own dump/OTA

overpressure's stage 1 (overpressure/stage1.py) with the PICO 4's spec: its packages, its
overlay (initramfs/), the prebuilt pico4-reboot and pico4-splash, its kernel arguments, and its
own files in the root (hostname, build record). The stock kernel, device tree, load addresses
and OS version/patch level stay byte for byte. Pure Python 3.9+, no dependencies.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

from overpressure import REPO, compress, cpio, ota, stage1

from . import FIRMWARE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # devices/phoenix

CACHE = os.path.join(str(REPO), "cache")
OVERLAY = os.path.join(ROOT, "initramfs")
PREBUILT = os.path.join(ROOT, "prebuilt", "aarch64")
CATALOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "firmware.json")


# Userspace on top of the Alpine minirootfs (resolved with dependencies).
PACKAGES = [
    "busybox-extras",  # telnetd, udhcpd, httpd
    "dropbear", "dropbear-scp", "openssh-sftp-server",  # ssh/scp/sftp (Windows has an ssh client)
    "mtools",  # builds the read-only FAT "log drive" exposed over USB
    "kmod", "dtc", "evtest", "i2c-tools", "libgpiod", "tinyalsa", "strace", "htop",
    "drm_info", "libdrm-tests", "usbutils", "pciutils", "lsblk", "device-mapper",
    # pico4-userspace: mount the installed system and switch to it, keep the SSH host key
    "e2fsprogs", "dropbear-convert",
]

# Appended to the stock command line:
#  printk.devkmsg=on              don't rate-limit our /dev/kmsg log lines (they
#                                 also end up in pstore)
#  rng_core.default_quality=1000  let the Qualcomm hardware RNG credit entropy, so
#                                 dropbear's first host-key generation cannot block
CMDLINE_APPEND = ["printk.devkmsg=on", "rng_core.default_quality=1000"]

INTERESTING_CONFIG = [
    "DEVTMPFS", "USB_CONFIGFS_NCM", "USB_CONFIGFS_RNDIS", "USB_CONFIGFS_ECM", "USB_CONFIGFS_ACM",
    "USB_CONFIGFS_SERIAL", "USB_CONFIGFS_MASS_STORAGE", "USB_CONFIGFS_F_FS", "USB_CONFIGFS_F_GSI",
    "USB_CONFIGFS_UEVENT", "BLK_DEV_LOOP", "VFAT_FS", "UEVENT_HELPER", "FW_LOADER_USER_HELPER_FALLBACK",
    "PSTORE", "PSTORE_CONSOLE", "PSTORE_PMSG", "PSTORE_RAM", "MODULES", "IKCONFIG_PROC", "DRM",
    "DRM_MSM", "FB", "FB_VIRTUAL", "VT", "LEDS_CLASS", "LEDS_QTI_TRI_LED", "LEDS_QPNP_FLASH_V2",
    "QCOM_WATCHDOG_V2", "PANIC_TIMEOUT", "CMDLINE", "SECURITY_SELINUX", "ANDROID_PARANOID_NETWORK",
    "HW_RANDOM", "NET_NS", "CGROUPS", "NAMESPACES",
]


SPEC = stage1.Spec(
    overlay=Path(OVERLAY),
    packages=PACKAGES,
    files={f"usr/sbin/{name}": Path(PREBUILT) / name for name in ("pico4-reboot", "pico4-splash")},
)


def log(msg: str) -> None:
    print(msg, flush=True)


# -- stock image ------------------------------------------------------------------

def resolve_stock(args) -> tuple[str, str]:
    """Return (label, location) for the stock boot image."""
    if args.stock_boot:
        return os.path.basename(args.stock_boot.rstrip("/")), args.stock_boot
    builds = ota.load_catalog(CATALOG)
    if args.firmware not in builds:
        raise SystemExit(f"unknown --firmware {args.firmware!r}; known: {', '.join(builds)}")
    return args.firmware, builds[args.firmware]["url"]


def load_stock(args) -> tuple[str, bytes]:
    label, location = resolve_stock(args)
    log(f">> stock boot image: {label}")
    data = ota.fetch_boot_image(location, CACHE, log)
    return label, data


# -- rootfs -----------------------------------------------------------------------

def build_rootfs(args, stock_entries, build_info: dict) -> cpio.Tree:
    """overpressure's stage-1 root with the PICO 4's own files."""
    tree = stage1.build_rootfs(SPEC, CACHE, stock_entries, build_info, log=log)
    tree.add_symlink("srv/www/report", "/run/pico4/report")  # browsable via httpd
    tree.add_file("etc/motd", b"")  # the login banner comes from pico4-info
    tree.add_file("etc/hostname", b"pico4\n")
    tree.add_file("etc/hosts", b"127.0.0.1\tlocalhost pico4\n::1\tlocalhost\n")
    tree.add_file("etc/pico4/build-info", json.dumps(build_info, indent=2).encode() + b"\n")
    return tree


# -- commands ---------------------------------------------------------------------

def cmd_inspect(args) -> int:
    label, data = load_stock(args)
    rep, _img, kinfo, _entries = stage1.analyse(label, data, INTERESTING_CONFIG)
    stage1.print_report(rep, log)
    out_dir = os.path.join(args.out, "inspect", label)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "analysis.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2)
    if kinfo.config_text:
        with open(os.path.join(out_dir, "kernel.config"), "w", encoding="utf-8", newline="\n") as f:
            f.write(kinfo.config_text)
    log(f">> wrote {out_dir}")
    for p in stage1.check_requirements(kinfo, SPEC.required_config):
        log(f"!! {p}")
    return 0


def cmd_build(args) -> int:
    label, data = load_stock(args)
    rep, stock, kinfo, entries = stage1.analyse(label, data, INTERESTING_CONFIG)
    stage1.print_report(rep, log)
    problems = stage1.check_requirements(kinfo, SPEC.required_config)
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
        "alpine": SPEC.alpine_version,
    }
    tree = build_rootfs(args, entries, build_info)
    raw = tree.to_cpio()
    ramdisk = compress.gzip_compress(raw)
    log(f">> ramdisk: {len(tree.nodes)} entries, {len(raw) / 1e6:.1f} MB cpio, {len(ramdisk) / 1e6:.1f} MB gzip")

    cmdline = stage1.kernel_cmdline(stock, list(CMDLINE_APPEND) + args.cmdline_append)
    image = stage1.assemble(stock, ramdisk, list(CMDLINE_APPEND) + args.cmdline_append)
    if rep["image_size"] and len(image) > rep["image_size"]:
        log(f"!! image ({len(image)} bytes) is larger than the stock partition image ({rep['image_size']} bytes): "
            "too big to flash")

    os.makedirs(args.out, exist_ok=True)
    name = f"pico4-stage1-{label}"
    paths = {
        "image": os.path.join(args.out, f"{name}.img"),
        "ramdisk": os.path.join(args.out, f"{name}-initramfs.cpio.gz"),
        # The unmodified stock image: what to flash back after a manual "fastboot flash boot".
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
        tree.to_tar(f)
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
    log("   on the headset: python -m overpressure dev image, then dev flash (devices/phoenix/docs/hardware-testing.md)")
    return 0


def cmd_match(args) -> int:
    """Map a headset's build fingerprint + ro.oem.state to a firmware.json entry."""
    builds = ota.load_catalog(CATALOG)
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
    ap = argparse.ArgumentParser(prog="python -m overpressure", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("match", help="find the firmware.json entry for a headset")
    m.add_argument("--fingerprint", required=True, help="adb shell getprop ro.build.fingerprint")
    m.add_argument("--oem-state", required=True, help="adb shell getprop ro.oem.state")
    for name in ("inspect", "build"):
        p = sub.add_parser(name)
        src = p.add_mutually_exclusive_group()
        src.add_argument("--firmware", default=FIRMWARE,
                         help="stock build from firmware.json (default: %(default)s)")
        src.add_argument("--stock-boot", help="stock boot.img, OTA zip, or URL of either")
        p.add_argument("--out", default=os.path.join(str(REPO), "out"), help="output directory")
        if name == "build":
            p.add_argument("--cmdline-append", nargs="*", default=[], metavar="ARG",
                           help="extra kernel arguments, e.g. pico4.autoreboot=0")
            p.add_argument("--force", action="store_true", help="build even if kernel checks fail")
    args = ap.parse_args(argv)
    return {"inspect": cmd_inspect, "build": cmd_build, "match": cmd_match}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
