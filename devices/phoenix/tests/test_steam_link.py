# SPDX-License-Identifier: Unlicense
"""Steam Link's client on the PICO 4: its video decoder and controllers."""

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

    def test_vrdevice_path_never_opens_the_cameras(self):
        # The Frame's lookup (v4l2-ctl --list-devices) opens every node and broke head tracking.
        script = read(ROOTFS / "usr/local/bin/vrdevice_path")
        commands = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("--list-devices", commands)
        # Only the codec's own device.
        self.assertIn("\t*.video-codec) ;;", script)
        self.assertIn('video_decode) cards=("Qualcomm Venus video decoder") ;;', script)
        self.assertIn('video_encode) cards=("Qualcomm Venus video encoder") ;;', script)


if __name__ == "__main__":
    unittest.main()
