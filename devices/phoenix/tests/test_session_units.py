# SPDX-License-Identifier: Unlicense
"""The session's units: SteamVR's restart limit and the fallback to Monado's session."""

import configparser
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOTFS = ROOT / "userspace/rootfs"


def unit(path: str) -> configparser.ConfigParser:
    """Parse a systemd unit below the rootfs, keys in their case."""
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = lambda optionstr: optionstr  # keep the keys' case
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

    def test_steamvr_ending_by_itself_ends_the_session(self):
        # RestartMode=direct does not reach gamescope, which stays without Steam's panel.
        ended = "usr/libexec/pico4/steamvr-ended"
        self.assertEqual(self.dropin["Service"]["ExecStopPost"], f"-/{ended}")
        script = (ROOT / "userspace/rootfs" / ended).read_text(encoding="utf-8")
        self.assertIn('[ -n "$job" ] && [ "$job" != 0 ] && exit 0', script)
        self.assertIn("exec systemctl --user --no-block stop gamescope-session.target", script)

    def test_failure_ends_the_session(self):
        # Valve's session is the only one: a SteamVR that keeps failing ends the session's target
        # (gamescope would stay without SteamVR), and the session's own start limit bounds the loop.
        failed = self.dropin["Unit"]["OnFailure"]
        self.assertEqual(failed, "pico4-steamvr-failed.service")
        stop = unit(f"usr/lib/systemd/user/{failed}")["Service"]["ExecStart"]
        self.assertEqual(stop, "/usr/bin/systemctl --user stop gamescope-session.target")
        session = unit("usr/lib/systemd/system/pico4-session.service")
        burst = int(session["Unit"]["StartLimitBurst"])
        # A SteamVR crash loop: three starts about 25 s apart plus the session's restart.
        per_session = 3 * int(self.dropin["Service"]["RestartSec"]) + 5
        self.assertGreater(int(session["Unit"]["StartLimitIntervalSec"]), burst * per_session)


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
        # zink on Turnip is the GPU's OpenGL, as on the Frame.
        for name in ("gamescope-session", "steam"):
            dropin = (ROOTFS / f"usr/lib/systemd/user/{name}.service.d/50-pico4.conf").read_text(encoding="utf-8")
            self.assertRegex(dropin, r"(?m)^Environment=MESA_LOADER_DRIVER_OVERRIDE=zink$", name)

    def test_web_helper_composites_on_vulkan(self):
        # On ANGLE's OpenGL backend (zink) the client's VR overlays flicker between
        # frames while a dialog opens; Valve's unit sets STEAM_EXTRA_ARGS empty.
        dropin = (ROOTFS / "usr/lib/systemd/user/steam.service.d/50-pico4.conf").read_text(encoding="utf-8")
        self.assertRegex(dropin, r"(?m)^Environment=STEAM_EXTRA_ARGS=-cef-use-vulkan$")

    def test_games_run_on_the_fast_cores(self):
        # Valve's unit gives games cores 3-7 (0xf8, the Frame's chip); on the XR2 core 3
        # is an A55. Cores 4-7 (0xf0) are the A77s and the prime core, PICO OS's VR app mask.
        dropin = (ROOTFS / "usr/lib/systemd/user/steam.service.d/50-pico4.conf").read_text(encoding="utf-8")
        self.assertRegex(dropin, r"(?m)^Environment=STEAM_LAUNCH_WRAPPER_AFFINITY_LIST=0xf0$")
        self.assertRegex(dropin, r"(?m)^Environment=STEAM_DESKTOP_APP_AFFINITY_LIST=0xf0$")


class VsyncLeadTest(unittest.TestCase):
    def test_steamvr_runs_with_the_lead(self):
        # SteamVR starts its compositor about 4 ms before the vblank it sees.
        dropin = (ROOTFS / "usr/lib/systemd/user/steamvr.service.d/50-pico4.conf").read_text(encoding="utf-8")
        self.assertRegex(dropin, r"(?m)^Environment=PICO4_VSYNC_LEAD_US=5000$")
        # Only the layer reads the variable; it publishes the lead it applies, and
        # the driver predicts that much further from the published value.
        hmd = (ROOT / "src/monado/pico4_hmd.c").read_text(encoding="utf-8")
        self.assertNotIn('"PICO4_VSYNC_LEAD_US"', hmd)
        layer = (ROOT / "src/swapchain/pico4-swapchain-usage.c").read_text(encoding="utf-8")
        self.assertIn('"PICO4_VSYNC_LEAD_US"', layer)
        for source in (hmd, layer):
            self.assertIn('"%s/pico4-display-lead-us"', source)


class CompressedScanoutTest(unittest.TestCase):
    def test_steamvr_keeps_one_frame_ahead(self):
        # SteamVR waits for each present to be shown before it starts the next
        # frame; without a frame ahead the GPU idles a third of the time (about 58 fps).
        dropin = (ROOTFS / "usr/lib/systemd/user/steamvr.service.d/50-pico4.conf").read_text(encoding="utf-8")
        self.assertRegex(dropin, r"(?m)^Environment=PICO4_FRAMES_AHEAD=1$")
        layer = (ROOT / "src/swapchain/pico4-swapchain-usage.c").read_text(encoding="utf-8")
        self.assertIn('"PICO4_FRAMES_AHEAD"', layer)


class FrameSettingsTest(unittest.TestCase):
    def test_session_takes_the_frames_compositor_path(self):
        # The Steam Frame's compositor settings are the baseline; they live in its
        # driver's frame_hmd_additional.vrsettings, which SteamVR loads only with that driver.
        settings = json.loads((ROOT / "userspace/session/steamvr.vrsettings").read_text(encoding="utf-8"))
        steamvr = settings["steamvr"]
        for key in (
            "useFacetRenderer",
            "renderSystemLayerInDistortPass",
            "enableSimplifiedShaders",
            "enableAchromaticDistortion",
            "enableScanoutScaling",
            "enableLinuxVulkanAsync",
        ):
            self.assertIs(steamvr[key], True, key)  # noqa: FBT003  # a JSON true, not only a truthy value
        self.assertEqual(steamvr["systemLayerWidth"], 1400)
        self.assertEqual(steamvr["overlayRenderQuality_2"], 1)
        self.assertEqual(steamvr["forcedDriver"], "monado")
        self.assertIs(settings["driver_frame_hmd"]["enable"], False)  # noqa: FBT003  # a JSON false, not only falsy
        # Standby follows the wear sensor and pauses the compositor, as on the Frame.
        self.assertIs(settings["power"]["pauseCompositorOnStandby"], True)  # noqa: FBT003  # a JSON true, not only truthy
        # The dashboard's room setup in the headset needs the HMD to say it supports it,
        # and its steps past the recenter need 11004 too.
        room = (ROOT / "userspace/pkgbuilds/pico4-monado/monado-steamvr-room.patch").read_text(encoding="utf-8")
        for prop in (11003, 11004):
            self.assertIn(
                f"SetBoolProperty(m_ulPropertyContainer, static_cast<vr::ETrackedDeviceProperty>({prop}), true)", room
            )
        self.assertNotIn("inHMDRoomSetupDebug", json.dumps(settings))
        # Facet builds its mesh from the driver's exact inverse distortion.
        patch = (ROOT / "userspace/pkgbuilds/pico4-monado/monado-steamvr-pico4.patch").read_text(encoding="utf-8")
        self.assertIn("IVRDisplayComponent_Version3", patch)
        self.assertIn("pico4_hmd_compute_inverse_distortion", patch)


class KthreadPrioritiesTest(unittest.TestCase):
    def test_unit_raises_the_gpu_workqueues(self):
        service = unit("usr/lib/systemd/system/pico4-kthread-priorities.service")
        script = (ROOTFS / service["Service"]["ExecStart"].lstrip("/")).read_text(encoding="utf-8")
        self.assertIn("for wq in msm ring0 ring1 ring2 ring3", script)
        build = (ROOT / "scripts/build-userspace.sh").read_text(encoding="utf-8")
        self.assertIn("pico4-kthread-priorities.service", build)

    def test_valve_kernel_gets_the_frames_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp, "bin")
            bin_dir.mkdir()
            log = Path(tmp, "log").as_posix()
            processes = (
                "10 gpu-worker",
                "11 crtc_event:91",
                "12 atomic-worker-0",
                "13 spi0",
                "14 irq/252-soundwi",
                "15 kworker/0:1",
            )
            for name, body in (
                ("ps", "printf '%s\\n' " + " ".join(f"'{p}'" for p in processes)),
                ("chrt", f'echo "chrt $*" >> {log}'),
                # An interrupt thread refuses a new affinity.
                ("taskset", f'echo "taskset $*" >> {log}; [ "$3" != 14 ]'),
            ):
                path = bin_dir / name
                path.write_bytes(f"#!/bin/sh\n{body}\n".encode())
                path.chmod(0o755)
            workqueues = Path(tmp, "sys/bus/workqueue/devices")
            for wq in ("msm", "ring0", "ring1", "ring2", "ring3", "dsi_drm_work_0", "dsi_drm_work_1"):
                (workqueues / wq).mkdir(parents=True)
                (workqueues / wq / "prio").write_text("255\n")
            bash = shutil.which("bash")
            assert bash is not None, "needs bash"
            env = dict(
                os.environ,
                PICO4_SYSFS=Path(tmp, "sys").as_posix(),
                PATH=f"{bin_dir.as_posix()}{os.pathsep}{os.environ['PATH']}",
            )
            script = ROOTFS / "usr/libexec/pico4/kthread-priorities"
            r = subprocess.run(  # noqa: S603  # the repository's script under bash, no shell
                [bash, script.as_posix()], env=env, capture_output=True, text=True, check=False
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            calls = Path(log).read_text().splitlines()
            for line in (
                "chrt -r -p 99 10",
                "chrt -r -p 99 11",
                "chrt -r -p 88 12",
                "chrt -r -p 80 13",
                "taskset -p 0x0f 13",
                "chrt -r -p 80 14",
            ):
                self.assertIn(line, calls)
            self.assertFalse([c for c in calls if c.endswith(" 15")])
            self.assertEqual((workqueues / "ring0/prio").read_text().strip(), "90")
            self.assertEqual((workqueues / "dsi_drm_work_1/prio").read_text().strip(), "90")


class SoundCardFormatTest(unittest.TestCase):
    def test_the_card_picks_its_own_format(self):
        # The kernel offers the DSP's 24-bit PCM as S32_LE (patch 0023), which PipeWire picks by itself:
        # at 16 bits the tails of sounds are crunchy.
        conf = (ROOTFS / "etc/wireplumber/wireplumber.conf.d/51-pico4-sound-card.conf").read_text(encoding="utf-8")
        self.assertNotIn("audio.format", conf)
        self.assertIn("alsa_output.platform-sound", conf)


class CompositorPacingTest(unittest.TestCase):
    LAYER = ROOT / "src/swapchain/pico4-swapchain-usage.c"

    def test_the_session_asks_for_stocks_pacing(self):
        unit_text = (ROOTFS / "usr/lib/systemd/user/steamvr.service.d/50-pico4.conf").read_text(encoding="utf-8")
        self.assertIn("Environment=PICO4_FRAMES_AHEAD=1\n", unit_text)
        self.assertIn("Environment=PICO4_VSYNC_LEAD_US=5000\n", unit_text)

    def test_the_layer_gives_it_the_images_it_needs(self):
        # With the two images SteamVR asks for, its acquire waits for a flip whatever the rest does.
        layer = self.LAYER.read_text(encoding="utf-8")
        self.assertIn("frames_ahead() > 0 && info.minImageCount < 4", layer)

    def test_a_present_counts_as_shown_with_the_one_before_it(self):
        layer = self.LAYER.read_text(encoding="utf-8")
        self.assertIn("next(device, swapchain, present_id - ahead, 0)", layer)
        self.assertIn("earlier.presentId = info->presentId - ahead;", layer)

    def test_a_queue_drains_and_a_late_job_keeps_its_slot(self):
        layer = self.LAYER.read_text(encoding="utf-8")
        self.assertIn("passed = blocked && value[0] > read_count ? value[0] : read_count;", layer)

    def test_the_driver_learns_the_lead(self):
        layer = self.LAYER.read_text(encoding="utf-8")
        self.assertIn('"%s/pico4-display-lead-us"', layer)
        self.assertIn('"%s/pico4-display-lead-us"', (ROOT / "src/monado/pico4_hmd.c").read_text(encoding="utf-8"))


class StoragePolicyRuleTest(unittest.TestCase):
    def test_only_block_devices_reach_the_helper(self):
        # The kernel's devtmpfs makes the nodes; the helper keeps the read-only policy.
        rules = (ROOTFS / "usr/lib/udev/rules.d/00-pico4-devnodes.rules").read_text(encoding="utf-8")
        self.assertLess(
            rules.index('SUBSYSTEM!="block", GOTO="pico4_devnodes_end"'),
            rules.index('RUN+="/usr/libexec/pico4/devnode remove"'),
        )
        self.assertIn('PROGRAM="/usr/libexec/pico4/devnode add"', rules)
        helper = (ROOTFS / "usr/libexec/pico4/devnode").read_text(encoding="utf-8")
        self.assertNotIn("mknod", helper)
        # Every device that needs it in one blockdev call: the sweep runs before the file systems.
        self.assertIn('blockdev --setro "${to_ro[@]}"', helper)
        self.assertNotIn("$(sed ", helper)


class ConsoleQuietTest(unittest.TestCase):
    def test_only_critical_messages_on_the_headset_console(self):
        # journald forwards errors to the kernel log; printed on the UART console with
        # interrupts off they delayed the display interrupt.
        journald = (ROOTFS / "usr/lib/systemd/journald.conf.d/50-pico4.conf").read_text(encoding="utf-8")
        match = re.search(
            r"(?m)^kernel\.printk = (\d)",
            (ROOTFS / "etc/sysctl.d/50-pico4-console.conf").read_text(encoding="utf-8"),
        )
        assert match is not None
        level = int(match.group(1))
        self.assertIn("MaxLevelKMsg=warning", journald)  # warning is 4: not printed below that level
        self.assertLess(level, 4)


class MonadoServiceTest(unittest.TestCase):
    def test_monado_takes_vulkans_display_on_msm(self):
        # Turnip's display WSI hands an image back only once the next flip completed: with
        # Monado's default of two images every second refresh was missed (45 Hz).
        unit = (ROOTFS / "usr/lib/systemd/system/pico4-monado.service").read_text(encoding="utf-8")
        self.assertIn("ExecStart=/usr/libexec/pico4/monado-service", unit)
        self.assertIn("ConditionPathExists=/dev/dri/renderD128\n", unit)
        script = (ROOTFS / "usr/libexec/pico4/monado-service").read_text(encoding="utf-8")
        for setting in (
            "XRT_COMPOSITOR_FORCE_VK_DISPLAY=0",
            "XRT_COMPOSITOR_PREFERRED_IMAGE_COUNT=3",
            "XRT_COMPOSITOR_COMPUTE=0",
        ):
            self.assertIn(setting, script)
        self.assertIn('exec /usr/bin/monado-service "$@"', script)


class PanelProbeTest(unittest.TestCase):
    def test_msm_probes_the_panels_before_the_session(self):
        # Without the probe msm's DSI connector has no modes and SteamVR finds no display.
        probe = unit("usr/lib/systemd/system/pico4-panel-probe.service")
        self.assertIn("pico4-session.service", probe["Unit"]["Before"])
        self.assertIn('echo detect > "$c"', probe["Service"]["ExecStart"])
        build = (ROOT / "scripts/build-userspace.sh").read_text(encoding="utf-8")
        self.assertIn("pico4-panel-probe.service", build[build.index("in_t systemctl enable") :])


class SessionTest(unittest.TestCase):
    def test_valves_session_only(self):
        # The session is Valve's target, or another of Valve's that session.conf names.
        script = (ROOTFS / "usr/libexec/pico4/session").read_text(encoding="utf-8")
        self.assertIn("target=${PICO4_SESSION_TARGET:-gamescope-session.target}", script)
        self.assertIn('exec /usr/bin/start-gamescope-session "$target"', script)

    def test_session_starts_again_after_it_ends(self):
        # A compositor that dies in a running session makes vrserver quit everything,
        # gamescope with it, and start-gamescope-session returns 0; sddm's Relogin=true starts
        # the session again on the Frame.
        session = unit("usr/lib/systemd/system/pico4-session.service")
        self.assertEqual(session["Service"]["Restart"], "always")
        self.assertEqual(session["Service"]["RestartMode"], "direct")
        restart = int(session["Service"]["RestartSec"])
        self.assertLessEqual(restart, 10)
        # Bounded: a session that keeps ending stays stopped after its start limit.
        interval = int(session["Unit"]["StartLimitIntervalSec"])
        burst = int(session["Unit"]["StartLimitBurst"])
        self.assertGreaterEqual(interval, burst * (restart + 30))
        self.assertNotIn("OnFailure", session["Unit"])
        # Valve's session needs the GPU, and no longer the Monado service.
        text = (ROOT / "userspace/rootfs/usr/lib/systemd/system/pico4-session.service").read_text(encoding="utf-8")
        self.assertIn("\nConditionPathExists=/dev/dri/renderD128\n", text)
        self.assertNotIn("pico4-monado.socket", session["Unit"].get("Wants", ""))

    def test_deliberate_stop_stays_stopped(self):
        # Valve's units take up to 10 s (gamescope) and 20 s (SteamVR) to stop.
        session = unit("usr/lib/systemd/system/pico4-session.service")
        self.assertGreaterEqual(int(session["Service"]["TimeoutStopSec"]), 30)

    def test_deliberate_restarts_reset_the_count(self):
        check = (ROOTFS / "usr/bin/pico4-valve-session-check").read_text(encoding="utf-8")
        restarts = [m.start() for m in re.finditer(r"(?m)^systemctl restart pico4-session.service$", check)]
        self.assertEqual(len(restarts), 2)
        for at in restarts:
            self.assertTrue(check[:at].rstrip("\n").endswith("systemctl reset-failed pico4-session.service"))

    def test_new_session_resets_the_start_limit(self):
        # The user manager outlives a session: without a reset, sessions started one after
        # another count against SteamVR's crash-loop limit.
        script = (ROOTFS / "usr/libexec/pico4/session").read_text(encoding="utf-8")
        self.assertLess(
            script.index("systemctl --user reset-failed"), script.index("exec /usr/bin/start-gamescope-session")
        )

    def test_check_expects_valves_session(self):
        check = (ROOTFS / "usr/bin/pico4-userspace-check").read_text(encoding="utf-8")
        self.assertIn("session_target=gamescope-session.target", check)


if __name__ == "__main__":
    unittest.main()
