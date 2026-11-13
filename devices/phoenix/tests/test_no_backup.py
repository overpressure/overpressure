# SPDX-License-Identifier: Unlicense
"""PICO OS's files come from PICO OS's partitions only, never from the headset's own data.

They come from the headset's backup (or PICO's public OTA) and reach the data partition at the install
(tooling/stockfiles.py); the headset's own factory data is read from its persist partition at boot
(pico4-persist.service).
"""

import re
import tempfile
import unittest
import zipfile
from pathlib import Path

from devices.phoenix.tooling import assets, stock, stockfiles
from overpressure import ota

ROOT = Path(__file__).resolve().parents[1]


def read(path: Path) -> str:
    """Return a file's text."""
    return path.read_text(encoding="utf-8")


class ExtractionTests(unittest.TestCase):
    def test_extraction_reads_only_pico_os_s_own_partitions_never_the_headsets(self):
        # From the OTA or from a backup, the same images: PICO OS's partitions, never persist.
        self.assertEqual(
            set(assets.BACKUP_IMAGES), set(stock.COPIED) - {"recovery.img", "boot.img"} | set(stock.BLOCK_IMAGES)
        )
        self.assertNotIn("persist", {partition for partition, _logical in assets.BACKUP_IMAGES.values()})
        self.assertNotRegex(read(ROOT / "tooling/assets.py"), r"persist\.bin|lun\d/")
        for name, kind in assets.KINDS.items():
            self.assertTrue(set(kind.images) <= set(assets.BACKUP_IMAGES), name)

    def test_the_headsets_own_data_stays_in_persist(self):
        # The image and PICO OS's files on the data partition link to persist, never copy from it.
        self.assertTrue(all(t.startswith("/mnt/vendor/persist/") for t in stockfiles.TREE_LINKS.values()))
        build = read(ROOT / "scripts/build-userspace.sh")
        self.assertNotIn("wlan_mac.bin", build)
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
