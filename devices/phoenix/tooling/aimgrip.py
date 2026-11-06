# SPDX-License-Identifier: Unlicense
"""PICO OS's aim-to-grip transform of the PICO 4 controllers, from its tracking service.

PICO OS publishes each controller's tracking pose as the aim pose and derives the grip pose from
it: `GetControllerTrackingDataGripPose` (libtrackingclient.pxr.so) calls
`pvr::TrackingClient::transform_pose` (0x41e0c), which composes grip = aim * T for hand 0 (left)
or 1 (right). T comes from `pvr::TrackingClient::getAim2GripTransform` (0x420d4): 14 doubles from
the tracking service's `pvr::ConfigManager::getAim2GripTrans` (libpvrtrackingservice.so
0xa5820), which parses the configuration key `sys_tracking_aim_to_grip_transform` and otherwise
falls back to 14 built-in doubles. They are two blocks, left then right, of a position
(x, y, z, metres) followed by a unit quaternion (w, x, y, z), in the aim frame. No configuration
file of the system image sets the key, so the built-in values apply.
scripts/extract-sensor-data.sh reads them from the headset's own firmware; they are vendor data
and stay out of the repository.

    python -m devices.phoenix.tooling.aimgrip LIBRARY OUTPUT.json
"""

from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path

from .ledmodel import LedModelError, read_virtual

#: Virtual address of the built-in 14 doubles in the PICO OS 5.13.7 libpvrtrackingservice.so.
DEFAULTS_ADDRESS = 0x75560
HANDS = ("left", "right")
#: Plausibility: the grip point lies within this distance of the aim point (metres).
MAX_OFFSET_M = 0.2
#: The quaternions are stored as unit quaternions.
UNIT_TOLERANCE = 1e-6
#: A position has three coordinates, a quaternion four.
POSITION, QUATERNION = 3, 4
#: The command line: LIBRARY OUTPUT.json.
ARGUMENTS = ("LIBRARY", "OUTPUT.json")


class AimGripError(ValueError):
    """The library holds no plausible aim-to-grip transform."""


def transforms(elf: bytes) -> dict[str, dict[str, list[float]]]:
    """Both hands' transforms, validated."""
    try:
        raw = read_virtual(elf, DEFAULTS_ADDRESS, 14 * 8)
    except LedModelError as exc:
        raise AimGripError(str(exc)) from exc
    values = struct.unpack("<14d", raw)
    result = {
        hand: {"position": list(values[7 * i : 7 * i + 3]), "orientation": list(values[7 * i + 3 : 7 * i + 7])}
        for i, hand in enumerate(HANDS)
    }
    validate(result)
    return result


def validate(result: dict[str, dict[str, list[float]]]) -> None:
    """Refuse anything implausible: a different library version puts other data at this address."""
    for hand in HANDS:
        block = result.get(hand) or {}
        p, q = block.get("position") or [], block.get("orientation") or []
        if len(p) != POSITION or len(q) != QUATERNION or not all(math.isfinite(v) for v in p + q):
            msg = f"{hand}: expected a finite position and quaternion"
            raise AimGripError(msg)
        if abs(math.sqrt(sum(v * v for v in q)) - 1.0) > UNIT_TOLERANCE:
            msg = f"{hand}: the orientation is not a unit quaternion"
            raise AimGripError(msg)
        if math.hypot(*p) >= MAX_OFFSET_M:
            msg = f"{hand}: the grip is {math.hypot(*p):.3f} m from the aim, not on the controller"
            raise AimGripError(msg)


def to_json(result: dict[str, dict[str, list[float]]], library: str) -> str:
    """Render the transforms as the extracted JSON file."""
    return (
        json.dumps(
            {
                "schema": 1,
                "source": library,
                "units": "m",
                "convention": "grip = aim * transform; position (x, y, z), orientation (w, x, y, z), in the aim frame",
                "left": result["left"],
                "right": result["right"],
            },
            indent=1,
        )
        + "\n"
    )


def main(argv: list[str] | None = None) -> int:
    """Run the extractor's command line."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != len(ARGUMENTS):
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    library, output = Path(args[0]), Path(args[1])
    try:
        result = transforms(library.read_bytes())
    except (OSError, AimGripError) as exc:
        print(f"{library}: {exc}", file=sys.stderr)
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(to_json(result, library.name), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
