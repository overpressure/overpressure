# SPDX-License-Identifier: Unlicense
"""The splash: a sign of life in the lenses while the installed system starts."""

import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "prebuilt/aarch64/pico4-splash"


class SplashTests(unittest.TestCase):
    """A sign of life in the lenses while the installed system starts."""

    def test_prebuilt_is_a_static_aarch64_program(self):
        data = BINARY.read_bytes()
        self.assertEqual(data[:4], b"\x7fELF")
        self.assertEqual(data[4], 2)  # 64-bit
        machine = struct.unpack_from("<H", data, 18)[0]
        self.assertEqual(machine, 183)  # EM_AARCH64
        # No interpreter: stage 1 runs it without a C library.
        phoff, phentsize, phnum = struct.unpack_from("<Q", data, 32)[0], *struct.unpack_from("<HH", data, 54)
        types = [struct.unpack_from("<I", data, phoff + i * phentsize)[0] for i in range(phnum)]
        self.assertNotIn(3, types)  # PT_INTERP
        self.assertLess(len(data), 32 * 1024)

    def test_source_hands_the_display_over(self):
        source = (ROOT / "src/splash/pico4-splash.c").read_text(encoding="utf-8")
        # Master must be free for the next compositor: Monado and SteamVR's compositor
        # become master by opening the device or with SET_MASTER.
        self.assertIn("DRM_IOCTL_DROP_MASTER", source)
        self.assertIn('quit("a compositor took the display", 0)', source)

    def test_source_draws_the_logo(self):
        source = (ROOT / "src/splash/pico4-splash.c").read_text(encoding="utf-8")
        self.assertIn('#include "pico4-splash-draw.h"', source)
        self.assertIn("draw_bar(&buffer, now_ms());", source)  # the bar follows the clock across instances
        # The headset in the logo's colours: docs/logo/overpressure-mark.svg is the drawing it follows.
        drawing = (ROOT.parents[1] / "docs/logo/overpressure-mark.svg").read_text(encoding="utf-8").lower()
        draw = (ROOT / "src/splash/pico4-splash-draw.h").read_text(encoding="utf-8")
        colours = ("5a92ff", "2a66ec", "1a43b8", "2457cf", "163a9e", "173a9a", "0b0d12", "ff7849")
        for colour in colours:
            self.assertIn(f"#{colour}", drawing)
            self.assertIn(f"0x{colour}", draw.lower())
        self.assertIn('NAME[] = "overpressure"', draw)

    def test_source_drives_msm(self):
        source = (ROOT / "src/splash/pico4-splash.c").read_text(encoding="utf-8")
        # Valve's kernel: the device is "msm", one primary plane over the whole mode.
        self.assertIn('eq(name, "msm")', source)
        self.assertIn("find_primary();", source)
        self.assertIn("DRM_PLANE_TYPE_PRIMARY", source)

    def test_stage_one_does_not_show_it(self):
        # The installed system's preinit starts it; stage 1 is the maintenance environment.
        self.assertNotIn("pico4-splash", (ROOT / "initramfs/init").read_text(encoding="utf-8"))
        image = (ROOT / "tooling/image.py").read_text(encoding="utf-8")
        self.assertNotIn("pico4-splash", image)


if __name__ == "__main__":
    unittest.main()
