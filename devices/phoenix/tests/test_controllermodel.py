# SPDX-License-Identifier: Unlicense
from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from devices.phoenix.tooling import controllermodel

PNG = controllermodel.PNG_SIGNATURE + b"synthetic texture"


def box_obj(sign: float = 1.0, units: str = "centimeters", extra: str = "") -> str:
    """A synthetic model: a 6 x 8 x 10 cm box of quads, its corner x scaled by `sign`, and a
    battery-indicator group that must not appear in the output (not the real model)."""
    corners = [(sx * 3 * sign + 1 * sign, sy * 4, sz * 5) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
    quads = [(1, 2, 4, 3), (5, 7, 8, 6), (1, 5, 6, 2), (3, 4, 8, 7), (1, 3, 7, 5), (2, 6, 8, 4)]
    lines = [f"# This file uses {units} as units for non-parametric coordinates.", "", "mtllib x.mtl", "g default"]
    lines += [f"v {x} {y} {z}" for x, y, z in corners]
    lines += ["vt 0 0", "vt 1 0", "vt 1 1", "vt 0 1", "vn 0 0 1", "s 1", "g Controller PICO4_Controller_Left",
              "usemtl lambert2SG"]
    lines += ["f " + " ".join(f"{v}/{k + 1}/1" for k, v in enumerate(q)) for q in quads]
    lines += ["g default", "v 0 0 0", "g PICO4_Controller_Left Power", "usemtl initialShadingGroup", "f 9/1/1 1/2/1 2/3/1"]
    return "\n".join(lines) + "\n" + extra


def model_dir(root: Path, left: str | None = None, right: str | None = None, png: bytes = PNG) -> Path:
    d = root / "Neo3Phx"
    d.mkdir()
    (d / "60.obj").write_text(left if left is not None else box_obj(1.0))
    (d / "61.obj").write_text(right if right is not None else box_obj(-1.0))
    for stem in ("60", "61"):
        (d / f"{stem}_idle.png").write_bytes(png)
    return d


def parse_written_obj(text: str) -> tuple[list[tuple[float, ...]], list[list[int]]]:
    positions, faces = [], []
    for line in text.splitlines():
        words = line.split()
        if words and words[0] == "v":
            positions.append(tuple(map(float, words[1:])))
        elif words and words[0] == "f":
            faces.append([int(w.split("/")[0]) for w in words[1:]])
    return positions, faces


class ControllerModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_writes_both_render_models_in_metres_triangulated_without_the_battery_indicator(self):
        out = self.root / "out"
        controllermodel.convert(model_dir(self.root), out)
        for hand, sign in (("left", 1.0), ("right", -1.0)):
            name = f"pico4_controller_{hand}"
            d = out / name
            self.assertEqual(sorted(p.name for p in d.iterdir()),
                             sorted([f"{name}_status.obj"] + [f"{name}.{ext}" for ext in ("json", "mtl", "obj", "png")]))
            positions, faces = parse_written_obj((d / f"{name}.obj").read_text())
            self.assertEqual(len(positions), 8)  # the indicator's vertex is gone
            self.assertEqual(len(faces), 12)
            self.assertTrue(all(len(f) == 3 for f in faces))
            xs = sorted({round(p[0], 6) for p in positions})
            self.assertEqual(xs, sorted([round(sign * -0.02, 6), round(sign * 0.04, 6)]))
            self.assertEqual(sorted({round(p[2], 6) for p in positions}), [-0.05, 0.05])
            self.assertIn(f"map_Kd {name}.png", (d / f"{name}.mtl").read_text())
            self.assertEqual((d / f"{name}.png").read_bytes(), PNG)
            self.assertNotIn(b"\r\n", (d / f"{name}.obj").read_bytes())
            components = json.loads((d / f"{name}.json").read_text())["components"]
            self.assertEqual(components["body"]["filename"], f"{name}.obj")
            self.assertEqual(components["aim"]["component_local"]["origin"], [0.0, 0.0, 0.0])
            self.assertNotIn("grip", components)

    def test_the_indicator_becomes_steamvrs_status_component_on_the_panels_pips(self):
        # A strip across x, tilted: its near edge is lower and further back, its normal up and back.
        strip = ["v -0.5 0.5 1.0", "v 0.5 0.5 1.0", "v 0.5 0.1 1.3", "v -0.5 0.1 1.3"]
        obj = box_obj(1.0).replace("g PICO4_Controller_Left Power", "\n".join(strip + ["vn 0 0.6 0.8", "g PICO4_Controller_Left Power"]))
        obj = obj.replace("f 9/1/1 1/2/1 2/3/1", "f 10/1/2 11/2/2 12/3/2\nf 10/1/2 12/3/2 13/4/2")
        out = self.root / "out"
        controllermodel.convert(model_dir(self.root, left=obj), out)
        name = "pico4_controller_left"
        component = json.loads((out / name / f"{name}.json").read_text())["components"]["status"]
        self.assertEqual(component["filename"], f"{name}_status.obj")
        self.assertEqual(component["visibility"], {"default": False})
        self.assertEqual(component["component_local"]["origin"], [0.0, 0.003, 0.0115])
        self.assertAlmostEqual(component["component_local"]["rotate_xyz"][0], math.degrees(math.atan2(0.6, -0.8)), places=3)
        lines = (out / name / f"{name}_status.obj").read_text().splitlines()
        positions = [tuple(map(float, l.split()[1:])) for l in lines if l.startswith("v ")]
        uvs = [tuple(map(float, l.split()[1:])) for l in lines if l.startswith("vt ")]
        by_corner = dict(zip(positions, uvs))
        left, top, right, bottom = 23 / 256, 172 / 256, 233 / 256, 197 / 256
        for corner, (u, v) in (((-0.005, 0.005, 0.01), (left, 1 - top)), ((0.005, 0.005, 0.01), (right, 1 - top)),
                               ((0.005, 0.001, 0.013), (right, 1 - bottom)), ((-0.005, 0.001, 0.013), (left, 1 - bottom))):
            self.assertAlmostEqual(by_corner[corner][0], u, places=5)
            self.assertAlmostEqual(by_corner[corner][1], v, places=5)

    def test_a_model_without_the_indicator_is_refused(self):
        obj = box_obj(1.0).replace("g PICO4_Controller_Left Power", "g PICO4_Controller_Left Other")
        with self.assertRaises(controllermodel.ControllerModelError):
            controllermodel.convert(model_dir(self.root, left=obj), self.root / "out")

    def test_grip_components_follow_the_aim_to_grip_transform(self):
        half = math.radians(34.0) / 2
        transform = {"position": [0.0016, 0.0141, 0.0491], "orientation": [math.cos(half), math.sin(half), 0.0, 0.0]}
        out = self.root / "out"
        controllermodel.convert(model_dir(self.root), out, {"left": transform, "right": transform})
        components = json.loads((out / "pico4_controller_right" / "pico4_controller_right.json").read_text())["components"]
        for pose in ("grip", "openxr_grip", "handgrip"):
            local = components[pose]["component_local"]
            self.assertEqual(local["origin"], [0.0016, 0.0141, 0.0491])
            self.assertAlmostEqual(local["rotate_xyz"][0], 34.0, places=3)
            self.assertEqual(local["rotate_xyz"][1:], [0.0, 0.0])
        tilted = dict(transform, orientation=[math.cos(half), 0.0, math.sin(half), 0.0])
        (self.root / "tilted").mkdir()
        with self.assertRaises(controllermodel.ControllerModelError):
            controllermodel.convert(model_dir(self.root / "tilted"), out, {"left": tilted, "right": tilted})

    def test_check_accepts_leds_on_the_surface_in_the_aim_frame_and_refuses_others(self):
        lever = {"left": [0.001, -0.002, 0.003], "right": [-0.001, -0.002, 0.003]}

        def led_in_m(aim_point, hand):
            # The tracker's model frame M: aim = (-m.y, m.x, m.z) - lever.
            x, y, z = (a + l for a, l in zip(aim_point, lever[hand]))
            return [y, -x, z]

        # Points on the faces of each box (left box x from -0.02 to 0.04 m, y +-0.04, z +-0.05).
        on_left = [(0.04, 0.0, 0.0), (0.0, 0.04, 0.01), (0.01, -0.01, 0.05)]
        on_right = [(-x, y, z) for x, y, z in on_left]
        leds = {"left": [led_in_m(p, "left") for p in on_left], "right": [led_in_m(p, "right") for p in on_right]}
        worst = controllermodel.convert(model_dir(self.root), self.root / "out", None, leds, lever)
        self.assertLess(max(worst.values()), 1e-9)
        inside = {hand: pts + [led_in_m((0.01, 0.0, 0.0), hand)] for hand, pts in leds.items()}
        (self.root / "inside").mkdir()
        with self.assertRaises(controllermodel.ControllerModelError):
            controllermodel.convert(model_dir(self.root / "inside"), self.root / "out2", None, inside, lever)

    def test_refuses_what_is_not_this_pair_of_models(self):
        cases = {
            "metres": dict(left=box_obj(1.0, units="meters")),
            "no body": dict(left="# This file uses centimeters as units\nv 0 0 0\n"),
            "not mirrored": dict(right=box_obj(1.0)),
            "bad face": dict(left=box_obj(1.0, extra="g Controller X\nf 1/1/1 99/1/1 2/1/1\n")),
            "not a png": dict(png=b"GIF89a"),
        }
        for i, (name, kwargs) in enumerate(cases.items()):
            root = self.root / str(i)
            root.mkdir()
            with self.subTest(name), self.assertRaises(controllermodel.ControllerModelError):
                controllermodel.convert(model_dir(root, **kwargs), root / "out")

    def test_main_reports_errors(self):
        self.assertEqual(controllermodel.main([str(self.root / "missing"), str(self.root / "out")]), 1)


if __name__ == "__main__":
    unittest.main()
