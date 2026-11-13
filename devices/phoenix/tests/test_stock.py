# SPDX-License-Identifier: Unlicense
"""devices.phoenix.tooling.stock: the PICO 4's members of PICO's full OTA, and its firmware default."""

import json
import unittest
from pathlib import Path

from devices.phoenix.tooling import FIRMWARE, doctor, localconfig, stock
from overpressure import ota

ROOT = Path(__file__).resolve().parents[1]


class StockTests(unittest.TestCase):
    def test_members_cover_every_output(self):
        members = set(ota.members_needed(stock.COPIED, stock.BLOCK_IMAGES))
        self.assertIn("firmware-update/NON-HLOS.bin", members)
        self.assertIn("firmware-update/dspso.bin", members)
        self.assertIn("system.new.dat.br", members)
        self.assertIn("vendor.transfer.list", members)


class FirmwareDefaultTests(unittest.TestCase):
    """One default: Python reads tooling's FIRMWARE, and these files have to repeat it."""

    def test_python_reads_the_constant(self):
        version = FIRMWARE.split("-")[0]
        self.assertEqual(localconfig.Settings().firmware, FIRMWARE)
        self.assertEqual(doctor.FIRMWARE_VERSION, version)
        self.assertIn(FIRMWARE, json.loads(stock.CATALOG.read_text(encoding="utf-8"))["builds"])

    def test_the_makefile_repeats_it(self):
        mk = (ROOT / "device.mk").read_text(encoding="utf-8")
        self.assertIn(f"FIRMWARE ?= {FIRMWARE}\n", mk)

    def test_the_example_settings_repeat_it(self):
        example = (ROOT.parents[1] / ".env.example").read_text(encoding="utf-8")
        self.assertIn(f"PICO4_FIRMWARE={FIRMWARE}\n", example)


if __name__ == "__main__":
    unittest.main()
