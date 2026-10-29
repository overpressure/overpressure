"""The maintenance stage 1 on Valve's kernel: a recovery image for a one-shot test boot.

It takes the dev loop's recovery image (out/dev/dev-recovery.img: stage 1's ramdisk, its command
line, the stock recovery_dtbo) and replaces the kernel and the device tree with the ones
build-kernel.sh made. The result is a whole recovery partition with the stock vbmeta behind it,
as "dev image" builds it, because recovery is a chained AVB partition.

Started by a reboot with reason "recovery", the ABL boots it once; any reboot after that (a
panic, a deadline, a hard reset) starts the installed system on the stock kernel again.

    python recovery_image.py [--debug] [--trace FUNCTION] [--gpu-firmware DIR] [--cmdline WORD]...
        KERNEL_OUT STOCK_RECOVERY [DEV_RECOVERY] OUT

--gpu-firmware adds PICO's GPU firmware to stage 1, which msm needs while it binds the display.

--debug logs every driver probe, so the RAM console (read by the stock kernel after the reboot)
ends with the probe a stalled boot waits for, the panic shows every task and CPU, and stage 1
panics where it would return to fastboot. --trace records FUNCTION and everything it calls
(ftrace's function graph) and prints the record on a panic: a call without a return is where a
stalled function hangs.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from overpressure import avb, bootimg, compress, cpio  # noqa: E402

DTB = "sm8250-pico4-phoenix.dtb"
#: The ABL passes only the boot image header's first command line field (512 bytes with its
#: terminator) and drops the extra one, so everything has to fit there.
MAX_CMDLINE = 511
#: The base image's words for Android and the downstream kernel; stage 1 reads none of them, and
#: the ABL appends what it does read (androidboot.mode, androidboot.serialno).
STOCK_ONLY = ("androidboot.", "lpm_levels.", "video=", "msm_rtb.", "service_locator.", "swiotlb=",
              "buildvariant=")
#: Words for the new kernel: keep what the bootloader left on (clocks, power domains and
#: regulators no driver claims yet, such as the display's), reboot shortly after a panic, no stock
#: modules, which this kernel cannot load, stage 1 waits up to 5 minutes for the USB controller,
#: whose probe may wait for others on this kernel, and the GPU is a DRM device of its own, so
#: that a GPU that fails to start does not take the display with it.
EXTRA = ("clk_ignore_unused", "pd_ignore_unused", "regulator_ignore_unused", "panic=5",
         "pico4poc.modules=0", "pico4poc.kernel=valve", "pico4poc.udc_timeout=300",
         "msm.separate_gpu_kms=1")
#: --debug: each probe before it starts (dd.c's dynamic debug) and with its result, all levels
#: on the console, and on a panic every task's stack (a probe asleep in a killable or idle wait,
#: which the hung-task detector skips) and the backtraces of all CPUs, and the DRM drivers' and
#: KMS steps (drm.debug: driver, kms, prime, atomic), the last of which a modeset that stalls the
#: SoC leaves in the RAM console; a hard lockup (the buddy detector) panics.
DEBUG = ("initcall_debug", "dd.dyndbg=+p", "ignore_loglevel", "panic_sys_info=tasks,all_bt",
         "drm.debug=0x1e", "nmi_watchdog=panic")
#: --debug also changes stage 1 (an archive appended to its ramdisk, whose entries replace the
#: first archive's): where it gives up and returns to fastboot, it panics instead. Only the
#: panic's reset keeps the RAM console; the return to fastboot leaves it corrupted. Its wait for
#: the test runner drops from 600 s to the USB controller wait plus 30 s.
REBOOT = "usr/sbin/pico4-reboot"
REBOOT_REAL = "usr/libexec/pico4-poc/pico4-reboot-real"
REBOOT_WRAPPER = f"""#!/bin/sh
# Test image of Valve's kernel: a return to fastboot panics instead, so that the RAM console
# survives the reset and the installed system can read this boot's kernel log.
if [ "$1" = bootloader ]; then
	echo "pico4-reboot bootloader: panicking instead (kernel test image)" > /dev/kmsg
	sync
	echo c > /proc/sysrq-trigger
fi
exec /{REBOOT_REAL} "$@"
""".encode()
CONNECT_TIMEOUT = "etc/pico4-poc/test-connect-timeout"


#: --gpu-firmware: PICO's Adreno 650 firmware (assets/graphics-firmware-*, from the stock OTA)
#: where msm looks for it (the microcode and the GMU's) and the device tree names the zap shader.
#: msm loads the GPU while it binds the display, before stage 1 could provide anything.
GPU_FIRMWARE = {"a650_sqe.fw": "lib/firmware/qcom", "a650_gmu.bin": "lib/firmware/qcom",
                "a650_zap.*": "lib/firmware/qcom/sm8250/pico/phoenix"}


def tree_with_dirs(paths) -> cpio.Tree:
    """An archive with the parent directories of every path."""
    tree = cpio.Tree()
    for path in sorted({str(Path(p).parent) for p in paths}):
        parts = Path(path).parts
        for i in range(1, len(parts) + 1):
            if not tree.exists("/".join(parts[:i])):
                tree.mkdir("/".join(parts[:i]))
    return tree


def debug_archive(ramdisk: bytes) -> cpio.Tree:
    """The --debug changes to stage 1."""
    nodes = dict(cpio.read_cpio(compress.decompress(ramdisk)[0]))
    if REBOOT not in nodes or CONNECT_TIMEOUT not in nodes:
        raise ValueError("stage 1's ramdisk has no pico4-reboot or test runner timeout")
    tree = tree_with_dirs((REBOOT, REBOOT_REAL, CONNECT_TIMEOUT))
    tree.add_file(REBOOT_REAL, nodes[REBOOT].data, 0o755)
    tree.add_file(REBOOT, REBOOT_WRAPPER, 0o755)
    udc = int(dict(e.split("=") for e in EXTRA if "=" in e)["pico4poc.udc_timeout"])
    tree.add_file(CONNECT_TIMEOUT, f"{udc + 30}\n".encode())
    return tree


def firmware_archive(directory: Path) -> cpio.Tree:
    """--gpu-firmware: the GPU's firmware files at msm's paths."""
    files = {}
    for pattern, dest in GPU_FIRMWARE.items():
        found = sorted(directory.glob(pattern))
        if not found:
            raise ValueError(f"{directory} has no {pattern}")
        files.update({f"{dest}/{f.name}": f for f in found})
    tree = tree_with_dirs(files)
    for path, source in files.items():
        tree.add_file(path, source.read_bytes())
    return tree


def appended(ramdisk: bytes, *trees: cpio.Tree) -> bytes:
    """The ramdisk with each archive appended; the kernel unpacks them in order."""
    return ramdisk + b"".join(gzip.compress(tree.to_cpio(), mtime=0) for tree in trees)


def trace_words(function: str) -> tuple:
    """--trace: the function graph below one kernel function, printed on a panic."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", function):
        raise ValueError(f"not a kernel function name: {function!r}")
    return ("ftrace=function_graph", f"ftrace_graph_filter={function}", "ftrace_dump_on_oops",
            "trace_buf_size=64K")


def build(kernel_out: Path, stock_recovery: Path, dev_recovery: Path, debug: bool = False,
          trace: str | None = None, gpu_firmware: Path | None = None, cmdline_words=()) -> bytes:
    base = bootimg.parse(dev_recovery.read_bytes())
    if "pico4poc.part=recovery" not in base.full_cmdline.split():
        raise ValueError(f"{dev_recovery} is not the dev loop's recovery image")
    kernel = (kernel_out / "Image.gz").read_bytes()
    dtb = (kernel_out / DTB).read_bytes()
    # A later word replaces an earlier one with its key: the given words win over the defaults.
    keyed = {}
    for word in EXTRA + (DEBUG if debug else ()) + (trace_words(trace) if trace else ()) + tuple(cmdline_words):
        keyed.pop(word.split("=")[0], None)
        keyed[word.split("=")[0]] = word
    extra = tuple(keyed.values())
    words = [w for w in base.full_cmdline.split()
             if w.split("=")[0] not in {e.split("=")[0] for e in extra} and not w.startswith(STOCK_ONLY)]
    cmdline = " ".join(words + list(extra))
    if len(cmdline) > MAX_CMDLINE:
        raise ValueError(f"the command line has {len(cmdline)} bytes; the ABL passes only {MAX_CMDLINE}")
    image = base.with_cmdline(cmdline)
    image.kernel, image.dtb = kernel, dtb
    archives = ([firmware_archive(gpu_firmware)] if gpu_firmware else []) + \
        ([debug_archive(base.ramdisk)] if debug else [])
    ramdisk = appended(base.ramdisk, *archives)
    image.ramdisk = ramdisk
    image.boot_signature = b""
    data = image.pack()
    check = bootimg.parse(data)
    if (check.kernel, check.dtb, check.ramdisk, check.recovery_dtbo) != (kernel, dtb, ramdisk, base.recovery_dtbo):
        raise RuntimeError("the test image failed validation")
    return avb.with_stock_vbmeta(stock_recovery.read_bytes(), data)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--debug", action="store_true", help="log every driver probe")
    ap.add_argument("--trace", metavar="FUNCTION", help="print FUNCTION's call graph on a panic")
    ap.add_argument("--gpu-firmware", metavar="DIR", type=Path,
                    help="add the GPU firmware from DIR (assets/graphics-firmware-*)")
    ap.add_argument("--cmdline", metavar="WORD", action="append", default=[],
                    help="add a kernel command line word, replacing one with its key (repeatable)")
    ap.add_argument("paths", nargs="+", type=Path, metavar="KERNEL_OUT STOCK_RECOVERY [DEV_RECOVERY] OUT")
    args = ap.parse_args(argv)
    if len(args.paths) not in (3, 4):
        ap.error("expected KERNEL_OUT STOCK_RECOVERY [DEV_RECOVERY] OUT")
    root = Path(__file__).resolve().parents[3]
    dev_recovery = args.paths[2] if len(args.paths) == 4 else root / "out" / "dev" / "dev-recovery.img"
    data = build(args.paths[0], args.paths[1], dev_recovery, args.debug, args.trace, args.gpu_firmware,
                 args.cmdline)
    out = args.paths[-1]
    out.write_bytes(data)
    print(f"{out} {hashlib.sha256(data).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
