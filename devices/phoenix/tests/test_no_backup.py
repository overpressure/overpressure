# SPDX-License-Identifier: Unlicense
"""The image is built without the user's backup.

The stock files come from PICO's public OTA (python -m overpressure stock), the headset's own factory data
from its persist partition at boot (pico4-persist.service).
"""

import re
import tempfile
import unittest
import zipfile
from pathlib import Path

from devices.phoenix.tooling import assets, stock
from overpressure import ota

ROOT = Path(__file__).resolve().parents[1]


def read(path: Path) -> str:
    """Return a file's text."""
    return path.read_text(encoding="utf-8")


class ExtractionTests(unittest.TestCase):
    def test_no_extraction_reads_the_backup(self):
        source = read(ROOT / "tooling/assets.py")
        self.assertNotRegex(source, r"lun\d/|persist\.bin|superimg|backup")
        for name, kind in assets.KINDS.items():
            self.assertTrue(set(kind.images) <= set(stock.COPIED) | set(stock.BLOCK_IMAGES), name)

    def test_the_build_ships_no_persist_data(self):
        build = read(ROOT / "scripts/build-userspace.sh")
        self.assertNotIn("wlan_mac.bin; do", build)
        self.assertNotRegex(build, r'"\$sensor_data/sensors/registry"')
        for link in (
            '/mnt/vendor/persist/sensors/registry/registry "$qcom/sensors/registry"',
            '/mnt/vendor/persist/sensors/registry/sns_reg_version "$qcom/sensors/sns_reg_version"',
            '/mnt/vendor/persist/pvr "$qcom/calibration/pvr"',
        ):
            self.assertIn("ln -sfn " + link, build)
        self.assertIn('mkdir -p "$T/mnt/vendor/persist"', build)


class DevImageTests(unittest.TestCase):
    def test_recovery_from_the_ota(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "ota.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("boot.img", b"ANDROID!boot")
                zf.writestr("recovery.img", b"ANDROID!recovery")
            self.assertEqual(ota.fetch_boot_image(str(zip_path), log=lambda *_: None), b"ANDROID!boot")
            self.assertEqual(
                ota.fetch_boot_image(str(zip_path), log=lambda *_: None, partition="recovery"), b"ANDROID!recovery"
            )
        devloop = read(ROOT / "tooling/devloop.py")
        image = devloop[devloop.index("    def image(self)") : devloop.index("    def enrolled_keys(")]
        self.assertIn('ota.fetch_boot_image(self.stock_location(), str(ROOT / "cache"), print, "recovery")', image)
        self.assertNotIn("stock_partition", image)


class StockLibTests(unittest.TestCase):
    def test_the_build_scripts_dump_through_overpressures_ext4_reader(self):
        lib = read(ROOT.parent.parent / "scripts/stock-lib.sh")
        self.assertTrue(re.search(r"dump\(\) \{", lib))
        self.assertIn("python3 -m overpressure.ext4 cat", lib)
        self.assertNotIn("debugfs", lib)


if __name__ == "__main__":
    unittest.main()
