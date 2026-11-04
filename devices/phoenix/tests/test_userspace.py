import copy
import unittest

from devices.phoenix.tooling import hardware_test as hw, userspace

# The stock PICO 4 5.13.7/5.13.8 kernel's answers (IKCONFIG), as far as the
# requirement table asks.
STOCK = {f"CONFIG_{o}": "y" for o in (
    "CGROUPS", "INOTIFY_USER", "SIGNALFD", "TIMERFD", "EPOLL", "UNIX", "TMPFS", "ZRAM", "EXT4_FS",
    "CGROUP_BPF", "SECCOMP_FILTER", "NET_NS", "TMPFS_POSIX_ACL", "ANDROID_PARANOID_NETWORK",
    "UEVENT_HELPER", "FW_LOADER_USER_HELPER_FALLBACK")}
VERSION = "Linux version 4.19.81-perf+ (smartcm@dc01) (clang version 8.0.12) #1 SMP PREEMPT"

# `pico4-fan state` on the headset (docs/cooling.md).
FAN_STATE = {"profile": "late", "duty": 30, "reason": "table", "boost": False, "rpm": 5700,
             "hottest": "cpu-1-4-usr", "hottest_mean_c": 61.2, "error": None}

PASSING = {
    "schema": 1, "status": "passed", "errors": [], "kernel": "4.19.81-perf+",
    "systemd": {"pid1": "/usr/lib/systemd/systemd", "version": "257.7", "state": "running",
                "failed_units": [], "tainted": "old-kernel"},
    "dev": {"fstype": "tmpfs", "missing_nodes": [], "udev_devices": 412},
    "storage": {"root": "/dev/zram0", "writable_block_devices": []},
    "seats": ["seat0"], "packages": 348,
    "session": {"user": "steamos", "user_state": "active", "service": "active", "target": "pico4-session.target",
                "target_state": "active", "failed_units": [], "monado_socket": "active", "monado_service": "inactive",
                "access_denied": []},
    "fan": {"service": "active", "state": FAN_STATE},
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


class PowerWarningTests(unittest.TestCase):
    """an issue: a tethered headset that drains its battery is reported, never failed."""

    def power(self, port, status, input_ma=1250, online=True, current_ma=800):
        return {"usb_online": online, "port": port, "input_ma": input_ma, "usb_mv": 4846,
                "battery": {"status": status, "capacity": 15, "current_ma": current_ma}}

    def test_charging_port_keeps_up(self):
        self.assertEqual(userspace.power_warnings(self.power("USB_CDP", "Charging")), [])
        self.assertEqual(userspace.power_warnings(self.power("USB_CDP", "Full")), [])

    def test_discharge_while_tethered_warns(self):
        (warning,) = userspace.power_warnings(self.power("USB", "Discharging", 490))
        self.assertIn("discharges while tethered at 15 %", warning)
        self.assertIn("USB port, 490 mA input", warning)

    def test_drain_while_charging_warns(self):
        # an issue: the charger said "Charging" while the battery lost 0.37 A under the session.
        (warning,) = userspace.power_warnings(self.power("USB_CDP", "Charging", 1500, current_ma=-372))
        self.assertIn("discharges while tethered at 15 %", warning)

    def test_trickle_warns(self):
        (warning,) = userspace.power_warnings(self.power("USB_CDP", "Charging", 1500, current_ma=35))
        self.assertIn("charges at only 35 mA at 15 %", warning)

    def test_standard_port_warns_before_it_drains(self):
        (warning,) = userspace.power_warnings(self.power("USB", "Charging", 490))
        self.assertIn("standard USB port", warning)

    def test_untethered_or_unknown_is_quiet(self):
        self.assertEqual(userspace.power_warnings(self.power("USB", "Discharging", online=False)), [])
        self.assertEqual(userspace.power_warnings(None), [])
        # A check without the section (older images) still validates.
        value = copy.deepcopy(PASSING)
        value.pop("power", None)
        userspace.validate_check(value)


class CheckValidationTests(unittest.TestCase):
    def test_passing_check(self):
        userspace.validate_check(PASSING, require_vulkan=True, require_fan=True)

    def test_fan_only_required_on_the_headset(self):
        # QEMU has no lens MCU UART: the check skips the fan service.
        value = copy.deepcopy(PASSING)
        value["fan"] = {"service": "skipped", "state": None}
        userspace.validate_check(value)
        with self.assertRaises(ValueError):
            userspace.validate_check(value, require_fan=True)

    def test_fan_duty_must_be_acknowledged(self):
        # Acknowledged: the daemon's duty is the one the MCU acknowledged, without an error since.
        userspace.validate_check(PASSING, require_fan=True)
        cases = (
            # Refused: SetFanSpeed never got its 79, so there is no duty, and the error is named.
            ({"duty": None, "error": "SetFanSpeed 30 %: no reply to 0x0e (nothing)"}, "SetFanSpeed 30 %"),
            # Acknowledged once, but the MCU stopped answering.
            ({"error": "GetFanRPM: no reply to 0x10 (nothing)"}, "GetFanRPM"),
            # Missing: no reply yet and no error either (a daemon that has not sent).
            ({"duty": None}, "not acknowledged"),
            ({"duty": "30"}, "not acknowledged"),
        )
        for change, message in cases:
            value = copy.deepcopy(PASSING)
            value["fan"]["state"].update(change)
            with self.assertRaisesRegex(ValueError, message):
                userspace.validate_check(value, require_fan=True)
            with self.assertRaisesRegex(ValueError, message):
                userspace.validate_check(value)

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
            lambda v: v["dev"].update(missing_nodes=["dri/renderD128"]),
            lambda v: v["dev"].update(fstype="ramfs"),
            lambda v: v["storage"].update(writable_block_devices=["sda"]),
            lambda v: v["storage"].update(root="/dev/sda5"),
            lambda v: v["storage"].update(root="/dev/sda7", installed=False),
            lambda v: v["storage"].update(root="/dev/sda7", installed=True, writable_block_devices=["sda4"]),
            lambda v: v.update(seats=[]),
            lambda v: v.update(schema=2),
            lambda v: v["session"].update(user_state="lingering"),
            lambda v: v["session"].update(target_state="inactive"),
            lambda v: v["session"].update(service="failed"),
            lambda v: v["session"].update(access_denied=["/dev/spidev0.0"]),
            lambda v: v.pop("session"),
            lambda v: v["fan"].update(service="failed"),
            lambda v: v["fan"].update(service="inactive"),
            lambda v: v["fan"].update(state=None),
            lambda v: v.pop("fan"),
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

    def test_installed_system_writes_only_its_data_partition(self):
        mounts = ("/dev/sda7 / ext4 ro,noatime 0 0\n/dev/sda16 /data ext4 rw,noatime 0 0\n"
                  "overlay /etc overlay rw,relatime 0 0\n/dev/sda16 /var ext4 rw,noatime 0 0\n"
                  "/dev/sda16 /home ext4 rw,noatime 0 0\ntmpfs /run tmpfs rw 0 0")
        hw.validate_mounts(mounts, installed=True)
        hw.validate_mounts(mounts + "\n/dev/sda16 /var/log ext4 rw,noatime 0 0", installed=True)
        for bad in (mounts.replace("/ ext4 ro", "/ ext4 rw"),
                    mounts + "\n/dev/sda5 /mnt ext4 rw 0 0",
                    mounts.replace("/dev/sda16 /data ext4 rw,noatime 0 0\n", "")):
            with self.assertRaises(ValueError):
                hw.validate_mounts(bad, installed=True)
        with self.assertRaises(ValueError):
            hw.validate_mounts(mounts)

    def test_installed_data_partition_must_be_writable(self):
        mounts = "/dev/sda7 / ext4 ro,noatime 0 0\n/dev/sda16 /data ext4 rw,noatime 0 0"
        self.assertEqual(hw.data_device(mounts), "sda16")
        hw.validate_storage("BLOCK sda 1\nBLOCK sda7 1\nBLOCK sda16 0\nBLOCK loop0 0", "sda16")
        # A mainline kernel: the data partition's disk is writable too, its other partitions not.
        hw.validate_storage("BLOCK sda 0\nBLOCK sda7 1\nBLOCK sda16 0\nBLOCK sdb 1", "sda16")
        with self.assertRaises(ValueError):
            hw.validate_storage("BLOCK sda 1\nBLOCK sdb 0\nBLOCK sda16 0", "sda16")
        for bad in ("BLOCK sda 1\nBLOCK sda7 1\nBLOCK sda16 1",  # writeback would fail
                    "BLOCK sda 1\nBLOCK sda7 0\nBLOCK sda16 0"):  # another partition writable
            with self.assertRaises(ValueError):
                hw.validate_storage(bad, "sda16")
        with self.assertRaises(ValueError):
            hw.validate_storage("BLOCK sda 1\nBLOCK sda16 0")  # not installed: all read-only

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


VALVE_SESSION_PASSING = {
    "schema": 1, "status": "passed", "errors": [], "seconds": 30, "startup_seconds": 21,
    "session": {"target": "gamescope-session.target", "target_state": "active", "steamvr": "active",
                "gamescope": "active", "failed_units": [], "restored": "active"},
    "compositor": {"started": True, "gamescope_client": True, "asserts": 0, "present_wait_errors": 2689},
    "frames": {"target": "msm", "commits": 2700, "commit_hz": 89.7, "late": 2, "stalls": 0, "max_interval_ms": 22.3,
               "vblank_hz": 90.0, "gpu_jobs": 2700, "gpu_ms": {"mean": 5.9}},
    "devices": [{"index": 0, "position": [-0.01, 1.61, 0.07], "rotation": [-2.5, -1.56, 2.42]},
                {"index": 1, "position": [0.09, 0.96, -0.6], "rotation": [-0.27, 0.05, 0.86]},
                {"index": 2, "position": [0.48, 0.96, -0.24], "rotation": [-0.31, -0.13, -0.93]}],
    "max_temp_c": {"before": 80.1, "after_run": 92.3},
    "fan": {"before": FAN_STATE, "after_run": {**FAN_STATE, "duty": 50, "rpm": 8500, "hottest_mean_c": 90.4}},
    "performance": {"status": "passed", "failures": [], "commit_hz": 90.0, "late_percent": 0.07, "stalls": 0,
                    "present_wait_timeouts": 0, "hottest_c": 61.5, "window_seconds": 30.0},
}


class ValveSessionTests(unittest.TestCase):
    def test_passing_session(self):
        userspace.validate_valve_session(VALVE_SESSION_PASSING)
        # Without the fan daemon both states are null.
        userspace.validate_valve_session({**VALVE_SESSION_PASSING, "fan": {"before": None, "after_run": None}})

    def test_failures_are_rejected(self):
        devices = VALVE_SESSION_PASSING["devices"]
        cases = [
            ("not run", None),
            ("failed", {"status": "failed", "errors": ["steamvr.service is failed"]}),
            ("SteamVR stopped", {"session": {**VALVE_SESSION_PASSING["session"], "steamvr": "inactive"}}),
            ("not restored", {"session": {**VALVE_SESSION_PASSING["session"], "restored": "failed"}}),
            ("no compositor", {"compositor": {"started": False, "gamescope_client": True, "asserts": 0}}),
            ("no gamescope", {"compositor": {"started": True, "gamescope_client": False, "asserts": 0}}),
            ("assertion", {"compositor": {"started": True, "gamescope_client": True, "asserts": 1}}),
            ("no frames", {"frames": None}),
            ("not msm", {"frames": {**VALVE_SESSION_PASSING["frames"], "target": "other"}}),
            ("nothing committed", {"frames": {**VALVE_SESSION_PASSING["frames"], "commits": 0}}),
            ("stalled", {"frames": {**VALVE_SESSION_PASSING["frames"], "commit_hz": 12.0}}),
            ("vblanks", {"frames": {**VALVE_SESSION_PASSING["frames"], "vblank_hz": 60.0}}),
            ("headset only", {"devices": devices[:1]}),
            ("pose not a number", {"devices": [devices[0], {**devices[1], "rotation": ["nan", 0, 0]}, devices[2]]}),
            ("controller at the origin", {"devices": [devices[0], {**devices[1], "position": [0, 0, 0]}, devices[2]]}),
            ("no fan record", {"fan": None}),
            ("fan record not a state", {"fan": {"before": "30 %", "after_run": None}}),
            ("no performance verdict", {"performance": None}),
            ("performance missed", {"performance": {"status": "failed", "failures": ["52.0 commits/s"]}}),
        ]
        for name, change in cases:
            value = None if change is None else {**VALVE_SESSION_PASSING, **change}
            with self.subTest(name), self.assertRaises(ValueError):
                userspace.validate_valve_session(value)
