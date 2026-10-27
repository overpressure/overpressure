"""The IMX471 RGB camera in pico4_cam: the settings PICO OS's camera module gives it."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAM = ROOT / "src/monado/pico4_cam.c"
HEADER = ROOT / "src/monado/pico4_cam.h"
SLAM = ROOT / "src/monado/pico4_slam.c"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def function(source: str, name: str) -> str:
    start = source.index("\n" + name + "(")
    return source[start:source.index("\n}\n", start)]


def table(source: str) -> list:
    start = source.index("imx471_50fps[] = {")
    body = source[start:source.index("};", start)]
    return [(int(a, 16), int(v, 16)) for a, v in re.findall(r"\{0x([0-9a-f]{4}), 0x([0-9a-f]{2})\}", body)]


class RgbCameraTests(unittest.TestCase):
    def test_slot_and_geometry(self):
        header = read(HEADER)
        self.assertIn("#define PICO4_CAM_RGB_SLOT 4", header)
        self.assertIn("#define PICO4_CAM_RGB_WIDTH 2328", header)
        self.assertIn("#define PICO4_CAM_RGB_HEIGHT 1748", header)
        self.assertIn("#define PICO4_CAM_SLOTS 5", header)

    def test_mode_table_is_the_50_fps_mode(self):
        regs = dict(table(read(CAM)))
        self.assertEqual(len(table(read(CAM))), 114)
        self.assertEqual(regs[0x0136], 0x18)  # 24 MHz external clock
        self.assertEqual((regs[0x0340] << 8) | regs[0x0341], 4520)  # frame length
        self.assertEqual((regs[0x0342] << 8) | regs[0x0343], 2560)  # line length
        self.assertEqual((regs[0x034c] << 8) | regs[0x034d], 2328)
        self.assertEqual((regs[0x034e] << 8) | regs[0x034f], 1748)
        self.assertEqual(regs[0x0114], 0x03)  # four lanes
        self.assertEqual(regs[0x0901], 0x22)  # 2x2 binning
        # 50 fps at this frame length: 4425 ns a line, as the header says.
        self.assertAlmostEqual(1e9 / (4520 * 50), 4424.8, places=1)

    def test_probe_power_and_link(self):
        source = read(CAM)
        self.assertIn("#define IMX471_I2C_ADDR 0x34", source)
        self.assertIn("#define IMX471_CHIP_ID_REG 0x0016", source)
        self.assertIn("#define IMX471_CHIP_ID 0x0471", source)
        probe = function(source, "sensor_probe")
        # PICO OS's power-up order: reset low, VIO, VANA, VDIG, GPIO2, standby, MCLK, reset high, GPIO1.
        up = re.findall(r"SENSOR_CMD_PWR_UP, SENSOR_SEQ_(\w+)", probe[:probe.index("} else {")])
        self.assertEqual(up, ["RESET", "VIO", "VANA", "VDIG", "CUSTOM2", "STANDBY", "MCLK", "RESET", "CUSTOM1"])
        csiphy = function(source, "csiphy_setup")
        for line in ("ci->lane_mask = IMX471_LANE_MASK;", "ci->lane_assign = IMX471_LANE_ASSIGN;",
                     "ci->lane_cnt = 4;", "ci->data_rate = IMX471_LANE_BPS;", "ci->mipi_flags = IMX471_MIPI_FLAGS;"):
            self.assertIn(line, csiphy)
        self.assertIn("#define IMX471_SETTLE_TIME 3400000000ull", source)  # settle count 17
        # The data rate per lane: the output pixel clock x 10 bits / 4 lanes.
        self.assertIn("#define IMX471_LANE_BPS 2064848000ull", source)
        self.assertEqual(825939200 * 10 // 4, 2064848000)

    def test_gain_register(self):
        # Analog gain = 1024 / (1024 - register), at most 16x (960): 2x is 512.
        rgb = function(read(CAM), "build_rgb_table")
        self.assertIn("unsigned again = 1024u - 1024u * 16u / gain;", rgb)
        for gain, register in ((16, 0), (32, 512), (256, 960)):
            self.assertEqual(1024 - 1024 * 16 // gain, register)

    def test_frames_have_no_metadata_line(self):
        fill = function(read(CAM), "fill_frame")
        self.assertIn("f->meta = NULL; // no metadata line: the image starts at once", fill)

    def test_paused_while_nobody_shows_it(self):
        # The sensor stops streaming while the room view is closed (PICO OS opens the camera
        # only for its see-through); the request manager's SOF watchdog is then no failure.
        pause = function(read(CAM), "pico4_cam_set_rgb_streaming")
        self.assertIn("const struct reg8 reg = {0x0100, on ? 0x01 : 0x00};", pause)
        self.assertIn("k->paused = !on;", pause)
        drain = function(read(CAM), "drain_crm")
        guard = drain.index("CAM_REQ_MGR_ERROR_TYPE_SOF_FREEZE && k->paused")
        self.assertLess(guard, drain.index("k->failed = true;"))
        service = function(read(SLAM), "rgb_service")
        self.assertIn("pico4_passthrough_streaming(s->passthrough)", service)
        self.assertIn("(int64_t)5 * U_TIME_1S_IN_NS", service)
        self.assertIn("pico4_cam_set_rgb_streaming(s->cam, want)", service)


if __name__ == "__main__":
    unittest.main()
