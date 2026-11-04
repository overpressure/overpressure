import importlib.machinery
import importlib.util
import json
import unittest
from pathlib import Path

from devices.phoenix.tooling import userspace

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "userspace/rootfs/usr/libexec/pico4-poc/msm-frame-stats"
_loader = importlib.machinery.SourceFileLoader("msm_frame_stats", str(PATH))
_spec = importlib.util.spec_from_loader("msm_frame_stats", _loader)
stats = importlib.util.module_from_spec(_spec)
_loader.exec_module(stats)
PERF = ROOT / "userspace/rootfs/usr/libexec/pico4-poc/session-performance"
_ploader = importlib.machinery.SourceFileLoader("session_performance_msm", str(PERF))
_pspec = importlib.util.spec_from_loader("session_performance_msm", _ploader)
perf = importlib.util.module_from_spec(_pspec)
_ploader.exec_module(perf)

REFRESH = 1 / 90


def trace(flip_times, vblank_count, jobs):
    """tracefs lines as msm's events print them (formats from the 7.2 kernel)."""
    lines = []
    for i in range(vblank_count):
        lines.append(f"          <idle>-0       [000] d.h1.  {100 + i * REFRESH:.6f}: drm_vblank_event: "
                     f"crtc=0, seq={i}, time={i}, high-prec=true")
    for t in flip_times:
        lines.append(f"     kworker/0:1-42      [000] d.h1.  {t:.6f}: dpu_crtc_complete_flip: id=95")
    for pid, elapsed_ms in jobs:
        lines.append(f"    RenderThread-{pid}    [001] d..1.  101.000000: msm_gpu_submit_retired: id=1 pid={pid} "
                     f"ring=0:5 elapsed={int(elapsed_ms * 1e6)} ns mhz=587 start=1 end=2")
    return lines


class MsmFrameStatsTests(unittest.TestCase):
    def test_job_margins_are_taken_before_each_vblank(self):
        vblanks = [100 + i * REFRESH for i in range(200)]
        ends = [v - 0.007 for v in vblanks[1:]]
        ends[50] = vblanks[51] - 0.0005   # one job that ends just in time
        del ends[100]                     # one vblank without a job
        margins = stats.job_margins(ends, vblanks, REFRESH)
        self.assertEqual(margins["frames"], 199)
        self.assertEqual(margins["without_a_job"], 1)
        self.assertAlmostEqual(margins["p50"], 7.0, places=1)
        self.assertLess(margins["p99"], 1.0)

    def test_a_little_margin_fails_the_check_and_the_web_helpers_share_is_reported(self):
        flips = [100 + i * REFRESH for i in range(901)]
        end = stats.parse(trace(flips, 901, [(700, 5.9)]), {700})
        end["job_margin_ms"] = {"p50": 5.5, "p99": 0.0, "frames": 900, "without_a_job": 12}
        end["web_helper_gpu_percent"] = 12.5
        start = {"target": "msm", "commits": 0, "commit_hz": 0, "late": 0, "stalls": 0}
        v = perf.verdict(start, end, 60.0)
        self.assertEqual(v["status"], "failed")
        self.assertIn("below 1.0 ms", v["failures"][0])
        self.assertEqual(v["web_helper_gpu_percent"], 12.5)
        end["job_margin_ms"]["p99"] = 6.3
        self.assertEqual(perf.verdict(start, end, 60.0)["status"], "passed")

    def test_a_window_without_a_panel_passes_with_a_warning(self):
        flips = [100 + i * REFRESH for i in range(901)]
        end = stats.parse(trace(flips, 901, [(700, 5.9)]), {700})
        end["job_margin_ms"] = {"p50": 7.1, "p99": 6.3, "frames": 900, "without_a_job": 0}
        end["web_helper_gpu_percent"] = 0.0
        start = {"target": "msm", "commits": 0, "commit_hz": 0, "late": 0, "stalls": 0}
        v = perf.verdict(start, end, 60.0)
        self.assertEqual(v["status"], "passed")
        self.assertIn("no Steam panel was tested", v["warnings"][0])
        line = userspace.format_valve_performance(v)
        self.assertIn("job margin p99 6.3 ms, web helpers 0.0 % of the GPU", line)
        self.assertIn("warning: no Steam panel was tested", line)
        end["web_helper_gpu_percent"] = 12.5
        self.assertEqual(perf.verdict(start, end, 60.0)["warnings"], [])

    def test_the_web_helpers_threads_count_apart_from_the_compositors(self):
        flips = [100 + i * REFRESH for i in range(91)]
        lines = trace(flips, 91, [(700, 5.9), (800, 500.0)])
        s = stats.parse(lines, {700}, {800})
        self.assertEqual(s["gpu_jobs"], 1)
        self.assertAlmostEqual(s["web_helper_gpu_percent"], 50.0, places=0)

    def test_flips_vblanks_and_the_compositors_gpu_time(self):
        flips = [100 + i * REFRESH for i in range(91)]
        flips[50:] = [t + REFRESH for t in flips[50:]]   # one late flip (two refreshes)
        lines = trace(flips, 181, [(700, 5.9), (702, 6.1), (701, 12.0)])
        s = stats.parse(lines, {700, 702})
        self.assertEqual(s["target"], "msm")
        self.assertEqual(s["commits"], 91)
        self.assertEqual(s["late"], 1)
        self.assertEqual(s["stalls"], 0)
        self.assertAlmostEqual(s["vblank_hz"], 90.0, places=1)
        self.assertAlmostEqual(s["max_interval_ms"], 2 * REFRESH * 1e3, places=2)
        self.assertEqual(s["gpu_jobs"], 2)   # thread 701 belongs to another process
        self.assertAlmostEqual(s["gpu_ms"]["mean"], 6.0, places=3)

    def test_performance_judges_the_recording_as_the_layers_window(self):
        flips = [100 + i * REFRESH for i in range(900)]
        end = stats.parse(trace(flips, 900, [(700, 6.0)]), {700})
        start = {"target": "msm", "commits": 0, "commit_hz": 0, "late": 0, "stalls": 0}
        v = perf.verdict(start, end, 60.0)
        self.assertEqual(v["status"], "passed", v["failures"])
        self.assertAlmostEqual(v["commit_hz"], 90.0, delta=0.2)
        self.assertEqual(v["gpu_ms"]["mean"], 6.0)
        json.dumps(v)

    def test_stalls_and_no_frames(self):
        s = stats.parse(trace([100.0, 100.2], 3, []), {700})
        self.assertEqual(s["stalls"], 1)
        self.assertNotIn("gpu_ms", s)
        self.assertEqual(stats.parse([], set())["commits"], 0)


if __name__ == "__main__":
    unittest.main()
