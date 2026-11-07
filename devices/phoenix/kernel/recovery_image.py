# SPDX-License-Identifier: Unlicense
"""Boot images with Valve's kernel.

The installed system's (--direct --boot), and a recovery image for a one-shot test boot. It
takes the dev loop's recovery image (out/dev/dev-recovery.img: stage 1's ramdisk, its command
line, the stock recovery_dtbo) and replaces the kernel and the device tree with the ones
build-kernel.sh made. The recovery image is a whole recovery partition with the stock vbmeta
behind it, as "dev image" builds it, because recovery is a chained AVB partition.

Started by a reboot with reason "recovery", the ABL boots a recovery image once; any reboot after
that (a panic, a deadline, a hard reset) starts the installed system from boot again.

    python recovery_image.py [--debug] [--trace FUNCTION] [--gpu-firmware DIR] [--wlan-firmware DIR]
        [--cmdline WORD]... [--stage1 DIR] [--direct [--boot]]
        KERNEL_OUT STOCK_RECOVERY [DEV_RECOVERY] OUT

--direct has no stage 1: no ramdisk, and the kernel mounts the system image in super read-only as
its root and starts its preinit (docs/install.md). In recovery, a failed start reboots plainly
(pico4.failover=reboot), since a reboot into recovery would start this image again. With --boot
it is the boot partition's image of the installed system instead: no AVB footer, the dtbo
partition's overlay, and preinit's reboot into recovery after a failed start reaches the dev
image.

Without --direct, the image starts the maintenance stage 1 on Valve's kernel. --gpu-firmware
adds PICO's GPU firmware to it, --wlan-firmware the QCA6390's, which ath11k loads while its PCIe
device probes. --stage1 puts the files of a stage-1 overlay (devices/phoenix/initramfs) over the
base image's, so that the test image carries this tree's stage 1 without a new dev image.

OUT.json beside the image records its hash and its build information: stage 1's with the new
kernel's version, or with --direct the kernel's version alone, so that `dev check` recognises
the running build.

--debug logs every driver probe, so the RAM console (read by the installed system or the
maintenance stage 1 after the reboot) ends with the probe a stalled boot waits for, the panic
shows every task and CPU, and stage 1 panics where it would return to fastboot. --trace records
FUNCTION and everything it calls (ftrace's function graph) and prints the record on a panic: a
call without a return is where a stalled function hangs.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from overpressure import avb, bootimg, compress, cpio, kernelinfo
from overpressure import stage1 as stage1_overlay

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

DTB = "sm8250-pico4-phoenix.dtb"
#: The ABL passes only the boot image header's first command line field (512 bytes with its
#: terminator) and drops the extra one, so everything has to fit there.
MAX_CMDLINE = 511
#: The base image's words for Android and the downstream kernel; stage 1 reads none of them, and
#: the ABL appends what it does read (androidboot.mode, androidboot.serialno).
STOCK_ONLY = ("androidboot.", "lpm_levels.", "video=", "msm_rtb.", "service_locator.", "swiotlb=", "buildvariant=")
#: Words for the new kernel: keep what the bootloader left on (clocks, power domains and
#: regulators no driver claims yet, such as the display's), reboot shortly after a panic, no stock
#: modules, which this kernel cannot load, stage 1 waits up to 5 minutes for the USB controller,
#: whose probe may wait for others on this kernel. The GPU and the display are one DRM device,
#: as on the Frame, where Turnip finds the panels for SteamVR's direct mode
#: (--cmdline msm.separate_gpu_kms=1 parts them, so that a GPU that fails to start does not take
#: the display with it).
EXTRA = (
    "clk_ignore_unused",
    "pd_ignore_unused",
    "regulator_ignore_unused",
    "panic=5",
    "pico4.modules=0",
    "pico4.kernel=valve",
    "pico4.udc_timeout=300",
)
#: --direct: the kernel's own words of EXTRA (stage 1 reads the pico4. ones) and the root: the system
#: image in super, read-only, waited for at most 20 s (then a panic, and panic=5 reboots), its
#: preinit as the first process. pico4.root=installed and pico4.autoreboot=0 tell the system's units
#: that this is the installed system, not a test run with deadlines.
DIRECT = (
    "clk_ignore_unused",
    "pd_ignore_unused",
    "regulator_ignore_unused",
    "panic=5",
    "root=PARTLABEL=super",
    "rootfstype=ext4",
    "rootwait=20",
    "ro",
    "init=/usr/libexec/pico4/preinit",
    "pico4.root=installed",
    "pico4.autoreboot=0",
)
#: --direct in recovery: preinit's reboot after a failed start is a plain one, into boot.
DIRECT_ONESHOT = ("pico4.failover=reboot",)
#: --debug: each probe before it starts (dd.c's dynamic debug) and with its result, all levels
#: on the console, and on a panic every task's stack (a probe asleep in a killable or idle wait,
#: which the hung-task detector skips) and the backtraces of all CPUs, and the DRM drivers' and
#: KMS steps (drm.debug: driver, kms, prime, atomic), the last of which a modeset that stalls the
#: SoC leaves in the RAM console; a hard lockup (the buddy detector) panics.
DEBUG = (
    "initcall_debug",
    "dd.dyndbg=+p",
    "ignore_loglevel",
    "panic_sys_info=tasks,all_bt",
    "drm.debug=0x1e",
    "nmi_watchdog=panic",
)
#: --debug also changes stage 1 (an archive appended to its ramdisk, whose entries replace the
#: first archive's): where it gives up and returns to fastboot, it panics instead. Only the
#: panic's reset keeps the RAM console; the return to fastboot leaves it corrupted. Its wait for
#: the test runner drops from 600 s to the USB controller wait plus 30 s.
REBOOT = "usr/sbin/pico4-reboot"
REBOOT_REAL = "usr/libexec/pico4/pico4-reboot-real"
REBOOT_HEADER = "#!/bin/sh\n# Test image of Valve's kernel.\n"
#: --debug: a return to fastboot panics instead, so that the RAM console survives the reset and
#: the installed system can read this boot's kernel log.
REBOOT_PANIC = """if [ "$1" = bootloader ]; then
	echo "pico4-reboot bootloader: panicking instead (kernel test image)" > /dev/kmsg
	sync
	echo c > /proc/sysrq-trigger
fi
"""
CONNECT_TIMEOUT = "etc/pico4/test-connect-timeout"
BUILD_INFO = "etc/pico4/build-info"


#: --gpu-firmware: PICO's Adreno 650 firmware (assets/graphics-firmware-*, from the stock OTA)
#: where msm looks for it (the microcode and the GMU's) and the device tree names the zap shader.
#: msm loads the GPU while it binds the display, before stage 1 could provide anything.
GPU_FIRMWARE = {
    "a650_sqe.fw": "lib/firmware/qcom",
    "a650_gmu.bin": "lib/firmware/qcom",
    "a650_zap.*": "lib/firmware/qcom/sm8250/pico/phoenix",
}
#: --wlan-firmware: the QCA6390's firmware and board data (assets/wlan-*, from the stock OTA)
#: under ath11k's names. The headset's board data is bdwlanq.elf (cnss2 picks it); ath11k takes
#: an ELF board file as board.bin.
ATH11K = "lib/firmware/ath11k/QCA6390/hw2.0"
WLAN_FIRMWARE = {
    "firmware/qca6390/amss20.bin": f"{ATH11K}/amss.bin",
    "firmware/qca6390/m3.bin": f"{ATH11K}/m3.bin",
    "firmware/qca6390/bdwlanq.elf": f"{ATH11K}/board.bin",
}


def tree_with_dirs(paths: Iterable[str]) -> cpio.Tree:
    """Start an archive with the parent directories of every path."""
    tree = cpio.Tree()
    for path in sorted({str(Path(p).parent) for p in paths}):
        parts = Path(path).parts
        for i in range(1, len(parts) + 1):
            if not tree.exists("/".join(parts[:i])):
                tree.mkdir("/".join(parts[:i]))
    return tree


def stage1_archive(ramdisk: bytes) -> cpio.Tree:
    """Archive the --debug changes to stage 1."""
    nodes = dict(cpio.read_cpio(compress.decompress(ramdisk)[0]))
    if REBOOT not in nodes or CONNECT_TIMEOUT not in nodes:
        msg = "stage 1's ramdisk has no pico4-reboot or test runner timeout"
        raise ValueError(msg)
    tree = tree_with_dirs((REBOOT, REBOOT_REAL, CONNECT_TIMEOUT))
    tree.add_file(REBOOT_REAL, nodes[REBOOT].data, 0o755)
    tree.add_file(REBOOT, f'{REBOOT_HEADER}{REBOOT_PANIC}exec /{REBOOT_REAL} "$@"\n'.encode(), 0o755)
    udc = int({k: v for k, _, v in (e.partition("=") for e in EXTRA)}["pico4.udc_timeout"])
    tree.add_file(CONNECT_TIMEOUT, f"{udc + 30}\n".encode())
    return tree


def firmware_archive(directory: Path) -> cpio.Tree:
    """--gpu-firmware: the GPU's firmware files at msm's paths."""
    files = {}
    for pattern, dest in GPU_FIRMWARE.items():
        found = sorted(directory.glob(pattern))
        if not found:
            msg = f"{directory} has no {pattern}"
            raise ValueError(msg)
        files.update({f"{dest}/{f.name}": f for f in found})
    return files_archive(files)


def wlan_firmware_archive(directory: Path) -> cpio.Tree:
    """--wlan-firmware: the QCA6390's files at ath11k's paths."""
    files = {dest: directory / source for source, dest in WLAN_FIRMWARE.items()}
    missing = [str(f) for f in files.values() if not f.is_file()]
    if missing:
        msg = f"no {', '.join(missing)}"
        raise ValueError(msg)
    return files_archive(files)


def files_archive(files: dict[str, Path]) -> cpio.Tree:
    """Archive each file at its path."""
    tree = tree_with_dirs(files)
    for path, source in files.items():
        tree.add_file(path, source.read_bytes())
    return tree


def overlay_archive(directory: Path) -> cpio.Tree:
    """--stage1: a stage-1 overlay's files, which replace the base ramdisk's."""
    tree = cpio.Tree()
    stage1_overlay.add_overlay(tree, directory)
    if "init" not in tree.nodes:
        msg = f"{directory} is no stage-1 overlay (no init)"
        raise ValueError(msg)
    return tree


def build_info(ramdisk: bytes, kernel: bytes) -> dict[str, Any] | None:
    """Stage 1's build information with the new kernel's version (None without any)."""
    try:
        nodes = dict(cpio.read_cpio(compress.decompress(ramdisk)[0]))
    except (ValueError, EOFError, OSError):
        return None
    if BUILD_INFO not in nodes:
        return None
    info = json.loads(nodes[BUILD_INFO].data)
    info["kernel"] = kernelinfo.analyse(kernel).version
    return info


def build_info_archive(info: dict[str, Any]) -> cpio.Tree:
    """Archive stage 1's build information, which replaces the base ramdisk's."""
    tree = tree_with_dirs((BUILD_INFO,))
    tree.add_file(BUILD_INFO, json.dumps(info, indent=2).encode() + b"\n", 0o644)
    return tree


def dev_recovery_image(dev_recovery: Path) -> bootimg.BootImage:
    """Return the dev loop's recovery image: its header, stage 1 and the stock recovery_dtbo."""
    base = bootimg.parse(dev_recovery.read_bytes())
    if "pico4.part=recovery" not in base.full_cmdline.split():
        msg = f"{dev_recovery} is not the dev loop's recovery image"
        raise ValueError(msg)
    return base


def appended(ramdisk: bytes, *trees: cpio.Tree) -> bytes:
    """Append each archive to the ramdisk; the kernel unpacks them in order."""
    return ramdisk + b"".join(gzip.compress(tree.to_cpio(), mtime=0) for tree in trees)


def trace_words(function: str) -> tuple[str, ...]:
    """--trace: the function graph below one kernel function, printed on a panic."""
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


def build(  # noqa: PLR0913  # one keyword per command-line option
    kernel_out: Path,
    stock_recovery: Path,
    dev_recovery: Path,
    *,
    debug: bool = False,
    trace: str | None = None,
    gpu_firmware: Path | None = None,
    cmdline_words: Iterable[str] = (),
    overlay: Path | None = None,
    wlan_firmware: Path | None = None,
    direct: bool = False,
    boot: bool = False,
) -> bytes:
    """Build the image: a whole recovery partition, or with BOOT the boot partition's image.

    The header comes from the dev loop's recovery image. Without DIRECT the image keeps its stage
    1 and recovery_dtbo. DIRECT: no ramdisk, the kernel starts the installed system from super.
    BOOT (only with DIRECT): the boot partition's image, without the recovery_dtbo (the ABL
    applies the dtbo partition's overlay on a normal boot) and without AVB.
    """
    base = dev_recovery_image(dev_recovery)
    if boot and not direct:
        msg = "--boot needs --direct (the installed system's boot image)"
        raise ValueError(msg)
    if direct and (overlay or gpu_firmware or wlan_firmware):
        msg = "--direct has no stage 1 for --stage1, --gpu-firmware or --wlan-firmware"
        raise ValueError(msg)
    kernel = (kernel_out / "Image.gz").read_bytes()
    dtb = (kernel_out / DTB).read_bytes()
    debug_words = (DEBUG if debug else ()) + (trace_words(trace) if trace else ())
    if direct:
        extra = keyed_words(DIRECT, () if boot else DIRECT_ONESHOT, debug_words, cmdline_words)
        drop = ("pico4.",)
    else:
        extra = keyed_words(EXTRA, debug_words, cmdline_words)
        drop = ()
    keys = {e.split("=")[0] for e in extra}
    words = [
        w for w in base.full_cmdline.split() if w.split("=")[0] not in keys and not w.startswith(STOCK_ONLY + drop)
    ]
    cmdline = " ".join(words + list(extra))
    if len(cmdline) > MAX_CMDLINE:
        msg = f"the command line has {len(cmdline)} bytes; the ABL passes only {MAX_CMDLINE}"
        raise ValueError(msg)
    image = base.with_cmdline(cmdline)
    image.kernel, image.dtb = kernel, dtb
    if boot:
        image.recovery_dtbo = b""
    if direct:
        ramdisk = b""
    else:
        info = build_info(base.ramdisk, kernel)
        archives = (
            ([overlay_archive(overlay)] if overlay else [])
            + ([firmware_archive(gpu_firmware)] if gpu_firmware else [])
            + ([wlan_firmware_archive(wlan_firmware)] if wlan_firmware else [])
            + ([stage1_archive(base.ramdisk)] if debug else [])
            + ([build_info_archive(info)] if info else [])
        )
        ramdisk = appended(base.ramdisk, *archives)
    image.ramdisk = ramdisk
    image.boot_signature = b""
    data = image.pack()
    check = bootimg.parse(data)
    if (check.kernel, check.dtb, check.ramdisk, check.recovery_dtbo) != (kernel, dtb, ramdisk, image.recovery_dtbo):
        msg = "the test image failed validation"
        raise RuntimeError(msg)
    return data if boot else avb.with_stock_vbmeta(stock_recovery.read_bytes(), data)


def image_build_info(dev_recovery: Path, kernel: bytes, *, direct: bool) -> dict[str, Any] | None:
    """OUT.json's build information: stage 1's with the new kernel, or with --direct the kernel's."""
    if direct:
        return {"kernel": kernelinfo.analyse(kernel).version, "boot": "direct"}
    return build_info(dev_recovery_image(dev_recovery).ramdisk, kernel)


def main(argv: Sequence[str] | None = None) -> int:
    """Write OUT and OUT.json, which records its hash and build information."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--debug", action="store_true", help="log every driver probe")
    ap.add_argument("--trace", metavar="FUNCTION", help="print FUNCTION's call graph on a panic")
    ap.add_argument(
        "--gpu-firmware", metavar="DIR", type=Path, help="add the GPU firmware from DIR (assets/graphics-firmware-*)"
    )
    ap.add_argument(
        "--wlan-firmware", metavar="DIR", type=Path, help="add the QCA6390's firmware from DIR (assets/wlan-*)"
    )
    ap.add_argument(
        "--cmdline",
        metavar="WORD",
        action="append",
        default=[],
        help="add a kernel command line word, replacing one with its key (repeatable)",
    )
    ap.add_argument(
        "--stage1",
        metavar="DIR",
        type=Path,
        help="put the stage-1 overlay DIR (devices/phoenix/initramfs) over the base's files",
    )
    ap.add_argument("--direct", action="store_true", help="no stage 1: the kernel starts the installed system")
    ap.add_argument(
        "--boot", action="store_true", help="the boot partition's image of the installed system (with --direct)"
    )
    ap.add_argument("paths", nargs="+", type=Path, metavar="KERNEL_OUT STOCK_RECOVERY [DEV_RECOVERY] OUT")
    args = ap.parse_args(argv)
    if len(args.paths) not in (3, 4):
        ap.error("expected KERNEL_OUT STOCK_RECOVERY [DEV_RECOVERY] OUT")
    root = Path(__file__).resolve().parents[3]
    kernel_out, stock_recovery, *given, out = args.paths
    dev_recovery = given[0] if given else root / "out" / "dev" / "dev-recovery.img"
    data = build(
        kernel_out,
        stock_recovery,
        dev_recovery,
        debug=args.debug,
        trace=args.trace,
        gpu_firmware=args.gpu_firmware,
        cmdline_words=args.cmdline,
        overlay=args.stage1,
        wlan_firmware=args.wlan_firmware,
        direct=args.direct,
        boot=args.boot,
    )
    out.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    info = image_build_info(dev_recovery, (kernel_out / "Image.gz").read_bytes(), direct=args.direct)
    key = "boot_sha256" if args.boot else "recovery_sha256"
    Path(f"{out}.json").write_bytes((json.dumps({key: digest, "build_info": info}, indent=2) + "\n").encode("utf-8"))
    print(f"{out} {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
