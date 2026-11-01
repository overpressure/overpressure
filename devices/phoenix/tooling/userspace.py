"""Roadmap step 4: what the SteamOS userspace needs from the stock PICO 4 kernel.

The table is checked against a kernel's embedded config (IKCONFIG), so a new
firmware build is re-evaluated instead of trusting the analysis in docs/userspace.md.
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


SESSION_USER = "steamos"  # the Frame's session user, logged in by pico4-session.service (docs/session.md)
# Valve's session's processes that still run for seconds after pico4-session.service stopped
# (its user manager stops them), and how long the runner waits for them.
SESSION_PROCESSES = "vrserver|vrcompositor|vrwebhelper|gamescope-wl|steam|steamwebhelper"
SESSION_STOP_SECONDS = 60
# Valve's Turnip.
TURNIP_ICD = "/usr/share/vulkan/icd.d/freedreno_icd.aarch64.json"


def validate_check(value, require_vulkan: bool = False, require_fan: bool = False) -> None:
    """Validate pico4-userspace-check JSON from the headset or QEMU.

    Besides systemd, /dev and the storage policy, the session user must be
    logged in with Valve's session target active. The fan
    service must run wherever the lens MCU's UART exists, which require_fan
    demands (the headset), and the MCU must have acknowledged its duty without an
    error since: a session must not run without cooling.
    """
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
    # The check holds /dev to the kernel's kind: a tmpfs copy on the stock kernel, devtmpfs on mainline.
    if dev.get("fstype") not in ("tmpfs", "devtmpfs") or dev.get("missing_nodes") or not dev.get("udev_devices"):
        raise ValueError("/dev is incomplete")
    storage = value.get("storage") or {}
    # The installed system's root is super, read-only; the check verified it and lists every
    # writable partition except userdata, the data partition.
    if storage.get("writable_block_devices") or not (
            str(storage.get("root", "")).startswith("/dev/zram") or storage.get("installed") is True):
        raise ValueError("Storage policy violated: physical block devices must stay read-only")
    link = (value.get("usb_link") or {}).get("status", "absent")
    if link not in ("unchanged", "absent"):
        raise ValueError(f"USB link to the PC changed during the handoff: {link}")
    if "seat0" not in (value.get("seats") or []):
        raise ValueError("logind has no seat0")
    session = value.get("session") or {}
    if (session.get("user") != SESSION_USER or session.get("user_state") != "active"
            or session.get("service") != "active" or session.get("target_state") != "active"):
        raise ValueError(f"The {SESSION_USER} session is not running: {session}")
    if session.get("access_denied"):
        raise ValueError(f"The {SESSION_USER} user cannot open {', '.join(session['access_denied'])}")
    fan = value.get("fan") or {}
    service = fan.get("service")
    if service not in ("active", "skipped") or (require_fan and service != "active"):
        raise ValueError(f"The fan service is not running (docs/cooling.md): {service}")
    if service == "active":
        state = fan.get("state")
        if not isinstance(state, dict):
            raise ValueError("The fan daemon writes no state")
        if state.get("error"):
            raise ValueError(f"The lens MCU fails the fan (docs/cooling.md): {state['error']}")
        if type(state.get("duty")) is not int:
            raise ValueError("The lens MCU has not acknowledged a fan duty (docs/cooling.md)")
    if require_vulkan:
        vk = value.get("vulkan") or {}
        if vk.get("status") != "passed":
            raise ValueError("Turnip Vulkan test did not pass in the userspace")
        validate_vulkan(vk.get("result"))


TRICKLE_MA = 200  # a charging battery below this, not yet nearly full, gets a warning


def power_warnings(power) -> list:
    """Warnings for the charger state pico4-userspace-check reports; they fail nothing.

    The charger limits its input by BC1.2 detection: a standard port (SDP, real_type USB)
    gives it about 0.5 A at 4.7 V, less than a session draws, so a tethered headset drains
    its battery there; a charging port (CDP) gives 1.5 A, about what Valve's session draws while
    it renders, and a wall charger keeps up (docs/development.md). The charger reports
    "Charging" also while the battery drains, so the battery's current decides.
    """
    if not isinstance(power, dict) or not power.get("usb_online"):
        return []
    port, input_ma = power.get("port") or "unknown", power.get("input_ma")
    supply = f"{port} port, {input_ma} mA input" if isinstance(input_ma, int) else f"{port} port"
    battery = power.get("battery") or {}
    level, current = battery.get("capacity"), battery.get("current_ma")
    at = f" at {level} %" if isinstance(level, int) else ""
    warnings = []
    if battery.get("status") == "Discharging" or (isinstance(current, int) and current < 0):
        warnings.append(f"the battery discharges while tethered{at} ({supply}): use a charging port or a wall charger")
    elif port == "USB":
        warnings.append(f"standard USB port ({supply}): the charger cannot keep up with a session; "
                        "use a charging port or a wall charger")
    elif (battery.get("status") == "Charging" and isinstance(current, int) and current < TRICKLE_MA
          and isinstance(level, int) and level < 95):
        warnings.append(f"the battery charges at only {current} mA{at} ({supply})")
    return warnings


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


DISPLAY_HZ = 90.0
DISPLAY_RATE_MARGIN = 0.95   # frames and commits per second, against the display rate
DISPLAY_EYE_CONTRAST = 20.0  # mean red/blue difference that tells the eyes apart


VR_DISPLAY_SECONDS = 15


def validate_vr_display(value, stats) -> None:
    """Validate `pico4-vrdisplay-test` JSON and the pico4-vrdisplay layer's statistics.

    SteamVR's compositor reaches the panels through this path (docs/session.md): the
    display must be found and acquired through DRM, frames and vblank events
    must run at the display rate, and the scanned-out image must have the
    client's red left and blue right half on the matching panels.
    """
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise ValueError("Display layer check produced no schema-1 result")
    if value.get("status") != "pass":
        raise ValueError("Display layer check failed: " + str(value.get("error", "frame or vblank rate")))
    if value.get("display") != "PICO 4" or value.get("acquire") != "drm":
        raise ValueError(f"Display {value.get('display')!r} was not acquired through DRM ({value.get('acquire')})")
    if (value.get("frame_hz") or 0) < DISPLAY_RATE_MARGIN * DISPLAY_HZ:
        raise ValueError(f"Display layer frames ran at {value.get('frame_hz')} Hz")
    if abs((value.get("vblank_hz") or 0) - DISPLAY_HZ) > 0.01 * DISPLAY_HZ:
        raise ValueError(f"The swapchain's vblank counter advanced at {value.get('vblank_hz')} Hz")
    # The client re-arms its vblank event between events and can miss one.
    if (value.get("event_hz") or 0) < 0.9 * DISPLAY_HZ:
        raise ValueError(f"Vblank events arrived at {value.get('event_hz')} Hz")
    counter = value.get("counter") or [0, 0]
    if len(counter) != 2 or counter[1] <= counter[0]:
        raise ValueError(f"The swapchain's vblank counter did not advance: {counter}")
    if not isinstance(stats, dict) or stats.get("target") != "pico4_vrdisplay" or stats.get("fake"):
        raise ValueError("The pico4-vrdisplay layer did not drive the panels")
    if stats.get("retire_timeouts") or stats.get("gpu_timeouts") or not stats.get("commits"):
        raise ValueError("The display layer lost fences or committed nothing")
    if (stats.get("commit_hz") or 0) < DISPLAY_RATE_MARGIN * DISPLAY_HZ:
        raise ValueError(f"The display layer committed {stats.get('commit_hz')} frames per second")
    means = stats.get("eye_mean_rgb")
    if not means or len(means) != 2:
        raise ValueError("The display layer recorded no scanned-out frame")
    (left_r, _, left_b), (right_r, _, right_b) = means
    if left_r - left_b < DISPLAY_EYE_CONTRAST or right_b - right_r < DISPLAY_EYE_CONTRAST:
        raise ValueError(f"Scanned-out eye colours {means} do not match red left / blue right")



def validate_vr_sync(value) -> None:
    """Validate `pico4-vrsync-test` JSON: one timeline semaphore shared by two processes.

    SteamVR shares such semaphores (OPAQUE_FD) between its compositor and other
    processes; on this kernel the display layer emulates them over sync files
    (docs/session.md). The waiting process's GPU work must start after the
    signalling work ended, host waits and signals must reach the other process,
    a wait submitted before its signal must hold until the signal arrives, and a
    wait for an earlier batch of the same submission must not hold at all.
    """
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise ValueError("Semaphore sharing check produced no schema-1 result")
    failed = [k for k in ("gpu_wait", "host_wait", "host_signal", "early_wait", "same_submit")
              if value.get(k) is not True]
    if value.get("child_exit") != 0:
        failed.append("child process")
    if failed or value.get("status") != "pass":
        raise ValueError("Semaphore sharing failed: " + (", ".join(failed) or "status"))
    start, end = value.get("child_start_ticks") or 0, value.get("parent_end_ticks") or 0
    if not 0 < end <= start:
        raise ValueError(f"The waiting GPU work started at {start}, before the signalling work ended at {end}")


VALVE_SESSION_SECONDS = 30  # bounded: the session heats the headset quickly (docs/cooling.md)
VALVE_SESSION_START_C = 70.0  # the hottest thermal zone before the session starts...
VALVE_SESSION_COOLDOWN_SECONDS = 180  # ...or this long a wait for it
# Integration only needs SteamVR's compositor to present; the check's performance
# verdict (session-performance, docs/session.md) holds it to the display rate.
VALVE_SESSION_MIN_HZ = 0.4 * DISPLAY_HZ


def validate_valve_session(value) -> None:
    """Validate `pico4-valve-session-check` JSON: Valve's VR session on the panels.

    gamescope's session must run with SteamVR, whose compositor presents
    through the pico4-vrdisplay layer without lost fences and shows an image;
    SteamVR must report the headset and both controllers, the controllers
    placed by the arm model; and the configured session must come back. The
    fan daemon's state before and after is recorded (null without the daemon).
    The performance verdict over the run after startup must pass as well.
    """
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise ValueError("Valve session check produced no schema-1 result")
    if value.get("status") != "passed":
        raise ValueError("Valve session check failed: " + "; ".join(value.get("errors") or ["no reason"]))
    session = value.get("session") or {}
    states = {k: session.get(k) for k in ("target_state", "steamvr", "gamescope", "restored")}
    if any(state != "active" for state in states.values()):
        raise ValueError(f"Valve's session did not run or return: {states}")
    compositor = value.get("compositor") or {}
    if not compositor.get("started") or not compositor.get("gamescope_client") or compositor.get("asserts"):
        raise ValueError(f"SteamVR's compositor did not run with gamescope: {compositor}")
    # The stock kernel: the display layer's statistics. A mainline kernel's msm: its own events
    # (msm-frame-stats), without an image: the compressed scanout is readable only by the master.
    layer = value.get("layer")
    target = layer.get("target") if isinstance(layer, dict) else None
    if target not in ("pico4_vrdisplay", "msm") or layer.get("fake"):
        raise ValueError("SteamVR's compositor did not present through the pico4-vrdisplay layer or msm")
    if not layer.get("commits") or layer.get("retire_timeouts") or layer.get("gpu_timeouts"):
        raise ValueError("The display lost fences or committed nothing in Valve's session")
    if (layer.get("commit_hz") or 0) < VALVE_SESSION_MIN_HZ:
        raise ValueError(f"SteamVR's compositor presented {layer.get('commit_hz')} frames per second")
    if abs((layer.get("vblank_hz") or 0) - DISPLAY_HZ) > 0.01 * DISPLAY_HZ:
        raise ValueError(f"The panels' vblanks ran at {layer.get('vblank_hz')} Hz")
    means = layer.get("eye_mean_rgb")
    if target == "pico4_vrdisplay" and (not means or len(means) != 2 or any(max(eye) < 2.0 for eye in means)):
        raise ValueError(f"SteamVR's scanned-out image is black or missing: {means}")
    devices = {d.get("index"): d for d in value.get("devices") or [] if isinstance(d, dict)}
    if not {0, 1, 2} <= devices.keys():
        raise ValueError(f"SteamVR reports devices {sorted(devices)}, not the headset and two controllers")
    for index in (0, 1, 2):
        numbers = list(devices[index].get("position") or []) + list(devices[index].get("rotation") or [])
        if len(numbers) != 6 or not all(isinstance(n, (int, float)) and math.isfinite(n) for n in numbers):
            raise ValueError(f"SteamVR's pose of device {index} is not valid: {devices[index]}")
    for index in (1, 2):
        if not any(devices[index]["position"]):
            raise ValueError(f"Controller {index} has no position in SteamVR")
    fan = value.get("fan")
    if not isinstance(fan, dict) or any(fan.get(k) is not None and not isinstance(fan.get(k), dict)
                                        for k in ("before", "after_run")):
        raise ValueError(f"The Valve session check recorded no fan state: {fan}")
    performance = value.get("performance")
    if not isinstance(performance, dict) or performance.get("status") not in ("passed", "failed"):
        raise ValueError("The Valve session check gave no performance verdict")
    if performance["status"] != "passed":
        raise ValueError("Valve's session missed its performance limits: "
                         + "; ".join(performance.get("failures") or ["no reason"]))


def format_valve_performance(performance) -> str:
    """One line for the runner's output: the verdict and what it measured."""
    if not isinstance(performance, dict):
        return "performance: no verdict"
    text = f"performance: {performance.get('status')}"
    if performance.get("commit_hz") is not None:
        text += (f" ({performance['commit_hz']} commits/s, {performance.get('late_percent')} % late, "
                 f"{performance.get('stalls')} stalls, {performance.get('present_wait_timeouts')} present-wait "
                 f"timeouts, hottest {performance.get('hottest_c')} °C over {performance.get('window_seconds')} s; "
                 f"startup stalls {(performance.get('startup') or {}).get('stalls')})")
    failures = performance.get("failures") or []
    return text + ("; " + "; ".join(failures) if failures else "")
