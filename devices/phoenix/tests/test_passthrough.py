# SPDX-License-Identifier: Unlicense
"""SteamVR's camera from the forward tracking cameras (docs/xr.md#passthrough)."""

import json
import math
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: Path) -> str:
    """Return a file's text."""
    return path.read_text(encoding="utf-8")


class RoomViewTests(unittest.TestCase):
    """SteamVR's room view takes frames only from a client of its private passthrough interface."""

    def test_producer_runs_with_steamvr(self):
        units = ROOT / "userspace/rootfs/usr/lib/systemd/user"
        service = read(units / "pico4-roomview.service")
        self.assertIn("PartOf=steamvr.service", service)
        self.assertIn("ExecStart=/usr/bin/pico4-roomview", service)
        self.assertIn("Wants=pico4-roomview.service", read(units / "steamvr.service.d/52-pico4-roomview.conf"))

    def test_projection_matches_the_shader(self):
        # The vertex shader's fisheye (Kannala-Brandt) mapping, as the producer feeds it: a point
        # in front of a camera (CV axes) lands inside the image; in OpenVR's axes it would not.
        fx, fy, cx, cy, w, h = 232.7, 232.9, 234.0, 324.2, 480, 640
        k = (-0.0024, 0.0226, -0.014, 0.0013)

        def uv(x: float, y: float, z: float) -> tuple[float, float]:
            z = max(z, 0.001)
            r = math.hypot(x, y)
            if r > 1e-8:
                t = math.atan2(r, z)
                t2 = t * t
                s = t * (1 + t2 * (k[0] + t2 * (k[1] + t2 * (k[2] + t2 * k[3])))) / r
                x, y = x * s, y * s
            else:
                x, y = x / z, y / z
            return (fx * x + cx) / w, (fy * y + cy) / h

        u, v = uv(0.1, 0.05, 1.0)  # ahead of the camera, CV axes
        self.assertTrue(0 < u < 1 and 0 < v < 1)
        u, v = uv(0.1, -0.05, -1.0)  # the same point in OpenVR's axes: behind, clamped z
        self.assertFalse(0 < u < 1 and 0 < v < 1)


class SettingsTests(unittest.TestCase):
    def test_camera_on_as_the_frame(self):
        s = json.loads(read(ROOT / "userspace/session/steamvr.vrsettings"))
        camera = s["camera"]
        self.assertTrue(camera["enableCamera"])
        self.assertEqual(camera["roomView"], 2)
        self.assertFalse(camera["enableCameraForCollisionBounds"])
        self.assertFalse(camera["showOnController"])
        self.assertTrue(s["steamvr"]["contentAwareRoomviewMonochromeTint"])
        self.assertFalse(s["dashboard"]["allowPassthroughPortalManager"])


if __name__ == "__main__":
    unittest.main()
