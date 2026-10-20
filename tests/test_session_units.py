"""The session's units: SteamVR's restart limit and the fallback to Monado's session."""
import configparser
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOTFS = ROOT / "userspace/rootfs"


def unit(path):
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read_string((ROOTFS / path).read_text(encoding="utf-8"))
    return parser


class SteamvrRestartTest(unittest.TestCase):
    def setUp(self):
        self.dropin = unit("etc/systemd/user/steamvr.service.d/50-pico4-restart-limit.conf")

    def test_restarts_are_spaced(self):
        # A start takes about 15 s; with the wait the radio resets at most every 30 s or so.
        self.assertGreaterEqual(int(self.dropin["Service"]["RestartSec"]), 15)
        # Restarts must not pass through the failed state, or OnFailure= runs at the first crash.
        self.assertEqual(self.dropin["Service"]["RestartMode"], "direct")

    def test_start_limit_trips(self):
        interval = int(self.dropin["Unit"]["StartLimitIntervalSec"])
        burst = int(self.dropin["Unit"]["StartLimitBurst"])
        restart = int(self.dropin["Service"]["RestartSec"])
        # The bursts of a crash loop (restart wait plus a start of up to 20 s) fit the interval.
        self.assertGreaterEqual(interval, burst * (restart + 20))
        self.assertLessEqual(burst, 3)

    def test_failure_reaches_the_fallback(self):
        failed = self.dropin["Unit"]["OnFailure"]
        self.assertEqual(failed, "pico4-steamvr-failed.service")
        report = unit(f"usr/lib/systemd/user/{failed}")["Service"]["ExecStart"]
        flag = report.split()[-1]
        path = unit("usr/lib/systemd/system/pico4-session-fallback.path")["Path"]
        self.assertEqual(path["PathExists"], flag)
        self.assertEqual(path["Unit"], "pico4-session-fallback.service")
        # The session user may create the flag and remove the override.
        tmpfiles = (ROOTFS / "usr/lib/tmpfiles.d/pico4-session.conf").read_text(encoding="utf-8")
        self.assertRegex(tmpfiles, r"(?m)^d /run/pico4-session 0775 root steamos")
        self.assertTrue(flag.startswith("/run/pico4-session/"))


class FallbackTest(unittest.TestCase):
    def test_fallback_overrides_once(self):
        service = (ROOTFS / "usr/lib/systemd/system/pico4-session-fallback.service").read_text(encoding="utf-8")
        self.assertIn("PICO4_SESSION_TARGET=pico4-session.target > /run/pico4-session/override", service)
        self.assertIn("rm -f /run/pico4-session/steamvr-failed", service)
        self.assertIn("systemctl restart pico4-session.service", service)
        session = (ROOTFS / "usr/lib/systemd/system/pico4-session.service").read_text(encoding="utf-8")
        # The override is read after session.conf, so it wins.
        self.assertLess(session.index("EnvironmentFile=-/etc/pico4-poc/session.conf"),
                        session.index("EnvironmentFile=-/run/pico4-session/override"))
        script = (ROOTFS / "usr/libexec/pico4-poc/session").read_text(encoding="utf-8")
        self.assertIn("rm -f /run/pico4-session/override", script)

    def test_path_unit_enabled(self):
        build = (ROOT / "scripts/build-userspace.sh").read_text(encoding="utf-8")
        enable = re.search(r"in_t systemctl enable (.*?)\n(?!\s)", build, re.S).group(1)
        self.assertIn("pico4-session-fallback.path", enable)


if __name__ == "__main__":
    unittest.main()
