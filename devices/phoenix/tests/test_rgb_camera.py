# SPDX-License-Identifier: Unlicense
"""The IMX471 RGB camera: the settings PICO OS's camera module gives it, in the kernel's
driver, and its V4L2 path in pico4_cam."""

import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAM = ROOT / "src/monado/pico4_cam.c"
HEADER = ROOT / "src/monado/pico4_cam.h"
SLAM = ROOT / "src/monado/pico4_slam.c"
DRIVER = ROOT / "kernel/src/drivers/media/i2c/pico4-imx471.c"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def function(source: str, name: str) -> str:
    start = source.index("\n" + name + "(")
    return source[start:source.index("\n}\n", start)]


class RgbCameraTests(unittest.TestCase):
    def test_slot_and_geometry(self):
        header = read(HEADER)
        self.assertIn("#define PICO4_CAM_RGB_SLOT 4", header)
        self.assertIn("#define PICO4_CAM_RGB_WIDTH 2328", header)
        self.assertIn("#define PICO4_CAM_RGB_HEIGHT 1748", header)
        self.assertIn("#define PICO4_CAM_SLOTS 5", header)

    def test_the_50_fps_mode_fits_the_driver(self):
        # The table is generated at the build (kernel/imx471_mode.py) for this mode.
        spec = importlib.util.spec_from_file_location("imx471_mode", ROOT / "kernel/imx471_mode.py")
        mode = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mode)
        source = read(DRIVER)
        self.assertRegex(source, rf"#define IMX471_WIDTH\s+{mode.WIDTH}")
        self.assertRegex(source, rf"#define IMX471_HEIGHT\s+{mode.HEIGHT}")
        self.assertRegex(source, rf"#define IMX471_MAX_LINES\s+{mode.FRAME_LENGTH - 10}")
        # 50 fps at this frame length: 4425 ns a line, as the header says.
        self.assertAlmostEqual(1e9 / (4520 * 50), 4424.8, places=1)
        self.assertIn("#define PICO4_CAM_RGB_LINE_NS 4425", read(HEADER))

    def test_identity_and_link(self):
        source = read(DRIVER)
        self.assertRegex(source, r"#define IMX471_REG_CHIP_ID\s+0x0016")
        self.assertRegex(source, r"#define IMX471_CHIP_ID\s+0x0471")
        # The data rate per lane: the output pixel clock x 10 bits / 4 lanes, on a double data rate.
        self.assertRegex(source, r"#define IMX471_PIXEL_RATE\s+825939200LL")
        self.assertRegex(source, r"#define IMX471_LINK_FREQ\s+1032424000LL")
        self.assertEqual(825939200 * 10 // 4, 2 * 1032424000)

    def test_gain_register(self):
        # Analog gain = 1024 / (1024 - register), at most 16x (960): 2x is 512.
        exposure = function(read(CAM), "v4l_rgb_exposure")
        self.assertIn("unsigned again = 1024u - 1024u * 16u / gain;", exposure)
        self.assertIn("again > IMX471_ANALOG_GAIN_MAX ? IMX471_ANALOG_GAIN_MAX : again", exposure)
        self.assertIn("#define IMX471_ANALOG_GAIN_MAX 960", read(CAM))
        for gain, register in ((16, 0), (32, 512), (256, 960)):
            self.assertEqual(1024 - 1024 * 16 // gain, register)

    def test_frames_have_no_metadata_line(self):
        fill = function(read(CAM), "v4l_fill_frame")
        rgb = fill[fill.index("if (k->rgb) {\n\t\tf->width"):fill.index("return;")]
        self.assertIn("f->lines = PICO4_CAM_RGB_HEIGHT;", rgb)
        self.assertIn("f->data = vb->raw;", rgb)

    def test_paused_while_nobody_shows_it(self):
        # The sensor stops streaming while the room view is closed (PICO OS opens the camera
        # only for its see-through).
        pause = function(read(CAM), "pico4_cam_set_rgb_streaming")
        self.assertIn("struct v4l2_control ctrl = {.id = V4L_CID_RGB_PAUSED, .value = !on};", pause)
        service = function(read(SLAM), "rgb_service")
        self.assertIn("pico4_passthrough_streaming(s->passthrough)", service)
        self.assertIn("(int64_t)5 * U_TIME_1S_IN_NS", service)
        self.assertIn("pico4_cam_set_rgb_streaming(s->cam, want)", service)


if __name__ == "__main__":
    unittest.main()
