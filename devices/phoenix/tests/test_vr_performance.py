# SPDX-License-Identifier: Unlicense
"""The VR performance hint: cpufreq limits and governors for a VR session, and its unit."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from devices.phoenix.tests.userland import needs_gnu_userland

ROOT = Path(__file__).resolve().parents[1]
ROOTFS = ROOT / "userspace/rootfs"
SCRIPT = ROOTFS / "usr/libexec/pico4/vr-performance"
UNIT = ROOTFS / "usr/lib/systemd/system/pico4-vr-performance.service"
BASH = shutil.which("bash")

# The kernel's defaults: no limit, and core_ctl's own minimum.


@needs_gnu_userland
class VrPerformanceMainlineTests(unittest.TestCase):
    """A mainline kernel: the same limits on the cpufreq policies, no core_ctl."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        for policy, low, high in ((0, 300000, 1804800), (4, 710400, 2419200), (7, 844800, 2841600)):
            d = self.tmp / f"sys/devices/system/cpu/cpufreq/policy{policy}"
            d.mkdir(parents=True)
            (d / "scaling_min_freq").write_text(f"{low}\n")
            (d / "scaling_max_freq").write_text(f"{high}\n")
            (d / "scaling_governor").write_text("schedutil\n")
        self.state = self.tmp / "run/vr-performance"

    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        assert BASH is not None  # the class is skipped without bash
        env = {
            "PATH": str(Path(BASH).parent),
            "PICO4_SYSFS": (self.tmp / "sys").as_posix(),
            "PICO4_VR_PERFORMANCE_STATE": self.state.as_posix(),
        }
        return subprocess.run(  # noqa: S603  # the repository's script under bash, no shell
            [BASH, SCRIPT.as_posix(), *args], env=env, capture_output=True, text=True, check=False
        )

    def limits(self, policy: int) -> tuple[str, str]:
        d = self.tmp / f"sys/devices/system/cpu/cpufreq/policy{policy}"
        return (d / "scaling_min_freq").read_text().strip(), (d / "scaling_max_freq").read_text().strip()

    def governor(self, policy: int) -> str:
        d = self.tmp / f"sys/devices/system/cpu/cpufreq/policy{policy}"
        return (d / "scaling_governor").read_text().strip()

    def test_only_the_compositors_cores_leave_their_governor(self):
        self.assertEqual(self.run_script("on").returncode, 0)
        self.assertEqual(self.governor(4), "performance")
        self.assertEqual(self.governor(0), "schedutil")
        self.assertEqual(self.governor(7), "schedutil")
        self.run_script("on")  # a restart keeps the governor saved before the first
        self.assertEqual(self.run_script("off").returncode, 0)
        self.assertEqual(self.governor(4), "schedutil")

    def test_on_applies_the_hint_and_off_restores(self):
        self.assertEqual(self.run_script("on").returncode, 0)
        self.assertEqual(self.limits(0), ("576000", "1804800"))
        self.assertEqual(self.limits(4), ("864000", "2016000"))
        self.assertEqual(self.limits(7), ("941000", "2054000"))
        self.run_script("on")  # a restart keeps the limits saved before the first
        self.assertEqual(self.run_script("off").returncode, 0)
        self.assertEqual(self.limits(4), ("710400", "2419200"))
        self.assertEqual(self.limits(7), ("844800", "2841600"))
        self.assertFalse(self.state.exists())


class VrPerformanceUnitTests(unittest.TestCase):
    def test_unit_lives_as_long_as_a_vr_session(self):
        unit = UNIT.read_text(encoding="utf-8")
        self.assertRegex(unit, r"(?m)^StopWhenUnneeded=yes$")
        self.assertRegex(unit, r"(?m)^ExecStart=/usr/libexec/pico4/vr-performance on$")
        self.assertRegex(unit, r"(?m)^ExecStop=/usr/libexec/pico4/vr-performance off$")
        self.assertNotRegex(unit, r"(?m)^\[Install\]")
        for name in ("pico4-session", "pico4-monado"):
            text = (ROOTFS / f"usr/lib/systemd/system/{name}.service").read_text(encoding="utf-8")
            self.assertRegex(text, r"(?m)^Wants=pico4-vr-performance\.service$", name)
            self.assertRegex(text, r"(?m)^After=pico4-vr-performance\.service$", name)

    def test_files_stay_lf(self):
        for path in (SCRIPT, UNIT):
            self.assertNotIn(b"\r", path.read_bytes(), path.name)


if __name__ == "__main__":
    unittest.main()
