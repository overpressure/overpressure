import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOTFS = ROOT / "userspace/rootfs"
SCRIPT = ROOTFS / "usr/libexec/pico4-poc/vr-performance"
UNIT = ROOTFS / "usr/lib/systemd/system/pico4-vr-performance.service"
BASH = shutil.which("bash")

# The kernel's defaults: no limit, and core_ctl's own minimum.
NO_MAX = " ".join(f"{c}:4294967295" for c in range(8))
NO_MIN = " ".join(f"{c}:0" for c in range(8))


@unittest.skipIf(BASH is None, "needs bash")
class VrPerformanceScriptTests(unittest.TestCase):
    """an issue: the CPU part of PICO OS's VR power hint (powerhint.xml 0x130A-0x1312)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.params = self.tmp / "sys/module/msm_performance/parameters"
        self.params.mkdir(parents=True)
        # As the kernel lists them, with a trailing space.
        (self.params / "cpu_max_freq").write_text(NO_MAX + " \n")
        (self.params / "cpu_min_freq").write_text(NO_MIN + " \n")
        for cpu in (4, 7):
            ctl = self.tmp / f"sys/devices/system/cpu/cpu{cpu}/core_ctl"
            ctl.mkdir(parents=True)
            (ctl / "min_cpus").write_text("1\n")
        self.state = self.tmp / "run/vr-performance"

    def run_script(self, *args):
        env = {"PATH": str(Path(BASH).parent), "PICO4_SYSFS": (self.tmp / "sys").as_posix(),
               "PICO4_VR_PERFORMANCE_STATE": self.state.as_posix()}
        return subprocess.run([BASH, SCRIPT.as_posix(), *args], env=env, capture_output=True, text=True)

    def read(self, path):
        return (self.tmp / "sys" / path).read_text().strip()

    def test_on_applies_the_hint(self):
        self.assertEqual(self.run_script("on").returncode, 0)
        # Big cores 864-2016 MHz, little 576 MHz up to their own maximum, prime 941-2054 MHz.
        self.assertEqual(self.read("module/msm_performance/parameters/cpu_max_freq"),
                         "4:2016000 5:2016000 6:2016000 7:2054000")
        self.assertEqual(self.read("module/msm_performance/parameters/cpu_min_freq"),
                         "0:576000 1:576000 2:576000 3:576000 4:864000 5:864000 6:864000 7:941000")
        self.assertEqual(self.read("devices/system/cpu/cpu4/core_ctl/min_cpus"), "3")
        self.assertEqual(self.read("devices/system/cpu/cpu7/core_ctl/min_cpus"), "1")

    def test_off_restores_what_was_there(self):
        self.run_script("on")
        self.run_script("on")  # a restart keeps the limits saved before the first
        self.assertEqual(self.run_script("off").returncode, 0)
        # Written back without the kernel's trailing space.
        self.assertEqual((self.params / "cpu_max_freq").read_text(), NO_MAX + "\n")
        self.assertEqual((self.params / "cpu_min_freq").read_text(), NO_MIN + "\n")
        self.assertEqual(self.read("devices/system/cpu/cpu4/core_ctl/min_cpus"), "1")
        self.assertFalse(self.state.exists())

    def test_off_without_on_changes_nothing(self):
        self.assertEqual(self.run_script("off").returncode, 0)
        self.assertEqual(self.read("module/msm_performance/parameters/cpu_max_freq"), NO_MAX)

    def test_usage(self):
        self.assertEqual(self.run_script().returncode, 2)


class VrPerformanceUnitTests(unittest.TestCase):
    def test_unit_lives_as_long_as_a_vr_session(self):
        unit = UNIT.read_text(encoding="utf-8")
        self.assertRegex(unit, r"(?m)^StopWhenUnneeded=yes$")
        self.assertRegex(unit, r"(?m)^ExecStart=/usr/libexec/pico4-poc/vr-performance on$")
        self.assertRegex(unit, r"(?m)^ExecStop=/usr/libexec/pico4-poc/vr-performance off$")
        self.assertNotRegex(unit, r"(?m)^(CPUWeight|CPUQuota)=|^\[Install\]")
        for name in ("pico4-session", "pico4-monado"):
            text = (ROOTFS / f"usr/lib/systemd/system/{name}.service").read_text(encoding="utf-8")
            self.assertRegex(text, r"(?m)^Wants=pico4-vr-performance\.service$", name)
            self.assertRegex(text, r"(?m)^After=pico4-vr-performance\.service$", name)

    def test_files_stay_lf(self):
        for path in (SCRIPT, UNIT):
            self.assertNotIn(b"\r", path.read_bytes(), path.name)


if __name__ == "__main__":
    unittest.main()
