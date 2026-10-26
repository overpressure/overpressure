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

from pico4 import bootimg, cpio, hardware_test as hw


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


class FakeClock:
    """Virtual time for the runner: waits and deadlines pass without real sleeping."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0.0, seconds)


class HarnessTests(unittest.TestCase):
    INFO = {"kernel": "Linux version 4.19.81 test", "stock": "test"}

    @classmethod
    def setUpClass(cls):
        # One source image for every test: Windows' real-time scan of a freshly written
        # boot image costs about a quarter of a second on its first read.
        cls.shared = tempfile.TemporaryDirectory()
        cls.image = Path(cls.shared.name) / "poc.img"
        tree = cpio.Tree()
        tree.add_file("etc/pico4-poc/build-info", json.dumps(cls.INFO).encode())
        tree.add_file("init", b"#!/bin/sh\n", 0o755)
        tree.add_file("usr/libexec/pico4-poc/recovery-watch", b"#!/bin/sh\n", 0o755)
        tree.add_file("etc/inittab", b"::respawn:/usr/libexec/pico4-poc/svc recovery-watch\n"
                      b"::restart:/usr/libexec/pico4-poc/switch-userspace\n")
        tree.add_file("usr/bin/pico4-userspace", b"#!/bin/sh\n", 0o755)
        tree.add_file("usr/bin/zstd", b"\x7fELF", 0o755)
        tree.add_file("usr/libexec/pico4-poc/pico4-init", b"#!/bin/sh\n", 0o755)
        tree.mkdir("root", 0o700)
        cls.image.write_bytes(bootimg.BootImage(2, 4096, b"kernel", gzip.compress(tree.to_cpio()), dtb=b"dtb").pack())

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.image
        self.info = dict(self.INFO)
        self.clock = FakeClock()
        clock = patch.object(hw, "time", self.clock)
        clock.start()
        self.addCleanup(clock.stop)
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

    def enable_userspace(self, firmware=False, sensors=False, vrdisplay=False, steamvr=False):
        archive = self.root / "userspace" / "pico4-userspace.tar.zst"
        archive.parent.mkdir()
        archive.write_bytes(b"userspace archive")
        self.userspace_sha = hw.hashlib.sha256(archive.read_bytes()).hexdigest()
        (archive.parent / "pico4-userspace.manifest.json").write_text(json.dumps({
            "archive": {"sha256": self.userspace_sha},
            "packages": {"systemd": "257.7-2.2", **({"pico4-vrdisplay": "1-1"} if vrdisplay else {}),
                         **({"deckard-steamvr-rel": "r25358740-1"} if steamvr else {})},
            "gpu_firmware": [{"file": "a650_sqe.fw", "sha256": "0" * 64}] if firmware else [],
            "sensor_data": [{"file": "usr/lib/firmware/slpi.mdt", "sha256": "0" * 64}] if sensors else []}))
        self.args.userspace = archive
        self.args.userspace_timeout = 60
        self.args.out = self.root / "userspace-results"
        self.runner = hw.Runner(self.args)

    def userspace_check(self):
        value = {"schema": 1, "status": "passed", "errors": [],
                 "systemd": {"pid1": "/usr/lib/systemd/systemd", "version": "257.7", "state": "running",
                             "failed_units": []},
                 "dev": {"fstype": "tmpfs", "missing_nodes": [], "udev_devices": 300},
                 "storage": {"root": "/dev/zram0", "writable_block_devices": []}, "seats": ["seat0"],
                 "session": {"user": "steamos", "user_state": "active", "service": "active",
                             "target": "gamescope-session.target", "target_state": "active", "failed_units": [],
                             "monado_socket": "active", "monado_service": "inactive"},
                 "fan": {"service": "active", "state": self.FAN_STATE},
                 "vulkan": {"status": "skipped", "result": None}}
        if self.scenario == "userspace-check":
            value.update(status="failed", errors=["no failed units"])
            value["systemd"].update(state="degraded", failed_units=["sshd.service"])
        return json.dumps(value) + "\nEXIT=" + ("1" if value["status"] == "failed" else "0")

    def save_backup_manifest(self):
        (self.args.backup / "manifest.json").write_text(json.dumps(self.backup_manifest))

    # `pico4-fan state` (docs/cooling.md).
    FAN_STATE = {"profile": "late", "duty": 30, "reason": "table", "boost": False, "rpm": 5700,
                 "hottest": "cpu-1-4-usr", "hottest_mean_c": 61.2, "error": None}

    # What systemctl writes to stderr when it stops a socket-activated service.
    SYSTEMCTL_NOTE = ("Stopping 'pico4-monado.service', but its triggering units are still active:\n"
                      "pico4-monado.socket\n")

    def fake_command(self, argv, timeout=30, stdin=None):
        output = self.fake_device(argv, timeout, stdin)
        if str(argv[0]) == "ssh" and isinstance(output, str):
            # As on the headset: SSH passes systemctl's note on unless the command discards it.
            command = str(argv[-1])
            stops = command.count("systemctl stop pico4-monado.service")
            if stops > command.count("systemctl stop pico4-monado.service 2>/dev/null") or \
                    (command.startswith("pico4-valve-session-check") and "2>" not in command):
                output = self.SYSTEMCTL_NOTE + output
        return output

    def fake_device(self, argv, timeout=30, stdin=None):
        argv = list(map(str, argv))
        if stdin is not None:
            self.assertEqual(stdin.read(), b"userspace archive")
        self.calls.append(argv)
        if argv[0] == "ncm-python":
            if argv[-1] == "--check":
                if self.scenario == "ncm-dependencies":
                    raise ValueError("NCM dependencies missing")
                return json.dumps({"status": "ready"})
            if self.scenario == "ncm-device":
                raise ValueError("NCM gadget did not appear")
            return json.dumps({"status": "initialized", "serial": self.args.fastboot_serial or self.args.serial})
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
                raise ValueError("GetVar Variable Not found")
            if argv[-2:] == ["getvar", "all"]:
                output = "(bootloader) partition-size:boot: " + ("0x20000" if self.scenario == "size" else "0x10000")
                if self.scenario == "slotted":
                    output += "\n(bootloader) partition-size:boot_a: 0x10000\n(bootloader) partition-size:boot_b: 0x10000"
                return output
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
            if command.startswith("pico4-userspace receive"):
                self.assertIn(f"--sha256 {self.userspace_sha}", command)
                return "received " + ("0" * 64 if self.scenario == "userspace-sha" else self.userspace_sha)
            if command == "pico4-userspace switch":
                self.switched = True
                return "switching"
            if command == "cat /proc/1/comm":
                return "systemd" if getattr(self, "switched", False) else "init"
            if command.startswith("pico4-userspace-check"):
                return self.userspace_check()
            if command.startswith("pico4-imu stream"):
                self.assertIn("--rate 500 ", command)
                stream = {"samples": 5120, "rate_hz": 511.95, "non_increasing_timestamps": 0,
                          "config": {"sample_rate_hz": 500.0}, "mean": [9.7, 0.0, 1.5]}
                failed = self.scenario == "imu"
                return json.dumps({"schema": 1, "seconds": 10, "requested_rate_hz": 500.0,
                                   "streams": {"accel": stream, "gyro": stream}, "orientation": {},
                                   "status": "fail" if failed else "pass",
                                   "failures": ["accel: |g| 3.0 m/s^2"] if failed else []}) + \
                    "\nEXIT=" + ("1" if failed else "0")
            if command.startswith("if systemctl is-active -q pico4-session.service; then"):
                return "SESSION-PAUSED" if getattr(self, "session_running", True) else ""
            if command.startswith("systemctl is-active -q pico4-session.service ||"):
                self.resumed_session = True
                return ""
            if "pico4-vrdisplay-test" in command:
                self.assertIn("PICO4_VRDISPLAY=1", command)
                failed = self.scenario == "vrdisplay"
                return json.dumps({"schema": 1, "status": "fail" if failed else "pass", "display": "PICO 4",
                                   "mode": "4320x2160@90.000", "acquire": "drm", "frames": 1350, "frame_hz": 89.9,
                                   "event_hz": 88.7, "vblank_hz": 89.998, "counter": [100, 1450]}) + \
                    "\nEXIT=" + ("1" if failed else "0")
            if command == "cat /run/pico4-poc/vrdisplay.json":
                return json.dumps({"target": "pico4_vrdisplay", "fake": False, "commits": 1350, "commit_hz": 89.9,
                                   "vblank_hz": 89.998, "retire_timeouts": 0, "gpu_timeouts": 0,
                                   "eye_mean_rgb": [[255.0, 0.0, 0.0], [0.0, 0.0, 255.0]]})
            if "pico4-vrsync-test" in command:
                self.assertIn("PICO4_VRDISPLAY=1", command)
                self.assertIn("runuser -u steamos", command)
                failed = self.scenario == "vrsync"
                return json.dumps({"schema": 1, "status": "fail" if failed else "pass", "gpu_wait": not failed,
                                   "host_wait": True, "host_signal": True, "early_wait": True,
                                   "same_submit": True, "child_exit": 0,
                                   "counter": 2, "child_start_ticks": 3562566, "parent_end_ticks": 3554537,
                                   "work_ms": 160.5, "early_held_ms": 203.5}) + "\nEXIT=" + ("1" if failed else "0")
            if command.startswith("cat /sys/class/thermal/thermal_zone*/temp"):
                return "61.2"
            if command.startswith("pico4-valve-session-check"):
                self.assertIn("--seconds 30", command)
                failed = self.scenario == "valve"
                return json.dumps({
                    "schema": 1, "status": "failed" if failed else "passed",
                    "errors": ["steamvr.service is failed"] if failed else [], "seconds": 30, "startup_seconds": 21,
                    "session": {"target": "gamescope-session.target", "target_state": "active",
                                "steamvr": "failed" if failed else "active", "gamescope": "active",
                                "failed_units": [], "restored": "active"},
                    "compositor": {"started": True, "gamescope_client": True, "asserts": 0,
                                   "present_wait_errors": 2689},
                    "layer": {"target": "pico4_vrdisplay", "fake": False, "commits": 2700, "commit_hz": 42.49,
                              "vblank_hz": 89.998, "retire_timeouts": 0, "gpu_timeouts": 0,
                              "eye_mean_rgb": [[9.4, 12.3, 18.2], [9.1, 12.2, 18.3]]},
                    "devices": [{"index": 0, "position": [-0.01, 1.61, 0.07], "rotation": [-2.5, -1.56, 2.42]},
                                {"index": 1, "position": [0.09, 0.96, -0.6], "rotation": [-0.27, 0.05, 0.86]},
                                {"index": 2, "position": [0.48, 0.96, -0.24], "rotation": [-0.31, -0.13, -0.93]}],
                    "max_temp_c": {"before": 80.1, "after_run": 92.3},
                    "fan": {"before": self.FAN_STATE, "after_run": {**self.FAN_STATE, "duty": 50}},
                    "performance": {"status": "failed" if self.scenario == "slow" else "passed",
                                    "failures": ["52.0 commits/s, below 85.5"] if self.scenario == "slow" else [],
                                    "commit_hz": 52.0 if self.scenario == "slow" else 90.0, "late_percent": 0.1,
                                    "stalls": 0, "present_wait_timeouts": 0, "hottest_c": 61.5,
                                    "window_seconds": 30.0, "startup": {"stalls": 2}}}) + \
                    "\nEXIT=" + ("1" if failed else "0")
            if command.startswith(("pico4-imu list", "systemctl status",                                    "cat /run/pico4-poc/vrdisplay.log", "cat /run/pico4-poc/vrsync.log")):
                return "sensor evidence"
            if "pico4-kms-test" in command:
                return "EXIT=1" if self.scenario == "graphics" else "EXIT=0"
            if command == "cat /run/pico4-poc/kms.json":
                return json.dumps(dict(schema=2,status="passed",presentation="page_flip",retire_fence=True,
                    composition="rgb_layers",buffer_layout="combined",eye_routing="sspp_layout",width=4320,height=2160,modes=[
                    dict(requested_hz=h,vblank_hz=float(h),completion_hz=float(h),flips=10*h,
                         vblank_samples=10*h,vblank_sequence_gaps=0,status="passed") for h in (90,72)]))
            if "test-run" in command:
                wrong = self.scenario == "identity" or (self.scenario == "session-identity"
                        and self.runner.result.get("development_session", {}).get("status") == "active")
                return ("wrong-run" if wrong else self.runner.run_id) + "\n1234567"
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
        image = bootimg.parse((self.runner.folder / "test-boot.img").read_bytes())
        tree = dict(cpio.read_cpio(gzip.decompress(image.ramdisk)))
        self.assertEqual(tree["etc/pico4-poc/test-connect-timeout"].data, b"31\n")
        self.assertEqual(tree["etc/pico4-poc/test-timeout"].data, b"302\n")
        self.assertTrue(any(cmd[-1] == "touch /run/pico4-poc/test-authenticated" for cmd in self.calls))
        for cmd in self.calls:
            self.assertNotIn("flash", cmd)
            self.assertNotIn("unlock", cmd)
            if cmd[0] in ("adb", "fastboot") and cmd[-1] != "devices":
                self.assertEqual(cmd[1:3], ["-s", "ABC"])

    def test_graphics_phase_collects_measurements_and_restores(self):
        self.enable_flash_mode()
        self.args.graphics="kms"
        self.args.graphics_seconds=10
        self.assertEqual(self.execute(),0)
        self.assertTrue(self.restored)
        self.assertEqual(self.runner.result["graphics"]["kms"]["status"],"passed")
        self.assertEqual(self.runner.result["graphics_visual_confirmation"],"not_recorded")

    def test_userspace_handoff_is_checked_and_restored(self):
        self.enable_flash_mode()
        self.enable_userspace()
        self.assertEqual(self.execute(), 0)
        self.assertTrue(self.restored)
        state = self.runner.result["userspace"]
        self.assertEqual((state["sha256"], state["check"]["status"]), (self.userspace_sha, "passed"))
        self.assertEqual(state["check"]["fan"], {"service": "active", "state": self.FAN_STATE})
        # Two deadlines of 60 s plus 300 s handoff margin on top of the normal budget.
        self.assertEqual(self.runner.result["device_recovery"]["test_timeout"], 302 + 420)
        self.assertTrue((self.runner.folder / "userspace-check.json").exists())
        self.assertTrue((self.runner.folder / "storage-userspace.txt").exists())
        receive = [c for c in self.calls if c[0] == "ssh" and c[-1].startswith("pico4-userspace receive")]
        self.assertEqual(len(receive), 1)

    def test_head_imu_is_checked_with_sensor_data(self):
        for scenario, code in (("", 0), ("imu", 1)):
            with self.subTest(scenario or "passing"):
                self.setUp()
                self.enable_flash_mode()
                self.enable_userspace(sensors=True)
                self.scenario = scenario
                self.assertEqual(self.execute(), code)
                self.assertTrue(self.restored)
                self.assertEqual(self.runner.result["userspace"]["imu"]["status"], "fail" if code else "pass")
                self.assertTrue((self.runner.folder / "userspace-imu.json").exists())
                self.assertTrue((self.runner.folder / "userspace-sensors-list.txt").exists())

    def test_display_checks_pause_the_session(self):
        for running, scenario, code in ((True, "", 0), (True, "vrdisplay", 1), (False, "", 0)):
            with self.subTest(running=running, scenario=scenario or "passing"):
                self.setUp()
                self.enable_flash_mode()
                self.enable_userspace(sensors=True, vrdisplay=True)
                self.scenario, self.session_running, self.resumed_session = scenario, running, False
                self.assertEqual(self.execute(), code)
                # Started again after the checks, also after a failed one; untouched when not paused.
                self.assertEqual(self.resumed_session, running)

    def test_vr_display_is_checked_with_the_layer(self):
        for scenario, code in (("", 0), ("vrdisplay", 1)):
            with self.subTest(scenario or "passing"):
                self.setUp()
                self.enable_flash_mode()
                self.enable_userspace(sensors=True, vrdisplay=True)
                self.scenario = scenario
                self.assertEqual(self.execute(), code)
                self.assertTrue(self.restored)
                state = self.runner.result["userspace"]["vrdisplay"]
                self.assertEqual((state["acquire"], state["layer"]["target"]), ("drm", "pico4_vrdisplay"))
                self.assertTrue((self.runner.folder / "userspace-vrdisplay-stats.json").exists())

    def test_semaphores_and_valve_session_are_checked_with_steamvr(self):
        for scenario, code in (("", 0), ("vrsync", 1), ("valve", 1), ("slow", 1)):
            with self.subTest(scenario or "passing"):
                self.setUp()
                self.enable_flash_mode()
                self.enable_userspace(sensors=True, vrdisplay=True, steamvr=True)
                self.scenario = scenario
                self.assertEqual(self.execute(), code)
                self.assertTrue(self.restored)
                state = self.runner.result["userspace"]
                self.assertEqual(state["vrsync"]["status"], "fail" if scenario == "vrsync" else "pass")
                if scenario == "vrsync":
                    self.assertNotIn("valve_session", state)
                    continue
                valve = state["valve_session"]
                self.assertEqual((valve["layer"]["target"], len(valve["devices"])), ("pico4_vrdisplay", 3))
                self.assertEqual(valve["fan"]["after_run"]["duty"], 50)
                self.assertEqual(valve["performance"]["status"], "failed" if scenario == "slow" else "passed")
                self.assertEqual(state["valve_session_cooldown"], {"seconds": 0, "hottest_c": 61.2})
                self.assertTrue((self.runner.folder / "userspace-valve-session.json").exists())
                self.assertTrue((self.runner.folder / "userspace-valve-vrcompositor.txt").exists())

    def test_valve_session_skipped_without_steamvr(self):
        self.enable_flash_mode()
        self.enable_userspace(sensors=True, vrdisplay=True)
        self.assertEqual(self.execute(), 0)
        self.assertIn("vrsync", self.runner.result["userspace"])
        self.assertNotIn("valve_session", self.runner.result["userspace"])
        self.assertFalse(any("pico4-valve-session-check" in command[-1] for command in self.calls))

    def test_vr_display_skipped_without_the_layer(self):
        self.enable_flash_mode()
        self.enable_userspace(sensors=True)
        self.assertEqual(self.execute(), 0)
        self.assertNotIn("vrdisplay", self.runner.result["userspace"])
        self.assertFalse(any("pico4-vrdisplay-test" in command[-1] for command in self.calls))

    def test_head_imu_skipped_without_sensor_data(self):
        self.enable_flash_mode()
        self.enable_userspace()
        self.assertEqual(self.execute(), 0)
        self.assertNotIn("imu", self.runner.result["userspace"])
        self.assertFalse(any(c[0] == "ssh" and "pico4-imu" in c[-1] for c in self.calls))

    def test_userspace_failures_still_restore_stock(self):
        for scenario in ("userspace-check", "userspace-sha"):
            with self.subTest(scenario):
                self.setUp()
                self.enable_flash_mode()
                self.enable_userspace()
                self.scenario = scenario
                self.assertEqual(self.execute(), 1)
                self.assertTrue(self.restored)
                self.assertEqual(self.runner.result["recovery"], "verified")
                self.assertFalse(self.runner.result["boot_restore_required"])

    def test_userspace_requires_handoff_image(self):
        self.enable_userspace()
        tree = cpio.Tree()
        tree.add_file("etc/pico4-poc/build-info", json.dumps(self.info).encode())
        tree.add_file("usr/libexec/pico4-poc/recovery-watch", b"#!/bin/sh\n", 0o755)
        tree.add_file("etc/inittab", b"::respawn:/usr/libexec/pico4-poc/svc recovery-watch\n")
        self.source = self.root / "handoff-less.img"
        self.source.write_bytes(bootimg.BootImage(2, 4096, b"kernel", gzip.compress(tree.to_cpio()), dtb=b"dtb").pack())
        with self.assertRaisesRegex(ValueError, "handoff"):
            hw.prepare_image(self.source, self.root, "run", b"key", userspace=True)

    def test_graphics_failure_still_restores_stock(self):
        self.enable_flash_mode()
        self.args.graphics="kms"
        self.args.graphics_seconds=10
        self.scenario="graphics"
        self.assertEqual(self.execute(),1)
        self.assertTrue(self.restored)
        self.assertEqual(self.runner.result["recovery"],"verified")
        self.assertFalse(self.runner.result["boot_restore_required"])

    def test_development_session_restores_once_after_requested_end(self):
        self.enable_flash_mode()
        self.args.debug_session_seconds=60
        with patch.object(hw.time,"sleep",side_effect=lambda _: (self.runner.folder/"end-session").touch()):
            self.assertEqual(self.execute(),0)
        state=self.runner.result["development_session"]
        self.assertEqual((state["status"],state["end_reason"]),("completed","requested"))
        self.assertEqual(self.runner.result["graphics_acceptance"],"not_evaluated")
        self.assertTrue(self.restored)
        self.assertEqual(len([c for c in self.calls if c[0]=="fastboot" and c[3:5]==["flash","boot"]]),2)
        self.assertEqual(self.runner.result["device_recovery"]["test_timeout"],362)
        self.assertFalse((self.runner.folder/"id_ed25519").exists())

    def test_development_session_identity_failure_restores(self):
        self.enable_flash_mode()
        self.args.debug_session_seconds=60
        self.scenario="session-identity"
        self.assertEqual(self.execute(),1)
        self.assertEqual(self.runner.result["development_session"]["status"],"failed")
        self.assertTrue(self.restored)
        self.assertEqual(self.runner.result["recovery"],"verified")

    def test_development_session_deadline_does_not_wait_indefinitely(self):
        self.args.debug_session_seconds=60
        with patch.object(hw.time,"monotonic",side_effect=[0,61]), \
                patch.object(self.runner,"assertions") as assertions, \
                patch.object(self.runner,"ssh",return_value="kernel log"), \
                contextlib.redirect_stdout(io.StringIO()):
            self.runner.development_session()
        self.assertEqual(self.runner.result["development_session"]["end_reason"],"deadline")
        assertions.assert_called_once()

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
        self.enable_flash_mode()
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
        self.enable_flash_mode()
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
        self.assertIn("pico4poc.test_run=nonce", image.full_cmdline)
        self.assertEqual(entries["root/.ssh/authorized_keys"].data, b"ssh-ed25519 TEST\n")
        self.assertEqual(entries["root/.ssh/authorized_keys"].mode & 0o777, 0o600)
        self.assertEqual(result["build_info"], self.info)

    def test_rejects_writable_and_qemu_images(self):
        original = bootimg.parse(self.source.read_bytes())
        self.source = self.root / "variant.img"
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
