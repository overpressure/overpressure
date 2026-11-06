# SPDX-License-Identifier: Unlicense
"""Validate the Vulkan render evidence of the stage-2 check (pico4-vulkan-test)."""

from __future__ import annotations

#: pico4-vulkan-test renders a square of this many pixels a side and reads every pixel back.
RENDER_SIZE = 256


def validate_vulkan(value: object) -> None:
    """Refuse evidence that is not a passed Turnip render with every pixel verified."""
    if not (
        isinstance(value, dict)
        and value.get("status") == "passed"
        and value.get("driver") == "turnip"
        and value.get("width") == RENDER_SIZE
        and value.get("height") == RENDER_SIZE
        and value.get("verified_pixels") == RENDER_SIZE * RENDER_SIZE
        and value.get("bad_pixels") == 0
    ):
        msg = "Hardware Vulkan render/readback did not pass"
        raise ValueError(msg)
