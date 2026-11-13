# SPDX-License-Identifier: Unlicense
"""Build the PICO 4's stage-1 image: the maintenance environment, and the base of its test images.

    python -m overpressure inspect --firmware 5.13.7-global-seko  # analyse the backup's stock boot image
    python -m overpressure build   --firmware 5.13.7-global-seko  # -> out/pico4-stage1-5.13.7-global-seko.img
    python -m overpressure build   --stock-boot my-boot.img        # from another boot.img, an OTA zip or its URL

The stock boot image is the headset's own: the boot partition in its EDL backup (PICO4_BACKUP in .env,
or --backup DIR), checked against the backup's manifest; --firmware names that build (firmware.json's
verified list). Nothing of PICO OS's is downloaded unless --stock-boot names a URL.

overpressure's stage 1 (overpressure/stage1.py) with the PICO 4's spec: its packages, its
overlay (initramfs/), the prebuilt pico4-reboot, its kernel arguments, and its
own files in the root (hostname, build record). The stock kernel, device tree, load addresses
and OS version/patch level stay byte for byte. Pure Python 3.9+, no dependencies.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from overpressure import REPO, backup, compress, cpio, ota, stage1

from . import FIRMWARE, localconfig

if TYPE_CHECKING:
    from collections.abc import Iterable

ROOT = Path(__file__).absolute().parent.parent  # devices/phoenix

CACHE = str(REPO / "cache")
OVERLAY = ROOT / "initramfs"
PREBUILT = ROOT / "prebuilt" / "aarch64"
CATALOG = Path(__file__).absolute().parent / "firmware.json"
#: A build fingerprint's fields: brand/product/device:release/VERSION/build:type/tags.
FINGERPRINT_VERSION = 3


# Userspace on top of the Alpine minirootfs (resolved with dependencies).
PACKAGES = [
    "busybox-extras",  # telnetd, udhcpd, httpd
    "dropbear",
    "dropbear-scp",
    "openssh-sftp-server",  # ssh/scp/sftp (Windows has an ssh client)
    "mtools",  # builds the read-only FAT "log drive" exposed over USB
    "zstd",  # pico4-install write --zstd: the system image arrives compressed
    "openssl",  # pico4-install: SHA-256 with the CPU's instructions (BusyBox's is 42 MB/s)
    "kmod",
    "dtc",
    "evtest",
    "i2c-tools",
    "libgpiod",
    "tinyalsa",
    "strace",
    "htop",
    "drm_info",
    "libdrm-tests",
    "usbutils",
    "pciutils",
    "lsblk",
    "device-mapper",
    "e2fsprogs",  # pico4-install format-data: the installed system's data partition
    "curl",  # pico4-install build: the headset builds its system, downloading through the PC (SOCKS)
    "tar",  # pico4-install build: GNU tar unpacks the build host (Holo's rootfs) with numeric owners
]

# Appended to the stock command line:
#  printk.devkmsg=on              don't rate-limit our /dev/kmsg log lines (they
#                                 also end up in pstore)
#  rng_core.default_quality=1000  let the Qualcomm hardware RNG credit entropy, so
#                                 dropbear's first host-key generation cannot block
#  console=none                   no serial console: the device tree names the UART (stdout-path),
#                                 and at its 115200 baud the kernel's log took 5.9 s to replay
#                                 there when it registered, and every message after it waited for
#                                 it (/init came at 14 s); a console= of no device overrides it.
#                                 dmesg and pstore's RAM console keep every message.
CMDLINE_APPEND = ["printk.devkmsg=on", "rng_core.default_quality=1000", "console=none"]

INTERESTING_CONFIG = [
    "DEVTMPFS",
    "USB_CONFIGFS_NCM",
    "USB_CONFIGFS_RNDIS",
    "USB_CONFIGFS_ECM",
    "USB_CONFIGFS_ACM",
    "USB_CONFIGFS_SERIAL",
    "USB_CONFIGFS_MASS_STORAGE",
    "USB_CONFIGFS_F_FS",
    "USB_CONFIGFS_F_GSI",
    "USB_CONFIGFS_UEVENT",
    "BLK_DEV_LOOP",
    "VFAT_FS",
    "UEVENT_HELPER",
    "FW_LOADER_USER_HELPER_FALLBACK",
    "PSTORE",
    "PSTORE_CONSOLE",
    "PSTORE_PMSG",
    "PSTORE_RAM",
    "MODULES",
    "IKCONFIG_PROC",
    "DRM",
    "DRM_MSM",
    "FB",
    "FB_VIRTUAL",
    "VT",
    "LEDS_CLASS",
    "LEDS_QTI_TRI_LED",
    "LEDS_QPNP_FLASH_V2",
    "QCOM_WATCHDOG_V2",
    "PANIC_TIMEOUT",
    "CMDLINE",
    "SECURITY_SELINUX",
    "ANDROID_PARANOID_NETWORK",
    "HW_RANDOM",
    "NET_NS",
    "CGROUPS",
    "NAMESPACES",
]


SPEC = stage1.Spec(
    overlay=OVERLAY,
    packages=PACKAGES,
    files={"usr/sbin/pico4-reboot": PREBUILT / "pico4-reboot"},
)


def log(msg: str) -> None:
    """Print a progress line at once, also into a pipe."""
    print(msg, flush=True)


# -- stock image ------------------------------------------------------------------


def load_stock(args: argparse.Namespace) -> tuple[str, bytes]:
    """Return the stock boot image's label and bytes: the backup's boot partition, or --stock-boot's."""
    if args.stock_boot:
        label = Path(args.stock_boot.rstrip("/")).name
        log(f">> stock boot image: {label}")
        return label, ota.fetch_boot_image(args.stock_boot, CACHE, log)
    builds = ota.load_catalog(CATALOG)
    if args.firmware not in builds:
        msg = f"unknown --firmware {args.firmware!r}; known: {', '.join(builds)}"
        raise SystemExit(msg)
    folder = args.backup or localconfig.load().path("backup")
    if not (folder / "manifest.json").is_file():
        msg = (
            f"no backup of the headset at {folder}: the maintenance system is made from it. Import it (the "
            "window's Import backup..., or python -m overpressure backup import FILE), or name it in .env "
            "(PICO4_BACKUP)"
        )
        raise SystemExit(msg)
    try:
        path, data = backup.verified_partition(folder, "boot")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    log(f">> stock boot image: {args.firmware}, the headset's own ({path})")
    return args.firmware, data


# -- rootfs -----------------------------------------------------------------------


def build_rootfs(stock_entries: Iterable[tuple[str, cpio.Node]], build_info: dict) -> cpio.Tree:
    """Build overpressure's stage-1 root with the PICO 4's own files."""
    tree = stage1.build_rootfs(SPEC, CACHE, stock_entries, build_info, log=log)
    tree.add_symlink("srv/www/report", "/run/pico4/report")  # browsable via httpd
    tree.add_file("etc/motd", b"")  # the login banner comes from pico4-info
    tree.add_file("etc/hostname", b"pico4\n")
    tree.add_file("etc/hosts", b"127.0.0.1\tlocalhost pico4\n::1\tlocalhost\n")
    tree.add_file("etc/pico4/build-info", json.dumps(build_info, indent=2).encode() + b"\n")
    return tree


# -- commands ---------------------------------------------------------------------


def cmd_inspect(args: argparse.Namespace) -> int:
    """Analyse a stock boot image: its report, its kernel configuration and what stage 1 lacks."""
    label, data = load_stock(args)
    rep, _img, kinfo, _entries = stage1.analyse(label, data, INTERESTING_CONFIG)
    stage1.print_report(rep, log)
    out_dir = args.out / "inspect" / label
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "analysis.json").open("w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2)
    if kinfo.config_text:
        with (out_dir / "kernel.config").open("w", encoding="utf-8", newline="\n") as f:
            f.write(kinfo.config_text)
    log(f">> wrote {out_dir}")
    for p in stage1.check_requirements(kinfo, SPEC.required_config):
        log(f"!! {p}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    """Build the stage-1 boot image from the stock one, with its ramdisk, rootfs tar and record."""
    label, data = load_stock(args)
    rep, stock, kinfo, entries = stage1.analyse(label, data, INTERESTING_CONFIG)
    stage1.print_report(rep, log)
    problems = stage1.check_requirements(kinfo, SPEC.required_config)
    for p in problems:
        log(f"!! {p}")
    if problems and kinfo.config and not args.force:
        msg = "stock kernel lacks required features (use --force to build anyway)"
        raise SystemExit(msg)
    if kinfo.config and not kinfo.enabled("RD_GZIP"):
        msg = "stock kernel cannot unpack a gzip ramdisk"
        raise SystemExit(msg)

    # SOURCE_DATE_EPOCH makes the whole image byte-reproducible.
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    when = dt.datetime.fromtimestamp(int(epoch), dt.timezone.utc) if epoch else dt.datetime.now(dt.timezone.utc)
    build_info = {
        "built": when.strftime("%Y-%m-%d %H:%M UTC"),
        "stock": label,
        "stock_sha256": rep["image_sha256"],
        "kernel": rep["kernel"]["version"],
        "alpine": SPEC.alpine_version,
    }
    tree = build_rootfs(entries, build_info)
    raw = tree.to_cpio()
    ramdisk = compress.gzip_compress(raw)
    log(f">> ramdisk: {len(tree.nodes)} entries, {len(raw) / 1e6:.1f} MB cpio, {len(ramdisk) / 1e6:.1f} MB gzip")

    cmdline = stage1.kernel_cmdline(stock, list(CMDLINE_APPEND) + args.cmdline_append)
    image = stage1.assemble(stock, ramdisk, list(CMDLINE_APPEND) + args.cmdline_append)
    if rep["image_size"] and len(image) > rep["image_size"]:
        log(
            f"!! image ({len(image)} bytes) is larger than the stock partition image ({rep['image_size']} bytes): "
            "too big to flash"
        )

    args.out.mkdir(parents=True, exist_ok=True)
    name = f"pico4-stage1-{label}"
    paths = {"image": args.out / f"{name}.img", "ramdisk": args.out / f"{name}-initramfs.cpio.gz"}
    paths["image"].write_bytes(image)
    paths["ramdisk"].write_bytes(ramdisk)
    if args.stock_boot:
        # The unmodified stock image: what to flash back after a manual "fastboot flash boot" (the backup
        # holds the headset's own).
        (args.out / f"stock-boot-{label}.img").write_bytes(data)
    # The rootfs as a tar (no device nodes) for inspection and for the
    # preflight check, which runs these binaries on the headset under PICO OS.
    with (args.out / f"{name}-rootfs.tar").open("wb") as f:
        tree.to_tar(f)
    summary = {
        "image": paths["image"].name,
        "image_sha256": hashlib.sha256(image).hexdigest(),
        "image_size": len(image),
        "ramdisk_size": len(ramdisk),
        "cmdline": " ".join(cmdline),
        **build_info,
        "stock_analysis": rep,
    }
    with (args.out / f"{name}.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    log(f">> wrote {paths['image']} ({len(image) / 1e6:.1f} MB)")
    log(f"   sha256 {summary['image_sha256']}")
    log(
        "   on the headset: python -m overpressure dev image, then dev flash (devices/phoenix/docs/hardware-testing.md)"
    )
    return 0


def cmd_match(args: argparse.Namespace) -> int:
    """Map a headset's build fingerprint + ro.oem.state to a firmware.json entry."""
    builds = ota.load_catalog(CATALOG)
    for key, b in builds.items():
        if b.get("fingerprint") == args.fingerprint:
            print(key)
            return 0
    # e.g. Pico/Phoenix_ovs/PICOA8110:10/5.13.8/smartcm.1788408867:user/dev-keys
    parts = args.fingerprint.split("/")
    if len(parts) <= FINGERPRINT_VERSION:
        print(f"cannot parse fingerprint {args.fingerprint!r}", file=sys.stderr)
        return 1
    region = "global" if parts[1].endswith("_ovs") else "cn"
    flavour = "seko" if args.oem_state.lower() == "true" else "sek"
    key = f"{parts[FINGERPRINT_VERSION]}-{region}-{flavour}"
    if key in builds:
        print(key)
        print("(same version, region and OEM state; build number not confirmed)", file=sys.stderr)
        return 0
    print(f"no firmware.json entry for {key}; use --stock-boot with your own boot.img or OTA URL", file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    """Run the image commands: match, inspect and build."""
    ap = argparse.ArgumentParser(
        prog="python -m overpressure", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("match", help="find the firmware.json entry for a headset")
    m.add_argument("--fingerprint", required=True, help="adb shell getprop ro.build.fingerprint")
    m.add_argument("--oem-state", required=True, help="adb shell getprop ro.oem.state")
    for name in ("inspect", "build"):
        p = sub.add_parser(name)
        src = p.add_mutually_exclusive_group()
        p.add_argument(
            "--firmware", default=FIRMWARE, help="the headset's build in firmware.json (default: %(default)s)"
        )
        src.add_argument("--backup", type=Path, help="the EDL backup to take boot from (default: PICO4_BACKUP)")
        src.add_argument("--stock-boot", help="another stock boot.img, OTA zip, or URL of either")
        p.add_argument("--out", type=Path, default=REPO / "out", help="output directory")
        if name == "build":
            p.add_argument(
                "--cmdline-append",
                nargs="*",
                default=[],
                metavar="ARG",
                help="extra kernel arguments, e.g. pico4.autoreboot=0",
            )
            p.add_argument("--force", action="store_true", help="build even if kernel checks fail")
    args = ap.parse_args(argv)
    return {"inspect": cmd_inspect, "build": cmd_build, "match": cmd_match}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
