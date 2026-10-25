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
