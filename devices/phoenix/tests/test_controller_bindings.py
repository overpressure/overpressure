"""an issue: the PICO 4 controllers as SteamVR's pico_controller (docs/session.md#controllers-in-games)."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "userspace/pkgbuilds/pico4-monado"


def read(name: str) -> str:
    return (RECIPE / name).read_text(encoding="utf-8")


class ControllerTypeTests(unittest.TestCase):
    def setUp(self):
        self.patch = read("monado-steamvr-pico-controller.patch")
        self.profile = json.loads(read("pico4-controller-profile.json"))

    def test_driver_reports_steamvrs_type_with_our_profile(self):
        self.assertIn('+				m_input_profile = "{monado}/input/pico4_controller_profile.json";', self.patch)
        self.assertIn('+				m_controller_type = "pico_controller";', self.patch)
        self.assertEqual(self.profile["controller_type"], "pico_controller")
        # SteamVR's remapping turns games' Touch, Index and Vive bindings into this type's.
        self.assertEqual(self.profile["remapping"], "input/default_pico_controller_remapping.json")
        self.assertNotIn("compatibility_mode_controller_type", self.profile)  # SteamVR ignores it next to remapping

    def test_every_driver_input_is_in_the_profile(self):
        paths = re.findall(r'\+\t\tAdd(?:Output)?Control\([^;]*?"(/(?:input|output)/[a-z_]+)', self.patch)
        self.assertIn("/input/system", paths)
        self.assertIn("/output/haptic", paths)
        for path in paths:
            self.assertIn(path, self.profile["input_source"])

    def test_each_hand_has_one_system_button(self):
        # The menu button (left) and the PICO button (right), as SteamVR's PICO profile has it.
        self.assertIn("m_hand == XRT_HAND_LEFT ? XRT_INPUT_PICO4_MENU_CLICK : XRT_INPUT_PICO4_SYSTEM_CLICK", self.patch)
        self.assertEqual(self.patch.count('AddControl("/input/system/click"'), 1)

    def test_dashboard_bindings_use_the_profiles_inputs(self):
        bindings = json.loads(read("pico4-vrcompositor-bindings.json"))
        self.assertEqual(bindings["controller_type"], "pico_controller")
        self.assertEqual(self.profile["default_bindings"],
                         [{"app_key": "openvr.component.vrcompositor", "binding_url": "pico4_vrcompositor_bindings.json"},
                          {"app_key": "vrlink.client", "binding_url": "pico4_vrlink_bindings.json"}])
        for path in re.findall(r'"/user/hand/(?:left|right)(/(?:input|output|pose)/[a-z_]+)', json.dumps(bindings)):
            self.assertIn(path, self.profile["input_source"])

    def test_steam_link_client_gets_bindings_without_simulation(self):
        # an issue: without them SteamVR remaps vrlink to the knuckles and simulates an Index
        # headset, which the PC's Steam Link does not know.
        bindings = json.loads(read("pico4-vrlink-bindings.json"))
        self.assertEqual((bindings["app_key"], bindings["controller_type"]), ("vrlink.client", "pico_controller"))
        self.assertEqual(bindings["simulated_actions"], [])
        self.assertNotIn("simulate", json.dumps(bindings["options"]))
        for path in re.findall(r'"/user/hand/(?:left|right)(/(?:input|output|pose)/[a-z_]+)', json.dumps(bindings)):
            self.assertIn(path, self.profile["input_source"])
        self.assertIn('install -Dm644 pico4-vrlink-bindings.json "$input/pico4_vrlink_bindings.json"', read("PKGBUILD"))

    def test_the_panels_refresh_rates_for_steam_link(self):
        patch = read("monado-steamvr-frame-rates.patch")
        self.assertIn("+	float rates[] = {m_flDisplayFrequency};", patch)
        self.assertIn("vr::Prop_DisplayAvailableFrameRates_Float_Array", patch)
        self.assertIn("patch -d monado -p1 < monado-steamvr-frame-rates.patch", read("PKGBUILD"))

    def test_recipe_installs_the_profile_and_links_steamvrs_remapping(self):
        recipe = read("PKGBUILD")
        self.assertIn("patch -d monado -p1 < monado-steamvr-pico-controller.patch", recipe)
        self.assertIn('install -Dm644 pico4-controller-profile.json "$input/pico4_controller_profile.json"', recipe)
        self.assertIn("ln -s /opt/steamvr/resources/input/default_pico_controller_remapping.json "
                      '"$input/default_pico_controller_remapping.json"', recipe)


if __name__ == "__main__":
    unittest.main()
