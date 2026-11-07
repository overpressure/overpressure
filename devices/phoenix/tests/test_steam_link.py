# SPDX-License-Identifier: Unlicense
"""Steam Link's client on the PICO 4: its video decoder and controllers."""

import json
import re
import unittest
from pathlib import Path

from devices.phoenix.tooling import assets

ROOT = Path(__file__).resolve().parents[1]
ROOTFS = ROOT / "userspace/rootfs"
UNITS = ROOTFS / "usr/lib/systemd/system"


def read(path: Path) -> str:
    """Return a file's text."""
    return path.read_text(encoding="utf-8")


class SteamLinkTests(unittest.TestCase):
    """Steam Link's client (vrlink) on the PICO 4's video decoder (Venus) and controllers."""

    def setUp(self):
        self.build = read(ROOT / "scripts/build-userspace.sh")

    def test_venus_firmware_from_the_modem_partition(self):
        self.assertEqual(assets.VENUS, ("venus.*",))
        self.assertEqual(assets.KINDS["graphics-firmware"].sources["venus."], "modem /image")
        self.assertIn('install -m 0644 "$firmware"/venus.* "$T/usr/lib/firmware/"', self.build)

    def test_mainline_probes_venus_once_its_firmware_is_there(self):
        unit = read(UNITS / "pico4-venus-probe.service")
        self.assertIn("ConditionPathExists=/sys/bus/platform/drivers/qcom-venus\n", unit)
        self.assertIn("ConditionPathExists=/usr/lib/firmware/venus.mdt\n", unit)
        script = read(ROOTFS / "usr/libexec/pico4/venus-probe")
        self.assertIn('echo "$DEV" > /sys/bus/platform/drivers/qcom-venus/bind', script)
        self.assertIn('firmware-name = "venus.mdt";', read(ROOT / "kernel/sm8250-pico4-phoenix.dts"))
        config = read(ROOT / "kernel/pico4.config")
        self.assertIn("CONFIG_VIDEO_QCOM_VENUS=y", config)
        # Venus leaves SM8250 to iris whenever iris is built.
        self.assertIn("# CONFIG_VIDEO_QCOM_IRIS is not set", config)

    def test_vrdevice_path_never_opens_the_cameras(self):
        # The Frame's lookup (v4l2-ctl --list-devices) opens every node and broke head tracking.
        script = read(ROOTFS / "usr/local/bin/vrdevice_path")
        commands = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("--list-devices", commands)
        # Only the codec's own device.
        self.assertIn("\t*.video-codec) ;;", script)
        self.assertIn('video_decode) cards=("Qualcomm Venus video decoder") ;;', script)
        self.assertIn('video_encode) cards=("Qualcomm Venus video encoder") ;;', script)

    def test_the_compatibility_layer_reaches_vrlink_only(self):
        self.assertIn('build_package pico4-vrlink "$project"/src/vrlink/*', self.build)
        self.assertIn("pico4/pico4-vrlink", read(ROOT / "userspace/packages.txt").split())
        self.assertIn('grep -qx \'\\./vrlink "\\$@" &\' "$vrlink_run"', self.build)
        self.assertIn("LD_PRELOAD=/usr/lib/libpico4-vrlink-compat.so${LD_PRELOAD:+:$LD_PRELOAD} ./vrlink", self.build)

    def test_the_hook_is_exported_and_finds_the_real_interface(self):
        # Built with -fvisibility=hidden: an unexported hook interposes nothing.
        self.assertIn("-fvisibility=hidden", read(ROOT / "userspace/pkgbuilds/pico4-vrlink/PKGBUILD"))
        hooks = read(ROOT / "src/vrlink/pico4-vrlink-compat.c")
        self.assertIn('#define EXPORT __attribute__((visibility("default")))', hooks)
        self.assertIn('dlsym(RTLD_NEXT, "VR_GetGenericInterface")', hooks)

    def test_our_controllers_are_the_frames_to_vrlink(self):
        # vrlink sends input only for the Frame's type, and the PC's driver reads it only for that type.
        hooks = read(ROOT / "src/vrlink/pico4-vrlink-compat.c")
        self.assertIn("EXPORT void *\nVR_GetGenericInterface(const char *version, int32_t *error)\n{", hooks)
        self.assertIn('return vr_string_result("frame_controller", value, size, error);', hooks)
        # The PC's driver activates a controller only with a manufacturer; ours has none.
        self.assertIn('return vr_string_result("PICO", value, size, error);', hooks)

    def test_vrlinks_input_paths_are_the_reverse_of_the_bindings_remapping(self):
        # vrlink reads inputs by the Frame's paths: each one it asks for has to lead to one of ours.
        hooks = read(ROOT / "src/vrlink/pico4-vrlink-compat.c")
        paths = re.findall(r'\{"(/user/hand/[^"]+)/", "(/user/hand/[^"]+)/"\}', hooks)
        self.assertEqual(len(paths), 4)
        remapping = json.loads(read(ROOT / "userspace/session/frame-controller-remapping.json"))
        remapped = {(entry["from"], entry["to"]) for entry in remapping["autoremappings"]}
        for path in paths:
            frame, ours = path
            if frame.startswith("/user/hand/left/input/thumbstick"):
                # The file names the right hand's stick; the left's is its mirror.
                frame, ours = frame.replace("left", "right"), ours.replace("left", "right")
            self.assertIn((frame, ours), remapped)


if __name__ == "__main__":
    unittest.main()
