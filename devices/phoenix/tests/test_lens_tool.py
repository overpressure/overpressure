"""pico4-lens: only stock's lens frames can be built, and a move follows the stock service."""

import importlib.machinery
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_loader = importlib.machinery.SourceFileLoader("pico4_lens", str(ROOT / "userspace/rootfs/usr/bin/pico4-lens"))
_spec = importlib.util.spec_from_loader("pico4_lens", _loader)
lens = importlib.util.module_from_spec(_spec)
_loader.exec_module(lens)


def position_reply(value: int, hall: int = 893) -> bytes:
    body = bytes((0x79, value & 255, value >> 8, hall & 255, hall >> 8))
    return body + bytes((lens.xor(body),))


def state_reply(state: int) -> bytes:
    return bytes((0x79, state, 0x79 ^ state))


class FakePort:
    def __init__(self, states, end):
        self.states, self.end, self.sent = list(states), end, []

    def __call__(self, tx, length):
        self.sent.append(tx)
        if tx[1] == lens.GET_MOVE_STATE:
            return state_reply(self.states.pop(0))
        if tx[1] == lens.GET_POSITION:
            return position_reply(self.end)
        return bytes((0x79,))


class FrameTests(unittest.TestCase):
    def test_frames_as_the_stock_service(self):
        self.assertEqual(lens.frame(lens.GET_POSITION).hex(), "a0080000" "00a8")
        self.assertEqual(lens.frame(lens.GET_MOVE_STATE).hex(), "a0110000" "00b1")
        self.assertEqual(lens.frame(lens.REPORT_ENABLE).hex(), "a0220000" "0082")
        # 63.50 mm: A0 07 00 lo hi xor.
        self.assertEqual(lens.frame(lens.MOVE, 6350).hex(), "a00700ce18" "71")

    def test_nothing_else_can_be_built(self):
        for position in (6199, 7201, 5800, 0, 63.5, None):
            with self.assertRaises(ValueError):
                lens.frame(lens.MOVE, position)
        for command in set(range(256)) - {lens.GET_POSITION, lens.GET_MOVE_STATE, lens.REPORT_ENABLE}:
            with self.assertRaises(ValueError):
                lens.frame(command)
        with self.assertRaises(ValueError):
            lens.frame(lens.REPORT_ENABLE, 1)

    def test_replies(self):
        self.assertEqual(lens.parse_position(position_reply(7195)), 7195)
        self.assertEqual(lens.parse_position(position_reply(893, 7195)), 7195)
        for bad in (position_reply(7195)[:5], b"\x78" + position_reply(7195)[1:], position_reply(100, 200)):
            with self.assertRaises(ValueError):
                lens.parse_position(bad)
        self.assertEqual(lens.parse_state(state_reply(1)), 1)
        with self.assertRaises(ValueError):
            lens.parse_state(bytes((0x79, 1, 0)))


class MoveTests(unittest.TestCase):
    def test_move_sends_stocks_sequence_and_waits(self):
        port = FakePort([0, 1, 1, 0], 6351)
        self.assertEqual(lens.move(port, 6350, sleep=lambda s: None), 6351)
        self.assertEqual([tx[1] for tx in port.sent],
                         [lens.GET_MOVE_STATE, lens.REPORT_ENABLE, lens.MOVE, lens.GET_MOVE_STATE,
                          lens.GET_MOVE_STATE, lens.GET_MOVE_STATE, lens.GET_POSITION])

    def test_refused_while_moving(self):
        port = FakePort([1], 6350)
        with self.assertRaises(RuntimeError):
            lens.move(port, 6350, sleep=lambda s: None)
        self.assertEqual(len(port.sent), 1)

    def test_bad_target_sends_nothing(self):
        port = FakePort([0], 6350)
        with self.assertRaises(ValueError):
            lens.move(port, 5000, sleep=lambda s: None)
        self.assertEqual(port.sent, [])

    def test_end_of_range_or_wrong_position_is_an_error(self):
        with self.assertRaises(RuntimeError):
            lens.move(FakePort([0, 3], 6350), 6350, sleep=lambda s: None)
        with self.assertRaises(RuntimeError):
            lens.move(FakePort([0, 0], 6500), 6350, sleep=lambda s: None)

    def test_a_move_that_never_stops_times_out(self):
        clock = iter(range(0, 1000, 10))
        with self.assertRaises(TimeoutError):
            lens.move(FakePort([0] + [1] * 50, 6350), 6350, sleep=lambda s: None, now=lambda: next(clock))


class SessionTests(unittest.TestCase):
    def read(self, path):
        return (ROOT / path).read_text(encoding="utf-8")

    def test_driver_follows_the_lenses_and_steamvr_gets_them(self):
        hmd = self.read("src/monado/pico4_hmd.c")
        self.assertIn("ipd_thread", hmd)
        self.assertIn("fabs(now - known) > 0.0002", hmd)
        patch = self.read("userspace/pkgbuilds/pico4-monado/monado-steamvr-lenses-standby.patch")
        self.assertIn("SetDisplayEyeToHead", patch)
        self.assertIn("Prop_UserIpdMeters_Float", patch)
        recipe = self.read("userspace/pkgbuilds/pico4-monado/PKGBUILD")
        self.assertIn("patch -d monado -p1 < monado-steamvr-lenses-standby.patch", recipe)

    def test_standby_switches_the_panels_backlight(self):
        patch = self.read("userspace/pkgbuilds/pico4-monado/monado-steamvr-lenses-standby.patch")
        self.assertIn("VREvent_EnterStandbyMode", patch)
        self.assertIn("VREvent_LeaveStandbyMode", patch)
        hmd = self.read("src/monado/pico4_hmd.c")
        self.assertIn("/sys/class/backlight/pico4-panel/bl_power", hmd)
        panel = self.read("kernel/src/drivers/gpu/drm/panel/panel-pico4-ls026b3sa.c")
        self.assertIn('"pico4-panel"', panel)
        self.assertIn("if (ctx->enabled)", panel)
        rules = self.read("userspace/rootfs/usr/lib/udev/rules.d/70-pico4-xr.rules")
        self.assertIn('KERNEL=="pico4-panel"', rules)

    def test_the_driver_logs_the_battery_level_raw(self):
        controller = self.read("src/monado/pico4_controller.c")
        self.assertIn('"Battery level %d (raw)"', controller)
        self.assertNotIn("get_battery_status", controller)


class ValveMesaTests(unittest.TestCase):
    def read(self, path):
        return (ROOT / path).read_text(encoding="utf-8")

    def test_the_systems_mesa_is_valves(self):
        packages = self.read("userspace/packages.txt").splitlines()
        self.assertIn("deckard-arch-hotfixes-release-0.3/deckard-mesa-linux-aarch64", packages)
        self.assertNotIn("mesa", packages)
        self.assertNotIn("pico4/pico4-turnip-kgsl", packages)
        self.assertIn("pico4/pico4-libdisplay-info", packages)

    def test_steams_wifi_connections_are_understood(self):
        packages = self.read("userspace/packages.txt").splitlines()
        for package in ("networkmanager", "libnm"):
            self.assertIn(f"deckard-arch-hotfixes-release-0.3/{package}", packages)
            self.assertNotIn(package, packages)

    def test_iwd_keeps_the_interface_with_its_address(self):
        self.assertIn("DefaultInterface=ath11k_pci", self.read("userspace/rootfs/etc/iwd/main.conf"))

    def test_no_depth_projection_without_a_mesh(self):
        patch = self.read("userspace/pkgbuilds/pico4-monado/monado-steamvr-roomview-mesh.patch")
        self.assertIn('return pico4_hmd_depth_mesh != NULL && ::access("/dev/cvp", F_OK) == 0;', patch)

    def test_only_the_compositor_loses_storage_usage(self):
        layer = self.read("src/vrdisplay/pico4-swapchain-usage.c")
        self.assertIn('"vrcompositor"', layer)
        self.assertIn("info.imageUsage &= ~VK_IMAGE_USAGE_STORAGE_BIT;", layer)


class LensPanelTests(unittest.TestCase):
    def read(self, path):
        return (ROOT / path).read_text(encoding="utf-8")

    def test_it_moves_through_the_tool_and_runs_with_steamvr(self):
        panel = self.read("src/xr/pico4-lens-panel.cpp")
        self.assertIn('execlp("pico4-lens", "pico4-lens", "set", value', panel)
        self.assertIn("y = PICO4_PANEL_H - e.data.mouse.y", panel)
        # With an image through these two, the compositor crashes at every stop (docs/session.md).
        self.assertIn("overlay->SetOverlayTexture(handle, &texture)", panel)
        self.assertNotIn("overlay->SetOverlayRaw(", panel)
        self.assertNotIn("overlay->SetOverlayFromFile(", panel)
        conf = self.read("userspace/rootfs/usr/lib/systemd/user/steamvr.service.d/53-pico4-lens-panel.conf")
        self.assertIn("Wants=pico4-lens-panel.service", conf)
        self.assertIn("pico4-lens-panel pico4-lens-panel.cpp", self.read("userspace/pkgbuilds/pico4-monado/PKGBUILD"))

    def test_steamvrs_display_covers_the_motors_range(self):
        patch = self.read("userspace/pkgbuilds/pico4-monado/monado-steamvr-lenses-standby.patch")
        self.assertIn("Prop_IpdUIRangeMinMeters_Float, 0.062f", patch)
        self.assertIn("Prop_IpdUIRangeMaxMeters_Float, 0.072f", patch)


if __name__ == "__main__":
    unittest.main()
