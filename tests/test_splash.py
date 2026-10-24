import re
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOTFS = ROOT / "userspace/rootfs"
BINARY = ROOT / "prebuilt/aarch64/pico4-splash"


class SplashTests(unittest.TestCase):
    """an issue: a sign of life in the lenses while the installed system starts."""

    def test_prebuilt_is_a_static_aarch64_program(self):
        data = BINARY.read_bytes()
        self.assertEqual(data[:4], b"\x7fELF")
        self.assertEqual(data[4], 2)  # 64-bit
        machine = struct.unpack_from("<H", data, 18)[0]
        self.assertEqual(machine, 183)  # EM_AARCH64
        # No interpreter: stage 1 runs it without a C library.
        phoff, phentsize, phnum = struct.unpack_from("<Q", data, 32)[0], *struct.unpack_from("<HH", data, 54)
        types = [struct.unpack_from("<I", data, phoff + i * phentsize)[0] for i in range(phnum)]
        self.assertNotIn(3, types)  # PT_INTERP
        self.assertLess(len(data), 32 * 1024)

    def test_source_hands_the_display_over(self):
        source = (ROOT / "src/splash/pico4-splash.c").read_text(encoding="utf-8")
        # Master must be free for the next compositor: Monado and SteamVR's display layer
        # become master by opening the device or with SET_MASTER.
        self.assertIn("DRM_IOCTL_DROP_MASTER", source)
        # The stock six-layer arrangement, as Monado's KMS target and the display layer use.
        for color in ('"blue", "blue", "red", "red", "green", "green"', "4320x2160x90x"):
            self.assertIn(color, source)
        self.assertIn('quit("a compositor took the display", 0)', source)

    def test_stage_one_starts_it_for_the_installed_system_only(self):
        init = (ROOT / "initramfs/init").read_text(encoding="utf-8")
        branch = init[init.index('if [ "$(poc_arg root "")" = installed ]'):]
        branch = branch[:branch.index("\nfi\n")]
        self.assertRegex(branch, r"(?m)^\tpico4-splash \d+ [^\n]*&$")
        self.assertEqual(init.count("pico4-splash "), 1)
        image = (ROOT / "pico4/image.py").read_text(encoding="utf-8")
        self.assertIn('("pico4-reboot", "pico4-splash")', image)

    def test_stage_two_unit(self):
        unit = (ROOTFS / "usr/lib/systemd/system/pico4-splash.service").read_text(encoding="utf-8")
        self.assertIn("ConditionKernelCommandLine=pico4poc.root=installed", unit)
        self.assertIn("DefaultDependencies=no", unit)
        self.assertIn("Conflicts=shutdown.target", unit)
        self.assertRegex(unit, r"(?m)^ExecStart=/usr/bin/pico4-splash \d+$")
        self.assertIn("WantedBy=sysinit.target", unit)
        build = (ROOT / "scripts/build-userspace.sh").read_text(encoding="utf-8")
        self.assertRegex(build, r"systemctl enable [^\n]*(\\\n[^\n]*)*pico4-splash\.service")
        self.assertIn("for f in pico4-reboot pico4-splash; do", build)

    def test_session_without_a_compositor_ends_it(self):
        session = (ROOTFS / "usr/lib/systemd/system/pico4-session.service").read_text(encoding="utf-8")
        self.assertIn("ExecStartPost=-+/usr/libexec/pico4-poc/splash-done", session)
        done = (ROOTFS / "usr/libexec/pico4-poc/splash-done").read_text(encoding="utf-8")
        # Valve's session keeps it until SteamVR's compositor takes the display.
        keep = re.search(r"(?m)^(gamescope-session\.target[^)]*)\) ;;$", done)
        self.assertIsNotNone(keep)
        self.assertIn("plasma-session.target", keep.group(1))
        self.assertIn("systemctl stop --no-block pico4-splash.service", done)


if __name__ == "__main__":
    unittest.main()
