import configparser
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOTFS = ROOT / "userspace/rootfs"


class WifiBackendTests(unittest.TestCase):
    def test_wpa_supplicant_wins_over_valves_iwd_setting_on_the_stock_kernel(self):
        # steamos-manager writes 99-valve-wifi-backend.conf (iwd), which the stock kernel cannot
        # run; NetworkManager applies conf.d files in name order, the last value winning.
        ours = ROOTFS / "usr/share/pico4-poc/99-zz-pico4-wifi-backend.conf"
        self.assertGreater(ours.name, "99-valve-wifi-backend.conf")
        config = configparser.ConfigParser()
        config.read_string(ours.read_text(encoding="utf-8"))
        self.assertEqual(config["device"]["wifi.backend"], "wpa_supplicant")
        unit = (ROOTFS / "usr/lib/systemd/system/pico4-wifi-backend.service").read_text(encoding="utf-8")
        self.assertIn("ConditionPathExists=/sys/class/kgsl", unit)
        self.assertIn("/run/NetworkManager/conf.d/99-zz-pico4-wifi-backend.conf", unit)
        self.assertIn("Before=NetworkManager.service", unit)
        self.assertFalse((ROOTFS / "etc/NetworkManager/conf.d" / ours.name).exists())
        # A mainline kernel runs the Frame's iwd.
        packages = (ROOT / "userspace/packages.txt").read_text(encoding="utf-8").split()
        self.assertIn("iwd", packages)


class WpaSupplicantTests(unittest.TestCase):
    def test_sae_asks_for_wpa_version_2(self):
        # an issue: the 4.19 kernel's cfg80211 refuses NL80211_WPA_VERSION_3 in CONNECT.
        recipe = ROOT / "userspace/pkgbuilds/pico4-wpa-supplicant"
        patch = (recipe / "sae-wpa-version-2.patch").read_text(encoding="utf-8")
        self.assertIn("-\t\t\t\tver |= NL80211_WPA_VERSION_3;", patch)
        self.assertNotIn("+\t\t\t\tver |= NL80211_WPA_VERSION_3;", patch)
        pkgbuild = (recipe / "PKGBUILD").read_text(encoding="utf-8")
        self.assertIn('provides=("wpa_supplicant=2:$pkgver")', pkgbuild)
        self.assertIn("conflicts=(wpa_supplicant)", pkgbuild)
        packages = (ROOT / "userspace/packages.txt").read_text(encoding="utf-8").split()
        self.assertIn("pico4/pico4-wpa-supplicant", packages)
        build = (ROOT / "scripts/build-userspace.sh").read_text(encoding="utf-8")
        self.assertIn("build_package pico4-wpa-supplicant", build)
        # Steam's WPA3 profiles connect as saved: no WPA2 fallback any more.
        self.assertNotIn("wpa2-fallback", build)


if __name__ == "__main__":
    unittest.main()
