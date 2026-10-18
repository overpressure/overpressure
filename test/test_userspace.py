import copy
import unittest

from tools import hardware_test as hw, userspace

# The stock PICO 4 5.13.7/5.13.8 kernel's answers (IKCONFIG), as far as the
# requirement table asks.
STOCK = {f"CONFIG_{o}": "y" for o in (
    "CGROUPS", "INOTIFY_USER", "SIGNALFD", "TIMERFD", "EPOLL", "UNIX", "TMPFS", "ZRAM", "EXT4_FS",
    "CGROUP_BPF", "SECCOMP_FILTER", "NET_NS", "TMPFS_POSIX_ACL", "ANDROID_PARANOID_NETWORK",
    "UEVENT_HELPER", "FW_LOADER_USER_HELPER_FALLBACK")}
VERSION = "Linux version 4.19.81-perf+ (smartcm@dc01) (clang version 8.0.12) #1 SMP PREEMPT"

PASSING = {
    "schema": 1, "status": "passed", "errors": [], "kernel": "4.19.81-perf+",
    "systemd": {"pid1": "/usr/lib/systemd/systemd", "version": "257.7", "state": "running",
                "failed_units": [], "tainted": "old-kernel"},
    "dev": {"fstype": "tmpfs", "missing_nodes": [], "udev_devices": 412},
    "storage": {"root": "/dev/zram0", "writable_block_devices": []},
    "seats": ["seat0"], "packages": 348,
    "vulkan": {"status": "passed", "result": {"status": "passed", "driver": "turnip", "width": 256,
                                               "height": 256, "verified_pixels": 65536, "bad_pixels": 0}},
}


class KernelCompatTests(unittest.TestCase):
    def test_stock_kernel_has_no_blocker_but_needs_systemd_257(self):
        report = userspace.kernel_compat(STOCK, VERSION)
        self.assertEqual(report["kernel"], "4.19.81-perf+")
        self.assertEqual(report["blocking"], [])
        self.assertTrue(report["systemd"].startswith("257"))
        for option in ("FHANDLE", "USER_NS", "PID_NS", "MEMCG", "SYSVIPC", "BINFMT_MISC", "HIDRAW"):
            self.assertIn(option, report["degraded"])
        self.assertEqual({n["option"] for n in report["notes"]},
                         {"ANDROID_PARANOID_NETWORK", "UEVENT_HELPER", "FW_LOADER_USER_HELPER_FALLBACK"})
        self.assertIn("DEVTMPFS", userspace.format_compat(report))

    def test_missing_required_option_blocks(self):
        config = dict(STOCK)
        del config["CONFIG_ZRAM"]
        self.assertEqual(userspace.kernel_compat(config, VERSION)["blocking"], ["ZRAM"])

    def test_newer_kernel_lifts_systemd_ceiling(self):
        self.assertEqual(userspace.kernel_compat(STOCK, "6.18.0")["systemd"], "any")

    def test_kernel_without_config_is_rejected(self):
        with self.assertRaises(ValueError):
            userspace.kernel_compat({}, VERSION)


class CheckValidationTests(unittest.TestCase):
    def test_passing_check(self):
        userspace.validate_check(PASSING, require_vulkan=True)

    def test_vulkan_only_required_with_firmware(self):
        value = copy.deepcopy(PASSING)
        value["vulkan"] = {"status": "skipped", "result": None}
        userspace.validate_check(value)
        with self.assertRaises(ValueError):
            userspace.validate_check(value, require_vulkan=True)

    def test_failures_are_rejected(self):
        mutations = (
            lambda v: v.update(status="failed", errors=["no failed units"]),
            lambda v: v["systemd"].update(state="degraded"),
            lambda v: v["systemd"].update(failed_units=["sshd.service"]),
            lambda v: v["systemd"].update(version="258.2"),
            lambda v: v["systemd"].update(pid1="/sbin/init"),
            lambda v: v["dev"].update(missing_nodes=["kgsl-3d0"]),
            lambda v: v["dev"].update(fstype="devtmpfs"),
            lambda v: v["storage"].update(writable_block_devices=["sda"]),
            lambda v: v["storage"].update(root="/dev/sda5"),
            lambda v: v.update(seats=[]),
            lambda v: v.update(schema=2),
        )
        for mutate in mutations:
            value = copy.deepcopy(PASSING)
            mutate(value)
            with self.assertRaises(ValueError):
                userspace.validate_check(value)

    def test_vulkan_readback_must_be_complete(self):
        value = copy.deepcopy(PASSING)
        value["vulkan"]["result"]["bad_pixels"] = 3
        with self.assertRaises(ValueError):
            userspace.validate_check(value, require_vulkan=True)


class MountPolicyTests(unittest.TestCase):
    def test_zram_root_counts_as_ram(self):
        hw.validate_mounts("/dev/zram0 / ext4 rw,noatime,discard 0 0\ntmpfs /run tmpfs rw 0 0")

    def test_storage_stays_protected_in_stage_two(self):
        for value in ("/dev/zram0 / ext4 rw 0 0\n/dev/sda5 /mnt ext4 rw 0 0", "/dev/sda5 / ext4 rw 0 0"):
            with self.assertRaises(ValueError):
                hw.validate_mounts(value)


IMU_PASSING = {
    "schema": 1, "status": "pass", "failures": [], "seconds": 10, "requested_rate_hz": 500.0,
    "streams": {"accel": {"samples": 5120, "rate_hz": 511.95, "non_increasing_timestamps": 0},
                "gyro": {"samples": 5120, "rate_hz": 511.95, "non_increasing_timestamps": 0}},
}


class ImuValidationTests(unittest.TestCase):
    def test_passing_stream(self):
        userspace.validate_imu(IMU_PASSING)

    def test_failures_are_rejected(self):
        cases = [
            ("not run", None),
            ("failed check", {"status": "fail", "failures": ["accel: |g| 3.1 m/s^2"]}),
            ("unchecked run", {"status": "collected"}),
            ("other rate", {"requested_rate_hz": 100.0}),
            ("no gyro", {"streams": {"accel": IMU_PASSING["streams"]["accel"]}}),
            ("time going backwards", {"streams": {**IMU_PASSING["streams"],
                                                  "gyro": {"samples": 5000, "non_increasing_timestamps": 2}}}),
        ]
        for name, change in cases:
            value = None if change is None else {**copy.deepcopy(IMU_PASSING), **change}
            with self.subTest(name), self.assertRaises(ValueError):
                userspace.validate_imu(value)


POSE_PASSING = {
    "schema": 1, "status": "pass", "system": "Monado: PICO 4", "orientation_tracking": True,
    "located": 980, "orientation_tracked": 975, "invalid_after_warmup": 0,
    "final_tilt_from_vertical_deg": 8.6, "heading_rate_deg_per_s": -0.1,
}


class OpenXrPoseTests(unittest.TestCase):
    def test_tilt_uses_imu_x_as_up(self):
        level = {"streams": {"accel": {"mean": [9.81, 0.0, 0.0]}}}
        self.assertAlmostEqual(userspace.imu_tilt_deg(level), 0.0)
        tilted = {"streams": {"accel": {"mean": [9.81 * 0.5, 0.0, 9.81 * 0.75 ** 0.5]}}}
        self.assertAlmostEqual(userspace.imu_tilt_deg(tilted), 60.0, places=4)
        with self.assertRaises(ValueError):
            userspace.imu_tilt_deg({"streams": {}})

    def test_passing_pose(self):
        userspace.validate_openxr_pose(POSE_PASSING, accel_tilt_deg=8.9)

    def test_failures_are_rejected(self):
        cases = [
            ("not run", None, 8.9),
            ("failed", {"status": "fail", "error": "xrCreateInstance failed (-51)"}, 8.9),
            ("simulated HMD", {"system": "Monado: Simulated HMD"}, 8.9),
            ("lost tracking", {"invalid_after_warmup": 3}, 8.9),
            ("never tracked", {"orientation_tracked": 0}, 8.9),
            ("wrong tilt", {}, 12.0),
            ("drifting", {"heading_rate_deg_per_s": 2.0}, 8.9),
        ]
        for name, change, accel in cases:
            value = None if change is None else {**POSE_PASSING, **change}
            with self.subTest(name), self.assertRaises(ValueError):
                userspace.validate_openxr_pose(value, accel_tilt_deg=accel)


if __name__ == "__main__":
    unittest.main()
