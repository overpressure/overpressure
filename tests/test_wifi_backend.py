import configparser
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONF = ROOT / "userspace/rootfs/etc/NetworkManager/conf.d"


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


if __name__ == "__main__":
    unittest.main()
