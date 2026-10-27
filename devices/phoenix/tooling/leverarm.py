"""PICO OS's controller lever arm, from its controller tracking client library.

PICO OS's tracker publishes a controller pose that its client turns into the aim pose and then
moves by a per-hand lever arm: `alg_get_controller_data_new` (libpico_controller_tracking.client.so
0x13e20) predicts the pose and `getResetControllerPose` (0x1154c) adds R(q) * lever, with q the
predicted aim orientation, so the lever is in the aim frame. The lever depends on the controller
type the tracker reports; the PICO 4's controllers are type 1, whose lever is stored as the left
hand's (x, y) at 0x3c140, the right hand's at 0x3c170 and the shared z at 0x3bfd0, in millimetres.
scripts/extract-sensor-data.sh reads them from the headset's own firmware; they are vendor data and
stay out of the repository.

    python -m devices.phoenix.tooling.leverarm LIBRARY OUTPUT.json
"""

from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path

from .ledmodel import LedModelError, read_virtual

#: Virtual addresses of the type-1 lever in the PICO OS 5.13.7 libpico_controller_tracking.client.so.
XY_ADDRESSES = {"left": 0x3C140, "right": 0x3C170}
Z_ADDRESS = 0x3BFD0
HANDS = ("left", "right")
#: Plausibility: the lever stays on the controller (metres).
MAX_LENGTH_M = 0.1


class LeverArmError(ValueError):
    pass


def lever_arms(elf: bytes) -> dict[str, list[float]]:
    """Both hands' lever arms in metres, validated."""
    try:
        (z,) = struct.unpack("<d", read_virtual(elf, Z_ADDRESS, 8))
        result = {hand: [v / 1000.0 for v in struct.unpack("<2d", read_virtual(elf, XY_ADDRESSES[hand], 16)) + (z,)]
                  for hand in HANDS}
    except LedModelError as exc:
        raise LeverArmError(str(exc)) from exc
    validate(result)
    return result


def validate(result: dict[str, list[float]]) -> None:
    """A different library version puts other data at these addresses: refuse anything implausible."""
    left, right = result.get("left") or [], result.get("right") or []
    if len(left) != 3 or len(right) != 3 or not all(math.isfinite(v) for v in left + right):
        raise LeverArmError("expected a finite lever for each hand")
    for hand, lever in result.items():
        length = math.hypot(*lever)
        if not 0.0 < length < MAX_LENGTH_M:
            raise LeverArmError(f"{hand}: a lever of {length:.3f} m is not on the controller")
    # The hands mirror each other across the aim frame's x axis.
    if left[0] != -right[0] or left[1:] != right[1:]:
        raise LeverArmError("the left and right levers do not mirror in x")


def to_json(result: dict[str, list[float]], library: str) -> str:
    return json.dumps({
        "schema": 1,
        "source": library,
        "units": "m",
        "convention": "published aim position = position + R(aim orientation) * lever; (x, y, z) in the aim frame",
        "left": result["left"],
        "right": result["right"],
    }, indent=1) + "\n"


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    library, output = Path(args[0]), Path(args[1])
    try:
        result = lever_arms(library.read_bytes())
    except (OSError, LeverArmError) as exc:
        print(f"{library}: {exc}", file=sys.stderr)
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(to_json(result, library.name), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
