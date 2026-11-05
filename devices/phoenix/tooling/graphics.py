# SPDX-License-Identifier: Unlicense
"""Validate the Vulkan render evidence of the stage-2 check (pico4-vulkan-test)."""


def validate_vulkan(value):
    if not (isinstance(value, dict) and value.get("status") == "passed" and value.get("driver") == "turnip"
            and value.get("width") == 256 and value.get("height") == 256
            and value.get("verified_pixels") == 65536 and value.get("bad_pixels") == 0):
        raise ValueError("Hardware Vulkan render/readback did not pass")

