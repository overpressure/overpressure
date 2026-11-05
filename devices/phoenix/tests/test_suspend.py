# SPDX-License-Identifier: Unlicense
"""The driver's clocks across a suspend (docs/xr.md#head-imu)."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MONADO = ROOT / "src/monado"


def read(name: str) -> str:
    return (MONADO / name).read_text(encoding="utf-8")


class ResumeTests(unittest.TestCase):
    def test_slept_time_is_boottime_less_monotonic(self):
        cam = read("pico4_cam.c")
        body = cam[cam.index("pico4_slept_ns(void)\n{"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("now_ns(CLOCK_BOOTTIME)", body)
        self.assertIn("now_ns(CLOCK_MONOTONIC)", body)
        self.assertIn("#define PICO4_SLEEP_DETECT_NS", read("pico4_cam.h"))

    def test_imu_offset_is_taken_again_before_mapping_samples(self):
        # The QTimer runs on through a suspend while CLOCK_MONOTONIC stands: the first samples after
        # it would land the sleep's length in the future and the next ones step back, which Monado's
        # 3DoF filter asserts on (vrserver died at the resume of 2026-10-26).
        hmd = read("pico4_hmd.c")
        loop = hmd[hmd.index("imu_thread(void *ptr)"):]
        loop = loop[:loop.index("\n}\n")]
        detect = loop.index("slept - slept_ns > PICO4_SLEEP_DETECT_NS")
        self.assertLess(detect, loop.index("handle_sample(h, &samples[i])"))
        self.assertIn("calibrate_clock(h);", loop[detect:loop.index("handle_sample(h, &samples[i])")])

    def test_gyro_samples_only_move_forward(self):
        hmd = read("pico4_hmd.c")
        guard = hmd.index("if (ts <= h->last_gyro_ns)")
        self.assertLess(guard, hmd.index("m_imu_3dof_update(&h->fusion"))
        self.assertLess(guard, hmd.index("pico4_slam_push_imu(h->slam"))

    def test_camera_offset_is_taken_again_after_a_suspend(self):
        slam = read("pico4_slam.c")
        sof = slam[slam.index("frame_sof_ns(struct pico4_slam *s"):]
        sof = sof[:sof.index("\n}\n")]
        self.assertRegex(sof, r"slept - s->slept_ns > PICO4_SLEEP_DETECT_NS")
        self.assertIn("s->qtimer_offset_ns = qtimer_offset();", sof)
        self.assertIn("s->slept_ns = pico4_slept_ns();", slam)

    def test_capture_starts_again_after_a_suspend(self):
        # The capture streamed into the suspend and came back broken: the
        # camera thread's restart runs once the thread sees the system slept.
        slam = read("pico4_slam.c")
        thread = slam[slam.index("camera_thread(void *ptr)"):]
        thread = thread[:thread.index("\n}\n")]
        self.assertIn('s->restart_why = "the system slept";', thread)
        self.assertLess(thread.index("slept - slept_ns > PICO4_SLEEP_DETECT_NS"), thread.index("restart_capture(s, why)"))


if __name__ == "__main__":
    unittest.main()
