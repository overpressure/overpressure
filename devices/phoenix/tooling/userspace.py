# SPDX-License-Identifier: Unlicense
"""Validators of the on-device checks' JSON, and their limits."""

from __future__ import annotations

import math

from .graphics import validate_vulkan

SYSTEMD_MAX_MAJOR = 257  # the Frame's systemd, pinned


SESSION_USER = "steamos"  # the Frame's session user, logged in by pico4-session.service (docs/session.md)
# Valve's session's processes that still run for seconds after pico4-session.service stopped
# (its user manager stops them), and how long the runner waits for them.
SESSION_PROCESSES = "vrserver|vrcompositor|vrwebhelper|gamescope-wl|steam|steamwebhelper"
SESSION_STOP_SECONDS = 60


def validate_check(value: object, *, require_vulkan: bool = False) -> None:
    """Validate pico4-userspace-check JSON from the installed system.

    Besides systemd, /dev and the storage policy, the session user must be
    logged in with Valve's session target active. The fan service must run, and
    the MCU must have acknowledged its duty without an error since: a session
    must not run without cooling.
    """
    if not isinstance(value, dict) or value.get("schema") != 1:
        msg = "Userspace check produced no schema-1 result"
        raise ValueError(msg)
    errors = value.get("errors")
    if value.get("status") != "passed" or errors:
        raise ValueError("Userspace check failed: " + "; ".join(map(str, errors or ["unknown"])))
    _validate_system(value)
    _validate_session(value.get("session") or {})
    _validate_fan(value.get("fan") or {})
    if require_vulkan:
        vk = value.get("vulkan") or {}
        if vk.get("status") != "passed":
            msg = "Turnip Vulkan test did not pass in the userspace"
            raise ValueError(msg)
        validate_vulkan(vk.get("result"))


def _validate_system(value: dict) -> None:
    """Refuse a system whose PID 1, /dev, storage policy, USB link or seat is not as it must be."""
    systemd = value.get("systemd") or {}
    if (
        systemd.get("pid1") != "/usr/lib/systemd/systemd"
        or systemd.get("state") != "running"
        or systemd.get("failed_units")
        or not str(systemd.get("version", "")).startswith(f"{SYSTEMD_MAX_MAJOR}")
    ):
        msg = "systemd is not running cleanly as PID 1"
        raise ValueError(msg)
    dev = value.get("dev") or {}
    if dev.get("fstype") != "devtmpfs" or dev.get("missing_nodes") or not dev.get("udev_devices"):
        msg = "/dev is incomplete"
        raise ValueError(msg)
    storage = value.get("storage") or {}
    # The installed system's root is super, read-only; the check verified it and lists every
    # writable partition except userdata, the data partition.
    if storage.get("writable_block_devices") or storage.get("installed") is not True:
        msg = "Storage policy violated: physical block devices must stay read-only"
        raise ValueError(msg)
    link = (value.get("usb_link") or {}).get("status", "absent")
    if link != "up":
        msg = f"USB link to the PC is not up: {link}"
        raise ValueError(msg)
    if "seat0" not in (value.get("seats") or []):
        msg = "logind has no seat0"
        raise ValueError(msg)


def _validate_session(session: dict) -> None:
    """Refuse a session user that is not logged in with Valve's session, or cannot open a device."""
    if (
        session.get("user") != SESSION_USER
        or session.get("user_state") != "active"
        or session.get("service") != "active"
        or session.get("target_state") != "active"
    ):
        msg = f"The {SESSION_USER} session is not running: {session}"
        raise ValueError(msg)
    if session.get("access_denied"):
        msg = f"The {SESSION_USER} user cannot open {', '.join(session['access_denied'])}"
        raise ValueError(msg)


def _validate_fan(fan: dict) -> None:
    """Refuse a fan service that does not run, or a duty the MCU has not acknowledged."""
    service = fan.get("service")
    if service != "active":
        msg = f"The fan service is not running (docs/cooling.md): {service}"
        raise ValueError(msg)
    state = fan.get("state")
    if not isinstance(state, dict):
        msg = "The fan daemon writes no state"
        raise ValueError(msg)  # noqa: TRY004  # a failed check like every other: the runner catches ValueError
    if state.get("error"):
        msg = f"The lens MCU fails the fan (docs/cooling.md): {state['error']}"
        raise ValueError(msg)
    if type(state.get("duty")) is not int:
        msg = "The lens MCU has not acknowledged a fan duty (docs/cooling.md)"
        raise ValueError(msg)


TRICKLE_MA = 200  # a charging battery below this, not yet nearly full, gets a warning
NEARLY_FULL_PERCENT = 95  # a battery from here on charges slowly by design


def power_warnings(power: object) -> list[str]:
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
        warnings.append(
            f"standard USB port ({supply}): the charger cannot keep up with a session; "
            "use a charging port or a wall charger"
        )
    elif (
        battery.get("status") == "Charging"
        and isinstance(current, int)
        and current < TRICKLE_MA
        and isinstance(level, int)
        and level < NEARLY_FULL_PERCENT
    ):
        warnings.append(f"the battery charges at only {current} mA{at} ({supply})")
    return warnings


IMU_RATE_HZ = 500.0  # requested; the SSC runs the ICM-42688 at 512 Hz for it


def validate_imu(value: object, rate: float = IMU_RATE_HZ) -> None:
    """Validate `pico4-imu stream --check` JSON: head IMU streaming from the SLPI."""
    if not isinstance(value, dict) or value.get("schema") != 1:
        msg = "IMU check produced no schema-1 result"
        raise ValueError(msg)
    if value.get("status") != "pass" or value.get("failures"):
        raise ValueError(
            "Head IMU check failed: " + "; ".join(map(str, value.get("failures") or [value.get("error", "unknown")]))
        )
    if value.get("requested_rate_hz") != rate:
        msg = f"IMU check ran at {value.get('requested_rate_hz')} Hz, expected {rate}"
        raise ValueError(msg)
    streams = value.get("streams") or {}
    for name in ("accel", "gyro"):
        stream = streams.get(name) or {}
        if not stream.get("samples") or stream.get("non_increasing_timestamps"):
            msg = f"IMU {name} stream is missing or not monotonic"
            raise ValueError(msg)


DISPLAY_HZ = 90.0

VALVE_SESSION_SECONDS = 30  # bounded: the session heats the headset quickly (docs/cooling.md)
VALVE_SESSION_START_C = 70.0  # the hottest thermal zone before the session starts...
VALVE_SESSION_COOLDOWN_SECONDS = 180  # ...or this long a wait for it
# Integration only needs SteamVR's compositor to present; the check's performance
# verdict (session-performance, docs/session.md) holds it to the display rate.
VALVE_SESSION_MIN_HZ = 0.4 * DISPLAY_HZ
#: The panels' vblank rate may differ from the display rate by this fraction.
VBLANK_TOLERANCE = 0.01
#: SteamVR's devices: the headset, then the two controllers.
HEADSET_INDEX, CONTROLLER_INDICES = 0, (1, 2)
#: A pose: position (x, y, z) and rotation, three numbers each.
POSE_NUMBERS = 6


def validate_valve_session(value: object) -> None:
    """Validate `pico4-valve-session-check` JSON: Valve's VR session on the panels.

    gamescope's session must run with SteamVR, whose compositor presents;
    SteamVR must report the headset and both controllers
    with positions; and the configured session must come back. The
    fan daemon's state before and after is recorded (null without the daemon).
    The performance verdict over the run after startup must pass as well.
    """
    if not isinstance(value, dict) or value.get("schema") != 1:
        msg = "Valve session check produced no schema-1 result"
        raise ValueError(msg)
    if value.get("status") != "passed":
        raise ValueError("Valve session check failed: " + "; ".join(value.get("errors") or ["no reason"]))
    session = value.get("session") or {}
    states = {k: session.get(k) for k in ("target_state", "steamvr", "gamescope", "restored")}
    if any(state != "active" for state in states.values()):
        msg = f"Valve's session did not run or return: {states}"
        raise ValueError(msg)
    compositor = value.get("compositor") or {}
    if not compositor.get("started") or not compositor.get("gamescope_client") or compositor.get("asserts"):
        msg = f"SteamVR's compositor did not run with gamescope: {compositor}"
        raise ValueError(msg)
    _validate_frames(value.get("frames"))
    _validate_devices(value.get("devices") or [])
    fan = value.get("fan")
    if not isinstance(fan, dict) or any(
        fan.get(k) is not None and not isinstance(fan.get(k), dict) for k in ("before", "after_run")
    ):
        msg = f"The Valve session check recorded no fan state: {fan}"
        raise ValueError(msg)
    performance = value.get("performance")
    if not isinstance(performance, dict) or performance.get("status") not in ("passed", "failed"):
        msg = "The Valve session check gave no performance verdict"
        raise ValueError(msg)
    if performance["status"] != "passed":
        raise ValueError(
            "Valve's session missed its performance limits: " + "; ".join(performance.get("failures") or ["no reason"])
        )


def _validate_frames(frames: object) -> None:
    """Refuse a session whose compositor did not present through msm at the display's rate.

    The frames are msm's own events (msm-frame-stats), without an image: the compressed scanout
    is readable only by the master.
    """
    if not isinstance(frames, dict) or frames.get("target") != "msm":
        msg = "SteamVR's compositor did not present through msm"
        raise ValueError(msg)
    if not frames.get("commits"):
        msg = "The display committed nothing in Valve's session"
        raise ValueError(msg)
    if (frames.get("commit_hz") or 0) < VALVE_SESSION_MIN_HZ:
        msg = f"SteamVR's compositor presented {frames.get('commit_hz')} frames per second"
        raise ValueError(msg)
    if abs((frames.get("vblank_hz") or 0) - DISPLAY_HZ) > VBLANK_TOLERANCE * DISPLAY_HZ:
        msg = f"The panels' vblanks ran at {frames.get('vblank_hz')} Hz"
        raise ValueError(msg)


def _validate_devices(reported: list) -> None:
    """Refuse a session without the headset and both controllers, each with a valid pose."""
    devices = {d.get("index"): d for d in reported if isinstance(d, dict)}
    indices = (HEADSET_INDEX, *CONTROLLER_INDICES)
    if not set(indices) <= devices.keys():
        msg = f"SteamVR reports devices {sorted(devices)}, not the headset and two controllers"
        raise ValueError(msg)
    for index in indices:
        numbers = list(devices[index].get("position") or []) + list(devices[index].get("rotation") or [])
        if len(numbers) != POSE_NUMBERS or not all(isinstance(n, (int, float)) and math.isfinite(n) for n in numbers):
            msg = f"SteamVR's pose of device {index} is not valid: {devices[index]}"
            raise ValueError(msg)
    for index in CONTROLLER_INDICES:
        if not any(devices[index]["position"]):
            msg = f"Controller {index} has no position in SteamVR"
            raise ValueError(msg)


def format_valve_performance(performance: object) -> str:
    """Return one line for the runner's output: the verdict and what it measured."""
    if not isinstance(performance, dict):
        return "performance: no verdict"
    text = f"performance: {performance.get('status')}"
    if performance.get("commit_hz") is not None:
        text += (
            f" ({performance['commit_hz']} commits/s, {performance.get('late_percent')} % late, "
            f"{performance.get('stalls')} stalls, "
            f"hottest {performance.get('hottest_c')} °C over {performance.get('window_seconds')} s)"
        )
    margin = (performance.get("job_margin_ms") or {}).get("p99")
    if margin is not None:
        text += f"; job margin p99 {margin} ms, web helpers {performance.get('web_helper_gpu_percent')} % of the GPU"
    failures = performance.get("failures") or []
    text += "; " + "; ".join(failures) if failures else ""
    warnings = performance.get("warnings") or []
    return text + "".join(f"; warning: {w}" for w in warnings)
