# SPDX-License-Identifier: Unlicense
"""The userspace's host-side checks: SSH access, power warnings, check results, mounts, the IMU and Valve's session."""

import copy
import unittest
from pathlib import Path
from typing import Any

from devices.phoenix.tooling import userspace

# `pico4-fan state` on the headset (docs/cooling.md).
FAN_STATE: dict[str, Any] = {
    "profile": "late",
    "duty": 30,
    "reason": "table",
    "boost": False,
    "rpm": 5700,
    "hottest": "cpu-1-4-usr",
    "hottest_mean_c": 61.2,
    "error": None,
}

PASSING: dict[str, Any] = {
    "schema": 1,
    "status": "passed",
    "errors": [],
    "kernel": "7.2.0-pico4",
    "systemd": {"pid1": "/usr/lib/systemd/systemd", "version": "257.7", "state": "running", "failed_units": []},
    "dev": {"fstype": "devtmpfs", "missing_nodes": [], "udev_devices": 412},
    "storage": {"root": "/dev/sda7", "installed": True, "writable_block_devices": []},
    "seats": ["seat0"],
    "packages": 348,
    "session": {
        "user": "steamos",
        "user_state": "active",
        "service": "active",
        "target": "pico4-session.target",
        "target_state": "active",
        "failed_units": [],
        "monado_socket": "active",
        "monado_service": "inactive",
        "access_denied": [],
    },
    "fan": {"service": "active", "state": FAN_STATE},
    "vulkan": {
        "status": "passed",
        "result": {
            "status": "passed",
            "driver": "turnip",
            "width": 256,
            "height": 256,
            "verified_pixels": 65536,
            "bad_pixels": 0,
        },
    },
}


class SshAccessTests(unittest.TestCase):
    """Keys only, on USB and Wi-Fi (docs/security.md), and no key in anything the build shares."""

    ROOT = Path(__file__).resolve().parents[1]

    def test_sshd_takes_keys_only(self):
        config = (self.ROOT / "userspace/rootfs/etc/ssh/sshd_config.d/50-pico4.conf").read_text(encoding="utf-8")
        settings = dict(
            line.split(None, 1)
            for line in config.splitlines()
            if line and not line.startswith("#") and not line.startswith("HostKey")
        )
        self.assertEqual(settings["PermitRootLogin"], "prohibit-password")
        self.assertEqual(settings["PermitEmptyPasswords"], "no")
        self.assertEqual(settings["PasswordAuthentication"], "no")
        self.assertEqual(settings["KbdInteractiveAuthentication"], "no")
        self.assertEqual(settings["AuthenticationMethods"], "publickey")

    def test_no_account_has_a_usable_password(self):
        build = (self.ROOT / "scripts/build-userspace.sh").read_text(encoding="utf-8")
        self.assertNotIn("passwd -d", build)
        self.assertIn("in_t usermod -p '*' root", build)
        self.assertIn("in_t usermod -p '*' \"$user\"", build)

    def test_no_key_in_the_image_sources(self):
        tree = self.ROOT / "userspace/rootfs"
        names = [
            p
            for p in tree.rglob("*")
            if p.name in ("authorized_keys", "id_ed25519", "id_rsa") or p.name.startswith("ssh_host_")
        ]
        self.assertEqual(names, [])

    def test_the_installed_stage_1_serves_ssh_with_keys_only(self):
        svc = (self.ROOT / "initramfs/usr/libexec/pico4/svc").read_text(encoding="utf-8")
        for service in ("telnetd", "httpd", "serial"):
            self.assertIn(f"{service})\n\tinstalled_mode && idle\n", svc)
        self.assertIn("if installed_mode; then\n\t\texec dropbear -F -E -R -s -p 22", svc)


class PowerWarningTests(unittest.TestCase):
    """A tethered headset that drains its battery is reported, never failed."""

    def power(
        self, port: str, status: str, input_ma: int = 1250, *, online: bool = True, current_ma: int = 800
    ) -> dict[str, Any]:
        return {
            "usb_online": online,
            "port": port,
            "input_ma": input_ma,
            "usb_mv": 4846,
            "battery": {"status": status, "capacity": 15, "current_ma": current_ma},
        }

    def test_charging_port_keeps_up(self):
        self.assertEqual(userspace.power_warnings(self.power("USB_CDP", "Charging")), [])
        self.assertEqual(userspace.power_warnings(self.power("USB_CDP", "Full")), [])

    def test_discharge_while_tethered_warns(self):
        (warning,) = userspace.power_warnings(self.power("USB", "Discharging", 490))
        self.assertIn("discharges while tethered at 15 %", warning)
        self.assertIn("USB port, 490 mA input", warning)

    def test_drain_while_charging_warns(self):
        # The charger said "Charging" while the battery lost 0.37 A under the session.
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


class CheckValidationTests(unittest.TestCase):
    def test_passing_check(self):
        userspace.validate_check(PASSING, require_vulkan=True)

    def test_fan_duty_must_be_acknowledged(self):
        # Acknowledged: the daemon's duty is the one the MCU acknowledged, without an error since.
        userspace.validate_check(PASSING)
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
            lambda v: v["dev"].update(fstype="tmpfs"),
            lambda v: v["storage"].update(writable_block_devices=["sda"]),
            lambda v: v["storage"].update(root="/dev/zram0", installed=False),
            lambda v: v["storage"].update(writable_block_devices=["sda4"]),
            lambda v: v["fan"].update(service="skipped"),
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


IMU_PASSING: dict[str, Any] = {
    "schema": 1,
    "status": "pass",
    "failures": [],
    "seconds": 10,
    "requested_rate_hz": 500.0,
    "streams": {
        "accel": {"samples": 5120, "rate_hz": 511.95, "non_increasing_timestamps": 0},
        "gyro": {"samples": 5120, "rate_hz": 511.95, "non_increasing_timestamps": 0},
    },
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
            (
                "time going backwards",
                {"streams": {**IMU_PASSING["streams"], "gyro": {"samples": 5000, "non_increasing_timestamps": 2}}},
            ),
        ]
        for name, change in cases:
            value = None if change is None else {**copy.deepcopy(IMU_PASSING), **change}
            with self.subTest(name), self.assertRaises(ValueError):
                userspace.validate_imu(value)


VALVE_SESSION_PASSING: dict[str, Any] = {
    "schema": 1,
    "status": "passed",
    "errors": [],
    "seconds": 30,
    "startup_seconds": 21,
    "session": {
        "target": "gamescope-session.target",
        "target_state": "active",
        "steamvr": "active",
        "gamescope": "active",
        "failed_units": [],
        "restored": "active",
    },
    "compositor": {"started": True, "gamescope_client": True, "asserts": 0, "present_wait_errors": 2689},
    "frames": {
        "target": "msm",
        "commits": 2700,
        "commit_hz": 89.7,
        "late": 2,
        "stalls": 0,
        "max_interval_ms": 22.3,
        "vblank_hz": 90.0,
        "gpu_jobs": 2700,
        "gpu_ms": {"mean": 5.9},
    },
    "devices": [
        {"index": 0, "position": [-0.01, 1.61, 0.07], "rotation": [-2.5, -1.56, 2.42]},
        {"index": 1, "position": [0.09, 0.96, -0.6], "rotation": [-0.27, 0.05, 0.86]},
        {"index": 2, "position": [0.48, 0.96, -0.24], "rotation": [-0.31, -0.13, -0.93]},
    ],
    "max_temp_c": {"before": 80.1, "after_run": 92.3},
    "fan": {"before": FAN_STATE, "after_run": {**FAN_STATE, "duty": 50, "rpm": 8500, "hottest_mean_c": 90.4}},
    "performance": {
        "status": "passed",
        "failures": [],
        "commit_hz": 90.0,
        "late_percent": 0.07,
        "stalls": 0,
        "hottest_c": 61.5,
        "window_seconds": 30.0,
    },
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
