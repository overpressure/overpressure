# SPDX-License-Identifier: Unlicense
"""The hardware test's runner and its checks, against a fake headset, fastboot, adb, SSH and HTTP."""

import argparse
import contextlib
import gzip
import io
import json
import os
import tarfile
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, cast
from unittest.mock import patch

from devices.phoenix.tooling import devloop
from devices.phoenix.tooling import hardware_test as hw
from overpressure import bootimg, cpio, evidence


def report(run_id: str) -> bytes:
    """Build the headset's report archive for this run."""
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
        for value in (
            "PREFLIGHT: 19 passed, 1 failed",
            "PREFLIGHT: 0 passed, 0 failed",
            "",
            "truncated",
            "PREFLIGHT: 20 passed, 0 failed\nPREFLIGHT: 20 passed, 0 failed",
        ):
            with self.assertRaises(ValueError):
                hw.validate_preflight(value)

    def test_reports_must_identify_this_boot(self):
        hw.validate_report(report("current"), "current")
        with self.assertRaises(ValueError):
            hw.validate_report(report("old"), "current")
        with self.assertRaises(tarfile.TarError):
            hw.validate_report(b"not a report", "current")

    def test_fastboot_variables_reject_missing_and_ambiguous_values(self):
        self.assertEqual(
            hw.fastboot_value("(bootloader) partition-size:boot: 0x6000000\nOKAY", "partition-size:boot"), "0x6000000"
        )
        for output in ("", "has-slot:boot: no\nhas-slot:boot: yes", "has-slot:boot: \nOKAY"):
            with self.assertRaises(ValueError):
                hw.fastboot_value(output, "has-slot:boot")


class FakeClock:
    """Virtual time for the runner: waits and deadlines pass without real sleeping."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)


class HarnessTests(unittest.TestCase):
    INFO: ClassVar[dict] = {"kernel": "Linux version 4.19.81 test", "stock": "test"}

    @classmethod
    def setUpClass(cls) -> None:
        # One source image for every test: Windows' real-time scan of a freshly written
        # boot image costs about a quarter of a second on its first read.
        cls.shared = tempfile.TemporaryDirectory()
        cls.image = Path(cls.shared.name) / "poc.img"
        tree = cpio.Tree()
        tree.add_file("etc/pico4/build-info", json.dumps(cls.INFO).encode())
        tree.add_file("init", b"#!/bin/sh\n", 0o755)
        tree.add_file("usr/libexec/pico4/recovery-watch", b"#!/bin/sh\n", 0o755)
        tree.add_file(
            "etc/inittab",
            b"::respawn:/usr/libexec/pico4/svc recovery-watch\n::restart:/usr/libexec/pico4/switch-userspace\n",
        )
        tree.add_file("usr/bin/pico4-userspace", b"#!/bin/sh\n", 0o755)
        tree.add_file("usr/libexec/pico4/pico4-init", b"#!/bin/sh\n", 0o755)
        tree.mkdir("root", 0o700)
        cls.image.write_bytes(bootimg.BootImage(2, 4096, b"kernel", gzip.compress(tree.to_cpio()), dtb=b"dtb").pack())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.shared.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.image
        self.info = dict(self.INFO)
        self.clock = FakeClock()
        for module in (hw, evidence):
            clock = patch.object(module, "time", self.clock)
            clock.start()
            self.addCleanup(clock.stop)
        self.args = argparse.Namespace(
            command="flash-run",
            serial="ABC",
            fastboot_serial=None,
            adb="adb",
            fastboot="fastboot",
            ssh="ssh",
            ssh_keygen="ssh-keygen",
            image=self.source,
            out=self.root / "flash-results",
            host="172.16.42.1",
            boot_timeout=1,
            report_timeout=1,
            recovery_timeout=3,
            backup=self.root / "backup",
            confirm_device="ABC",
        )
        self.calls = []
        self.stock = True
        self.scenario = None
        self.restored = False
        # The verified backup that flash-run restores the stock boot image from.
        self.args.backup.mkdir()
        stock = bootimg.parse(self.source.read_bytes())
        stock.ramdisk = b"original stock ramdisk"
        (self.args.backup / "boot.bin").write_bytes(stock.pack().ljust(65536, b"\0"))
        self.backup_manifest: dict[str, Any] = {
            "schema": 1,
            "identity": {
                "schema": 1,
                "serial": "ABC",
                "fingerprint": "Pico/Phoenix_ovs/PICOA8110:10/5.13.7/test:user/dev-keys",
                "oem_state": "true",
                "chip_id": 1234567,
            },
            "files": {"boot.bin": hw.backups.digest(self.args.backup / "boot.bin")},
        }
        self.save_backup_manifest()
        self.runner = hw.Runner(self.args)

    def userspace_check(self):
        value = {
            "schema": 1,
            "status": "passed",
            "errors": [],
            "systemd": {"pid1": "/usr/lib/systemd/systemd", "version": "257.7", "state": "running", "failed_units": []},
            "dev": {"fstype": "devtmpfs", "missing_nodes": [], "udev_devices": 300},
            "storage": {"root": "/dev/sda7", "installed": True, "writable_block_devices": []},
            "seats": ["seat0"],
            "usb_link": {"status": "up", "now": "usb0 02:00:00:00:00:01 up 172.16.42.1/24"},
            "session": {
                "user": "steamos",
                "user_state": "active",
                "service": "active",
                "target": "gamescope-session.target",
                "target_state": "active",
                "failed_units": [],
                "monado_socket": "active",
                "monado_service": "inactive",
            },
            "fan": {"service": "active", "state": self.FAN_STATE},
            "vulkan": {"status": "skipped", "result": None},
        }
        if self.scenario == "userspace-check":
            value.update(status="failed", errors=["no failed units"])
            value["systemd"].update(state="degraded", failed_units=["sshd.service"])
        return json.dumps(value) + "\nEXIT=" + ("1" if value["status"] == "failed" else "0")

    def save_backup_manifest(self):
        (self.args.backup / "manifest.json").write_text(json.dumps(self.backup_manifest))

    # `pico4-fan state` (docs/cooling.md).
    FAN_STATE: ClassVar[dict] = {
        "profile": "late",
        "duty": 30,
        "reason": "table",
        "boost": False,
        "rpm": 5700,
        "hottest": "cpu-1-4-usr",
        "hottest_mean_c": 61.2,
        "error": None,
    }

    # What systemctl writes to stderr when it stops a socket-activated service.
    SYSTEMCTL_NOTE = (
        "Stopping 'pico4-monado.service', but its triggering units are still active:\npico4-monado.socket\n"
    )

    def fake_command(self, argv: Sequence[object], timeout: float = 30) -> str:
        output = self.fake_device(argv, timeout)
        if str(argv[0]) == "ssh" and isinstance(output, str):
            # As on the headset: SSH passes systemctl's note on unless the command discards it.
            command = str(argv[-1])
            stops = command.count("systemctl stop pico4-monado.service")
            if stops > command.count("systemctl stop pico4-monado.service 2>/dev/null") or (
                command.startswith("pico4-valve-session-check") and "2>" not in command
            ):
                output = self.SYSTEMCTL_NOTE + output
        return output

    def fake_device(  # noqa: C901, PLR0911, PLR0912, PLR0915  # a fake headset: one branch per command it answers
        self, command_line: Sequence[object], _timeout: float = 30
    ) -> str:
        argv = list(map(str, command_line))
        self.calls.append(argv)
        if argv[0] == "ncm-python":
            if argv[-1] == "--check":
                if self.scenario == "ncm-dependencies":
                    msg = "NCM dependencies missing"
                    raise ValueError(msg)
                return json.dumps({"status": "ready"})
            if self.scenario == "ncm-device":
                msg = "NCM gadget did not appear"
                raise ValueError(msg)
            return json.dumps({"status": "initialized", "serial": self.args.fastboot_serial or self.args.serial})
        if argv[0] == "ssh-keygen":
            (self.runner.folder / "id_ed25519").write_text("private")
            (self.runner.folder / "id_ed25519.pub").write_text("ssh-ed25519 AAAATEST")
            return ""
        if argv[0] == "adb":
            if "getprop ro.build.fingerprint" in argv[-1]:
                if not self.stock or (self.scenario == "recovery" and self.runner.moved):
                    msg = "Android did not return"
                    raise ValueError(msg)
                return "Pico/Phoenix_ovs/PICOA8110:10/5.13.7/test:user/dev-keys\ntrue\n1234567\n1"
            if "preflight.sh" in argv[-1] and "PICO4_PREFLIGHT_DIR=" in argv[-1]:
                return (
                    "PREFLIGHT: 20 passed, 1 failed"
                    if self.scenario == "preflight"
                    else "PREFLIGHT: 21 passed, 0 failed"
                )
            if argv[-2:] == ["reboot", "bootloader"]:
                self.stock = False
            return ""
        if argv[0] == "fastboot":
            if argv[-1] == "devices":
                return "ABC fastboot"
            if argv[-1] == "device-info":
                return "Device unlocked: false" if self.scenario == "locked" else "Device unlocked: true"
            if argv[-2:] == ["getvar", "has-slot:boot"]:
                msg = "GetVar Variable Not found"
                raise ValueError(msg)
            if argv[-2:] == ["getvar", "all"]:
                output = "(bootloader) partition-size:boot: " + ("0x20000" if self.scenario == "size" else "0x10000")
                if self.scenario == "slotted":
                    output += (
                        "\n(bootloader) partition-size:boot_a: 0x10000\n(bootloader) partition-size:boot_b: 0x10000"
                    )
                return output
            if argv[3:5] == ["flash", "boot"]:
                restoring = Path(argv[-1]).name == "stock-boot.img"
                journal = json.loads((self.runner.folder / "result.json").read_text())
                self.assertTrue(journal["boot_restore_required"])
                self.assertTrue(journal["manual_restore_commands"])
                if self.scenario == "interrupted-write" and not restoring:
                    raise KeyboardInterrupt
                if (self.scenario == "restore-write" and restoring) or (
                    self.scenario == "flash-write" and not restoring
                ):
                    msg = "flash failed after partial write"
                    raise ValueError(msg)
                if restoring:
                    self.restored = True
                elif self.scenario == "restore-tamper":
                    Path(self.runner.result["stock_boot"]["path"]).write_bytes(b"changed")
            if argv[-1] == "reboot":
                self.stock = self.restored if self.runner.flash_mode and self.runner.boot_attempted else True
            return "OKAY"
        if argv[0] == "ssh":
            command = argv[-1]
            if command.startswith("pico4-userspace-check"):
                return self.userspace_check()
            if command.startswith("pico4-imu stream"):
                self.assertIn("--rate 500 ", command)
                stream = {
                    "samples": 5120,
                    "rate_hz": 511.95,
                    "non_increasing_timestamps": 0,
                    "config": {"sample_rate_hz": 500.0},
                    "mean": [9.7, 0.0, 1.5],
                }
                failed = self.scenario == "imu"
                return (
                    json.dumps(
                        {
                            "schema": 1,
                            "seconds": 10,
                            "requested_rate_hz": 500.0,
                            "streams": {"accel": stream, "gyro": stream},
                            "orientation": {},
                            "status": "fail" if failed else "pass",
                            "failures": ["accel: |g| 3.0 m/s^2"] if failed else [],
                        }
                    )
                    + "\nEXIT="
                    + ("1" if failed else "0")
                )
            if command.startswith("if systemctl is-active -q pico4-session.service; then"):
                return "SESSION-PAUSED" if getattr(self, "session_running", True) else ""
            if command.startswith("systemctl is-active -q pico4-session.service ||"):
                self.resumed_session = True
                return ""
            if command.startswith("cat /sys/class/thermal/thermal_zone*/temp"):
                return "61.2"
            if command.startswith("pico4-valve-session-check"):
                self.assertIn("--seconds 30", command)
                failed = self.scenario == "valve"
                return (
                    json.dumps(
                        {
                            "schema": 1,
                            "status": "failed" if failed else "passed",
                            "errors": ["steamvr.service is failed"] if failed else [],
                            "seconds": 30,
                            "startup_seconds": 21,
                            "session": {
                                "target": "gamescope-session.target",
                                "target_state": "active",
                                "steamvr": "failed" if failed else "active",
                                "gamescope": "active",
                                "failed_units": [],
                                "restored": "active",
                            },
                            "compositor": {
                                "started": True,
                                "gamescope_client": True,
                                "asserts": 0,
                                "present_wait_errors": 2689,
                            },
                            "frames": {
                                "target": "msm",
                                "commits": 2700,
                                "commit_hz": 89.7,
                                "vblank_hz": 90.0,
                                "late": 2,
                            },
                            "devices": [
                                {"index": 0, "position": [-0.01, 1.61, 0.07], "rotation": [-2.5, -1.56, 2.42]},
                                {"index": 1, "position": [0.09, 0.96, -0.6], "rotation": [-0.27, 0.05, 0.86]},
                                {"index": 2, "position": [0.48, 0.96, -0.24], "rotation": [-0.31, -0.13, -0.93]},
                            ],
                            "max_temp_c": {"before": 80.1, "after_run": 92.3},
                            "fan": {"before": self.FAN_STATE, "after_run": {**self.FAN_STATE, "duty": 50}},
                            "performance": {
                                "status": "failed" if self.scenario == "slow" else "passed",
                                "failures": ["52.0 commits/s, below 85.5"] if self.scenario == "slow" else [],
                                "commit_hz": 52.0 if self.scenario == "slow" else 90.0,
                                "late_percent": 0.1,
                                "stalls": 0,
                                "hottest_c": 61.5,
                                "window_seconds": 30.0,
                            },
                        }
                    )
                    + "\nEXIT="
                    + ("1" if failed else "0")
                )
            if command.startswith(("pico4-imu list", "systemctl status")):
                return "sensor evidence"
            if "test-run" in command:
                return ("wrong-run" if self.scenario == "identity" else self.runner.run_id) + "\n1234567"
            if command == "cat /etc/pico4/build-info":
                return json.dumps({} if self.scenario == "build" else self.info)
            if command == "uname -m":
                return "aarch64"
            if command == "uname -r":
                return "7.2.0-pico4" if self.installed_system else "4.19.81"
            if command == "id -u":
                return "0"
            if "SHELL-" in command:
                return "SHELL-42"
            if command.startswith("for d in /sys/class/block"):
                if self.installed_system:
                    return "BLOCK sda 0\nBLOCK sda7 " + ("0" if self.scenario == "storage" else "1") + "\nBLOCK sda16 0"
                return "BLOCK sda 1\nBLOCK sda1 " + ("0" if self.scenario == "storage" else "1")
            if command == "cat /proc/mounts":
                if self.installed_system:
                    return "/dev/sda7 / ext4 ro,noatime 0 0\n/dev/sda16 /data ext4 rw,noatime 0 0"
                return "rootfs / rootfs rw 0 0"
            if command.startswith("head -c "):
                return ("bad" if self.scenario == "readback" else self.runner.result["boot_sha256"]) + "  -"
            if command.startswith("test -s"):
                return "READY"
            if "pico4-reboot" in command:
                self.stock = "bootloader" not in command
            return "boot log"
        raise AssertionError(argv)

    def fake_http(self, endpoint: str, _timeout: float = 15) -> bytes:
        if self.scenario == "http":
            msg = "HTTP unavailable"
            raise OSError(msg)
        if endpoint == "cgi-bin/status":
            return b"PICO 4 Linux"
        if endpoint == "cgi-bin/log?boot":
            return self.runner.run_id.encode()
        return report(self.runner.run_id)

    installed_system = False

    def stage2_checks(self, *, firmware: bool = False, sensors: bool = False, steamvr: bool = False) -> dict[str, Any]:
        """Run the stage-2 checks as `dev check` runs them on the installed system; return their state."""
        self.installed_system = True
        self.info = {"kernel": "Linux version 7.2.0-pico4 test", "stock": "test"}
        dev = SimpleNamespace(s=SimpleNamespace(adb_serial="ABC"), ssh_argv=lambda command: ["ssh", command])
        # A stand-in with only what DevChecks uses of a Dev.
        self.runner = devloop.DevChecks(cast("devloop.Dev", dev), self.root / "check", self.info, {"chip_id": 1234567})
        self.state: dict[str, Any] = {
            "installed": True,
            "gpu_firmware": firmware,
            "sensor_data": sensors,
            "steamvr": steamvr,
        }
        with (
            patch.object(self.runner, "command", side_effect=self.fake_command),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.runner.userspace_checks(self.state)
        return self.state

    def execute(self):
        with (
            patch.object(self.runner, "command", side_effect=self.fake_command),
            patch.object(self.runner, "http", side_effect=self.fake_http),
            contextlib.redirect_stdout(io.StringIO()),
        ):
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
        image = bootimg.parse((self.runner.folder / "test-boot.img").read_bytes())
        tree = dict(cpio.read_cpio(gzip.decompress(image.ramdisk)))
        self.assertEqual(tree["etc/pico4/test-connect-timeout"].data, b"31\n")
        self.assertEqual(tree["etc/pico4/test-timeout"].data, b"302\n")
        self.assertTrue(any(cmd[-1] == "touch /run/pico4/test-authenticated" for cmd in self.calls))
        for cmd in self.calls:
            if cmd[0] in ("adb", "fastboot") and cmd[-1] != "devices":
                self.assertEqual(cmd[1:3], ["-s", "ABC"])

    def test_userspace_check_and_storage_on_the_installed_system(self):
        state = self.stage2_checks()
        self.assertEqual(state["check"]["status"], "passed")
        self.assertEqual(state["check"]["fan"], {"service": "active", "state": self.FAN_STATE})
        self.assertTrue((self.runner.folder / "userspace-check.json").exists())
        self.assertTrue((self.runner.folder / "storage-userspace.txt").exists())

    def test_a_writable_partition_fails_the_stage2_checks(self):
        self.scenario = "storage"
        with self.assertRaisesRegex(ValueError, "writable disk or partition"):
            self.stage2_checks()

    def test_a_failed_userspace_check_stops_the_stage2_checks(self):
        self.scenario = "userspace-check"
        with self.assertRaisesRegex(ValueError, "Userspace check failed"):
            self.stage2_checks(sensors=True, steamvr=True)
        self.assertFalse(any(c[0] == "ssh" and "pico4-imu" in c[-1] for c in self.calls))

    def test_head_imu_is_checked_with_sensor_data(self):
        for scenario, failed in (("", False), ("imu", True)):
            with self.subTest(scenario or "passing"):
                self.setUp()
                self.scenario = scenario
                if failed:
                    with self.assertRaisesRegex(ValueError, "Head IMU check failed"):
                        self.stage2_checks(sensors=True)
                else:
                    self.stage2_checks(sensors=True)
                self.assertEqual(self.state["imu"]["status"], "fail" if failed else "pass")
                self.assertTrue((self.runner.folder / "userspace-imu.json").exists())
                self.assertTrue((self.runner.folder / "userspace-sensors-list.txt").exists())

    def test_valve_session_is_checked_with_steamvr(self):
        for scenario, message in (
            ("", None),
            ("valve", "Valve session check failed"),
            ("slow", "missed its performance limits"),
        ):
            with self.subTest(scenario or "passing"):
                self.setUp()
                self.scenario = scenario
                if message:
                    with self.assertRaisesRegex(ValueError, message):
                        self.stage2_checks(sensors=True, steamvr=True)
                else:
                    self.stage2_checks(sensors=True, steamvr=True)
                valve = self.state["valve_session"]
                self.assertEqual((valve["frames"]["target"], len(valve["devices"])), ("msm", 3))
                self.assertEqual(valve["fan"]["after_run"]["duty"], 50)
                self.assertEqual(valve["performance"]["status"], "failed" if scenario == "slow" else "passed")
                self.assertEqual(self.state["valve_session_cooldown"], {"seconds": 0, "hottest_c": 61.2})
                self.assertTrue((self.runner.folder / "userspace-valve-session.json").exists())
                self.assertTrue((self.runner.folder / "userspace-valve-vrcompositor.txt").exists())
                # The session comes back, also after a failed check.
                self.assertTrue(self.resumed_session)

    def test_valve_session_skipped_without_steamvr(self):
        state = self.stage2_checks(sensors=True)
        self.assertNotIn("valve_session", state)
        self.assertFalse(any("pico4-valve-session-check" in command[-1] for command in self.calls))

    def test_head_imu_skipped_without_sensor_data(self):
        state = self.stage2_checks()
        self.assertNotIn("imu", state)
        self.assertFalse(any(c[0] == "ssh" and "pico4-imu" in c[-1] for c in self.calls))

    def test_preflight_failure_never_reboots(self):
        self.scenario = "preflight"
        self.assertEqual(self.execute(), 1)
        self.assertFalse(any("reboot" in command for command in self.calls))
        self.assertEqual(self.runner.result["recovery"], "not_needed")

    def test_ncm_dependencies_are_checked_before_reboot(self):
        self.args.windows_ncm_python = "ncm-python"
        self.scenario = "ncm-dependencies"
        self.assertEqual(self.execute(), 1)
        self.assertFalse(self.runner.moved)
        self.assertFalse(any(cmd[0] in ("adb", "fastboot") for cmd in self.calls))

    def test_ncm_initialization_runs_once_before_ssh(self):
        self.args.windows_ncm_python = "ncm-python"
        self.args.boot_timeout = 3
        self.assertEqual(self.execute(), 0)
        requests = [n for n, cmd in enumerate(self.calls) if cmd[0] == "ncm-python" and "--serial" in cmd]
        self.assertEqual(len(requests), 1)
        first_ssh = next(n for n, cmd in enumerate(self.calls) if cmd[0] == "ssh")
        self.assertLess(requests[0], first_ssh)
        self.assertEqual(self.runner.result["windows_ncm"]["status"], "initialized")
        self.assertEqual(self.runner.result["recovery"], "verified")

    def test_ncm_failure_still_restores_stock_boot(self):
        self.args.windows_ncm_python = "ncm-python"
        self.scenario = "ncm-device"
        self.assertEqual(self.execute(), 1)
        self.assertEqual(self.runner.result["recovery"], "verified")
        self.assertFalse(self.runner.result["boot_restore_required"])

    def test_lock_failure_returns_to_stock(self):
        self.scenario = "locked"
        self.assertEqual(self.execute(), 1)
        self.assertFalse(self.runner.boot_attempted)
        self.assertEqual(self.runner.result["recovery"], "verified")

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
        self.assertFalse(any("test-authenticated" in command[-1] for command in self.calls))

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
        self.assertIn("pico4.test_run=nonce", image.full_cmdline)
        self.assertEqual(entries["root/.ssh/authorized_keys"].data, b"ssh-ed25519 TEST\n")
        self.assertEqual(entries["root/.ssh/authorized_keys"].mode & 0o777, 0o600)
        self.assertEqual(result["build_info"], self.info)

    def test_rejects_writable_and_qemu_images(self):
        original = bootimg.parse(self.source.read_bytes())
        self.source = self.root / "variant.img"
        for option in ("pico4.rw=1", "pico4.rw", "pico4.selftest=1", "pico4.selftest"):
            self.source.write_bytes(original.with_cmdline(option).pack())
            with self.assertRaises(ValueError):
                hw.prepare_image(self.source, self.runner.folder, "nonce", b"ssh-ed25519 TEST")

    def test_existing_results_are_never_overwritten(self):
        with self.assertRaises(FileExistsError):
            hw.Runner(self.args)

    def test_explicit_flash_run_restores_original_boot_and_verifies_android(self):
        self.assertEqual(self.execute(), 0)
        flashes = [cmd for cmd in self.calls if cmd[0] == "fastboot" and cmd[3:5] == ["flash", "boot"]]
        self.assertEqual([Path(cmd[-1]).name for cmd in flashes], ["test-boot.img", "stock-boot.img"])
        self.assertEqual(self.runner.result["boot_restore"], "android_boot_verified")
        self.assertFalse(self.runner.result["boot_restore_required"])
        self.assertEqual(
            (self.runner.folder / "stock-boot.img").read_bytes(), (self.args.backup / "boot.bin").read_bytes()
        )
        self.assertTrue((self.runner.folder / "boot-readback.txt").exists())
        self.assertTrue(any("pico4-reboot bootloader" in cmd[-1] for cmd in self.calls))
        self.assertFalse(any("flashing" in cmd or "erase" in cmd for cmd in self.calls))

    def test_partial_boot_write_still_restores_stock(self):
        self.scenario = "flash-write"
        self.assertEqual(self.execute(), 1)
        self.assertTrue(self.restored)
        self.assertEqual(self.runner.result["recovery"], "verified")
        self.assertFalse(self.runner.result["boot_restore_required"])

    def test_interrupted_boot_write_still_restores_stock(self):
        self.scenario = "interrupted-write"
        self.assertEqual(self.execute(), 1)
        self.assertTrue(self.restored)
        self.assertEqual(self.runner.result["recovery"], "verified")
        self.assertFalse(self.runner.result["boot_restore_required"])
        self.assertFalse(any(check["status"] == "running" for check in self.runner.result["checks"]))

    def test_failed_stock_write_preserves_recovery_requirement(self):
        self.scenario = "restore-write"
        self.assertEqual(self.execute(), 1)
        self.assertTrue(self.runner.result["boot_restore_required"])
        self.assertEqual(self.runner.result["recovery"], "failed")
        # Only the initial POC boot may reboot; a failed restoration stays in fastboot.
        self.assertEqual(sum(cmd[0] == "fastboot" and cmd[-1] == "reboot" for cmd in self.calls), 1)
        self.assertTrue(self.runner.result["manual_restore_commands"])

    def test_changed_pinned_restore_image_is_never_flashed(self):
        self.scenario = "restore-tamper"
        self.assertEqual(self.execute(), 1)
        self.assertTrue(self.runner.result["boot_restore_required"])
        self.assertIn("changed", self.runner.result["recovery_error"])
        self.assertFalse(self.restored)

    def test_readback_mismatch_fails_and_restores(self):
        self.scenario = "readback"
        self.assertEqual(self.execute(), 1)
        self.assertIn("readback", self.runner.result["error"])
        self.assertEqual(self.runner.result["recovery"], "verified")

    def test_wrong_backup_identity_never_reboots(self):
        self.backup_manifest["identity"]["chip_id"] += 1
        self.save_backup_manifest()
        self.assertEqual(self.execute(), 1)
        self.assertIn("identity", self.runner.result["error"])
        self.assertFalse(self.runner.moved)

    def test_backup_tampering_never_reboots(self):
        (self.args.backup / "boot.bin").write_bytes(b"changed")
        self.assertEqual(self.execute(), 1)
        self.assertIn("integrity", self.runner.result["error"])
        self.assertFalse(self.runner.moved)

    def test_different_stock_kernel_never_reboots(self):
        stock = bootimg.parse((self.args.backup / "boot.bin").read_bytes())
        stock.kernel = b"different kernel"
        (self.args.backup / "boot.bin").write_bytes(stock.pack().ljust(65536, b"\0"))
        self.backup_manifest["files"]["boot.bin"] = hw.backups.digest(self.args.backup / "boot.bin")
        self.save_backup_manifest()
        self.assertEqual(self.execute(), 1)
        self.assertIn("kernel", self.runner.result["error"])
        self.assertFalse(self.runner.moved)

    def test_live_partition_size_mismatch_never_flashes(self):
        self.scenario = "size"
        self.assertEqual(self.execute(), 1)
        self.assertIn("size", self.runner.result["error"])
        self.assertFalse(any("flash" in cmd for cmd in self.calls))
        self.assertEqual(self.runner.result["recovery"], "verified")

    def test_slotted_partition_never_flashes(self):
        self.scenario = "slotted"
        self.assertEqual(self.execute(), 1)
        self.assertIn("non-slotted", self.runner.result["error"])
        self.assertFalse(any("flash" in cmd for cmd in self.calls))
        self.assertEqual(self.runner.result["recovery"], "verified")

    def test_flash_confirmation_mismatch_never_reboots(self):
        self.args.confirm_device = "WRONG"
        self.assertEqual(self.execute(), 1)
        self.assertIn("confirm-device", self.runner.result["error"])
        self.assertFalse(self.runner.moved)


class PreflightDefaultTests(unittest.TestCase):
    def test_the_newest_rootfs_tar(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            for i, name in enumerate(("pico4-stage1-a-rootfs.tar", "pico4-stage1-b-rootfs.tar", "other.tar")):
                (out / name).write_bytes(b"")
                os.utime(out / name, (1000 + i, 1000 + i))
            os.utime(out / "pico4-stage1-a-rootfs.tar", (5000, 5000))
            self.assertEqual(hw.newest_rootfs(out).name, "pico4-stage1-a-rootfs.tar")
            with self.assertRaises(SystemExit):
                hw.newest_rootfs(out / "missing")


if __name__ == "__main__":
    unittest.main()
