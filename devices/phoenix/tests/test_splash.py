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

    def test_source_drives_a_mainline_kernels_msm_too(self):
        source = (ROOT / "src/splash/pico4-splash.c").read_text(encoding="utf-8")
        # Valve's kernel: the device is "msm", one primary plane over the whole mode.
        self.assertIn('eq(name, "msm_drm") || eq(name, "msm")', source)
        self.assertIn("find_primary();", source)
        self.assertIn("DRM_PLANE_TYPE_PRIMARY", source)

    def test_stage_one_starts_it_for_the_installed_system_only(self):
        init = (ROOT / "initramfs/init").read_text(encoding="utf-8")
        branch = init[init.index('if [ "$(poc_arg root "")" = installed ]'):]
        branch = branch[:branch.index("\nfi\n")]
        self.assertRegex(branch, r"(?m)^\tpico4-splash \d+ [^\n]*&$")
        self.assertEqual(init.count("pico4-splash "), 1)
        image = (ROOT / "tooling/image.py").read_text(encoding="utf-8")
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

    def test_compositor_ends_it(self):
        # Valve's session is the only one: SteamVR's compositor takes the display and the splash
        # exits by itself; nothing stops it earlier.
        session = (ROOTFS / "usr/lib/systemd/system/pico4-session.service").read_text(encoding="utf-8")
        self.assertNotIn("splash", session)
        self.assertFalse((ROOTFS / "usr/libexec/pico4-poc/splash-done").exists())


if __name__ == "__main__":
    unittest.main()
