"""Validate measured graphics evidence without equating it with visual confirmation."""
import math


def validate_vulkan(value):
    if not (isinstance(value, dict) and value.get("status") == "passed" and value.get("driver") == "turnip"
            and value.get("width") == 256 and value.get("height") == 256
            and value.get("verified_pixels") == 65536 and value.get("bad_pixels") == 0):
        raise ValueError("Hardware Vulkan render/readback did not pass")


def validate_kms(value, duration):
    if not (isinstance(value, dict) and value.get("schema") == 2 and value.get("status") == "passed"
            and value.get("presentation") == "page_flip" and value.get("retire_fence") is True
            and value.get("composition") == "rgb_layers" and value.get("buffer_layout") == "combined"
            and value.get("eye_routing") == "sspp_layout" and value.get("width") == 4320
            and value.get("height") == 2160):
        raise ValueError("Native KMS presentation did not pass")
    modes = value.get("modes", [])
    if not isinstance(modes, list) or len(modes) != 2 or not all(isinstance(m, dict) for m in modes) or [m.get("requested_hz") for m in modes] != [90, 72]:
        raise ValueError("Both native modes must be tested, starting at 90 Hz")
    for mode in modes:
        hz = mode["requested_hz"]
        rates = (mode.get("vblank_hz"), mode.get("completion_hz"))
        counts = (mode.get("flips"), mode.get("vblank_samples"))
        if (any(type(rate) not in (int, float) or not math.isfinite(rate)
                or not hz * 0.95 < rate < hz * 1.05 for rate in rates)
                or any(type(count) is not int or count < duration * hz * 0.9 for count in counts)
                or mode.get("vblank_sequence_gaps") != 0 or mode.get("status") != "passed"):
            raise ValueError(f"{hz} Hz presentation evidence is incomplete or outside tolerance")
