"""The session's units: SteamVR's restart limit and the fallback to Monado's session."""
import configparser
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOTFS = ROOT / "userspace/rootfs"


def unit(path):
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read_string((ROOTFS / path).read_text(encoding="utf-8"))
    return parser


class SteamvrRestartTest(unittest.TestCase):
    def setUp(self):
        self.dropin = unit("etc/systemd/user/steamvr.service.d/50-pico4-restart-limit.conf")

    def test_restarts_are_spaced(self):
        # A start takes about 15 s; with the wait the radio resets at most every 30 s or so.
        self.assertGreaterEqual(int(self.dropin["Service"]["RestartSec"]), 15)
        # Restarts must not pass through the failed state, or OnFailure= runs at the first crash.
        self.assertEqual(self.dropin["Service"]["RestartMode"], "direct")

    def test_start_limit_trips(self):
        interval = int(self.dropin["Unit"]["StartLimitIntervalSec"])
        burst = int(self.dropin["Unit"]["StartLimitBurst"])
        restart = int(self.dropin["Service"]["RestartSec"])
        # The bursts of a crash loop (restart wait plus a start of up to 20 s) fit the interval.
        self.assertGreaterEqual(interval, burst * (restart + 20))
        self.assertLessEqual(burst, 3)

    def test_failure_reaches_the_fallback(self):
        failed = self.dropin["Unit"]["OnFailure"]
        self.assertEqual(failed, "pico4-steamvr-failed.service")
        report = unit(f"usr/lib/systemd/user/{failed}")["Service"]["ExecStart"]
        flag = report.split()[-1]
        path = unit("usr/lib/systemd/system/pico4-session-fallback.path")["Path"]
        self.assertEqual(path["PathExists"], flag)
        self.assertEqual(path["Unit"], "pico4-session-fallback.service")
        # The session user may create the flag and remove the override.
        tmpfiles = (ROOTFS / "usr/lib/tmpfiles.d/pico4-session.conf").read_text(encoding="utf-8")
        self.assertRegex(tmpfiles, r"(?m)^d /run/pico4-session 0775 root steamos")
        self.assertTrue(flag.startswith("/run/pico4-session/"))


class SteamvrStopTest(unittest.TestCase):
    def test_failed_shutdown_request_is_not_a_failure(self):
        # A stop whose shutdown request finds vrserver gone must not count as a failed launch:
        # Valve's health check resets the Steam installation after three.
        dropin = (ROOTFS / "usr/lib/systemd/user/steamvr.service.d/50-pico4.conf").read_text(encoding="utf-8")
        stops = re.findall(r"(?m)^ExecStop=(.*)$", dropin)
        self.assertEqual(stops[0], "")  # Valve's own ExecStop is replaced, not added to
        self.assertEqual(stops[1:], ["-/usr/share/deckard/select_steamvr.sh bin/linuxarm64/vrstartup -shutdown"])


class OpenGLTest(unittest.TestCase):
    def test_gl_clients_use_zink(self):
        # an issue: Mesa's loader would pick msm for msm_drm's render node and fall back to
        # llvmpipe; zink on Turnip is the GPU's OpenGL.
        for name in ("gamescope-session", "steam"):
            dropin = (ROOTFS / f"usr/lib/systemd/user/{name}.service.d/50-pico4.conf").read_text(encoding="utf-8")
            self.assertRegex(dropin, r"(?m)^Environment=MESA_LOADER_DRIVER_OVERRIDE=zink$", name)
            # Zink finds the GPU for the render node through the layer's DRM nodes.
            self.assertRegex(dropin, r"(?m)^Environment=PICO4_VRDISPLAY=1$", name)

    def test_web_helper_composites_on_vulkan(self):
        # an issue: on ANGLE's OpenGL backend (zink) the client's VR overlays flicker between
        # frames while a dialog opens; Valve's unit sets STEAM_EXTRA_ARGS empty.
        dropin = (ROOTFS / "usr/lib/systemd/user/steam.service.d/50-pico4.conf").read_text(encoding="utf-8")
        self.assertRegex(dropin, r"(?m)^Environment=STEAM_EXTRA_ARGS=-cef-use-vulkan$")

    def test_games_get_the_semaphore_library(self):
        # an issue: the Steam API in a game takes System V semaphores; Steam Linux Runtime's
        # container ignores the host's /etc/ld.so.preload but imports LD_PRELOAD.
        preload = (ROOTFS / "etc/ld.so.preload").read_text(encoding="utf-8").split()
        dropin = (ROOTFS / "usr/lib/systemd/user/steam.service.d/50-pico4.conf").read_text(encoding="utf-8")
        self.assertRegex(dropin, r"(?m)^Environment=LD_PRELOAD=/usr/lib/libpico4-sysvsem\.so$")
        self.assertIn("/usr/lib/libpico4-sysvsem.so", preload)

    def test_turnip_keeps_the_drm_wsi(self):
        # A KGSL-only Turnip builds without libdrm, and its swapchains read DRM image
        # parameters as CPU ones; KGSL must still accept instances with VK_KHR_display.
        recipe = (ROOT / "userspace/pkgbuilds/pico4-turnip-kgsl/PKGBUILD").read_text(encoding="utf-8")
        self.assertIn("-Dfreedreno-kmds=kgsl,msm", recipe)
        self.assertIn("kgsl-display.patch", recipe)
        patch = (ROOT / "userspace/pkgbuilds/pico4-turnip-kgsl/kgsl-display.patch").read_text(encoding="utf-8")
        self.assertIn("-                       \"I can't KHR_display\");", patch)


class VsyncLeadTest(unittest.TestCase):
    def test_steamvr_runs_with_the_lead(self):
        # SteamVR starts its compositor about 4 ms before the vblank it sees.
        dropin = (ROOTFS / "usr/lib/systemd/user/steamvr.service.d/50-pico4.conf").read_text(encoding="utf-8")
        self.assertRegex(dropin, r"(?m)^Environment=PICO4_VRDISPLAY_VSYNC_LEAD_US=5000$")
        # Only the layer reads the variable; it publishes the lead it applies, and
        # the driver predicts that much further from the published value.
        hmd = (ROOT / "src/monado/pico4_hmd.c").read_text(encoding="utf-8")
        self.assertNotIn('"PICO4_VRDISPLAY_VSYNC_LEAD_US"', hmd)
        layer = (ROOT / "src/vrdisplay/pico4-vrdisplay.c").read_text(encoding="utf-8")
        self.assertIn('"PICO4_VRDISPLAY_VSYNC_LEAD_US"', layer)
        for source in (hmd, layer):
            self.assertIn('"%s/pico4-vrdisplay-lead-us"', source)


class CompressedScanoutTest(unittest.TestCase):
    def test_steamvr_scans_out_compressed(self):
        # an issue: linear scanout images doubled the lens-distortion pass's GPU time.
        dropin = (ROOTFS / "usr/lib/systemd/user/steamvr.service.d/50-pico4.conf").read_text(encoding="utf-8")
        self.assertRegex(dropin, r"(?m)^Environment=PICO4_VRDISPLAY_UBWC=2$")
        layer = (ROOT / "src/vrdisplay/pico4-vrdisplay.c").read_text(encoding="utf-8")
        self.assertIn('"PICO4_VRDISPLAY_UBWC"', layer)
        # The planes take the compressed modifier for XBGR8888 only; SteamVR renders BGRA:
        # mode 2 gives its views and presenting render passes the RGBA format instead.
        self.assertIn("DRM_FORMAT_MOD_QCOM_COMPRESSED", layer)
        for entry in ("vkCreateImageView", "vkCreateRenderPass", "vkCreateRenderPass2"):
            self.assertIn(f'"{entry}"', layer)
        self.assertIn("VK_IMAGE_LAYOUT_PRESENT_SRC_KHR", layer)

    def test_steamvr_keeps_one_frame_ahead(self):
        # an issue: SteamVR waits for each present to be shown before it starts the next
        # frame; without a frame ahead the GPU idles a third of the time (about 58 fps).
        dropin = (ROOTFS / "usr/lib/systemd/user/steamvr.service.d/50-pico4.conf").read_text(encoding="utf-8")
        self.assertRegex(dropin, r"(?m)^Environment=PICO4_VRDISPLAY_FRAMES_AHEAD=1$")
        layer = (ROOT / "src/vrdisplay/pico4-vrdisplay.c").read_text(encoding="utf-8")
        self.assertIn('"PICO4_VRDISPLAY_FRAMES_AHEAD"', layer)

    def test_steamvr_runs_with_render_pass_barriers(self):
        # an issue: without them chunks of the overlay are missing in the right eye.
        dropin = (ROOTFS / "usr/lib/systemd/user/steamvr.service.d/50-pico4.conf").read_text(encoding="utf-8")
        self.assertRegex(dropin, r"(?m)^Environment=PICO4_VRDISPLAY_PASS_BARRIER=1$")
        layer = (ROOT / "src/vrdisplay/pico4-vrdisplay.c").read_text(encoding="utf-8")
        self.assertIn('"PICO4_VRDISPLAY_PASS_BARRIER"', layer)
        for entry in ("vkCmdBeginRenderPass", "vkCmdBeginRenderPass2", "vkCmdBeginRendering"):
            self.assertIn(f'"{entry}"', layer)


class FrameSettingsTest(unittest.TestCase):
    def test_session_takes_the_frames_compositor_path(self):
        # an issue: the Steam Frame's compositor settings are the baseline; they live in its
        # driver's frame_hmd_additional.vrsettings, which SteamVR loads only with that driver.
        settings = json.loads((ROOT / "userspace/session/steamvr.vrsettings").read_text(encoding="utf-8"))
        steamvr = settings["steamvr"]
        for key in ("useFacetRenderer", "renderSystemLayerInDistortPass", "enableSimplifiedShaders",
                    "enableAchromaticDistortion", "enableScanoutScaling", "enableLinuxVulkanAsync"):
            self.assertIs(steamvr[key], True, key)
        self.assertEqual(steamvr["systemLayerWidth"], 1400)
        self.assertEqual(steamvr["overlayRenderQuality_2"], 1)
        self.assertEqual(steamvr["forcedDriver"], "monado")
        self.assertIs(settings["driver_frame_hmd"]["enable"], False)
        # Facet builds its mesh from the driver's exact inverse distortion.
        patch = (ROOT / "userspace/pkgbuilds/pico4-monado/monado-steamvr-pico4.patch").read_text(encoding="utf-8")
        self.assertIn("IVRDisplayComponent_Version3", patch)
        self.assertIn("pico4_hmd_compute_inverse_distortion", patch)


class KthreadPrioritiesTest(unittest.TestCase):
    def test_unit_raises_gpu_and_display_threads(self):
        service = unit("usr/lib/systemd/system/pico4-kthread-priorities.service")
        script = (ROOTFS / service["Service"]["ExecStart"].lstrip("/")).read_text(encoding="utf-8")
        for name in ("kgsl_worker_thr", "crtc_commit:*", "crtc_event:*"):
            self.assertIn(name, script)
        self.assertIn('chrt -r -p "$prio"', script)
        # Real-time threads (Monado's compositor) need the cpu controller off.
        self.assertNotIn("CPUWeight", service["Service"])
        self.assertNotIn("CPUQuota", service["Service"])
        build = (ROOT / "scripts/build-userspace.sh").read_text(encoding="utf-8")
        self.assertIn("pico4-kthread-priorities.service", build)


class ConsoleQuietTest(unittest.TestCase):
    def test_only_critical_messages_on_the_headset_console(self):
        # journald forwards errors to the kernel log; printed on the UART console with
        # interrupts off they delayed the display interrupt.
        service = unit("usr/lib/systemd/system/pico4-console-quiet.service")
        self.assertEqual(service["Unit"]["ConditionPathExists"], "/sys/class/tty/ttyMSM0")
        self.assertIn("echo 3 4 1 7 > /proc/sys/kernel/printk", service["Service"]["ExecStart"])
        self.assertIn("systemd-sysctl.service", service["Unit"]["After"])
        journald = (ROOTFS / "usr/lib/systemd/journald.conf.d/50-pico4.conf").read_text(encoding="utf-8")
        level = int(re.search(r"(?m)^kernel\.printk = (\d)", (ROOTFS / "etc/sysctl.d/50-pico4-console.conf").read_text(encoding="utf-8")).group(1))
        self.assertIn("MaxLevelKMsg=warning", journald)  # warning is 4: printed at the sysctl's level ...
        self.assertGreater(level, 4)
        build = (ROOT / "scripts/build-userspace.sh").read_text(encoding="utf-8")
        self.assertIn("pico4-console-quiet.service", build)  # ... so the unit must be enabled


class FallbackTest(unittest.TestCase):
    def test_fallback_overrides_once(self):
        service = (ROOTFS / "usr/lib/systemd/system/pico4-session-fallback.service").read_text(encoding="utf-8")
        self.assertIn("PICO4_SESSION_TARGET=pico4-session.target > /run/pico4-session/override", service)
        self.assertIn("rm -f /run/pico4-session/steamvr-failed", service)
        self.assertIn("systemctl restart pico4-session.service", service)
        session = (ROOTFS / "usr/lib/systemd/system/pico4-session.service").read_text(encoding="utf-8")
        # The override is read after session.conf, so it wins.
        self.assertLess(session.index("EnvironmentFile=-/etc/pico4-poc/session.conf"),
                        session.index("EnvironmentFile=-/run/pico4-session/override"))
        script = (ROOTFS / "usr/libexec/pico4-poc/session").read_text(encoding="utf-8")
        self.assertIn("rm -f /run/pico4-session/override", script)

    def test_new_session_resets_the_start_limit(self):
        # The user manager outlives a session: without a reset, sessions started one after
        # another count against SteamVR's crash-loop limit and end in the fallback.
        script = (ROOTFS / "usr/libexec/pico4-poc/session").read_text(encoding="utf-8")
        reset = script.index("systemctl --user reset-failed")
        self.assertLess(reset, script.index("exec /usr/bin/start-gamescope-session"))
        self.assertLess(reset, script.index('systemctl --user --wait start "$target"'))

    def test_check_expects_the_socket_link_only_outside_valves_session(self):
        # Valve's session removes the link (SteamVR owns the hardware); the check follows it.
        script = (ROOTFS / "usr/libexec/pico4-poc/session").read_text(encoding="utf-8")
        self.assertLess(script.index("gamescope-session.target | plasma-session.target)"),
                        script.index('rm -f "$runtime/monado_comp_ipc"'))
        check = (ROOTFS / "usr/bin/pico4-userspace-check").read_text(encoding="utf-8")
        valve = check.index("gamescope-session.target | plasma-session.target)")
        self.assertLess(valve, check.index("Monado socket not linked into Valve's session"))
        self.assertLess(check.index("Monado socket not linked into Valve's session"),
                        check.index('*) need "Monado socket linked into the session"'))

    def test_path_unit_enabled(self):
        build = (ROOT / "scripts/build-userspace.sh").read_text(encoding="utf-8")
        enable = re.search(r"in_t systemctl enable (.*?)\n(?!\s)", build, re.S).group(1)
        self.assertIn("pico4-session-fallback.path", enable)


if __name__ == "__main__":
    unittest.main()
