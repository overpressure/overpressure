# SPDX-License-Identifier: Unlicense
"""Valve's kernel's host tools: the RGB camera's table and the boot and recovery images."""

from __future__ import annotations

import gzip
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

from devices.phoenix.kernel import imx471_mode, recovery_image
from overpressure import avb, bootimg, cpio

if TYPE_CHECKING:
    from collections.abc import Sequence


#: The driver's mode, as imx471_mode.py selects it: line length, frame length, frame rate, size.
MODE = (2560, 4520, 50.0, 2328, 1748, 10)
#: A made-up table around the driver's controls, which the tool leaves out.
SETTINGS = [(0x1000, 0x11), (0x0202, 0x22), (0x0203, 0x33), (0x0204, 0x44), (0x0205, 0x55), (0x1001, 0x66)]


def camera_module(
    resolutions: Sequence[tuple[int, int, float, int, int, int]] = (MODE,),
    settings: Sequence[tuple[int, int]] = SETTINGS,
    record: dict[str, int] | None = None,
) -> bytes:
    """Build a camera module parameter file with these resolutions, each with `settings`; `record` overrides fields."""
    names, blob = [], bytearray()

    def node(name: str, payload: bytes) -> int:
        names.append((name, len(blob), len(payload)))
        blob.extend(payload)
        return len(names)  # ids count from 1; a pointer holds the id

    entries = b""
    for line, frame, rate, width, height, bits in resolutions:
        stream = node("streamConfiguration", struct.pack("<8Q", 0, 43, 0, 0, width, height, bits, 1))
        records = b""
        for reg, value in settings:
            data = node("registerData", struct.pack("<Q", value))
            delay = node("delayUs", struct.pack("<Q", 0))
            fields = {
                "slave": 0,
                "reg": reg,
                "count": 1,
                "data": data,
                "address": 2,
                "width": 1,
                "op": 0,
                "delays": 1,
                "delay": delay,
            }
            fields.update(record or {})
            records += struct.pack(
                "<10Q",
                fields["slave"],
                0,
                fields["reg"],
                fields["count"],
                fields["data"],
                fields["address"],
                fields["width"],
                fields["op"],
                fields["delays"],
                fields["delay"],
            )
        regs = node("regSetting", records)
        entry = [0] * 38
        entry[1:5] = [1, stream, line, frame]
        entry[20:22] = [len(settings), regs]
        packed = bytearray(struct.pack("<38Q", *entry))
        struct.pack_into("<d", packed, 80, rate)
        entries += bytes(packed)
    node("resolutionData", entries)

    symbols = b"".join(
        struct.pack("<Q32s4Q", i + 1, name.encode(), 0, 0, offset, size) for i, (name, offset, size) in enumerate(names)
    )
    table = 160
    first = table + 2 * 24
    second = first + len(symbols)
    head = bytearray(table)
    head[: len(imx471_mode.MAGIC)] = imx471_mode.MAGIC
    struct.pack_into("<I", head, 28, second + len(blob))
    struct.pack_into("<II", head, 152, table, 2)
    return bytes(head) + struct.pack("<6Q", 0, first, len(symbols), 1, second, len(blob)) + symbols + bytes(blob)


class Imx471ModeTests(unittest.TestCase):
    def test_the_modes_settings_without_the_drivers_controls(self):
        other = (2560, 3776, 60.0, 2328, 1748, 10)
        pairs = imx471_mode.mode(camera_module([other, MODE, other]))
        self.assertEqual(pairs, [(0x1000, 0x11), (0x1001, 0x66)])
        text = imx471_mode.header(pairs)
        self.assertIn("static const struct imx471_reg imx471_mode[] = {", text)
        self.assertIn("\t{ 0x1000, 0x11 },\n\t{ 0x1001, 0x66 },\n};\n", text)

    def test_only_one_resolution_may_be_the_mode(self):
        for resolutions in ([], [MODE, MODE], [(2560, 4520, 50.0, 2328, 1080, 10)]):
            with self.assertRaises(ValueError):
                imx471_mode.mode(camera_module(resolutions))

    def test_what_is_not_a_plain_byte_write_is_refused(self):
        for record in ({"op": 3}, {"width": 2}, {"address": 1}, {"slave": 1}, {"count": 2}, {"reg": 0x10000}):
            with self.assertRaises(ValueError, msg=record):
                imx471_mode.mode(camera_module(record=record))
        with self.assertRaises(ValueError):  # a value over a byte
            imx471_mode.mode(camera_module(settings=[*SETTINGS, (4098, 256)]))
        with self.assertRaises(ValueError):  # a pointer to a node of the wrong name
            imx471_mode.mode(camera_module(record={"data": 1}))

    def test_the_drivers_controls_must_be_there_once(self):
        for settings in (SETTINGS[:1], [*SETTINGS, (514, 119)]):
            with self.assertRaises(ValueError):
                imx471_mode.mode(camera_module(settings=settings))

    def test_a_damaged_file_is_refused(self):
        good = camera_module()
        imx471_mode.mode(good)
        for bad in (good[:-1], b"X" + good[1:], good + b"\0", b"", good[:152] + b"\xff" * 8 + good[160:]):
            with self.assertRaises(ValueError):
                imx471_mode.mode(bad)

    def test_only_known_files_become_a_header(self):
        data = camera_module()
        with tempfile.TemporaryDirectory() as tmp:
            module, out = Path(tmp) / "module.bin", Path(tmp) / "mode.h"
            module.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "unknown camera module"):
                imx471_mode.main([str(module), str(out)])
            self.assertFalse(out.exists())
            with mock.patch.object(imx471_mode, "MODULES", {hashlib.sha256(data).hexdigest()}):
                self.assertEqual(imx471_mode.main([str(module), str(out)]), 0)
            self.assertIn("{ 0x1000, 0x11 },", out.read_text())

    def test_the_drivers_file_is_the_magic_the_count_and_the_writes(self):
        table = imx471_mode.table([(0x1000, 0x11), (0x0301, 0x0A)])
        self.assertEqual(table, b"IMX471M1" + b"\x02\0\0\0" + b"\x10\x00\x11" + b"\x03\x01\x0a")
        data = camera_module()
        with tempfile.TemporaryDirectory() as tmp:
            module, out = Path(tmp) / "module.bin", Path(tmp) / "imx471-mode.bin"
            module.write_bytes(data)
            with mock.patch.object(imx471_mode, "MODULES", {hashlib.sha256(data).hexdigest()}):
                self.assertEqual(imx471_mode.main([str(module), str(out)]), 0)
            self.assertEqual(out.read_bytes(), imx471_mode.table([(0x1000, 0x11), (0x1001, 0x66)]))


def stock_partition(total: int = 64 * 4096) -> bytes:
    """Build a partition with an AVB footer pointing at its vbmeta, as a stock image has one."""
    data = bytearray(total)
    vbmeta = b"AVB0" + b"\x11" * 252
    data[1000 : 1000 + len(vbmeta)] = vbmeta
    data[-64:] = avb.FOOTER.pack(avb.FOOTER_MAGIC, 1, 0, 1000, 1000, len(vbmeta))
    return bytes(data)


class RecoveryImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "out").mkdir()
        (self.tmp / "out" / "Image.gz").write_bytes(b"new kernel")
        (self.tmp / "out" / recovery_image.DTB).write_bytes(b"new dtb")
        (self.tmp / "stock.img").write_bytes(stock_partition())

    def dev_recovery(self, cmdline: str, ramdisk: bytes = b"stage 1") -> Path:
        boot = bootimg.BootImage(
            header_version=2,
            page_size=4096,
            kernel=b"stock kernel",
            ramdisk=ramdisk,
            recovery_dtbo=b"overlays",
            dtb=b"stock dtb",
        ).with_cmdline(cmdline)
        path = self.tmp / "dev-recovery.img"
        path.write_bytes(avb.with_stock_vbmeta(stock_partition(), boot.pack()))
        return path

    def test_appended_archives_are_the_same_on_every_host(self):
        # gzip.compress writes the host's OS byte (Windows 0x0a, or 0xff); the images pin Unix.
        tree = cpio.Tree()
        tree.add_file("etc/pico4/x", b"x\n")
        data = recovery_image.appended(b"base", tree)
        self.assertEqual(data[:4], b"base")
        self.assertEqual(data[4 + 4 : 4 + 8], bytes(4))  # mtime 0
        self.assertEqual(data[4 + 9], 3)
        self.assertEqual(dict(cpio.read_cpio(gzip.decompress(data[4:])))["etc/pico4/x"].data, b"x\n")

    def test_kernel_and_tree_replaced_stage1_kept(self):
        dev = self.dev_recovery("androidboot.hardware=qcom panic=0 pico4.test_run=dev-loop pico4.part=recovery")
        data = recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev)
        self.assertEqual(len(data), len(stock_partition()))
        self.assertEqual(avb.footer(data)[2], 256)
        image = bootimg.parse(data)
        self.assertEqual((image.kernel, image.dtb), (b"new kernel", b"new dtb"))
        self.assertEqual((image.ramdisk, image.recovery_dtbo), (b"stage 1", b"overlays"))
        words = image.full_cmdline.split()
        self.assertIn("pico4.part=recovery", words)
        self.assertIn("pico4.modules=0", words)
        # The test image's own panic setting replaces the image's; Android's words go.
        self.assertEqual([w for w in words if w.startswith("panic=")], ["panic=5"])
        self.assertNotIn("androidboot.hardware=qcom", words)

    def test_a_command_line_beyond_the_abls_field_is_refused(self):
        dev = self.dev_recovery("pico4.part=recovery " + "x" * 500)
        with self.assertRaises(ValueError):
            recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev)

    def test_debug_adds_probe_logging_and_a_panic_for_fastboot(self):
        tree = cpio.Tree()
        for directory in ("usr", "usr/sbin", "etc", "etc/pico4"):
            tree.mkdir(directory)
        tree.add_file("usr/sbin/pico4-reboot", b"ELF reboot", 0o755)
        tree.add_file("etc/pico4/test-connect-timeout", b"600\n")
        stage1 = gzip.compress(tree.to_cpio(), mtime=0)
        dev = self.dev_recovery("androidboot.hardware=qcom pico4.part=recovery", stage1)
        image = bootimg.parse(recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev, debug=True))
        self.assertIn("initcall_debug", image.full_cmdline.split())
        # Stage 1 stays first; the appended archive moves the real binary aside and replaces it.
        self.assertTrue(image.ramdisk.startswith(stage1))
        added = dict(cpio.read_cpio(gzip.decompress(image.ramdisk[len(stage1) :])))
        self.assertEqual(added["usr/libexec/pico4/pico4-reboot-real"].data, b"ELF reboot")
        self.assertIn(b"/proc/sysrq-trigger", added["usr/sbin/pico4-reboot"].data)
        self.assertEqual(added["etc/pico4/test-connect-timeout"].data, b"330\n")
        plain = bootimg.parse(recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev))
        self.assertNotIn("initcall_debug", plain.full_cmdline.split())
        self.assertEqual(plain.ramdisk, stage1)

    def test_direct_has_no_stage1_and_starts_the_system_image(self):
        dev = self.dev_recovery("androidboot.hardware=qcom loop.max_part=7 pico4.test_run=dev-loop pico4.part=recovery")
        data = recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev, direct=True)
        # In recovery: the whole partition with the stock vbmeta, its own overlays, no ramdisk.
        self.assertEqual(len(data), len(stock_partition()))
        image = bootimg.parse(data)
        self.assertEqual((image.kernel, image.dtb, image.ramdisk), (b"new kernel", b"new dtb", b""))
        self.assertEqual(image.recovery_dtbo, b"overlays")
        words = image.full_cmdline.split()
        for word in recovery_image.DIRECT + recovery_image.DIRECT_ONESHOT:
            self.assertIn(word, words)
        # The kernel's own words of the base stay; stage 1's and Android's go.
        self.assertIn("loop.max_part=7", words)
        self.assertFalse([w for w in words if w.startswith(("pico4.test_run", "pico4.part", "pico4.modules"))])
        self.assertNotIn("androidboot.hardware=qcom", words)
        with self.assertRaises(ValueError):
            recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev, direct=True, overlay=self.tmp)

    def test_boot_is_the_installed_systems_own_image(self):
        dev = self.dev_recovery("pico4.test_run=dev-loop pico4.part=recovery")
        data = recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev, direct=True, boot=True)
        image = bootimg.parse(data)
        # A plain boot image (no AVB footer, no recovery overlays), no stage 1.
        self.assertEqual(data, image.pack())
        self.assertEqual(
            (image.kernel, image.dtb, image.recovery_dtbo, image.ramdisk), (b"new kernel", b"new dtb", b"", b"")
        )
        words = image.full_cmdline.split()
        self.assertIn("pico4.root=installed", words)
        # preinit's reboot into recovery after a failed start stays: recovery holds the dev image.
        self.assertNotIn("pico4.failover=reboot", words)
        with self.assertRaises(ValueError):
            recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev, boot=True)

    def test_direct_records_the_kernel_as_its_build(self):
        dev = self.dev_recovery("pico4.part=recovery")
        with mock.patch.object(recovery_image.kernelinfo, "analyse") as analyse:
            analyse.return_value.version = "Linux version 7.2.0-pico4 (x) # SMP"
            info = recovery_image.image_build_info(dev, b"new kernel", direct=True)
        self.assertEqual(info, {"kernel": "Linux version 7.2.0-pico4 (x) # SMP", "boot": "direct"})

    def test_stage1_overlay_replaces_the_bases_files(self):
        overlay = self.tmp / "initramfs"
        (overlay / "usr" / "bin").mkdir(parents=True)
        (overlay / "init").write_bytes(b"#!/bin/sh\r\necho new\r\n")
        (overlay / "usr" / "bin" / "pico4-install").write_bytes(b"#!/bin/sh\n")
        dev = self.dev_recovery("pico4.part=recovery")
        image = bootimg.parse(recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev, overlay=overlay))
        self.assertTrue(image.ramdisk.startswith(b"stage 1"))
        added = dict(cpio.read_cpio(gzip.decompress(image.ramdisk[len(b"stage 1") :])))
        self.assertEqual(added["init"].data, b"#!/bin/sh\necho new\n")
        self.assertTrue(added["usr/bin/pico4-install"].mode & 0o111)
        with self.assertRaises(ValueError):
            recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev, overlay=overlay / "usr")

    def test_gpu_firmware_goes_where_msm_and_the_tree_look(self):
        firmware = self.tmp / "graphics-firmware"
        firmware.mkdir()
        for name in ("a650_sqe.fw", "a650_gmu.bin", "a650_zap.mdt", "a650_zap.b00", "venus.mdt"):
            (firmware / name).write_bytes(name.encode())
        dev = self.dev_recovery("pico4.part=recovery")
        image = bootimg.parse(
            recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev, gpu_firmware=firmware)
        )
        self.assertTrue(image.ramdisk.startswith(b"stage 1"))
        added = dict(cpio.read_cpio(gzip.decompress(image.ramdisk[len(b"stage 1") :])))
        files = {path: node.data for path, node in added.items() if node.is_file}
        self.assertEqual(
            files,
            {
                "lib/firmware/qcom/a650_sqe.fw": b"a650_sqe.fw",
                "lib/firmware/qcom/a650_gmu.bin": b"a650_gmu.bin",
                "lib/firmware/qcom/sm8250/pico/phoenix/a650_zap.mdt": b"a650_zap.mdt",
                "lib/firmware/qcom/sm8250/pico/phoenix/a650_zap.b00": b"a650_zap.b00",
            },
        )
        (firmware / "a650_gmu.bin").unlink()
        with self.assertRaises(ValueError):
            recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev, gpu_firmware=firmware)

    def test_wlan_firmware_goes_where_ath11k_looks(self):
        firmware = self.tmp / "wlan" / "firmware" / "qca6390"
        firmware.mkdir(parents=True)
        for name in ("amss20.bin", "m3.bin", "bdwlanq.elf", "bdwlan.elf", "bdwlan.e01"):
            (firmware / name).write_bytes(name.encode())
        dev = self.dev_recovery("pico4.part=recovery")
        image = bootimg.parse(
            recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev, wlan_firmware=self.tmp / "wlan")
        )
        added = dict(cpio.read_cpio(gzip.decompress(image.ramdisk[len(b"stage 1") :])))
        files = {path: node.data for path, node in added.items() if node.is_file}
        self.assertEqual(
            files,
            {
                "lib/firmware/ath11k/QCA6390/hw2.0/amss.bin": b"amss20.bin",
                "lib/firmware/ath11k/QCA6390/hw2.0/m3.bin": b"m3.bin",
                "lib/firmware/ath11k/QCA6390/hw2.0/board.bin": b"bdwlanq.elf",
            },
        )
        (firmware / "bdwlanq.elf").unlink()
        with self.assertRaises(ValueError):
            recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev, wlan_firmware=self.tmp / "wlan")

    def test_extra_words_replace_their_keys(self):
        dev = self.dev_recovery("pico4.part=recovery panic=0")
        words = bootimg.parse(
            recovery_image.build(
                self.tmp / "out",
                self.tmp / "stock.img",
                dev,
                cmdline_words=["panel_pico4_ls026b3sa.wake_test_sleep_in=1", "panic=30"],
            )
        ).full_cmdline.split()
        self.assertIn("panel_pico4_ls026b3sa.wake_test_sleep_in=1", words)
        self.assertEqual([w for w in words if w.startswith("panic=")], ["panic=30"])

    def test_trace_records_one_function_graph(self):
        dev = self.dev_recovery("pico4.part=recovery")
        data = recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev, trace="qcom_smem_probe")
        words = bootimg.parse(data).full_cmdline.split()
        self.assertIn("ftrace_graph_filter=qcom_smem_probe", words)
        self.assertIn("ftrace_dump_on_oops", words)
        with self.assertRaises(ValueError):
            recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev, trace="a b")

    def test_stage1_build_info_names_the_new_kernel(self):
        tree = cpio.Tree()
        tree.mkdir("etc")
        tree.mkdir("etc/pico4")
        tree.add_file(
            "etc/pico4/build-info",
            json.dumps({"kernel": "Linux version 4.19.81-perf+ (stock)", "stock": "5.13.7"}).encode(),
        )
        kernel = gzip.compress(b"\0" * 64 + b"Linux version 7.2.0-pico4 (valve) #1 SMP\n\0", mtime=0)
        (self.tmp / "out" / "Image.gz").write_bytes(kernel)
        dev = self.dev_recovery("pico4.part=recovery", gzip.compress(tree.to_cpio(), mtime=0))
        image = bootimg.parse(recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev))
        expected = {"kernel": "Linux version 7.2.0-pico4 (valve) #1 SMP", "stock": "5.13.7"}
        added = dict(cpio.read_cpio(gzip.decompress(image.ramdisk[len(gzip.compress(tree.to_cpio(), mtime=0)) :])))
        self.assertEqual(json.loads(added["etc/pico4/build-info"].data), expected)
        out = self.tmp / "test.img"
        recovery_image.main([str(self.tmp / "out"), str(self.tmp / "stock.img"), str(dev), str(out)])
        record = json.loads(Path(f"{out}.json").read_text(encoding="utf-8"))
        self.assertEqual(record["build_info"], expected)
        self.assertEqual(record["recovery_sha256"], hashlib.sha256(out.read_bytes()).hexdigest())

    def test_only_the_dev_loops_recovery_image_is_a_base(self):
        dev = self.dev_recovery("androidboot.hardware=qcom pico4.test_run=dev-loop")
        with self.assertRaises(ValueError):
            recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev)


if __name__ == "__main__":
    unittest.main()
