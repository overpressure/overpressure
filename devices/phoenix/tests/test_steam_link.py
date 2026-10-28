import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOTFS = ROOT / "userspace/rootfs"
UNITS = ROOTFS / "usr/lib/systemd/system"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class SteamLinkTests(unittest.TestCase):
    """an issue: Steam Link's client (vrlink) on the PICO 4's video decoder (Venus, msm_vidc)."""

    def setUp(self):
        self.build = read(ROOT / "scripts/build-userspace.sh")

    def test_venus_firmware_from_the_modem_partition(self):
        extract = read(ROOT / "scripts/extract-graphics-firmware.sh")
        self.assertIn("mcopy -n -i \"$stock/modem.bin\" '::/image/venus.*' \"$output/\"", extract)
        self.assertIn('install -m 0644 "$firmware"/venus.* "$T/usr/lib/firmware/"', self.build)

    def test_venus_restart_level_before_anything_opens_the_codec(self):
        # The kernel default SYSTEM reboots the headset on a codec crash.
        level = read(ROOTFS / "usr/libexec/pico4-poc/venus-level")
        self.assertIn('[ "$(cat "$d/name" 2>/dev/null)" = venus ] || continue', level)
        self.assertIn('write RELATED "$d/restart_level"', level)
        unit = read(UNITS / "pico4-venus.service")
        self.assertIn("DefaultDependencies=no\n", unit)
        self.assertIn("Before=sysinit.target\n", unit)
        setup = read(UNITS / "steam-video-codec-setup.service.d/pico4-venus.conf")
        self.assertIn("Requires=pico4-venus.service\nAfter=pico4-venus.service\n", setup)
        self.assertIn("pico4-venus.service steam-video-codec-setup.service", self.build)
        mask = re.search(r"in_t systemctl mask sddm\.service.*?\n(?!\s)", self.build, re.S).group(0)
        self.assertNotIn("steam-video-codec-setup", mask)

    def test_vrdevice_path_never_opens_the_cameras(self):
        # The Frame's lookup (v4l2-ctl --list-devices) opens every node and broke head tracking.
        script = read(ROOTFS / "usr/local/bin/vrdevice_path")
        commands = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("--list-devices", commands)
        self.assertIn('[[ "$(readlink -f "$node/device")" == *qcom,vidc ]] || continue', script)
        self.assertIn("video_decode) card=msm_vidc_vdec ;;", script)
        self.assertIn("video_encode) card=msm_vidc_venc ;;", script)

    def test_the_compatibility_layer_reaches_vrlink_only(self):
        self.assertIn('build_package pico4-vrlink "$project"/src/vrlink/*', self.build)
        self.assertIn("pico4/pico4-vrlink", read(ROOT / "userspace/packages.txt").split())
        self.assertIn("grep -qx '\\./vrlink \"\\$@\" &' \"$vrlink_run\"", self.build)
        self.assertIn("LD_PRELOAD=/usr/lib/libpico4-vrlink-compat.so${LD_PRELOAD:+:$LD_PRELOAD} ./vrlink", self.build)
        self.assertNotIn("libpico4-vrlink-compat", read(ROOTFS / "etc/ld.so.preload"))

    def test_the_wrappers_are_exported(self):
        # Built with -fvisibility=hidden: unexported wrappers interpose nothing.
        hooks = read(ROOT / "src/vrlink/pico4-vrlink-compat.c")
        for signature in ("EXPORT int\nioctl(", "EXPORT void *\nmmap(", "EXPORT void *\nmmap64(", "EXPORT int\nclose(",
                          "EXPORT void *\ndlsym("):
            self.assertIn(signature, hooks)
        self.assertIn('#define EXPORT __attribute__((visibility("default")))', hooks)
        self.assertIn("./test_pico4_vidc_compat", read(ROOT / "userspace/pkgbuilds/pico4-vrlink/PKGBUILD"))

    def test_buffers_carry_their_fd_as_the_driver_wants(self):
        core = read(ROOT / "src/vrlink/pico4-vidc-compat.c")
        self.assertIn("planes[p].reserved[0] = (uint32_t)fd;", core)
        self.assertIn(".memory = V4L2_MEMORY_USERPTR", core)
        # The placeholder is flushed before it is streamed off (unflushed, the firmware faults).
        self.assertLess(core.index("unsigned returned = flush(d, false);"), core.index("placeholder stream-off"))
        # The driver accepts a flush of the bitstream alone and does nothing: only a full one works.
        self.assertIn(".flags = all ? VIDC_FLUSH_OUTPUT | VIDC_FLUSH_CAPTURE : VIDC_FLUSH_CAPTURE", core)

    def test_picture_memory_covers_the_largest_size(self):
        # After a smaller picture size the firmware keeps the larger buffers as its minimum.
        hooks = read(ROOT / "src/vrlink/pico4-vrlink-compat.c")
        self.assertIn("if (dma_buf && image && info->allocationSize < floor) {", hooks)
        self.assertIn('strcmp(name, "vkGetInstanceProcAddr") == 0', hooks)


if __name__ == "__main__":
    unittest.main()
