"""an issue: SteamVR's camera from the forward tracking cameras (docs/xr.md#passthrough)."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "userspace/pkgbuilds/pico4-monado"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class ComponentTests(unittest.TestCase):
    def test_patch_offers_the_camera_component(self):
        patch = read(RECIPE / "monado-steamvr-camera.patch")
        self.assertIn("+class CCameraComponent_Monado : public vr::IVRCameraComponent", patch)
        self.assertIn("strcmp(pchComponentNameAndVersion, vr::IVRCameraComponent_Version) == 0", patch)
        # Weak hooks: a build without the PICO 4 driver still links, and has no camera.
        for hook in ("pico4_hmd_camera_info", "pico4_hmd_camera_stream", "pico4_hmd_camera_frame"):
            self.assertRegex(patch, hook + r"\([^;]*\) __attribute__\(\(weak\)\);")
        self.assertIn("pico4_hmd_camera_info != NULL && pico4_hmd_camera_info(m_xdev, &info)", patch)
        # As the notes describe the Frame's: NV12, the two cameras stacked, FTheta distortion.
        self.assertIn("vr::EVRTrackedCameraFrameLayout_Stereo | vr::EVRTrackedCameraFrameLayout_VerticalLayout", patch)
        self.assertIn("vr::Prop_CameraToHeadTransforms_Matrix34_Array", patch)
        self.assertIn("vr::VRDistortionFunctionType_FTheta", patch)
        # The stream stops with the device.
        self.assertRegex(patch, r"\+\tif \(m_camera != NULL\) \{\n\+\t\tm_camera->StopVideoStream\(\);")

    def test_recipe_applies_and_builds_it(self):
        recipe = read(RECIPE / "PKGBUILD")
        self.assertIn("monado-steamvr-camera.patch", recipe.split("sha256sums=")[0])
        self.assertIn("patch -d monado -p1 < monado-steamvr-camera.patch", recipe)
        self.assertEqual(recipe.count("pico4_passthrough.c"), 3)  # source, install, host test
        driver = read(RECIPE / "monado-pico4-driver.patch")
        self.assertIn("+\t\tpico4/pico4_passthrough.c\n+\t\tpico4/pico4_passthrough.h\n", driver)
        self.assertIn("@@ -249,6 +249,66 @@", driver)

    def test_camera_thread_only_copies(self):
        # The copy never waits for the client (trylock), and only while a client streams.
        source = read(ROOT / "src/monado/pico4_passthrough.c")
        push = source[source.index("pico4_passthrough_push("):]
        push = push[:push.index("\n}\n")]
        self.assertIn("pthread_mutex_trylock(&p->lock) != 0", push)
        self.assertNotIn("pthread_mutex_lock", push)
        self.assertNotIn("pico4_passthrough_convert(", push)
        slam = read(ROOT / "src/monado/pico4_slam.c")
        self.assertIn("if (pico4_passthrough_streaming(s->passthrough)) {", slam)
        self.assertIn("pico4_passthrough_push(s->passthrough, set->pixels[3], set->pixels[2]", slam)


class RoomViewTests(unittest.TestCase):
    """SteamVR's room view takes frames only from a client of its private passthrough interface."""

    def test_recipe_builds_and_installs_the_producer(self):
        recipe = read(RECIPE / "PKGBUILD")
        sources = recipe.split("sha256sums=")[0]
        self.assertIn("pico4_roomview.h pico4-roomview.c", sources)
        self.assertRegex(recipe, r"-o pico4-roomview pico4-roomview\.c -lvulkan -ldl -lm")
        self.assertRegex(recipe, r"install -Dm755 [^\n]*pico4-roomview -t \"\$pkgdir/usr/bin\"")
        # The driver includes the layout: it goes into Monado's tree with the driver files.
        install = recipe[recipe.index("install -C -Dm644 -t monado/src/xrt/drivers/pico4"):]
        self.assertIn("pico4_roomview.h", install[:install.index("\n\n")])

    def test_producer_runs_with_steamvr(self):
        units = ROOT / "userspace/rootfs/usr/lib/systemd/user"
        service = read(units / "pico4-roomview.service")
        self.assertIn("PartOf=steamvr.service", service)
        self.assertIn("ExecStart=/usr/bin/pico4-roomview", service)
        self.assertNotRegex(service, r"CPUWeight|CPUQuota")  # RT groups (CLAUDE.md)
        self.assertIn("Wants=pico4-roomview.service", read(units / "steamvr.service.d/52-pico4-roomview.conf"))

    def test_both_sides_share_one_layout(self):
        header = read(ROOT / "src/monado/pico4_roomview.h")
        self.assertEqual(re.findall(r"(?m)^#include <([^>]+)>", header), ["stdint.h"])
        magic = int(re.search(r"PICO4_ROOMVIEW_MAGIC (0x[0-9a-f]+)ull", header).group(1), 16)
        self.assertEqual(magic.to_bytes(8, "little"), b"PCO4RVW1")
        producer = read(ROOT / "src/xr/pico4-roomview.c")
        self.assertIn('#include "pico4_roomview.h"', producer)
        driver = read(ROOT / "src/monado/pico4_passthrough.c")
        self.assertIn('#include "pico4_roomview.h"', driver)
        # The driver writes a frame's sequence last; the producer checks it after copying.
        export = driver[driver.index("export_run(void *arg)"):]
        export = export[:export.index("\n}\n")]
        self.assertLess(export.index("__atomic_store_n(&f->sequence, 0"), export.index("pico4_passthrough_wait("))
        self.assertLess(export.index("pico4_passthrough_wait("), export.index("__atomic_store_n(&f->sequence, frame.sequence"))
        newest = producer[producer.index("newest_frame(struct pico4_roomview"):]
        newest = newest[:newest.index("\n}\n")]
        self.assertLess(newest.index("memcpy(out->pixels[1]"), newest.index("!= seq"))

    def test_producer_uses_the_described_interface(self):
        producer = read(ROOT / "src/xr/pico4-roomview.c")
        self.assertIn('#define INTERFACE "IVRCameraPassthroughInternal_001"', producer)
        # Linear R8 dma-bufs, the format and modifier the compositor imports.
        self.assertIn("#define DRM_FORMAT_R8 0x20203852u", producer)
        self.assertIn(".tiling = VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT", producer)
        self.assertIn("VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT", producer)
        self.assertIn('_Static_assert(sizeof(struct frames_desc) == 0x38, "frames_desc");', producer)
        self.assertIn('_Static_assert(sizeof(struct dmabuf_attributes) == 0x68, "dmabuf_attributes");', producer)
        # Only free frames are written.
        self.assertIn("if (status[i] == FRAME_FREE) {", producer)
        # Valve's code is not linked: the client library is opened at run time.
        self.assertIn('dlopen(OPENVR_API, RTLD_NOW)', producer)
        # The import of the frames needs vrclient's compositor connection, which only a
        # compositor interface opens; the shared semaphore needs the display layer's emulation.
        self.assertLess(producer.index("vr_interface(COMPOSITOR_INTERFACE, &err)"),
                        producer.index("vr_interface(INTERFACE, &err)"))
        self.assertIn("VK_KHR_EXTERNAL_SEMAPHORE_FD_EXTENSION_NAME", producer)
        service = read(ROOT / "userspace/rootfs/usr/lib/systemd/user/pico4-roomview.service")
        self.assertIn("Environment=PICO4_VRDISPLAY=1", service)

    def test_metadata_as_the_room_view_shaders_read_it(self):
        producer = read(ROOT / "src/xr/pico4-roomview.c")
        meta = producer[producer.index("\nmetadata(const struct pico4_roomview *rv"):]
        meta = meta[:meta.index("\n}\n")]
        # The pixel shader clamps its coordinate to the valid rectangle: zero pins every pixel
        # to texel (0, 0).
        self.assertIn("#define META_VALID 0xcc", producer)
        self.assertIn("const float valid[4] = {0, 0, 1, 1};", meta)
        # The vertex shader projects in the computer-vision camera frame (looking along +z).
        self.assertIn("cv[r * 4 + 1] = -cv[r * 4 + 1];", meta)
        self.assertIn("cv[r * 4 + 2] = -cv[r * 4 + 2];", meta)
        self.assertIn("put_matrix(m, META_CAMERA, cv);", meta)
        # The compositor's clock is CLOCK_MONOTONIC_RAW; a later time shows nothing.
        self.assertIn("f->timestamp_ns + raw_offset", meta)
        self.assertIn("clock_gettime(CLOCK_MONOTONIC_RAW, &raw);", producer)
        # The pixel shader squares each sample: the linear luma goes out as its square root.
        self.assertIn("sqrt(i / 255.0)", producer)

    def test_projection_matches_the_shader(self):
        # The vertex shader's fisheye (Kannala-Brandt) mapping, as the producer feeds it: a point
        # in front of a camera (CV axes) lands inside the image; in OpenVR's axes it would not.
        import math
        fx, fy, cx, cy, w, h = 232.7, 232.9, 234.0, 324.2, 480, 640
        k = (-0.0024, 0.0226, -0.014, 0.0013)

        def uv(x, y, z):
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

    def test_double_press_toggles_the_room_view(self):
        # an issue: as the Frame's bindings on its headset's system button: once the dashboard,
        # twice the room view.
        bindings = json.loads(read(RECIPE / "pico4-vrcompositor-bindings.json"))
        sources = bindings["bindings"]["/actions/system"]["sources"]
        for path in ("/user/hand/right/input/system", "/user/hand/left/input/system"):
            source = next(s for s in sources if s["path"] == path)
            self.assertEqual(source["mode"], "complex_button")
            self.assertEqual(source["inputs"]["single"]["output"], "/actions/system/in/ToggleDashboard")
            self.assertEqual(source["inputs"]["double"]["output"], "/actions/system/in/ToggleRoomView")

    def test_exposure_pose_without_the_lead(self):
        # The reported poses are predicted by the lead (24 + 5 ms); the camera saw the head at the
        # exposure itself: with the lead the room trailed the head's turns.
        hmd = read(ROOT / "src/monado/pico4_hmd.c")
        pose = hmd[hmd.index("\nroomview_pose(void *ctx"):]
        pose = pose[:pose.index("\n}\n")]
        self.assertIn("ctrack_head(pico4_hmd(xdev), timestamp_ns, &tracked)", pose)
        self.assertNotIn("xrt_device_get_tracked_pose", pose)
        # But in the space of the reported poses: with the tracking origin's offset (1.6 m), which the
        # 3D room view's depth projection showed (the 2D view uses only the rotation).
        self.assertIn("math_pose_transform(&xdev->tracking_origin->initial_offset, &tracked, &pose)", pose)

    def test_depth_mesh_published_as_the_frame(self):
        # an issue: PICO OS's see-through depth mesh in the compositor's 3D room view, published
        # as the Frame's XR service publishes its own.
        patch = read(RECIPE / "monado-steamvr-roomview-mesh.patch")
        recipe = read(RECIPE / "PKGBUILD")
        self.assertIn("monado-steamvr-roomview-mesh.patch", recipe.split("sha256sums=")[0])
        self.assertLess(recipe.index("< monado-steamvr-pico-controller.patch"),
                        recipe.index("< monado-steamvr-roomview-mesh.patch"))
        for path in ("/meshLayout/layoutId", "/meshLayout/vertexCount", "/meshLayout/indexCount", "/meshData/meshId",
                     "/meshData/layoutId", "/meshData/elementCount", "/meshData/rawFromMeshPose",
                     "/meshData/posePredictionBaseTime", "/meshData/meshIsUpToDate", "/meshData/blockDataSize"):
            self.assertIn('"%s"' % path, patch)
        self.assertIn('GetGenericInterface("IVRPaths_001", &err)', patch)
        # The Frame's queues: 512-byte headers for the path properties (16 refused them), 4 data
        # and 2 layout blocks of one size.
        self.assertIn("mesh ? 512 : 16, mesh ? (i == 0 ? 4 : 2) : 1, 0)", patch)
        # Per mesh: the layout, then the data with the queue's meshIsUpToDate.
        run = patch[patch.index("+	MeshRun()"):]
        self.assertLess(run.index("WriteLayout(layout_id"), run.index("WriteMesh(info, vertices, layout_id"))
        self.assertIn("pico4_hmd_depth_mesh(m_xdev, info.id", run)
        self.assertRegex(patch, r"if \(DepthMeshSupported\(\)\) \{\n\+\t\t\tp->SetBoolProperty\(props, "
                                r"static_cast<vr::ETrackedDeviceProperty>\(2079\), true\);")
        self.assertNotIn("PICO4_ROOMVIEW_DEPTH_M", patch)
        self.assertNotIn("BuildSphere", patch)

    def test_depth_mesh_relative_to_the_shown_frame_head(self):
        # The compositor draws the room view in the frame of the shown camera frame's head (its
        # camera matrix is camera-from-head alone): a mesh in raw or standing space floated above
        # the head. The mesh goes relative to the newest frame's head, again with each frame.
        patch = read(RECIPE / "monado-steamvr-roomview-mesh.patch")
        self.assertNotIn("/chaperone/standing/transform", patch)
        self.assertNotIn("IVRServerInternal", patch)
        run = patch[patch.index("+	MeshRun()"):]
        self.assertIn("pico4_hmd_frame_head(m_xdev, &sequence, &frame_ns, head)", run)
        self.assertIn("(!new_mesh && sequence == frame)", run)
        self.assertIn("WriteMesh(info, vertices, layout_id, head, frame_ns)", run)
        write = patch[patch.index("+	WriteMesh("):]
        self.assertIn("v += head[4 * k + r] * (m[4 * k + c] - (c == 3 ? head[4 * k + 3] : 0));", write)
        self.assertIn("CompositorSeconds(frame_ns)", write)
        # The head is the one the frame carries (roomview_pose: raw space, no display lead).
        passthrough = read(ROOT / "src/monado/pico4_passthrough.c")
        newest = passthrough[passthrough.index("\npico4_passthrough_newest_head("):]
        self.assertIn("memcpy(out_tracking_from_head, f->tracking_from_head", newest[:newest.index("\n}\n")])

    def test_head_from_mesh_inverts_the_frame_head(self):
        # WriteMesh's arithmetic on a turned, shifted head: the mesh origin lands at the head's
        # inverse, and a point given in raw space comes out in the head's frame.
        import math
        a = 0.7
        head = [math.cos(a), 0, math.sin(a), 0.5, 0, 1, 0, 1.2, -math.sin(a), 0, math.cos(a), -0.3]
        mesh = [1, 0, 0, 0, 0, 1, 0, 1.6, 0, 0, 1, 0]
        out = [[sum(head[4 * k + r] * (mesh[4 * k + c] - (head[4 * k + 3] if c == 3 else 0)) for k in range(3))
                for c in range(4)] for r in range(3)]
        point = [0.2, -0.1, -1.5]
        raw = [sum(mesh[4 * r + k] * point[k] for k in range(3)) + mesh[4 * r + 3] for r in range(3)]
        in_head = [sum(out[r][k] * point[k] for k in range(3)) + out[r][3] for r in range(3)]
        back = [sum(head[4 * r + k] * in_head[k] for k in range(3)) + head[4 * r + 3] for r in range(3)]
        for got, want in zip(back, raw):
            self.assertAlmostEqual(got, want)

    def test_weak_driver_calls_have_c_linkage(self):
        # Without extern "C" a weak declaration names a C++ symbol nothing defines: it stays NULL,
        # and the feature silently never runs.
        for name in ("monado-steamvr-roomview-mesh.patch", "monado-steamvr-floor.patch"):
            patch = read(RECIPE / name)
            matches = list(re.finditer(r"(?m)^\+(.*)\n\+(pico4_hmd_\w+)\(", patch))
            self.assertTrue(matches, name)
            for match in matches:
                self.assertEqual(match.group(1), 'extern "C" bool', match.group(2))

    def test_depth_mesh_in_the_space_of_the_reported_poses(self):
        # The vertices are in the head pose's world; SteamVR's raw space adds the tracking origin's
        # offset, as roomview_pose does for the frames.
        hmd = read(ROOT / "src/monado/pico4_hmd.c")
        mesh = hmd[hmd.index("\npico4_hmd_depth_mesh("):]
        mesh = mesh[:mesh.index("\n}\n")]
        self.assertIn("offset = xdev->tracking_origin->initial_offset;", mesh)
        self.assertIn("memcpy(out->tracking_from_mesh, m, sizeof(m));", mesh)
        slam = read(ROOT / "src/monado/pico4_slam.c")
        reader = slam[slam.index("\npico4_slam_depth_mesh("):]
        reader = reader[:reader.index("\n}\n")]
        # The slot stays valid until the writer publishes the next mesh: checked after the copy.
        self.assertLess(reader.index("memcpy(indices"),
                        reader.index("__atomic_load_n(&feed->meshes_written, __ATOMIC_ACQUIRE) != n"))
        # Only while the cameras are shown; asking for a mesh then keeps pico4-slamd feeding its
        # depth pipeline.
        self.assertLess(reader.index("feed != NULL && pico4_passthrough_streaming(s->passthrough)"),
                        reader.index("__atomic_store_n(&feed->mesh_wanted_ns, (uint64_t)os_monotonic_get_ns()"))

    def test_depth_mesh_room_turn_matches_the_poses(self):
        # The room's turn and shift on a vertex (OpenXR axes) as stock_in_room turns a position
        # (tracker axes x up, y left, z back) before stock_to_openxr's axis change.
        import math
        yaw, shift = 0.7, (0.1, -0.4, 0.25)
        vertex = (0.3, -1.2, -2.0)  # x right, y up, z back
        tracker = (vertex[1], -vertex[0], vertex[2])
        c, s = math.cos(yaw), math.sin(yaw)
        room = (tracker[0] + shift[0], c * tracker[1] - s * tracker[2] + shift[1],
                s * tracker[1] + c * tracker[2] + shift[2])
        expected = (-room[1], room[0], room[2])
        # The driver's formula, as written in pico4_slam_depth_mesh.
        ty, tz = -vertex[0], vertex[2]
        up = vertex[1] + shift[0]
        left = c * ty - s * tz + shift[1]
        back = s * ty + c * tz + shift[2]
        for a, b in zip((-left, up, back), expected):
            self.assertAlmostEqual(a, b)
        slam = read(ROOT / "src/monado/pico4_slam.c")
        for line in ("const double ty = -v[0], tz = v[2];", "const double up = v[1] + feed->room_t[0];",
                     "const double left = c * ty - sn * tz + feed->room_t[1];",
                     "const double back = sn * ty + c * tz + feed->room_t[2];", "v[0] = (float)-left;"):
            self.assertIn(line, slam)

    def test_driver_starts_the_export_for_steamvrs_camera(self):
        hmd = read(ROOT / "src/monado/pico4_hmd.c")
        info = hmd[hmd.index("pico4_hmd_camera_info(struct xrt_device *xdev, struct pico4_passthrough_info *out)"):]
        info = info[:info.index("\n}\n")]
        self.assertIn("pico4_passthrough_export(p, PICO4_ROOMVIEW_PATH, roomview_pose, xdev)", info)


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


class HeaderTests(unittest.TestCase):
    def test_interface_is_plain_c(self):
        header = read(ROOT / "src/monado/pico4_passthrough.h")
        self.assertEqual(re.findall(r"(?m)^#include <([^>]+)>", header), ["stdbool.h", "stdint.h"])
        self.assertIn('extern "C" {', header)


if __name__ == "__main__":
    unittest.main()
