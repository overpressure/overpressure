"""Roadmap step 4: what the SteamOS userspace needs from the stock PICO 4 kernel.

The table is checked against a kernel's embedded config (IKCONFIG), so a new
firmware build is re-evaluated instead of trusting the analysis in USERSPACE.md.
"""
from __future__ import annotations

import math

from dataclasses import dataclass

from .graphics import validate_vulkan

# Severity: "required" options must be present (or have an adaptation that
# replaces them); "degraded" ones only cost a feature.
REQUIRED, DEGRADED = "required", "degraded"


@dataclass(frozen=True)
class Requirement:
    option: str
    level: str
    user: str
    without: str  # consequence and the adaptation used when the option is missing


REQUIREMENTS = (
    Requirement("DEVTMPFS", REQUIRED, "systemd, udev",
                "no automatic /dev: stage 1 hands over a tmpfs copy, udev rule 00-pico4-devnodes creates nodes"),
    Requirement("CGROUPS", REQUIRED, "systemd", "systemd cannot run"),
    Requirement("INOTIFY_USER", REQUIRED, "systemd", "systemd cannot run"),
    Requirement("SIGNALFD", REQUIRED, "systemd", "systemd cannot run"),
    Requirement("TIMERFD", REQUIRED, "systemd", "systemd cannot run"),
    Requirement("EPOLL", REQUIRED, "systemd", "systemd cannot run"),
    Requirement("UNIX", REQUIRED, "systemd, D-Bus", "systemd cannot run"),
    Requirement("TMPFS", REQUIRED, "systemd", "no /run, /dev copy or /tmp"),
    Requirement("ZRAM", REQUIRED, "handoff", "no compressed RAM disk for the root filesystem"),
    Requirement("EXT4_FS", REQUIRED, "handoff", "no root filesystem type (no btrfs/squashfs/erofs here)"),
    Requirement("FHANDLE", DEGRADED, "systemd, udev",
                "name_to_handle_at() missing: systemd falls back to mount-ID/st_dev checks"),
    Requirement("CGROUP_BPF", DEGRADED, "systemd", "no DevicePolicy=/IP filtering in units"),
    Requirement("SECCOMP_FILTER", DEGRADED, "systemd", "no SystemCallFilter= sandboxing"),
    Requirement("NET_NS", DEGRADED, "systemd", "PrivateNetwork= unavailable"),
    Requirement("USER_NS", DEGRADED, "systemd, Steam pressure-vessel",
                "PrivateUsers= ignored; Steam Linux Runtime containers need setuid bubblewrap"),
    Requirement("PID_NS", DEGRADED, "Steam pressure-vessel", "Steam Linux Runtime cannot isolate PIDs"),
    Requirement("IPC_NS", DEGRADED, "systemd", "PrivateIPC= ignored"),
    Requirement("UTS_NS", DEGRADED, "systemd", "ProtectHostname= ignored"),
    Requirement("MEMCG", DEGRADED, "systemd-oomd, logind", "no memory accounting: systemd-oomd is masked"),
    Requirement("CGROUP_PIDS", DEGRADED, "systemd", "TasksMax= has no effect"),
    Requirement("SYSVIPC", DEGRADED, "Xwayland MIT-SHM, some games",
                "shmget() fails: clients must use memfd/fd-passing shared memory"),
    Requirement("BINFMT_MISC", DEGRADED, "FEX (x86 games)",
                "x86 binaries cannot be registered: FEX must be invoked explicitly"),
    Requirement("HIDRAW", DEGRADED, "Steam Input, SteamVR", "no /dev/hidraw: controllers only via evdev/uhid"),
    Requirement("VT", DEGRADED, "logind", "seat0 without VTs (supported); no text consoles"),
    Requirement("DMIID", DEGRADED, "udev, steamos-manager", "device identity from /proc/device-tree instead"),
    Requirement("AUTOFS_FS", DEGRADED, "systemd", "no .automount units"),
    Requirement("EXT4_FS_POSIX_ACL", DEGRADED, "journald", "no ACLs on the zram root (device ACLs live on /dev tmpfs)"),
    Requirement("TMPFS_POSIX_ACL", DEGRADED, "logind", "no uaccess device ACLs for the session user"),
)

# Enabled options (one Android-only) that change how the userspace must behave.
KERNEL_NOTES = {
    "ANDROID_PARANOID_NETWORK": "IPv4/IPv6/Bluetooth sockets need CAP_NET_RAW or groups aid_inet(3003)/"
                                "aid_net_raw(3004)/aid_bt(3001): created by sysusers, granted to the session user",
    "UEVENT_HELPER": "legacy hotplug helper: emptied at the handoff, udev uses netlink",
    "FW_LOADER_USER_HELPER_FALLBACK": "firmware is loaded from /usr/lib/firmware first; udev has no fallback loader",
}

SYSTEMD_MAX_MAJOR = 257  # 258 raised the minimum kernel to 5.4


def _enabled(config: dict[str, str], option: str) -> bool:
    if option == "AUTOFS_FS":
        return any(config.get(f"CONFIG_{o}") in ("y", "m") for o in ("AUTOFS_FS", "AUTOFS4_FS"))
    return config.get(f"CONFIG_{option}") in ("y", "m")


def kernel_compat(config: dict[str, str], version: str) -> dict:
    """Evaluate REQUIREMENTS against an IKCONFIG dict (CONFIG_X -> value)."""
    if not config:
        raise ValueError("kernel has no embedded configuration (IKCONFIG)")
    rows = [{"option": r.option, "level": r.level, "enabled": _enabled(config, r.option),
             "user": r.user, "without": r.without} for r in REQUIREMENTS]
    quirks = [{"option": k, "effect": v} for k, v in KERNEL_NOTES.items() if _enabled(config, k)]
    # DEVTMPFS is required by systemd, but the handoff replaces it.
    blocking = [r["option"] for r in rows if r["level"] == REQUIRED and not r["enabled"]
                and r["option"] != "DEVTMPFS"]
    release = version.split()[2] if version.startswith("Linux version ") else version
    major_minor = tuple(int(x) for x in release.split("-")[0].split(".")[:2]) if release else (0, 0)
    return {
        "kernel": release,
        "systemd": f"{SYSTEMD_MAX_MAJOR}.x (258+ needs Linux >= 5.4)" if major_minor < (5, 4) else "any",
        "blocking": blocking,
        "degraded": [r["option"] for r in rows if r["level"] == DEGRADED and not r["enabled"]],
        "requirements": rows,
        "notes": quirks,
    }


def format_compat(report: dict) -> str:
    lines = [f"kernel {report['kernel']}: systemd {report['systemd']}"]
    for r in report["requirements"]:
        mark = "ok " if r["enabled"] else ("!! " if r["level"] == REQUIRED else "-- ")
        tail = "" if r["enabled"] else f"  -> {r['without']}"
        lines.append(f"  {mark}{r['option']:<20} {r['level']:<9} {r['user']}{tail}")
    for q in report["notes"]:
        lines.append(f"  ** {q['option']:<20} note      {q['effect']}")
    lines.append("blocking: " + (", ".join(report["blocking"]) or "none"))
    return "\n".join(lines)


def validate_check(value, require_vulkan: bool = False) -> None:
    """Validate pico4-userspace-check JSON from the headset or QEMU."""
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise ValueError("Userspace check produced no schema-1 result")
    errors = value.get("errors")
    if value.get("status") != "passed" or errors:
        raise ValueError("Userspace check failed: " + "; ".join(map(str, errors or ["unknown"])))
    systemd = value.get("systemd") or {}
    if (systemd.get("pid1") != "/usr/lib/systemd/systemd" or systemd.get("state") != "running"
            or systemd.get("failed_units") or not str(systemd.get("version", "")).startswith(f"{SYSTEMD_MAX_MAJOR}")):
        raise ValueError("systemd is not running cleanly as PID 1")
    dev = value.get("dev") or {}
    if dev.get("fstype") != "tmpfs" or dev.get("missing_nodes") or not dev.get("udev_devices"):
        raise ValueError("/dev is incomplete")
    storage = value.get("storage") or {}
    if storage.get("writable_block_devices") or not str(storage.get("root", "")).startswith("/dev/zram"):
        raise ValueError("Storage policy violated: physical block devices must stay read-only")
    link = (value.get("usb_link") or {}).get("status", "absent")
    if link not in ("unchanged", "absent"):
        raise ValueError(f"USB link to the PC changed during the handoff: {link}")
    if "seat0" not in (value.get("seats") or []):
        raise ValueError("logind has no seat0")
    if require_vulkan:
        vk = value.get("vulkan") or {}
        if vk.get("status") != "passed":
            raise ValueError("Turnip/KGSL Vulkan test did not pass in the userspace")
        validate_vulkan(vk.get("result"))


IMU_RATE_HZ = 500.0  # requested; the SSC runs the ICM-42688 at 512 Hz for it


def validate_imu(value, rate: float = IMU_RATE_HZ) -> None:
    """Validate `pico4-imu stream --check` JSON: head IMU streaming from the SLPI."""
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise ValueError("IMU check produced no schema-1 result")
    if value.get("status") != "pass" or value.get("failures"):
        raise ValueError("Head IMU check failed: " + "; ".join(map(str, value.get("failures") or [value.get("error", "unknown")])))
    if value.get("requested_rate_hz") != rate:
        raise ValueError(f"IMU check ran at {value.get('requested_rate_hz')} Hz, expected {rate}")
    streams = value.get("streams") or {}
    for name in ("accel", "gyro"):
        stream = streams.get(name) or {}
        if not stream.get("samples") or stream.get("non_increasing_timestamps"):
            raise ValueError(f"IMU {name} stream is missing or not monotonic")


def imu_tilt_deg(value) -> float:
    """Tilt of the HMD's up axis from vertical, from `pico4-imu stream` accel means.

    The HMD's up axis is the IMU's +X (factory camera extrinsics, XR.md).
    """
    mean = ((value.get("streams") or {}).get("accel") or {}).get("mean")
    if not mean or len(mean) != 3:
        raise ValueError("IMU result has no accelerometer mean")
    norm = math.sqrt(sum(v * v for v in mean))
    if norm == 0:
        raise ValueError("IMU accelerometer mean is zero")
    return math.degrees(math.acos(max(-1.0, min(1.0, mean[0] / norm))))


POSE_TILT_TOLERANCE_DEG = 2.0
POSE_HEADING_RATE_LIMIT = 0.5  # deg/s on a still headset, uncorrected gyro bias


def validate_openxr_pose(value, accel_tilt_deg: float) -> None:
    """Validate `pico4-openxr-pose` JSON: head pose from Monado's PICO 4 driver.

    On a still headset the pose must be valid and tracked after one second,
    tilted like the accelerometer says, and drift slowly about the vertical.
    """
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise ValueError("OpenXR pose check produced no schema-1 result")
    if value.get("status") != "pass":
        raise ValueError("OpenXR pose check failed: " + str(value.get("error", "invalid poses after warm-up")))
    if "PICO 4" not in str(value.get("system")) or not value.get("orientation_tracking"):
        raise ValueError(f"OpenXR system is {value.get('system')!r}, not the PICO 4 driver with orientation tracking")
    if not value.get("orientation_tracked") or value.get("invalid_after_warmup"):
        raise ValueError("OpenXR head orientation was not tracked throughout")
    tilt = value.get("final_tilt_from_vertical_deg")
    if tilt is None or abs(tilt - accel_tilt_deg) > POSE_TILT_TOLERANCE_DEG:
        raise ValueError(f"OpenXR tilt {tilt} deg disagrees with the accelerometer ({accel_tilt_deg:.2f} deg)")
    rate = value.get("heading_rate_deg_per_s")
    if rate is None or abs(rate) > POSE_HEADING_RATE_LIMIT:
        raise ValueError(f"OpenXR heading drifts at {rate} deg/s")


DISPLAY_HZ = 90.0
DISPLAY_SECONDS = 10
DISPLAY_RATE_MARGIN = 0.95   # frames and commits per second, against the display rate
DISPLAY_EYE_CONTRAST = 20.0  # mean red/blue difference that tells the eyes apart


def validate_openxr_display(value, kms) -> None:
    """Validate `pico4-openxr-display` JSON and the statistics of Monado's PICO 4 KMS target.

    Frames must run at the display rate through the fenced KMS path, with the
    left eye's content (red L) on the left panel and the right eye's (blue R) on
    the right one, measured in the image that was scanned out.
    """
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise ValueError("OpenXR display check produced no schema-1 result")
    if value.get("status") != "pass":
        raise ValueError("OpenXR display check failed: " + str(value.get("error", "frame rate or tracking")))
    if "PICO 4" not in str(value.get("system")):
        raise ValueError(f"OpenXR system is {value.get('system')!r}, not the PICO 4 driver")
    if (value.get("frame_hz") or 0) < DISPLAY_RATE_MARGIN * DISPLAY_HZ:
        raise ValueError(f"OpenXR frames ran at {value.get('frame_hz')} Hz")
    if (value.get("late_frames") or 0) > max(1, (value.get("frames") or 0) // 100):
        raise ValueError(f"{value.get('late_frames')} late OpenXR frames")
    if not isinstance(kms, dict) or kms.get("target") != "pico4_kms":
        raise ValueError("Monado did not present through the PICO 4 KMS target")
    if kms.get("retire_timeouts") or not kms.get("commits"):
        raise ValueError("The KMS target lost display fences or committed nothing")
    if abs((kms.get("vblank_hz") or 0) - DISPLAY_HZ) > 0.01 * DISPLAY_HZ:
        raise ValueError(f"Panel refresh measured at {kms.get('vblank_hz')} Hz")
    if (kms.get("commit_hz") or 0) < DISPLAY_RATE_MARGIN * DISPLAY_HZ:
        raise ValueError(f"Monado committed {kms.get('commit_hz')} frames per second")
    means = kms.get("eye_mean_rgb")
    if not means or len(means) != 2:
        raise ValueError("The KMS target recorded no scanned-out frame")
    (left_r, _, left_b), (right_r, _, right_b) = means
    if left_r - left_b < DISPLAY_EYE_CONTRAST or right_b - right_r < DISPLAY_EYE_CONTRAST:
        raise ValueError(f"Scanned-out eye colours {means} do not match the left/right test pattern")

