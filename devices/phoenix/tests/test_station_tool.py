# SPDX-License-Identifier: Unlicense
"""The station's camera rate in pico4-slamd (the tool's own tests are in overpressure/packages' pico4-xr)."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class StationRateTests(unittest.TestCase):
    """The cameras' rate: 50 Hz as stock, or PICO OS's 60 Hz for a faster room view."""

    def test_auto_exposure_takes_the_measured_rate(self):
        slamd = (ROOT / "src/stockslam/pico4-slamd.c").read_text(encoding="utf-8")
        self.assertIn("ts - c->last_set_ts < 36700000 ? 60 : AE_FPS", slamd)
        self.assertIn("&unused, __atomic_load_n(&c->fps, __ATOMIC_RELAXED),", slamd)


if __name__ == "__main__":
    unittest.main()
