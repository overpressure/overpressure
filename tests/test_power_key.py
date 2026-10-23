import configparser
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DROPIN = ROOT / "userspace/rootfs/usr/lib/systemd/logind.conf.d/50-pico4-power-key.conf"


class PowerKeyTests(unittest.TestCase):
    def test_short_press_keeps_the_session_and_long_press_powers_off(self):
        # PICO OS: a short press turns the screen off, never powers off (docs/install.md).
        config = configparser.ConfigParser()
        config.read_string(DROPIN.read_text(encoding="utf-8"))
        self.assertEqual(config["Login"]["HandlePowerKey"], "ignore")
        self.assertEqual(config["Login"]["HandlePowerKeyLongPress"], "poweroff")


if __name__ == "__main__":
    unittest.main()
