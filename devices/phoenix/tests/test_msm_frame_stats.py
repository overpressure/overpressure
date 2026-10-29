import importlib.machinery
import importlib.util
import json
import unittest
from pathlib import Path

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
