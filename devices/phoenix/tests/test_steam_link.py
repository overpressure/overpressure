import json
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
        self.assertIn("Requires=pico4-venus.service\n", setup)
        self.assertIn("After=pico4-venus.service pico4-venus-probe.service\n", setup)
        self.assertIn("pico4-venus.service pico4-venus-probe.service steam-video-codec-setup.service", self.build)
        mask = re.search(r"in_t systemctl mask sddm\.service.*?\n(?!\s)", self.build, re.S).group(0)
        self.assertNotIn("steam-video-codec-setup", mask)

    def test_mainline_probes_venus_once_its_firmware_is_there(self):
        unit = read(UNITS / "pico4-venus-probe.service")
        self.assertIn("ConditionPathExists=/sys/bus/platform/drivers/qcom-venus\n", unit)
        self.assertIn("ConditionPathExists=/usr/lib/firmware/venus.mdt\n", unit)
        script = read(ROOTFS / "usr/libexec/pico4-poc/venus-probe")
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
        # Only the codec's own device: the stock kernel's msm_vidc or a mainline kernel's Venus.
        self.assertIn("*qcom,vidc | *.video-codec) ;;", script)
        self.assertIn('video_decode) cards=("msm_vidc_vdec" "Qualcomm Venus video decoder") ;;', script)
        self.assertIn('video_encode) cards=("msm_vidc_venc" "Qualcomm Venus video encoder") ;;', script)

    def test_the_compatibility_layer_reaches_vrlink_only(self):
        self.assertIn('build_package pico4-vrlink "$project"/src/vrlink/*', self.build)
        self.assertIn("pico4/pico4-vrlink", read(ROOT / "userspace/packages.txt").split())
        self.assertIn("grep -qx '\\./vrlink \"\\$@\" &' \"$vrlink_run\"", self.build)
        self.assertIn("LD_PRELOAD=/usr/lib/libpico4-vrlink-compat.so${LD_PRELOAD:+:$LD_PRELOAD} ./vrlink", self.build)
        self.assertNotIn("libpico4-vrlink-compat", read(ROOTFS / "usr/lib/systemd/system/pico4-stock-preload.service"))

    def test_the_wrappers_are_exported(self):
        # Built with -fvisibility=hidden: unexported wrappers interpose nothing.
        hooks = read(ROOT / "src/vrlink/pico4-vrlink-compat.c")
        for signature in ("EXPORT int\nioctl(", "EXPORT void *\nmmap(", "EXPORT void *\nmmap64(", "EXPORT int\nclose(",
                          "EXPORT void *\ndlsym("):
            self.assertIn(signature, hooks)
        self.assertIn('#define EXPORT __attribute__((visibility("default")))', hooks)
        self.assertIn("./test_pico4_vidc_compat", read(ROOT / "userspace/pkgbuilds/pico4-vrlink/PKGBUILD"))

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
        for frame, ours in paths:
            if frame.startswith("/user/hand/left/input/thumbstick"):
                # The file names the right hand's stick; the left's is its mirror.
                frame, ours = frame.replace("left", "right"), ours.replace("left", "right")
            self.assertIn((frame, ours), remapped)

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
