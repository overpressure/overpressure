# SPDX-License-Identifier: Unlicense
"""steamvr-settings: the image's SteamVR settings reach the user's file on the data partition."""
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "userspace/rootfs/usr/libexec/pico4/steamvr-settings"
_loader = importlib.machinery.SourceFileLoader("steamvr_settings", str(PATH))
_spec = importlib.util.spec_from_loader("steamvr_settings", _loader)
tool = importlib.util.module_from_spec(_spec)
_loader.exec_module(tool)


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.image = os.path.join(self.dir.name, "image.vrsettings")
        self.user = os.path.join(self.dir.name, "config", "steamvr.vrsettings")
        with open(self.image, "w", encoding="utf-8") as f:
            json.dump({"steamvr": {"promptForChaperoneAtGameLaunch": True, "forcedDriver": "monado"},
                       "collisionBounds": {"CollisionBoundsStyle": 2}}, f)

    def tearDown(self):
        self.dir.cleanup()

    def run_tool(self):
        return tool.main(["steamvr-settings", self.image, self.user])

    def read_user(self):
        with open(self.user, encoding="utf-8") as f:
            return json.load(f)

    def test_image_keys_set_and_steamvrs_own_kept(self):
        os.makedirs(os.path.dirname(self.user))
        with open(self.user, "w", encoding="utf-8") as f:
            json.dump({"steamvr": {"promptForChaperoneAtGameLaunch": False, "lastVersion": "2.17"},
                       "GpuSpeed": {"gpuSpeed0": 450}}, f)
        self.assertEqual(self.run_tool(), 0)
        user = self.read_user()
        self.assertTrue(user["steamvr"]["promptForChaperoneAtGameLaunch"])
        self.assertEqual(user["steamvr"]["forcedDriver"], "monado")
        self.assertEqual(user["steamvr"]["lastVersion"], "2.17")
        self.assertEqual(user["GpuSpeed"], {"gpuSpeed0": 450})
        self.assertEqual(user["collisionBounds"]["CollisionBoundsStyle"], 2)

    def test_missing_or_broken_file_gets_the_image_settings(self):
        self.assertEqual(self.run_tool(), 0)
        self.assertEqual(self.read_user()["steamvr"]["forcedDriver"], "monado")
        with open(self.user, "w", encoding="utf-8") as f:
            f.write("{ half")
        self.assertEqual(self.run_tool(), 0)
        self.assertEqual(self.read_user()["collisionBounds"]["CollisionBoundsStyle"], 2)

    def test_unchanged_file_is_not_rewritten(self):
        self.run_tool()
        before = os.stat(self.user).st_mtime_ns
        os.utime(self.user, ns=(before - 10**9, before - 10**9))
        self.run_tool()
        self.assertEqual(os.stat(self.user).st_mtime_ns, before - 10**9)


class WiringTests(unittest.TestCase):
    def test_steamvr_runs_it_first_and_the_image_carries_the_file(self):
        dropin = (ROOT / "userspace/rootfs/usr/lib/systemd/user/steamvr.service.d/50-pico4.conf").read_text(
            encoding="utf-8")
        self.assertIn("ExecStartPre=/usr/libexec/pico4/steamvr-settings\n", dropin)
        build = (ROOT / "scripts/build-userspace.sh").read_text(encoding="utf-8")
        self.assertIn('"$T/usr/share/pico4/steamvr.vrsettings"', build)
        self.assertNotIn(b"\r", PATH.read_bytes())

    def test_frame_chaperone_settings(self):
        # The Frame driver's own room-setup settings (frame_hmd_additional.vrsettings).
        s = json.loads((ROOT / "userspace/session/steamvr.vrsettings").read_text(encoding="utf-8"))
        self.assertTrue(s["steamvr"]["promptForChaperoneAtGameLaunch"])
        self.assertFalse(s["steamvr"]["requireChaperoneAtGameLaunch"])
        self.assertFalse(s["steamvr"]["floorDetectAtStart"])
        self.assertEqual(s["collisionBounds"]["CollisionBoundsStyle"], 2)
        self.assertFalse(s["internal"]["autoCenterPlayArea"])
        self.assertTrue(s["dashboard"]["_inhibitRecenterOnUserInteraction"])


if __name__ == "__main__":
    unittest.main()
