# SPDX-License-Identifier: Unlicense
"""overpressure.qualcomm: the SoC ID and EDL's partition maps."""
import tempfile
import unittest
from pathlib import Path

from overpressure import qualcomm


class QualcommTests(unittest.TestCase):
    def test_chip_input(self):
        self.assertEqual(qualcomm.chip_id(" 00123\r\n"), 123)
        self.assertEqual(qualcomm.chip_id("0xFFFFFFFF"), 0xFFFFFFFF)
        for value in ("-1", "4294967296", "123abc", "", "1; reboot", "+1"):
            with self.assertRaises(ValueError):
                qualcomm.chip_id(value)

    def test_partition_size_from_the_rawprogram_maps(self):
        with tempfile.TemporaryDirectory() as temp:
            backup = Path(temp)
            (backup / "lun4").mkdir()
            (backup / "lun4" / "rawprogram4.xml").write_text(
                '<data><program label="boot" num_partition_sectors="16" SECTOR_SIZE_IN_BYTES="4096" />'
                '<program label="misc" num_partition_sectors="1" SECTOR_SIZE_IN_BYTES="4096" /></data>')
            self.assertEqual(qualcomm.partition_size(backup, "boot"), 65536)
            with self.assertRaises(ValueError):
                qualcomm.partition_size(backup, "userdata")


if __name__ == "__main__":
    unittest.main()
