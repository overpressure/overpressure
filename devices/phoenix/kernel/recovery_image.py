# SPDX-License-Identifier: Unlicense
"""Boot images with Valve's kernel: the installed system's (--installed --boot), and a recovery
image for a one-shot test boot.

It takes the dev loop's recovery image (out/dev/dev-recovery.img: stage 1's ramdisk, its command
line, the stock recovery_dtbo) and replaces the kernel and the device tree with the ones
build-kernel.sh made. The result is a whole recovery partition with the stock vbmeta behind it,
as "dev image" builds it, because recovery is a chained AVB partition.

Started by a reboot with reason "recovery", the ABL boots it once; any reboot after that (a
panic, a deadline, a hard reset) starts the installed system from boot again.

    python recovery_image.py [--debug] [--trace FUNCTION] [--gpu-firmware DIR] [--wlan-firmware DIR]
        [--cmdline WORD]... [--installed INSTALL_BOOT [--boot]] [--stage1 DIR]
        KERNEL_OUT STOCK_RECOVERY [DEV_RECOVERY] OUT

--gpu-firmware adds PICO's GPU firmware to stage 1, which msm needs while it binds the display.
--wlan-firmware adds the QCA6390's, which ath11k loads while its PCIe device probes.

--installed starts the installed system instead of the maintenance stage 1: stage 1 and its
command line come from the installed system's boot image (out/dev/install-boot.img), and its
reboot into recovery after a failed start becomes a plain reboot, since recovery would start
this image again. With --boot it is the boot partition's image of the installed system
instead: no AVB footer, the dtbo partition's overlay, and stage 1's reboot into recovery after a
failed start reaches the dev image.

--stage1 puts the files of a stage-1 overlay (devices/phoenix/initramfs) over the base image's,
so that the test image carries this tree's stage 1 without a new dev image.

Stage 1's build information names the new kernel, and OUT.json beside the image records it, so
that `dev check` recognises the test image's stage 1 as a known build.

--debug logs every driver probe, so the RAM console (read by the installed system or the
maintenance stage 1 after the reboot) ends with the probe a stalled boot waits for, the panic shows every task and CPU, and stage 1
panics where it would return to fastboot. --trace records FUNCTION and everything it calls
(ftrace's function graph) and prints the record on a panic: a call without a return is where a
stalled function hangs.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from overpressure import avb, bootimg, compress, cpio, kernelinfo, stage1 as stage1_overlay  # noqa: E402

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
#: whose probe may wait for others on this kernel. The GPU and the display are one DRM device,
#: as on the Frame, where Turnip finds the panels for SteamVR's direct mode
#: (--cmdline msm.separate_gpu_kms=1 parts them, so that a GPU that fails to start does not take
#: the display with it).
EXTRA = ("clk_ignore_unused", "pd_ignore_unused", "regulator_ignore_unused", "panic=5",
         "pico4.modules=0", "pico4.kernel=valve", "pico4.udc_timeout=300")
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
#: --installed: recovery holds this image, so a reboot into recovery would start it again.
REBOOT_PLAIN = f"""if [ "$1" = recovery ]; then
	echo "pico4-reboot recovery: a plain reboot instead (kernel test image)" > /dev/kmsg
	exec /{REBOOT_REAL}
fi
"""
CONNECT_TIMEOUT = "etc/pico4/test-connect-timeout"
BUILD_INFO = "etc/pico4/build-info"


#: --gpu-firmware: PICO's Adreno 650 firmware (assets/graphics-firmware-*, from the stock OTA)
#: where msm looks for it (the microcode and the GMU's) and the device tree names the zap shader.
#: msm loads the GPU while it binds the display, before stage 1 could provide anything.
GPU_FIRMWARE = {"a650_sqe.fw": "lib/firmware/qcom", "a650_gmu.bin": "lib/firmware/qcom",
                "a650_zap.*": "lib/firmware/qcom/sm8250/pico/phoenix"}
#: --wlan-firmware: the QCA6390's firmware and board data (assets/wlan-*, from the stock OTA)
#: under ath11k's names. The headset's board data is bdwlanq.elf (cnss2 picks it); ath11k takes
#: an ELF board file as board.bin.
ATH11K = "lib/firmware/ath11k/QCA6390/hw2.0"
WLAN_FIRMWARE = {"firmware/qca6390/amss20.bin": f"{ATH11K}/amss.bin",
                 "firmware/qca6390/m3.bin": f"{ATH11K}/m3.bin",
                 "firmware/qca6390/bdwlanq.elf": f"{ATH11K}/board.bin"}


def tree_with_dirs(paths) -> cpio.Tree:
    """An archive with the parent directories of every path."""
    tree = cpio.Tree()
    for path in sorted({str(Path(p).parent) for p in paths}):
        parts = Path(path).parts
        for i in range(1, len(parts) + 1):
            if not tree.exists("/".join(parts[:i])):
                tree.mkdir("/".join(parts[:i]))
    return tree


def stage1_archive(ramdisk: bytes, debug: bool, installed: bool) -> cpio.Tree:
    """The --debug and --installed changes to stage 1."""
    nodes = dict(cpio.read_cpio(compress.decompress(ramdisk)[0]))
    if REBOOT not in nodes or (debug and CONNECT_TIMEOUT not in nodes):
        raise ValueError("stage 1's ramdisk has no pico4-reboot or test runner timeout")
    tree = tree_with_dirs((REBOOT, REBOOT_REAL) + ((CONNECT_TIMEOUT,) if debug else ()))
    tree.add_file(REBOOT_REAL, nodes[REBOOT].data, 0o755)
    wrapper = REBOOT_HEADER + (REBOOT_PANIC if debug else "") + (REBOOT_PLAIN if installed else "")
    tree.add_file(REBOOT, f'{wrapper}exec /{REBOOT_REAL} "$@"\n'.encode(), 0o755)
    if debug:
        udc = int(dict(e.split("=") for e in EXTRA if "=" in e)["pico4.udc_timeout"])
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
    return files_archive(files)


def wlan_firmware_archive(directory: Path) -> cpio.Tree:
    """--wlan-firmware: the QCA6390's files at ath11k's paths."""
    files = {dest: directory / source for source, dest in WLAN_FIRMWARE.items()}
    missing = [str(f) for f in files.values() if not f.is_file()]
    if missing:
        raise ValueError(f"no {', '.join(missing)}")
    return files_archive(files)


def files_archive(files: dict) -> cpio.Tree:
    """An archive with each file at its path."""
    tree = tree_with_dirs(files)
    for path, source in files.items():
        tree.add_file(path, source.read_bytes())
    return tree


#: --stage1 also carries the prebuilt boot splash beside the overlay (prebuilt/aarch64), as the
#: stage-1 image does; pico4-reboot stays the base image's (--debug and --installed wrap it).
PREBUILT = ("pico4-splash",)


def overlay_archive(directory: Path) -> cpio.Tree:
    """--stage1: a stage-1 overlay's files, which replace the base ramdisk's."""
    tree = cpio.Tree()
    stage1_overlay.add_overlay(tree, directory)
    if "init" not in tree.nodes:
        raise ValueError(f"{directory} is no stage-1 overlay (no init)")
    for name in PREBUILT:
        program = directory.parent / "prebuilt" / "aarch64" / name
        if program.is_file():
            if not tree.exists("usr/sbin"):
                tree.mkdir("usr/sbin")
            tree.add_file(f"usr/sbin/{name}", program.read_bytes(), 0o755)
    return tree


def build_info(ramdisk: bytes, kernel: bytes) -> dict | None:
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


def build_info_archive(info: dict) -> cpio.Tree:
    tree = tree_with_dirs((BUILD_INFO,))
    tree.add_file(BUILD_INFO, json.dumps(info, indent=2).encode() + b"\n", 0o644)
    return tree


def stage1_image(dev_recovery: Path, installed: Path | None):
    """The recovery partition's image and the one stage 1 and its words come from."""
    base = bootimg.parse(dev_recovery.read_bytes())
    if "pico4.part=recovery" not in base.full_cmdline.split():
        raise ValueError(f"{dev_recovery} is not the dev loop's recovery image")
    stage1 = base
    if installed:
        stage1 = bootimg.parse(installed.read_bytes())
        if "pico4.root=installed" not in stage1.full_cmdline.split():
            raise ValueError(f"{installed} is not the installed system's boot image")
    return base, stage1


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
          trace: str | None = None, gpu_firmware: Path | None = None, cmdline_words=(),
          installed: Path | None = None, overlay: Path | None = None,
          wlan_firmware: Path | None = None, boot: bool = False) -> bytes:
    # The header and the recovery_dtbo stay the recovery partition's; stage 1 and its words can
    # be the installed system's. --boot: the boot partition's image, all of it the installed
    # system's (the ABL applies the dtbo partition's overlay on a normal boot), without AVB.
    base, stage1 = stage1_image(dev_recovery, installed)
    if boot:
        if not installed:
            raise ValueError("--boot needs --installed (the installed system's boot image)")
        base = stage1
    kernel = (kernel_out / "Image.gz").read_bytes()
    dtb = (kernel_out / DTB).read_bytes()
    # A later word replaces an earlier one with its key: the given words win over the defaults.
    keyed = {}
    for word in EXTRA + (DEBUG if debug else ()) + (trace_words(trace) if trace else ()) + tuple(cmdline_words):
        keyed.pop(word.split("=")[0], None)
        keyed[word.split("=")[0]] = word
    extra = tuple(keyed.values())
    words = [w for w in stage1.full_cmdline.split()
             if w.split("=")[0] not in {e.split("=")[0] for e in extra} and not w.startswith(STOCK_ONLY)]
    cmdline = " ".join(words + list(extra))
    if len(cmdline) > MAX_CMDLINE:
        raise ValueError(f"the command line has {len(cmdline)} bytes; the ABL passes only {MAX_CMDLINE}")
    image = base.with_cmdline(cmdline)
    image.kernel, image.dtb = kernel, dtb
    info = build_info(stage1.ramdisk, kernel)
    # In boot, stage 1's reboot into recovery after a failed start reaches the dev image, as it
    # should: only the one-shot recovery image needs the plain reboot instead.
    plain_reboot = bool(installed) and not boot
    archives = ([overlay_archive(overlay)] if overlay else []) + \
        ([firmware_archive(gpu_firmware)] if gpu_firmware else []) + \
        ([wlan_firmware_archive(wlan_firmware)] if wlan_firmware else []) + \
        ([stage1_archive(stage1.ramdisk, debug, plain_reboot)] if debug or plain_reboot else []) + \
        ([build_info_archive(info)] if info else [])
    ramdisk = appended(stage1.ramdisk, *archives)
    image.ramdisk = ramdisk
    image.boot_signature = b""
    data = image.pack()
    check = bootimg.parse(data)
    if (check.kernel, check.dtb, check.ramdisk, check.recovery_dtbo) != (kernel, dtb, ramdisk, base.recovery_dtbo):
        raise RuntimeError("the test image failed validation")
    return data if boot else avb.with_stock_vbmeta(stock_recovery.read_bytes(), data)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--debug", action="store_true", help="log every driver probe")
    ap.add_argument("--trace", metavar="FUNCTION", help="print FUNCTION's call graph on a panic")
    ap.add_argument("--gpu-firmware", metavar="DIR", type=Path,
                    help="add the GPU firmware from DIR (assets/graphics-firmware-*)")
    ap.add_argument("--wlan-firmware", metavar="DIR", type=Path,
                    help="add the QCA6390's firmware from DIR (assets/wlan-*)")
    ap.add_argument("--cmdline", metavar="WORD", action="append", default=[],
                    help="add a kernel command line word, replacing one with its key (repeatable)")
    ap.add_argument("--installed", metavar="INSTALL_BOOT", type=Path,
                    help="start the installed system with INSTALL_BOOT's stage 1 (out/dev/install-boot.img)")
    ap.add_argument("--stage1", metavar="DIR", type=Path,
                    help="put the stage-1 overlay DIR (devices/phoenix/initramfs) over the base's files")
    ap.add_argument("--boot", action="store_true",
                    help="the boot partition's image of the installed system (with --installed)")
    ap.add_argument("paths", nargs="+", type=Path, metavar="KERNEL_OUT STOCK_RECOVERY [DEV_RECOVERY] OUT")
    args = ap.parse_args(argv)
    if len(args.paths) not in (3, 4):
        ap.error("expected KERNEL_OUT STOCK_RECOVERY [DEV_RECOVERY] OUT")
    root = Path(__file__).resolve().parents[3]
    dev_recovery = args.paths[2] if len(args.paths) == 4 else root / "out" / "dev" / "dev-recovery.img"
    data = build(args.paths[0], args.paths[1], dev_recovery, args.debug, args.trace, args.gpu_firmware,
                 args.cmdline, args.installed, args.stage1, args.wlan_firmware, args.boot)
    out = args.paths[-1]
    out.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    stage1 = stage1_image(dev_recovery, args.installed)[1]
    info = build_info(stage1.ramdisk, (args.paths[0] / "Image.gz").read_bytes())
    key = "boot_sha256" if args.boot else "recovery_sha256"
    Path(f"{out}.json").write_bytes((json.dumps({key: digest, "build_info": info}, indent=2) + "\n").encode("utf-8"))
    print(f"{out} {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
