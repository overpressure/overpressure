"""The limit on the core dumps that vrcompositor leaves at each SteamVR stop."""
import configparser
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "userspace/rootfs/etc/systemd/coredump.conf.d/50-pico4-limit.conf"


class CoredumpLimitTests(unittest.TestCase):
    def test_the_dumps_have_a_disk_limit(self):
        config = configparser.ConfigParser()
        config.read_string(CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(config["Coredump"]["MaxUse"], "1G")

    def test_core_dumps_stay_on(self):
        config = configparser.ConfigParser()
        config.read_string(CONFIG.read_text(encoding="utf-8"))
        self.assertNotIn("Storage", config["Coredump"])
        self.assertNotIn("ProcessSizeMax", config["Coredump"])


if __name__ == "__main__":
    unittest.main()
