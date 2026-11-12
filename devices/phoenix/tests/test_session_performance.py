# SPDX-License-Identifier: Unlicense
"""The runner's line for session-performance's verdict, on golden verdicts."""

import unittest

from devices.phoenix.tooling import userspace

LIMITS = {
    "commit_hz_min": 85.5,
    "late_percent_max": 1.0,
    "stalls_max": 0,
    "hottest_c_max": 90.0,
    "job_margin_p99_ms_min": 1.0,
    "web_helper_gpu_percent_tested": 1.0,
}

# session-performance's verdict on 30 s of Valve's session at 90 commits per second, 61.5 °C.
PASSED = {
    "status": "passed",
    "failures": [],
    "warnings": [],
    "window_seconds": 30.0,
    "commit_hz": 90.0,
    "late_percent": 0.07,
    "stalls": 0,
    "gpu_ms": {"mean": 3.1, "p90": 4.0, "max": 9.8},
    "job_margin_ms": None,
    "web_helper_gpu_percent": None,
    "hottest_c": 61.5,
    "max_interval_ms": 20.4,
    "limits": LIMITS,
}

# The same window at 90.5 °C.
HOT = {**PASSED, "status": "failed", "failures": ["hottest zone 90.5 °C, above 90.0 °C"], "hottest_c": 90.5}

# A window with the job margins measured and no Steam panel drawn.
NO_PANEL = {
    **PASSED,
    "warnings": ["no Steam panel was tested: the web helpers drew nothing in this window"],
    "job_margin_ms": {"p50": 7.1, "p99": 6.3, "frames": 900, "without_a_job": 0},
    "web_helper_gpu_percent": 0.0,
    "hottest_c": 60.0,
}


class RunnerLineTests(unittest.TestCase):
    def test_the_line_names_both_numbers(self):
        line = userspace.format_valve_performance(PASSED)
        self.assertTrue(line.startswith("performance: passed (90.0 commits/s"), line)

    def test_the_line_names_the_failures(self):
        self.assertIn("hottest zone 90.5", userspace.format_valve_performance(HOT))

    def test_the_line_names_the_job_margin_and_the_warnings(self):
        line = userspace.format_valve_performance(NO_PANEL)
        self.assertIn("job margin p99 6.3 ms, web helpers 0.0 % of the GPU", line)
        self.assertIn("warning: no Steam panel was tested", line)

    def test_no_verdict(self):
        self.assertEqual(userspace.format_valve_performance(None), "performance: no verdict")


if __name__ == "__main__":
    unittest.main()
