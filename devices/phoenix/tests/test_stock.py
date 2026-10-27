"""devices.phoenix.tooling.stock: the PICO 4's members of PICO's full OTA."""
import unittest

from devices.phoenix.tooling import stock


class StockTests(unittest.TestCase):
    def test_members_cover_every_output(self):
        members = set(stock.members_needed())
        self.assertIn("firmware-update/NON-HLOS.bin", members)
        self.assertIn("firmware-update/dspso.bin", members)
        self.assertIn("system.new.dat.br", members)
        self.assertIn("vendor.transfer.list", members)


if __name__ == "__main__":
    unittest.main()
