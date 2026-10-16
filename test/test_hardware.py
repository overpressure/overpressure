import argparse
import contextlib
import gzip
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import bootimg, cpio, hardware_test as hw


def report(run_id):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, content in (("summary.txt", b"kernel"), ("system.txt", run_id.encode()), ("dmesg.txt", b"boot")):
            entry = tarfile.TarInfo("report/" + name)
            entry.size = len(content)
            archive.addfile(entry, io.BytesIO(content))
    return output.getvalue()


class ValidationTests(unittest.TestCase):
    def test_preflight_failures_and_missing_summary(self):
        hw.validate_preflight("ok test\nPREFLIGHT: 20 passed, 0 failed\n")
        for value in ("PREFLIGHT: 19 passed, 1 failed", "PREFLIGHT: 0 passed, 0 failed", "", "truncated",
                      "PREFLIGHT: 20 passed, 0 failed\nPREFLIGHT: 20 passed, 0 failed"):
            with self.assertRaises(ValueError):
                hw.validate_preflight(value)

    def test_storage_checks_partitions_not_just_disks(self):
        hw.validate_storage("BLOCK sda 1\nBLOCK sda1 1\nBLOCK loop0 0\nBLOCK ram0 0")
        for value in ("BLOCK sda 1\nBLOCK sda1 0", "BLOCK sda 0", "BLOCK loop0 1", "", "BLOCK sda ?"):
            with self.assertRaises(ValueError):
                hw.validate_storage(value)

    def test_reports_must_identify_this_boot(self):
        hw.validate_report(report("current"), "current")
        with self.assertRaises(ValueError):
            hw.validate_report(report("old"), "current")
        with self.assertRaises(tarfile.TarError):
            hw.validate_report(b"not a report", "current")

    def test_mounts_cannot_hide_writable_storage_or_disk_root(self):
        hw.validate_mounts("rootfs / rootfs rw 0 0\n/dev/sda1 /mnt rofs ro 0 0")
        for value in ("", "proc /proc proc rw 0 0", "/dev/root / ext4 ro 0 0",
                      "rootfs / rootfs rw 0 0\n/dev/sda1 /mnt ext4 rw 0 0"):
            with self.assertRaises(ValueError):
                hw.validate_mounts(value)

    def test_fastboot_variables_reject_missing_and_ambiguous_values(self):
        self.assertEqual(hw.fastboot_value("(bootloader) partition-size:boot: 0x6000000\nOKAY",
                                          "partition-size:boot"), "0x6000000")
        for output in ("", "has-slot:boot: no\nhas-slot:boot: yes", "has-slot:boot: \nOKAY"):
            with self.assertRaises(ValueError):
                hw.fastboot_value(output, "has-slot:boot")


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "poc.img"
        self.info = {"kernel": "Linux version 4.19.81 test", "stock": "test"}
        tree = cpio.Tree()
        tree.add_file("etc/pico4-poc/build-info", json.dumps(self.info).encode())
        tree.add_file("init", b"#!/bin/sh\n", 0o755)
        tree.mkdir("root", 0o700)
        img = bootimg.BootImage(2, 4096, b"kernel", gzip.compress(tree.to_cpio()), dtb=b"dtb")
        self.source.write_bytes(img.pack())
        self.args = argparse.Namespace(command="run", serial="ABC", fastboot_serial=None, adb="adb",
                fastboot="fastboot", ssh="ssh", ssh_keygen="ssh-keygen", image=self.source,
                out=self.root / "results", host="172.16.42.1", boot_timeout=1, report_timeout=1,
                recovery_timeout=3)
        self.runner = hw.Runner(self.args)
        self.calls = []
        self.stock = True
        self.scenario = None
        self.restored = False

    def enable_flash_mode(self):
        self.args.command = "flash-run"
        self.args.backup = self.root / "backup"
        self.args.confirm_device = "ABC"
        self.args.out = self.root / "flash-results"
        self.args.backup.mkdir()
        stock = bootimg.parse(self.source.read_bytes())
        stock.ramdisk = b"original stock ramdisk"
        (self.args.backup / "boot.bin").write_bytes(stock.pack().ljust(65536, b"\0"))
        self.backup_manifest = {"schema": 1, "identity": {"schema": 1, "serial": "ABC",
            "fingerprint": "Pico/Phoenix_ovs/PICOA8110:10/5.13.8/test:user/dev-keys",
            "oem_state": "false", "chip_id": 1234567},
            "files": {"boot.bin": hw.device.digest(self.args.backup / "boot.bin")}}
        self.save_backup_manifest()
        self.runner = hw.Runner(self.args)

    def save_backup_manifest(self):
        (self.args.backup / "manifest.json").write_text(json.dumps(self.backup_manifest))

    def fake_command(self, argv, timeout=30):
        argv = list(map(str, argv))
        self.calls.append(argv)
        if argv[0] == "ssh-keygen":
            (self.runner.folder / "id_ed25519").write_text("private")
            (self.runner.folder / "id_ed25519.pub").write_text("ssh-ed25519 AAAATEST")
            return ""
        if argv[0] == "adb":
            if "getprop ro.build.fingerprint" in argv[-1]:
                if not self.stock or self.scenario == "recovery" and self.runner.moved:
                    raise ValueError("Android did not return")
                return "Pico/Phoenix_ovs/PICOA8110:10/5.13.8/test:user/dev-keys\nfalse\n1234567\n1"
            if "preflight.sh" in argv[-1] and "PICO4_PREFLIGHT_DIR=" in argv[-1]:
                return "PREFLIGHT: 20 passed, 1 failed" if self.scenario == "preflight" else "PREFLIGHT: 21 passed, 0 failed"
            if argv[-2:] == ["reboot", "bootloader"]:
                self.stock = False
            return ""
        if argv[0] == "fastboot":
            if argv[-1] == "devices":
                return "ABC fastboot"
            if argv[-1] == "device-info":
                return "Device unlocked: false" if self.scenario == "locked" else "Device unlocked: true"
            if argv[-2:] == ["getvar", "has-slot:boot"]:
                return "has-slot:boot: " + ("yes" if self.scenario == "slotted" else "no")
            if argv[-2:] == ["getvar", "partition-size:boot"]:
                return "partition-size:boot: " + ("0x20000" if self.scenario == "size" else "0x10000")
            if argv[3:5] == ["flash", "boot"]:
                restoring = Path(argv[-1]).name == "stock-boot.img"
                journal = json.loads((self.runner.folder / "result.json").read_text())
                self.assertTrue(journal["boot_restore_required"])
                self.assertTrue(journal["manual_restore_commands"])
                if self.scenario == "interrupted-write" and not restoring:
                    raise KeyboardInterrupt()
                if self.scenario == "restore-write" and restoring or self.scenario == "flash-write" and not restoring:
                    raise ValueError("flash failed after partial write")
                if restoring:
                    self.restored = True
                elif self.scenario == "restore-tamper":
                    Path(self.runner.result["stock_boot"]["path"]).write_bytes(b"changed")
            if "boot" in argv and self.scenario == "boot":
                raise ValueError("boot unsupported")
            if argv[-1] == "reboot":
                self.stock = self.restored if self.runner.flash_mode and self.runner.boot_attempted else True
            return "OKAY"
        if argv[0] == "ssh":
            command = argv[-1]
            if "test-run" in command:
                return ("wrong-run" if self.scenario == "identity" else self.runner.run_id) + "\n1234567"
            if command == "cat /etc/pico4-poc/build-info":
                return json.dumps({} if self.scenario == "build" else self.info)
            if command == "uname -m":
                return "aarch64"
            if command == "uname -r":
                return "4.19.81"
            if command == "id -u":
                return "0"
            if "SHELL-" in command:
                return "SHELL-42"
            if command.startswith("for d in /sys/class/block"):
                return "BLOCK sda 1\nBLOCK sda1 " + ("0" if self.scenario == "storage" else "1")
            if command == "cat /proc/mounts":
                return "rootfs / rootfs rw 0 0"
            if command.startswith("head -c "):
                return ("bad" if self.scenario == "readback" else self.runner.result["boot_sha256"]) + "  -"
            if command.startswith("test -s"):
                return "READY"
            if "pico4-reboot" in command:
                self.stock = "bootloader" not in command
            return "boot log"
        raise AssertionError(argv)

    def fake_http(self, endpoint, timeout=15):
        if self.scenario == "http":
            raise OSError("HTTP unavailable")
        if endpoint == "cgi-bin/status":
            return b"PICO 4 Linux POC"
        if endpoint == "cgi-bin/log?boot":
            return self.runner.run_id.encode()
        return report(self.runner.run_id)

    def execute(self):
        with patch.object(self.runner, "command", side_effect=self.fake_command), \
                patch.object(self.runner, "http", side_effect=self.fake_http), \
                contextlib.redirect_stdout(io.StringIO()):
            return self.runner.execute()

    def test_success_preserves_image_and_collects_verified_recovery(self):
        before = self.source.read_bytes()
        self.assertEqual(self.execute(), 0)
        self.assertEqual(self.source.read_bytes(), before)
        result = json.loads((self.runner.folder / "result.json").read_text())
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["recovery"], "verified")
        self.assertEqual(result["identity"]["chip_id"], 1234567)
        self.assertNotEqual(result["boot_sha256"], result["source_sha256"])
        self.assertFalse((self.runner.folder / "id_ed25519").exists())
        self.assertTrue((self.runner.folder / "report.tar.gz").exists())
        for cmd in self.calls:
            self.assertNotIn("flash", cmd)
            self.assertNotIn("unlock", cmd)
            if cmd[0] in ("adb", "fastboot") and cmd[-1] != "devices":
                self.assertEqual(cmd[1:3], ["-s", "ABC"])

    def test_preflight_failure_never_reboots(self):
        self.scenario = "preflight"
        self.assertEqual(self.execute(), 1)
        self.assertFalse(any("reboot" in command for command in self.calls))
        self.assertEqual(self.runner.result["recovery"], "not_needed")

    def test_lock_failure_returns_to_stock(self):
        self.scenario = "locked"
        self.assertEqual(self.execute(), 1)
        self.assertFalse(self.runner.boot_attempted)
        self.assertEqual(self.runner.result["recovery"], "verified")

    def test_unsupported_ram_boot_is_failure_even_after_recovery(self):
        self.scenario = "boot"
        self.assertEqual(self.execute(), 1)
        self.assertEqual(self.runner.result["recovery"], "verified")
        self.assertIn("unsupported", self.runner.result["error"])

    def test_writable_partition_fails_and_recovers(self):
        self.scenario = "storage"
        self.assertEqual(self.execute(), 1)
        self.assertEqual(self.runner.result["recovery"], "verified")
        self.assertTrue(any("pico4-reboot" in command[-1] for command in self.calls))

    def test_build_mismatch_fails(self):
        self.scenario = "build"
        self.assertEqual(self.execute(), 1)
        self.assertIn("metadata", self.runner.result["error"])

    def test_http_failure_still_collects_recovery_log(self):
        self.scenario = "http"
        self.assertEqual(self.execute(), 1)
        self.assertTrue((self.runner.folder / "recovery-boot.log").exists())
        self.assertEqual(self.runner.result["recovery"], "verified")

    def test_wrong_ssh_target_is_never_rebooted(self):
        self.scenario = "identity"
        self.assertEqual(self.execute(), 1)
        self.assertFalse(any("pico4-reboot" in command[-1] for command in self.calls))

    def test_recovery_failure_is_not_a_pass(self):
        self.scenario = "recovery"
        self.assertEqual(self.execute(), 1)
        self.assertEqual(self.runner.result["recovery"], "failed")

    def test_image_contains_only_expected_instrumentation(self):
        result = hw.prepare_image(self.source, self.runner.folder, "nonce", b"ssh-ed25519 TEST")
        image = bootimg.parse((self.runner.folder / "test-boot.img").read_bytes())
        entries = dict(cpio.read_cpio(gzip.decompress(image.ramdisk)))
        self.assertEqual(image.kernel, b"kernel")
        self.assertEqual(image.dtb, b"dtb")
        self.assertIn("pico4poc.test_run=nonce", image.full_cmdline)
        self.assertEqual(entries["root/.ssh/authorized_keys"].data, b"ssh-ed25519 TEST\n")
        self.assertEqual(entries["root/.ssh/authorized_keys"].mode & 0o777, 0o600)
        self.assertEqual(result["build_info"], self.info)

    def test_rejects_writable_and_qemu_images(self):
        original = bootimg.parse(self.source.read_bytes())
        for option in ("pico4poc.rw=1", "pico4poc.rw", "pico4poc.selftest=1", "pico4poc.selftest"):
            self.source.write_bytes(original.with_cmdline(option).pack())
            with self.assertRaises(ValueError):
                hw.prepare_image(self.source, self.runner.folder, "nonce", b"ssh-ed25519 TEST")

    def test_subprocess_failure_and_timeout_are_logged(self):
        with patch.object(hw.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"device missing")):
            with self.assertRaises(ValueError):
                self.runner.command(["fake"])
        self.assertIn("device missing", (self.runner.folder / "command-0001.log").read_text())
        with patch.object(hw.subprocess, "run", side_effect=subprocess.TimeoutExpired("fake", 1, output=b"partial")):
            with self.assertRaises(ValueError):
                self.runner.command(["fake"], timeout=1)
        self.assertIn("TIMEOUT", (self.runner.folder / "command-0002.log").read_text())

    def test_existing_results_are_never_overwritten(self):
        with self.assertRaises(FileExistsError):
            hw.Runner(self.args)

    def test_explicit_flash_run_restores_original_boot_and_verifies_android(self):
        self.enable_flash_mode()
        self.assertEqual(self.execute(), 0)
        flashes = [cmd for cmd in self.calls if cmd[0] == "fastboot" and cmd[3:5] == ["flash", "boot"]]
        self.assertEqual([Path(cmd[-1]).name for cmd in flashes], ["test-boot.img", "stock-boot.img"])
        self.assertEqual(self.runner.result["boot_restore"], "android_boot_verified")
        self.assertFalse(self.runner.result["boot_restore_required"])
        self.assertEqual((self.runner.folder / "stock-boot.img").read_bytes(),
                         (self.args.backup / "boot.bin").read_bytes())
        self.assertTrue((self.runner.folder / "boot-readback.txt").exists())
        self.assertTrue(any("pico4-reboot bootloader" in cmd[-1] for cmd in self.calls))
        self.assertFalse(any("flashing" in cmd or "erase" in cmd for cmd in self.calls))

    def test_partial_boot_write_still_restores_stock(self):
        self.enable_flash_mode()
        self.scenario = "flash-write"
        self.assertEqual(self.execute(), 1)
        self.assertTrue(self.restored)
        self.assertEqual(self.runner.result["recovery"], "verified")
        self.assertFalse(self.runner.result["boot_restore_required"])

    def test_interrupted_boot_write_still_restores_stock(self):
        self.enable_flash_mode()
        self.scenario = "interrupted-write"
        self.assertEqual(self.execute(), 1)
        self.assertTrue(self.restored)
        self.assertEqual(self.runner.result["recovery"], "verified")
        self.assertFalse(self.runner.result["boot_restore_required"])
        self.assertFalse(any(check["status"] == "running" for check in self.runner.result["checks"]))

    def test_failed_stock_write_preserves_recovery_requirement(self):
        self.enable_flash_mode()
        self.scenario = "restore-write"
        self.assertEqual(self.execute(), 1)
        self.assertTrue(self.runner.result["boot_restore_required"])
        self.assertEqual(self.runner.result["recovery"], "failed")
        # Only the initial POC boot may reboot; a failed restoration stays in fastboot.
        self.assertEqual(sum(cmd[0] == "fastboot" and cmd[-1] == "reboot" for cmd in self.calls), 1)
        self.assertTrue(self.runner.result["manual_restore_commands"])

    def test_changed_pinned_restore_image_is_never_flashed(self):
        self.enable_flash_mode()
        self.scenario = "restore-tamper"
        self.assertEqual(self.execute(), 1)
        self.assertTrue(self.runner.result["boot_restore_required"])
        self.assertIn("changed", self.runner.result["recovery_error"])
        self.assertFalse(self.restored)

    def test_readback_mismatch_fails_and_restores(self):
        self.enable_flash_mode()
        self.scenario = "readback"
        self.assertEqual(self.execute(), 1)
        self.assertIn("readback", self.runner.result["error"])
        self.assertEqual(self.runner.result["recovery"], "verified")

    def test_wrong_backup_identity_never_reboots(self):
        self.enable_flash_mode()
        self.backup_manifest["identity"]["chip_id"] += 1
        self.save_backup_manifest()
        self.assertEqual(self.execute(), 1)
        self.assertIn("identity", self.runner.result["error"])
        self.assertFalse(self.runner.moved)

    def test_backup_tampering_never_reboots(self):
        self.enable_flash_mode()
        (self.args.backup / "boot.bin").write_bytes(b"changed")
        self.assertEqual(self.execute(), 1)
        self.assertIn("integrity", self.runner.result["error"])
        self.assertFalse(self.runner.moved)

    def test_different_stock_kernel_never_reboots(self):
        self.enable_flash_mode()
        stock = bootimg.parse((self.args.backup / "boot.bin").read_bytes())
        stock.kernel = b"different kernel"
        (self.args.backup / "boot.bin").write_bytes(stock.pack().ljust(65536, b"\0"))
        self.backup_manifest["files"]["boot.bin"] = hw.device.digest(self.args.backup / "boot.bin")
        self.save_backup_manifest()
        self.assertEqual(self.execute(), 1)
        self.assertIn("kernel", self.runner.result["error"])
        self.assertFalse(self.runner.moved)

    def test_live_partition_size_mismatch_never_flashes(self):
        self.enable_flash_mode()
        self.scenario = "size"
        self.assertEqual(self.execute(), 1)
        self.assertIn("size", self.runner.result["error"])
        self.assertFalse(any("flash" in cmd for cmd in self.calls))
        self.assertEqual(self.runner.result["recovery"], "verified")

    def test_slotted_partition_never_flashes(self):
        self.enable_flash_mode()
        self.scenario = "slotted"
        self.assertEqual(self.execute(), 1)
        self.assertIn("non-slotted", self.runner.result["error"])
        self.assertFalse(any("flash" in cmd for cmd in self.calls))
        self.assertEqual(self.runner.result["recovery"], "verified")

    def test_flash_confirmation_mismatch_never_reboots(self):
        self.enable_flash_mode()
        self.args.confirm_device = "WRONG"
        self.assertEqual(self.execute(), 1)
        self.assertIn("confirm-device", self.runner.result["error"])
        self.assertFalse(self.runner.moved)


if __name__ == "__main__":
    unittest.main()
