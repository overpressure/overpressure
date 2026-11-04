import importlib.machinery
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from devices.phoenix.tooling import userspace

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "userspace/rootfs/usr/libexec/pico4-poc/session-performance"
_loader = importlib.machinery.SourceFileLoader("session_performance", str(PATH))
_spec = importlib.util.spec_from_loader("session_performance", _loader)
perf = importlib.util.module_from_spec(_spec)
_loader.exec_module(perf)


def stats(commits, seconds, late, stalls, timeouts=0, gpu_mean=3.1, max_interval=1008.4):
    """The layer's statistics as pico4-vrdisplay writes them (only the fields the verdict reads, and some)."""
    return {"target": "pico4_vrdisplay", "fake": False, "mode": "4320x2160", "format": "XBGR8888", "images": 3,
            "presents": commits + 2, "commits": commits, "commit_hz": round((commits - 1) / seconds, 4),
            "max_interval_ms": max_interval, "late": late, "stalls": stalls, "busy_retries": 0,
            "retire_waits": commits, "retire_timeouts": 0, "gpu_timeouts": 0, "vblanks": commits,
            "vblank_hz": 89.9982, "gpu_wait_ms": {"mean": gpu_mean, "max": 9.8},
            "retire_wait_ms": {"mean": 0.2, "max": 1.1}, "present_waits": commits,
            "present_wait_timeouts": timeouts, "max_present_wait_lag": 1, "vblank_tick_intervals": 0,
            "vblank_intervals": commits, "eye_mean_rgb": [[22.1, 30.5, 61.0], [21.8, 30.2, 60.7]]}


# Valve's session on the Frame's compositor path: two one-second stalls while SteamVR starts, then 90 commits per second.
START = stats(commits=812, seconds=10.0, late=7, stalls=2)
END = stats(commits=3512, seconds=40.0, late=9, stalls=2)


class VerdictTests(unittest.TestCase):
    def test_startup_stalls_stay_out_of_the_window(self):
        v = perf.verdict(START, END, 61.5)
        self.assertEqual(v["status"], "passed", v["failures"])
        self.assertAlmostEqual(v["commit_hz"], 90.0, delta=0.05)
        self.assertEqual((v["stalls"], v["present_wait_timeouts"]), (0, 0))
        self.assertAlmostEqual(v["late_percent"], 100 * 2 / 2700, places=2)
        self.assertEqual(v["startup"], {"stalls": 2, "max_interval_ms": 1008.4})
        self.assertEqual(v["limits"]["commit_hz_min"], 85.5)

    def test_gpu_wait_is_the_window_mean(self):
        v = perf.verdict(stats(1001, 10.0, 0, 0, gpu_mean=2.0), stats(2001, 20.0, 0, 0, gpu_mean=3.0), 60.0)
        self.assertAlmostEqual(v["gpu_wait_ms_mean"], (3.0 * 2001 - 2.0 * 1001) / 1000, places=3)

    def test_each_limit_fails_on_its_own(self):
        cases = [
            ("rate", stats(commits=2312, seconds=40.0, late=9, stalls=2), 61.5, "commits/s"),
            ("late", stats(commits=3512, seconds=40.0, late=60, stalls=2), 61.5, "late"),
            ("stall", stats(commits=3512, seconds=40.0, late=9, stalls=3), 61.5, "above 100 ms"),
            ("timeouts", stats(commits=3512, seconds=40.0, late=9, stalls=2, timeouts=4), 61.5, "present-wait"),
            ("heat", END, 90.5, "hottest zone"),
            ("no temperature", END, None, "temperature"),
        ]
        for name, end, hottest, words in cases:
            with self.subTest(name):
                v = perf.verdict(START, end, hottest)
                self.assertEqual(v["status"], "failed")
                self.assertEqual(len(v["failures"]), 1, v["failures"])
                self.assertIn(words, v["failures"][0])

    def test_missing_or_empty_statistics_fail(self):
        self.assertEqual(perf.verdict(None, END, 60.0)["status"], "failed")
        self.assertIn("no commits", perf.verdict(END, END, 60.0)["failures"][0])

    def test_command_line(self):
        with tempfile.TemporaryDirectory() as temp:
            start, end = Path(temp) / "start.json", Path(temp) / "end.json"
            start.write_text(json.dumps(START))
            end.write_text(json.dumps(END))
            out = subprocess.run([sys.executable, str(PATH), str(start), str(end), "61.5"],
                                 capture_output=True, text=True, encoding="utf-8", check=True).stdout
            self.assertEqual(json.loads(out)["status"], "passed")
            out = subprocess.run([sys.executable, str(PATH), str(start), str(Path(temp) / "missing"), "nan"],
                                 capture_output=True, text=True, encoding="utf-8", check=True).stdout
            self.assertEqual(json.loads(out)["status"], "failed")

    def test_runner_line_names_both_numbers_and_failures(self):
        line = userspace.format_valve_performance(perf.verdict(START, END, 61.5))
        self.assertTrue(line.startswith("performance: passed (90.0 commits/s"), line)
        self.assertIn("startup stalls 2", line)
        failed = userspace.format_valve_performance(perf.verdict(START, END, 90.5))
        self.assertIn("hottest zone 90.5", failed)
        # The agreed limit: up to 90 °C passes.
        self.assertEqual(perf.verdict(START, END, 90.0)["status"], "passed")
        self.assertEqual(userspace.format_valve_performance(None), "performance: no verdict")


if __name__ == "__main__":
    unittest.main()
