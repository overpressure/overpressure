# SPDX-License-Identifier: Unlicense
"""The memory setup: the Frame's zram swap."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNITS = ROOT / "userspace/rootfs/usr/lib/systemd/system"
GENERATOR = "/usr/lib/systemd/system-generators/zram-generator"


class ZramSwapTests(unittest.TestCase):
    """The Frame's zram swap."""

    def setUp(self):
        self.unit = (UNITS / "pico4-zram-swap.service").read_text(encoding="utf-8")
        self.build = (ROOT / "scripts/build-userspace.sh").read_text(encoding="utf-8")

    def test_the_frames_setup_without_device_units(self):
        # zram-generator's swap unit has no PICO OS priority.
        self.assertIn('ln -sfn /dev/null "$T/etc/systemd/system-generators/zram-generator"', self.build)
        self.assertIn(f"ExecStart={GENERATOR} --setup-device zram0\n", self.unit)
        self.assertIn(f"ExecStop={GENERATOR} --reset-device zram0\n", self.unit)
        self.assertRegex(self.build, r"pico4-persist\.service pico4-zram-swap\.service")

    def test_the_priority_the_stock_kernel_swaps_to(self):
        # ByteDance's get_swap_pages: ordinary reclaim takes only a device of priority 32758.
        self.assertIn("ExecStart=/usr/bin/swapon --priority 32758 /dev/zram0\n", self.unit)
        self.assertNotRegex(self.unit, r"--priority (?!32758\b)\d+")

    def test_pico_os_reclaim_settings(self):
        conf = (ROOT / "userspace/rootfs/etc/sysctl.d/50-pico4-memory.conf").read_text(encoding="utf-8")
        for line in (
            "vm.swappiness = 100",
            "vm.watermark_scale_factor = 90",
            "vm.watermark_boost_factor = 0",
            "vm.overcommit_memory = 1",
        ):
            self.assertIn(line + "\n", conf)
        self.assertNotIn("\r", conf)

    def test_the_tracker_outlives_a_game_that_runs_memory_out(self):
        # As PICO OS's init runs its core services (pvrtrackingservice among them): never chosen
        # by the OOM killer. With overcommit as PICO OS sets it its allocations do not fail either.
        unit = (ROOT / "userspace/rootfs/usr/lib/systemd/system/pico4-slamd.service").read_text(encoding="utf-8")
        self.assertIn("OOMScoreAdjust=-1000\n", unit)

    def test_unit_stays_lf(self):
        self.assertNotIn("\r", self.unit)


if __name__ == "__main__":
    unittest.main()
