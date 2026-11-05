# SPDX-License-Identifier: Unlicense
"""Valve's session check and the userspace check on msm, the only GPU path."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "userspace/rootfs/usr/bin"


class SessionCheckTests(unittest.TestCase):
    def test_frames_come_from_msms_events(self):
        check = (BIN / "pico4-valve-session-check").read_text(encoding="utf-8")
        self.assertIn('msm-frame-stats record "$seconds"', check)
        self.assertIn('printf \',"frames":%s,"devices":%s', check)

    def test_userspace_check_requires_the_session_where_the_gpu_is(self):
        check = (BIN / "pico4-userspace-check").read_text(encoding="utf-8")
        gate = check.index('if [ -e /dev/dri/renderD128 ]; then\n\tneed "session service active')
        self.assertIn('need "session target $session_target active', check[gate:])


if __name__ == "__main__":
    unittest.main()
