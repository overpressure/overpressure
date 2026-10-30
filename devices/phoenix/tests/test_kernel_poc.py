"""The Frame-kernel prototype's host tools: the overlay sink and the one-shot recovery image."""
from __future__ import annotations

import gzip
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from overpressure import avb, bootimg, cpio
from devices.phoenix.kernel import dtbo_sink, panel_sequence, recovery_image


def fdt(nodes: dict) -> bytes:
    """A minimal flattened device tree: {node name: {property: value}} below the root."""
    strings, structure = b"", b""

    def name_offset(name: str) -> int:
        nonlocal strings
        raw = name.encode() + b"\0"
        if raw not in strings:
            strings += raw
        return strings.index(raw)

    def begin(name: str) -> bytes:
        raw = name.encode() + b"\0"
        return struct.pack(">I", 1) + raw + b"\0" * (-len(raw) % 4)

    structure += begin("")
    for node, props in nodes.items():
        structure += begin(node)
        for prop, value in props.items():
            value = value.encode() + b"\0"
            structure += struct.pack(">III", 3, len(value), name_offset(prop)) + value + b"\0" * (-len(value) % 4)
        structure += struct.pack(">I", 2)
    structure += struct.pack(">II", 2, 9)
    off_struct = 40 + 16  # header, then an empty memory reservation map
    off_strings = off_struct + len(structure)
    total = off_strings + len(strings)
    header = struct.pack(">IIIIIIIIII", 0xD00DFEED, total, off_struct, off_strings, 40, 17, 16, 0,
                         len(strings), len(structure))
    return header + b"\0" * 16 + structure + strings


def fdt_tree(tree: dict) -> bytes:
    """A flattened device tree from {name: bytes (a property) or dict (a child node)}."""
    strings, structure = b"", b""

    def begin(name: str) -> bytes:
        raw = name.encode() + b"\0"
        return struct.pack(">I", 1) + raw + b"\0" * (-len(raw) % 4)

    def node(name: str, content: dict) -> bytes:
        nonlocal strings
        out = begin(name)
        for key, value in content.items():
            if isinstance(value, bytes):
                raw = key.encode() + b"\0"
                if raw not in strings:
                    strings += raw
                out += struct.pack(">III", 3, len(value), strings.index(raw)) + value + b"\0" * (-len(value) % 4)
        for key, value in content.items():
            if isinstance(value, dict):
                out += node(key, value)
        return out + struct.pack(">I", 2)

    structure = node("", tree) + struct.pack(">I", 9)
    off_struct = 40 + 16
    off_strings = off_struct + len(structure)
    header = struct.pack(">IIIIIIIIII", 0xD00DFEED, off_strings + len(strings), off_struct, off_strings, 40, 17, 16,
                         0, len(strings), len(structure))
    return header + b"\0" * 16 + structure + strings


ON = bytes.fromhex("29 01 00 00 00 00 02 b0 00 05 01 00 00 78 00 01 11")
OFF = bytes.fromhex("05 01 00 00 32 00 01 28")


def panel_overlay(on: bytes = ON, off: bytes = OFF) -> bytes:
    timing = {"qcom,mdss-dsi-on-command": on, "qcom,mdss-dsi-off-command": off, "qcom,other": b"x"}
    return fdt_tree({"fragment@0": {"__overlay__": {"qcom,mdss_dsi_sharp_ls026b3sa_90_video": {
        "qcom,mdss-dsi-panel-name": b"sharp\0",
        "qcom,mdss-dsi-display-timings": {"timing@0": timing}}}}})


def dtbo(overlays: list) -> bytes:
    """An Android dtbo image holding the given overlays."""
    header, entry = 32, 32
    offset = header + entry * len(overlays)
    entries, blobs = b"", b""
    for blob in overlays:
        entries += struct.pack(">IIII", len(blob), offset + len(blobs), 0, 0) + b"\0" * 16
        blobs += blob
    total = offset + len(blobs)
    return struct.pack(">IIIIIIII", 0xD7B7AB1E, total, header, entry, len(overlays), header, 4096, 0) + entries + blobs


class DtboSinkTests(unittest.TestCase):
    def test_labels_of_every_overlay_resolve_into_the_sink(self):
        image = dtbo([
            fdt({"fragment@0": {"target": "x"}, "__fixups__": {"spmi_bus": "/fragment@0:target:0", "tlmm": "/a:b:0"}}),
            fdt({"__fixups__": {"tlmm": "/c:d:0", "CPU4": "/e:f:0"}}),
        ])
        labels = set()
        for overlay in dtbo_sink.overlays(image):
            labels |= dtbo_sink.fixup_labels(overlay)
        self.assertEqual(labels, {"spmi_bus", "tlmm", "CPU4"})
        text = dtbo_sink.sink(labels)
        # Own labels for the nodes, the overlay's names only in the symbol table.
        self.assertIn("pico4,refs = <&dtbo_CPU4 &dtbo_spmi_bus &dtbo_tlmm>;", text)
        self.assertIn("dtbo_tlmm: tlmm {", text)
        self.assertIn('tlmm = "/pico4-dtbo-sink/tlmm";', text)
        self.assertNotIn("compatible", text)

    def test_property_names_elsewhere_are_not_labels(self):
        self.assertEqual(dtbo_sink.fixup_labels(fdt({"node": {"__fixups__": "x"}})), set())

    def test_a_recovery_image_supplies_its_recovery_dtbo(self):
        image = dtbo([fdt({"__fixups__": {"soc": "/x:y:0"}})])
        boot = bootimg.BootImage(header_version=2, page_size=4096, kernel=b"k", ramdisk=b"r",
                                 recovery_dtbo=image, dtb=b"d")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recovery.img"
            path.write_bytes(boot.pack())
            self.assertEqual(dtbo_sink.dtbo_of(path), image)

    def test_odd_labels_and_other_data_are_refused(self):
        with self.assertRaises(ValueError):
            dtbo_sink.sink({"bad label"})
        with self.assertRaises(ValueError):
            dtbo_sink.overlays(b"\0" * 64)


class PanelSequenceTests(unittest.TestCase):
    def test_the_stock_sequences_become_the_drivers_properties(self):
        image = dtbo([fdt_tree({"other": {"x": b"y"}}), panel_overlay(), panel_overlay()])
        seqs = panel_sequence.sequences(image)
        self.assertEqual(seqs, {"qcom,mdss-dsi-on-command": ON, "qcom,mdss-dsi-off-command": OFF})
        self.assertEqual(panel_sequence.commands(ON), [(0x29, 0, b"\xb0\x00"), (0x05, 120, b"\x11")])
        text = panel_sequence.include(seqs)
        self.assertIn("&display_panel {", text)
        self.assertIn("pico4,on-command = [29 01 00 00 00 00 02 b0 00 05 01 00 00 78 00 01 11];", text)
        self.assertIn("pico4,off-command = [05 01 00 00 32 00 01 28];", text)

    def test_disagreeing_or_odd_sequences_are_refused(self):
        with self.assertRaises(ValueError):
            panel_sequence.sequences(dtbo([panel_overlay(), panel_overlay(off=ON)]))
        with self.assertRaises(ValueError):
            panel_sequence.sequences(dtbo([fdt_tree({"other": {"x": b"y"}})]))
        # A read (0x06) or a payload longer than the data are not a power-on sequence.
        for bad in (bytes.fromhex("06 01 00 00 00 00 01 0a"), bytes.fromhex("29 01 00 00 00 00 05 b0 00")):
            with self.assertRaises(ValueError):
                panel_sequence.commands(bad)


def stock_partition(total=64 * 4096) -> bytes:
    data = bytearray(total)
    vbmeta = b"AVB0" + b"\x11" * 252
    data[1000:1000 + len(vbmeta)] = vbmeta
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
        boot = bootimg.BootImage(header_version=2, page_size=4096, kernel=b"stock kernel", ramdisk=ramdisk,
                                 recovery_dtbo=b"overlays", dtb=b"stock dtb").with_cmdline(cmdline)
        path = self.tmp / "dev-recovery.img"
        path.write_bytes(avb.with_stock_vbmeta(stock_partition(), boot.pack()))
        return path

    def test_kernel_and_tree_replaced_stage1_kept(self):
        dev = self.dev_recovery("androidboot.hardware=qcom panic=0 pico4poc.test_run=dev-loop pico4poc.part=recovery")
        data = recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev)
        self.assertEqual(len(data), len(stock_partition()))
        self.assertEqual(avb.footer(data)[2], 256)
        image = bootimg.parse(data)
        self.assertEqual((image.kernel, image.dtb), (b"new kernel", b"new dtb"))
        self.assertEqual((image.ramdisk, image.recovery_dtbo), (b"stage 1", b"overlays"))
        words = image.full_cmdline.split()
        self.assertIn("pico4poc.part=recovery", words)
        self.assertIn("pico4poc.modules=0", words)
        # The prototype's own panic setting replaces the image's; Android's words go.
        self.assertEqual([w for w in words if w.startswith("panic=")], ["panic=5"])
        self.assertNotIn("androidboot.hardware=qcom", words)

    def test_a_command_line_beyond_the_abls_field_is_refused(self):
        dev = self.dev_recovery("pico4poc.part=recovery " + "x" * 500)
        with self.assertRaises(ValueError):
            recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev)

    def test_debug_adds_probe_logging_and_a_panic_for_fastboot(self):
        tree = cpio.Tree()
        for directory in ("usr", "usr/sbin", "etc", "etc/pico4-poc"):
            tree.mkdir(directory)
        tree.add_file("usr/sbin/pico4-reboot", b"ELF reboot", 0o755)
        tree.add_file("etc/pico4-poc/test-connect-timeout", b"600\n")
        stage1 = gzip.compress(tree.to_cpio(), mtime=0)
        dev = self.dev_recovery("androidboot.hardware=qcom pico4poc.part=recovery", stage1)
        image = bootimg.parse(recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev, debug=True))
        self.assertIn("initcall_debug", image.full_cmdline.split())
        # Stage 1 stays first; the appended archive moves the real binary aside and replaces it.
        self.assertTrue(image.ramdisk.startswith(stage1))
        added = dict(cpio.read_cpio(gzip.decompress(image.ramdisk[len(stage1):])))
        self.assertEqual(added["usr/libexec/pico4-poc/pico4-reboot-real"].data, b"ELF reboot")
        self.assertIn(b"/proc/sysrq-trigger", added["usr/sbin/pico4-reboot"].data)
        self.assertEqual(added["etc/pico4-poc/test-connect-timeout"].data, b"330\n")
        plain = bootimg.parse(recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev))
        self.assertNotIn("initcall_debug", plain.full_cmdline.split())
        self.assertEqual(plain.ramdisk, stage1)

    def test_installed_takes_the_installed_stage1_and_reboots_plain_for_recovery(self):
        def stage1(marker: bytes) -> bytes:
            tree = cpio.Tree()
            for directory in ("usr", "usr/sbin"):
                tree.mkdir(directory)
            tree.add_file("usr/sbin/pico4-reboot", b"ELF reboot", 0o755)
            tree.add_file("init", marker)
            return gzip.compress(tree.to_cpio(), mtime=0)
        dev = self.dev_recovery("pico4poc.test_run=dev-loop pico4poc.part=recovery", stage1(b"maintenance"))
        install = bootimg.BootImage(header_version=2, page_size=4096, kernel=b"stock kernel",
                                    ramdisk=stage1(b"installed"), dtb=b"stock dtb").with_cmdline(
            "androidboot.hardware=qcom pico4poc.root=installed pico4poc.autoreboot=0")
        (self.tmp / "install-boot.img").write_bytes(install.pack())
        image = bootimg.parse(recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev,
                                                   installed=self.tmp / "install-boot.img"))
        words = image.full_cmdline.split()
        self.assertIn("pico4poc.root=installed", words)
        self.assertNotIn("pico4poc.test_run=dev-loop", words)
        # The recovery partition's own overlays stay.
        self.assertEqual(image.recovery_dtbo, b"overlays")
        self.assertTrue(image.ramdisk.startswith(install.ramdisk))
        added = dict(cpio.read_cpio(gzip.decompress(image.ramdisk[len(install.ramdisk):])))
        wrapper = added["usr/sbin/pico4-reboot"].data
        self.assertIn(b'"$1" = recovery', wrapper)
        self.assertNotIn(b"/proc/sysrq-trigger", wrapper)
        self.assertEqual(added["usr/libexec/pico4-poc/pico4-reboot-real"].data, b"ELF reboot")
        # The maintenance stage 1 is no installed system's boot image.
        with self.assertRaises(ValueError):
            recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev, installed=dev)

    def test_boot_is_the_installed_systems_own_image(self):
        def stage1(marker: bytes) -> bytes:
            tree = cpio.Tree()
            for directory in ("usr", "usr/sbin"):
                tree.mkdir(directory)
            tree.add_file("usr/sbin/pico4-reboot", b"ELF reboot", 0o755)
            tree.add_file("init", marker)
            return gzip.compress(tree.to_cpio(), mtime=0)
        dev = self.dev_recovery("pico4poc.test_run=dev-loop pico4poc.part=recovery", stage1(b"maintenance"))
        install = bootimg.BootImage(header_version=2, page_size=4096, kernel=b"stock kernel",
                                    ramdisk=stage1(b"installed"), dtb=b"stock dtb").with_cmdline(
            "androidboot.hardware=qcom pico4poc.root=installed pico4poc.autoreboot=0")
        (self.tmp / "install-boot.img").write_bytes(install.pack())
        data = recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev,
                                    installed=self.tmp / "install-boot.img", boot=True)
        image = bootimg.parse(data)
        # A plain boot image (no AVB footer), the installed system's header, words and stage 1.
        self.assertEqual(data, image.pack())
        self.assertEqual((image.kernel, image.dtb, image.recovery_dtbo), (b"new kernel", b"new dtb", b""))
        self.assertIn("pico4poc.root=installed", image.full_cmdline.split())
        self.assertIn("pico4poc.kernel=valve", image.full_cmdline.split())
        # stage 1's reboot into recovery after a failed start stays: recovery holds the dev image.
        self.assertEqual(image.ramdisk, install.ramdisk)
        with self.assertRaises(ValueError):
            recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev, boot=True)

    def test_stage1_overlay_replaces_the_bases_files(self):
        overlay = self.tmp / "initramfs"
        (overlay / "usr" / "bin").mkdir(parents=True)
        (overlay / "init").write_bytes(b"#!/bin/sh\r\necho new\r\n")
        (overlay / "usr" / "bin" / "pico4-userspace").write_bytes(b"#!/bin/sh\n")
        dev = self.dev_recovery("pico4poc.part=recovery")
        image = bootimg.parse(recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev, overlay=overlay))
        self.assertTrue(image.ramdisk.startswith(b"stage 1"))
        added = dict(cpio.read_cpio(gzip.decompress(image.ramdisk[len(b"stage 1"):])))
        self.assertEqual(added["init"].data, b"#!/bin/sh\necho new\n")
        self.assertTrue(added["usr/bin/pico4-userspace"].mode & 0o111)
        self.assertNotIn("usr/sbin/pico4-splash", added)
        # The prebuilt splash beside the overlay comes along.
        (self.tmp / "prebuilt" / "aarch64").mkdir(parents=True)
        (self.tmp / "prebuilt" / "aarch64" / "pico4-splash").write_bytes(b"ELF splash")
        image = bootimg.parse(recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev, overlay=overlay))
        added = dict(cpio.read_cpio(gzip.decompress(image.ramdisk[len(b"stage 1"):])))
        self.assertEqual(added["usr/sbin/pico4-splash"].data, b"ELF splash")
        self.assertTrue(added["usr/sbin/pico4-splash"].mode & 0o111)
        with self.assertRaises(ValueError):
            recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev, overlay=overlay / "usr")

    def test_gpu_firmware_goes_where_msm_and_the_tree_look(self):
        firmware = self.tmp / "graphics-firmware"
        firmware.mkdir()
        for name in ("a650_sqe.fw", "a650_gmu.bin", "a650_zap.mdt", "a650_zap.b00", "venus.mdt"):
            (firmware / name).write_bytes(name.encode())
        dev = self.dev_recovery("pico4poc.part=recovery")
        image = bootimg.parse(recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev,
                                                   gpu_firmware=firmware))
        self.assertTrue(image.ramdisk.startswith(b"stage 1"))
        added = dict(cpio.read_cpio(gzip.decompress(image.ramdisk[len(b"stage 1"):])))
        files = {path: node.data for path, node in added.items() if node.is_file}
        self.assertEqual(files, {
            "lib/firmware/qcom/a650_sqe.fw": b"a650_sqe.fw",
            "lib/firmware/qcom/a650_gmu.bin": b"a650_gmu.bin",
            "lib/firmware/qcom/sm8250/pico/phoenix/a650_zap.mdt": b"a650_zap.mdt",
            "lib/firmware/qcom/sm8250/pico/phoenix/a650_zap.b00": b"a650_zap.b00",
        })
        (firmware / "a650_gmu.bin").unlink()
        with self.assertRaises(ValueError):
            recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev, gpu_firmware=firmware)

    def test_wlan_firmware_goes_where_ath11k_looks(self):
        firmware = self.tmp / "wlan" / "firmware" / "qca6390"
        firmware.mkdir(parents=True)
        for name in ("amss20.bin", "m3.bin", "bdwlanq.elf", "bdwlan.elf", "bdwlan.e01"):
            (firmware / name).write_bytes(name.encode())
        dev = self.dev_recovery("pico4poc.part=recovery")
        image = bootimg.parse(recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev,
                                                   wlan_firmware=self.tmp / "wlan"))
        added = dict(cpio.read_cpio(gzip.decompress(image.ramdisk[len(b"stage 1"):])))
        files = {path: node.data for path, node in added.items() if node.is_file}
        self.assertEqual(files, {
            "lib/firmware/ath11k/QCA6390/hw2.0/amss.bin": b"amss20.bin",
            "lib/firmware/ath11k/QCA6390/hw2.0/m3.bin": b"m3.bin",
            "lib/firmware/ath11k/QCA6390/hw2.0/board.bin": b"bdwlanq.elf",
        })
        (firmware / "bdwlanq.elf").unlink()
        with self.assertRaises(ValueError):
            recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev, wlan_firmware=self.tmp / "wlan")

    def test_extra_words_replace_their_keys(self):
        dev = self.dev_recovery("pico4poc.part=recovery panic=0")
        words = bootimg.parse(recovery_image.build(
            self.tmp / "out", self.tmp / "stock.img", dev,
            cmdline_words=["panel_pico4_ls026b3sa.keep_boot_state=1", "panic=30"])).full_cmdline.split()
        self.assertIn("panel_pico4_ls026b3sa.keep_boot_state=1", words)
        self.assertEqual([w for w in words if w.startswith("panic=")], ["panic=30"])

    def test_trace_records_one_function_graph(self):
        dev = self.dev_recovery("pico4poc.part=recovery")
        data = recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev, trace="qcom_smem_probe")
        words = bootimg.parse(data).full_cmdline.split()
        self.assertIn("ftrace_graph_filter=qcom_smem_probe", words)
        self.assertIn("ftrace_dump_on_oops", words)
        with self.assertRaises(ValueError):
            recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev, trace="a b")

    def test_stage1_build_info_names_the_new_kernel(self):
        tree = cpio.Tree()
        tree.mkdir("etc")
        tree.mkdir("etc/pico4-poc")
        tree.add_file("etc/pico4-poc/build-info", json.dumps({"kernel": "Linux version 4.19.81-perf+ (stock)",
                                                               "stock": "5.13.7"}).encode())
        kernel = gzip.compress(b"\0" * 64 + b"Linux version 7.2.0-pico4 (valve) #1 SMP\n\0", mtime=0)
        (self.tmp / "out" / "Image.gz").write_bytes(kernel)
        dev = self.dev_recovery("pico4poc.part=recovery", gzip.compress(tree.to_cpio(), mtime=0))
        image = bootimg.parse(recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev))
        expected = {"kernel": "Linux version 7.2.0-pico4 (valve) #1 SMP", "stock": "5.13.7"}
        added = dict(cpio.read_cpio(gzip.decompress(image.ramdisk[len(gzip.compress(tree.to_cpio(), mtime=0)):])))
        self.assertEqual(json.loads(added["etc/pico4-poc/build-info"].data), expected)
        out = self.tmp / "test.img"
        recovery_image.main([str(self.tmp / "out"), str(self.tmp / "stock.img"), str(dev), str(out)])
        record = json.loads(Path(f"{out}.json").read_text(encoding="utf-8"))
        self.assertEqual(record["build_info"], expected)
        self.assertEqual(record["recovery_sha256"], hashlib.sha256(out.read_bytes()).hexdigest())

    def test_only_the_dev_loops_recovery_image_is_a_base(self):
        dev = self.dev_recovery("androidboot.hardware=qcom pico4poc.test_run=dev-loop")
        with self.assertRaises(ValueError):
            recovery_image.build(self.tmp / "out", self.tmp / "stock.img", dev)


if __name__ == "__main__":
    unittest.main()
