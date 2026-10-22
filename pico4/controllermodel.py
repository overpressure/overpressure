"""SteamVR render models of the PICO 4 controllers, from the models PICO OS ships.

PICO OS keeps a textured model of each PICO 4 controller for its SDK's controller loader in
`/system/pre_resource/data/misc/user/controller/Neo3Phx/` (copied to /data/misc/user/controller
on first boot): `60.obj` (left) and `61.obj` (right), Maya exports in centimetres with
triangles and quads, positions, UVs and normals, each in a group `Controller` (the body) and a
group `Power` (the six triangles of the battery indicator, drawn with another texture). Their
textures `60_*.png` and `61_*.png` (1024 x 1024 RGBA) are twelve identical files per hand, one
per button state; `*_idle.png` is used. The models' axes and origin are those of the aim pose
PICO OS publishes (x right, y up, z back): PICO OS's own nominal LED rings
(calibration/controllers/led-models.json), taken into the aim frame as the tracker does
(aim = (-m.y, m.x, m.z), less the lever arm of lever-arm.json), lie on the models' surface
without any rotation or offset. `--check` verifies that for every LED.

For each hand this writes a SteamVR render model directory `pico4_controller_<hand>/`: the body
as a triangulated OBJ in metres with its MTL and PNG, and a JSON with the body component and the
pose components `aim`, `openxr_aim` and `tip` (the model's origin), plus `grip` and
`openxr_grip` from PICO OS's aim-to-grip transform when it is given. The models are vendor data:
they are read from the headset's own firmware and stay out of the repository.

    python -m pico4.controllermodel MODEL_DIR OUTPUT_DIR [--aim-to-grip JSON] [--check LED_JSON LEVER_JSON]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

HANDS = {"left": "60", "right": "61"}
NAME = "pico4_controller_{hand}"
#: The group that holds the body; the battery indicator ("Power") uses a texture of its own.
BODY_GROUP = "Controller"
CM = 0.01
#: Plausibility: the body's extent along each axis (metres).
MIN_EXTENT_M, MAX_EXTENT_M = 0.04, 0.25
#: The left model is the right one mirrored in x within this (metres), by bounding box.
MIRROR_TOLERANCE_M = 0.002
#: Every LED of the nominal ring lies within this distance of the body's surface (metres).
LED_SURFACE_TOLERANCE_M = 0.003
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ControllerModelError(ValueError):
    pass


@dataclass
class Mesh:
    """The body of one model: positions (metres), UVs, normals and triangles of index triples."""
    positions: list[tuple[float, float, float]] = field(default_factory=list)
    uvs: list[tuple[float, float]] = field(default_factory=list)
    normals: list[tuple[float, float, float]] = field(default_factory=list)
    #: Each corner is (position, uv, normal), 0-based into the lists above.
    triangles: list[tuple[tuple[int, int, int], ...]] = field(default_factory=list)


def parse_obj(text: str, hand: str) -> Mesh:
    """The body group of a PICO OS controller OBJ, in metres, triangulated and compactly indexed."""
    if "centimeters" not in text.split("\n", 1)[0]:
        raise ControllerModelError(f"{hand}: not a model in centimetres")
    positions: list[tuple[float, ...]] = []
    uvs: list[tuple[float, ...]] = []
    normals: list[tuple[float, ...]] = []
    group = ""
    faces = []
    for number, line in enumerate(text.splitlines(), 1):
        words = line.split()
        if not words or words[0].startswith("#"):
            continue
        try:
            if words[0] == "v":
                positions.append(tuple(float(w) * CM for w in words[1:4]))
            elif words[0] == "vt":
                uvs.append(tuple(float(w) for w in words[1:3]))
            elif words[0] == "vn":
                normals.append(tuple(float(w) for w in words[1:4]))
            elif words[0] == "g":
                group = " ".join(words[1:])
            elif words[0] == "f" and group.split()[:1] == [BODY_GROUP]:
                corners = [tuple(int(i) - 1 for i in w.split("/")) for w in words[1:]]
                if len(corners) < 3 or any(len(c) != 3 or min(c) < 0 for c in corners):
                    raise ValueError("a face needs three or more v/vt/vn corners")
                faces.append(corners)
        except ValueError as exc:
            raise ControllerModelError(f"{hand}: line {number}: {exc}") from None
    if not faces:
        raise ControllerModelError(f"{hand}: no face in a group {BODY_GROUP!r}")
    mesh = Mesh()
    remap: list[dict[int, int]] = [{}, {}, {}]
    sources = (positions, uvs, normals)
    targets = (mesh.positions, mesh.uvs, mesh.normals)
    for corners in faces:
        indexed = []
        for corner in corners:
            out = []
            for k, i in enumerate(corner):
                if i >= len(sources[k]):
                    raise ControllerModelError(f"{hand}: a face refers to a missing vertex")
                if i not in remap[k]:
                    remap[k][i] = len(targets[k])
                    targets[k].append(sources[k][i])
                out.append(remap[k][i])
            indexed.append(tuple(out))
        # A fan: the faces are triangles and convex quads.
        for i in range(1, len(indexed) - 1):
            mesh.triangles.append((indexed[0], indexed[i], indexed[i + 1]))
    return mesh


def bounds(mesh: Mesh) -> tuple[list[float], list[float]]:
    return ([min(p[i] for p in mesh.positions) for i in range(3)],
            [max(p[i] for p in mesh.positions) for i in range(3)])


def validate(meshes: dict[str, Mesh]) -> None:
    """Another firmware may ship other models here: refuse anything that is not this pair."""
    for hand, mesh in meshes.items():
        low, high = bounds(mesh)
        extents = [h - l for l, h in zip(low, high)]
        if min(extents) < MIN_EXTENT_M or max(extents) > MAX_EXTENT_M:
            raise ControllerModelError(f"{hand}: extents {', '.join(f'{e:.3f}' for e in extents)} m, not a controller")
        if any(not 0.0 <= v <= 1.0 for uv in mesh.uvs for v in uv):
            raise ControllerModelError(f"{hand}: texture coordinates outside the texture")
    (left_low, left_high), (right_low, right_high) = bounds(meshes["left"]), bounds(meshes["right"])
    mirrored = [-right_high[0], right_low[1], right_low[2]], [-right_low[0], right_high[1], right_high[2]]
    worst = max(abs(a - b) for a, b in zip(left_low + left_high, mirrored[0] + mirrored[1]))
    if worst > MIRROR_TOLERANCE_M:
        raise ControllerModelError(f"the left model is not the right one mirrored in x ({worst * 1000:.1f} mm off)")


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def point_triangle_distance(p, a, b, c) -> float:
    """Distance from p to the closest point of triangle abc (by the triangle's Voronoi regions)."""
    ab, ac, ap = _sub(b, a), _sub(c, a), _sub(p, a)
    d1, d2 = _dot(ab, ap), _dot(ac, ap)
    if d1 <= 0 and d2 <= 0:
        return math.dist(p, a)
    bp = _sub(p, b)
    d3, d4 = _dot(ab, bp), _dot(ac, bp)
    if d3 >= 0 and d4 <= d3:
        return math.dist(p, b)
    cp = _sub(p, c)
    d5, d6 = _dot(ab, cp), _dot(ac, cp)
    if d6 >= 0 and d5 <= d6:
        return math.dist(p, c)
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        t = d1 / (d1 - d3)
        return math.dist(p, tuple(a[i] + t * ab[i] for i in range(3)))
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        t = d2 / (d2 - d6)
        return math.dist(p, tuple(a[i] + t * ac[i] for i in range(3)))
    va = d3 * d6 - d5 * d4
    if va <= 0 and d4 - d3 >= 0 and d5 - d6 >= 0:
        t = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return math.dist(p, tuple(b[i] + t * (c[i] - b[i]) for i in range(3)))
    s = 1.0 / (va + vb + vc)
    v, w = vb * s, vc * s
    return math.dist(p, tuple(a[i] + ab[i] * v + ac[i] * w for i in range(3)))


def surface_distance(mesh: Mesh, p) -> float:
    return min(point_triangle_distance(p, *(mesh.positions[corner[0]] for corner in tri)) for tri in mesh.triangles)


def led_distances(mesh: Mesh, leds: list[list[float]], lever: list[float]) -> list[float]:
    """Each LED's distance from the surface, taken into the published aim frame as the tracker does."""
    points = [(-m[1] - lever[0], m[0] - lever[1], m[2] - lever[2]) for m in leds]
    return [surface_distance(mesh, p) for p in points]


def check_frame(meshes: dict[str, Mesh], led_models: dict, lever_arms: dict) -> dict[str, float]:
    """The worst LED distance per hand; refuses a model whose frame is not the aim frame."""
    worst = {}
    for hand, mesh in meshes.items():
        distances = led_distances(mesh, led_models[hand], lever_arms[hand])
        worst[hand] = max(distances)
        if worst[hand] > LED_SURFACE_TOLERANCE_M:
            raise ControllerModelError(
                f"{hand}: an LED of PICO OS's ring is {worst[hand] * 1000:.1f} mm off the model in the aim frame")
    return worst


def grip_component(transform: dict) -> dict:
    """SteamVR's component_local for PICO OS's aim-to-grip transform (a turn about x only)."""
    w, x, y, z = transform["orientation"]
    if abs(y) > 1e-9 or abs(z) > 1e-9:
        # SteamVR's order of rotate_xyz is not established here; a turn about one axis needs none.
        raise ControllerModelError("the aim-to-grip rotation is not about x alone")
    angle = math.degrees(2.0 * math.atan2(x, w))
    return {"origin": [round(v, 7) for v in transform["position"]], "rotate_xyz": [round(angle, 4), 0.0, 0.0]}


def write_obj(mesh: Mesh, name: str) -> str:
    lines = [f"# PICO 4 controller ({name}) from PICO OS, in metres, in the aim frame (x right, y up, z back)",
             f"mtllib {name}.mtl", f"o {name}", f"usemtl {name}"]
    lines += [f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in mesh.positions]
    lines += [f"vt {u:.6f} {v:.6f}" for u, v in mesh.uvs]
    lines += [f"vn {x:.6f} {y:.6f} {z:.6f}" for x, y, z in mesh.normals]
    lines += ["f " + " ".join(f"{p + 1}/{t + 1}/{n + 1}" for p, t, n in tri) for tri in mesh.triangles]
    return "\n".join(lines) + "\n"


def write_mtl(name: str) -> str:
    return f"newmtl {name}\nKa 0 0 0\nKd 1 1 1\nKs 0 0 0\nd 1\nillum 1\nmap_Kd {name}.png\n"


def write_json(name: str, grip: dict | None) -> str:
    identity = {"component_local": {"origin": [0.0, 0.0, 0.0], "rotate_xyz": [0.0, 0.0, 0.0]}}
    components = {"body": {"filename": f"{name}.obj", **identity}}
    for pose in ("tip", "aim", "openxr_aim"):
        components[pose] = identity
    if grip is not None:
        for pose in ("grip", "openxr_grip"):
            components[pose] = {"component_local": grip}
    return json.dumps({"components": components}, indent=1) + "\n"


def convert(model_dir: Path, output_dir: Path, aim_to_grip: dict | None = None,
            led_models: dict | None = None, lever_arms: dict | None = None) -> dict[str, float]:
    """Writes both render models; returns the worst LED distance per hand when checked."""
    meshes, textures = {}, {}
    for hand, stem in HANDS.items():
        meshes[hand] = parse_obj((model_dir / f"{stem}.obj").read_text(encoding="utf-8", errors="replace"), hand)
        textures[hand] = (model_dir / f"{stem}_idle.png").read_bytes()
        if not textures[hand].startswith(PNG_SIGNATURE):
            raise ControllerModelError(f"{hand}: {stem}_idle.png is not a PNG file")
    validate(meshes)
    worst = check_frame(meshes, led_models, lever_arms) if led_models is not None else {}
    grips = {hand: grip_component(aim_to_grip[hand]) for hand in HANDS} if aim_to_grip else {}
    for hand, mesh in meshes.items():
        name = NAME.format(hand=hand)
        target = output_dir / name
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{name}.obj").write_text(write_obj(mesh, name), encoding="utf-8", newline="\n")
        (target / f"{name}.mtl").write_text(write_mtl(name), encoding="utf-8", newline="\n")
        (target / f"{name}.png").write_bytes(textures[hand])
        (target / f"{name}.json").write_text(write_json(name, grips.get(hand)), encoding="utf-8", newline="\n")
    return worst


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pico4.controllermodel", description=__doc__.split("\n\n")[0])
    parser.add_argument("model_dir", type=Path, help="PICO OS's Neo3Phx directory (60.obj, 61.obj, *_idle.png)")
    parser.add_argument("output_dir", type=Path, help="receives pico4_controller_left/ and pico4_controller_right/")
    parser.add_argument("--aim-to-grip", type=Path, help="calibration/controllers/aim-to-grip.json")
    parser.add_argument("--check", nargs=2, type=Path, metavar=("LED_JSON", "LEVER_JSON"),
                        help="led-models.json and lever-arm.json: verify that the models sit in the aim frame")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        grip = json.loads(args.aim_to_grip.read_text()) if args.aim_to_grip else None
        leds = levers = None
        if args.check:
            leds, levers = (json.loads(p.read_text()) for p in args.check)
        worst = convert(args.model_dir, args.output_dir, grip, leds, levers)
    except (OSError, ValueError, KeyError) as exc:
        print(f"{args.model_dir}: {exc}", file=sys.stderr)
        return 1
    for hand, distance in worst.items():
        print(f"{hand}: every LED within {distance * 1000:.1f} mm of the model")
    return 0


if __name__ == "__main__":
    sys.exit(main())
