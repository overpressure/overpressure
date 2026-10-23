import configparser
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONF = ROOT / "userspace/rootfs/etc/NetworkManager/conf.d"
UNITS = ROOT / "userspace/rootfs/usr/lib/systemd/system"
FALLBACK = ROOT / "userspace/rootfs/usr/libexec/pico4-poc/wifi-wpa2-fallback"


class WifiBackendTests(unittest.TestCase):
    def test_wpa_supplicant_wins_over_valves_iwd_setting(self):
        # steamos-manager writes 99-valve-wifi-backend.conf (iwd), which the stock kernel cannot
        # run; NetworkManager applies conf.d files in name order, the last value winning.
        ours = CONF / "99-zz-pico4-wifi-backend.conf"
        self.assertGreater(ours.name, "99-valve-wifi-backend.conf")
        config = configparser.ConfigParser()
        config.read_string(ours.read_text(encoding="utf-8"))
        self.assertEqual(config["device"]["wifi.backend"], "wpa_supplicant")
        packages = (ROOT / "userspace/packages.txt").read_text(encoding="utf-8").split()
        self.assertNotIn("iwd", packages)


class WifiFallbackTests(unittest.TestCase):
    def test_saved_profiles_are_watched(self):
        path = (UNITS / "pico4-wifi-wpa2-fallback.path").read_text(encoding="utf-8")
        self.assertIn("PathChanged=/etc/NetworkManager/system-connections", path)
        service = (UNITS / "pico4-wifi-wpa2-fallback.service").read_text(encoding="utf-8")
        self.assertIn("ExecStart=/usr/libexec/pico4-poc/wifi-wpa2-fallback", service)
        build = (ROOT / "scripts/build-userspace.sh").read_text(encoding="utf-8")
        self.assertRegex(build, r"systemctl enable [^\n]*(\\\n[^\n]*)*pico4-wifi-wpa2-fallback\.path")

    def test_only_networks_that_offer_wpa2_switch(self):
        script = FALLBACK.read_text(encoding="utf-8")
        # WPA3-only profiles whose network also offers PSK, nothing else.
        self.assertIn('= sae ]', script)
        self.assertRegex(script, r"/\(\^\| \)psk\( \|\$\)/")
        self.assertIn("802-11-wireless-security.key-mgmt wpa-psk", script)
        # The passphrase stays as the client saved it: the script never reads or sets it.
        self.assertNotIn("802-11-wireless-security.psk", script)


if __name__ == "__main__":
    unittest.main()
