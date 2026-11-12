# SPDX-License-Identifier: Unlicense
"""Steam Link's client on the PICO 4: its video decoder's firmware."""

import unittest

from devices.phoenix.tooling import assets


class SteamLinkTests(unittest.TestCase):
    """Steam Link's client (vrlink) on the PICO 4's video decoder (Venus)."""

    def test_venus_firmware_from_the_modem_partition(self):
        self.assertEqual(assets.VENUS, ("venus.*",))
        self.assertEqual(assets.KINDS["graphics-firmware"].sources["venus."], "modem /image")


if __name__ == "__main__":
    unittest.main()
