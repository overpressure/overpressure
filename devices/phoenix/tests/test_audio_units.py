# SPDX-License-Identifier: Unlicense
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOTFS = ROOT / "userspace/rootfs"
UNITS = ROOTFS / "usr/lib/systemd/system"


def unit(name: str) -> str:
    return (UNITS / name).read_text(encoding="utf-8")


class AudioUnitTests(unittest.TestCase):

    def test_audio_process_is_started_as_on_pico_os(self):
        pd = unit("pico4-audiopd.service")
        self.assertIn("Requires=pico4-adsp.service", pd)
        self.assertIn("ExecStart=/usr/libexec/pico4/audiopd", pd)
        self.assertIn("Restart=always", pd)
        script = (ROOTFS / "usr/libexec/pico4/audiopd").read_text(encoding="utf-8")
        self.assertIn("node=/dev/fastrpc-adsp", script)
        self.assertIn('-f "$node" -d adsp -P audiopd -R /usr/share/qcom/pico4-audio', script)

    def test_dsp_boots_through_remoteproc(self):
        adsp = unit("pico4-adsp.service")
        self.assertIn("ConditionPathExists=/sys/class/remoteproc", adsp)
        self.assertIn("ExecStart=/usr/libexec/pico4/remoteproc-boot adsp", adsp)

    def test_target_pulls_in_all_units(self):
        target = unit("pico4-audio.target")
        for name in ("pico4-adsp.service", "pico4-audiopd.service"):
            self.assertIn(name, target)
            self.assertIn("WantedBy=pico4-audio.target", unit(name))
        build = (ROOT / "scripts/build-userspace.sh").read_text(encoding="utf-8")
        self.assertRegex(build, r"systemctl enable [^\n]*(\\\n[^\n]*)*pico4-audio\.target")

class AudioTimingTests(unittest.TestCase):
    def test_rtkit_runs_with_the_frames_limits(self):
        # Same name as the Frame's vendor drop-in, so that it replaces it (docs/userspace.md#audio).
        dropin = (ROOTFS / "etc/systemd/system/rtkit-daemon.service.d/rtkit-limits.conf").read_text(encoding="utf-8")
        self.assertIn("ExecStart=\n", dropin)
        command = dropin[dropin.rindex("ExecStart=/usr/lib/rtkit-daemon"):]
        self.assertNotIn("--no-polkit", command)
        for option in ("--max-realtime-priority=80", "--our-realtime-priority=81", "--no-chroot"):
            self.assertIn(option, command)
        packages = (ROOT / "userspace/packages.txt").read_text(encoding="utf-8").split()
        self.assertIn("rtkit", packages)

    def test_sound_card_keeps_two_periods_of_headroom(self):
        rule = (ROOTFS / "etc/wireplumber/wireplumber.conf.d/51-pico4-sound-card.conf").read_text(encoding="utf-8")
        self.assertIn('node.name = "~alsa_output.platform-sound.*"', rule)
        period = int(re.search(r"api\.alsa\.period-size = (\d+)", rule).group(1))
        headroom = int(re.search(r"api\.alsa\.headroom = (\d+)", rule).group(1))
        self.assertEqual((period, headroom), (1024, 2 * period))

    def test_the_mainline_card_keeps_its_period_as_a_batch_device(self):
        # PipeWire halves a batch device's period (docs/userspace.md#audio).
        rule = (ROOTFS / "etc/wireplumber/wireplumber.conf.d/51-pico4-sound-card.conf").read_text(encoding="utf-8")
        self.assertIn('node.name = "~alsa_output.platform-sound.*"', rule)
        self.assertIn("api.alsa.disable-batch = true", rule)

    def test_the_graph_stays_at_about_the_deep_buffer_period(self):
        # A game's 5 ms request runs the graph at 128 frames, where clients miss cycles under its load.
        conf = (ROOTFS / "etc/pipewire/pipewire.conf.d/51-pico4-quantum.conf").read_text(encoding="utf-8")
        self.assertEqual(int(re.search(r"default\.clock\.min-quantum = (\d+)", conf).group(1)), 1024)


class CaptureThroughSuspendTests(unittest.TestCase):
    RECIPE = ROOT / "userspace/pkgbuilds/pico4-spa-alsa"

    def test_the_plugin_is_built_from_the_version_valve_ships_with_the_fix(self):
        recipe = (self.RECIPE / "PKGBUILD").read_text(encoding="utf-8")
        self.assertIn("pkgver=1.4.9\n", recipe)
        self.assertIn('"pipewire-audio=1:$pkgver"', recipe)
        patch = (self.RECIPE / "early-wakeup-from-the-clock.patch").read_text(encoding="utf-8")
        self.assertIn("+\t\tstate->next_time = SPA_MAX(current_time, get_time_ns(state)) +", patch)
        self.assertIn("-\t\tstate->next_time = current_time + (state->read_size - avail)", patch)

    def test_the_build_installs_it_over_valves(self):
        packages = (ROOT / "userspace/packages.txt").read_text(encoding="utf-8")
        self.assertIn("pico4/pico4-spa-alsa", packages)
        build = (ROOT / "scripts/build-userspace.sh").read_text(encoding="utf-8")
        self.assertIn("build_package pico4-spa-alsa", build)
        self.assertIn('"$T/usr/lib/spa-0.2/alsa/libspa-alsa.so"', build)


class MainlineAudioTests(unittest.TestCase):
    def test_every_verb_sets_what_a_saved_mixer_state_would_override(self):
        # alsa-restore's saved state wins over the profile's boot sequence.
        verb = (ROOTFS / "usr/share/alsa/ucm2/conf.d/sm8250/PICO4-HiFi.conf").read_text(encoding="utf-8")
        for control in ("'SpkrLeft Smart Boost Level' 7", "'SpkrRight Smart Boost Level' 7", "'ADC1 Volume' 12"):
            self.assertIn(control, verb)

    def test_va_macro_binds_after_the_audio_process(self):
        # Its probe votes the LPASS hardware through the AFE, which refuses until audiopd runs.
        u = unit("pico4-lpass-macros.service")
        self.assertIn("ConditionPathExists=/sys/bus/platform/drivers/va_macro", u)
        self.assertRegex(u, r"(?m)^After=.*pico4-audiopd\.service")
        self.assertIn("pico4-lpass-macros.service", unit("pico4-audio.target"))
        script = (ROOTFS / "usr/libexec/pico4/lpass-macros").read_text(encoding="utf-8")
        self.assertIn("DEV=3370000.codec", script)
        self.assertIn('echo "$DEV" > /sys/bus/platform/drivers_probe', script)

    def test_calibration_binds_once_the_card_is_up(self):
        # The ADSP maps the calibration's buffer only once the audio process runs; the card shows it.
        script = (ROOTFS / "usr/libexec/pico4/lpass-macros").read_text(encoding="utf-8")
        up = script.index('log "lpass-macros: sound card up"')
        self.assertLess(up, script.index("\t\tcalibration\n", up))
        self.assertIn('echo "${dev##*/}" > /sys/bus/platform/drivers_probe', script)

    def test_ucm_profile_uses_mainline_names(self):
        d = ROOTFS / "usr/share/alsa/ucm2/conf.d/sm8250"
        card = (d / "PICO4.conf").read_text(encoding="utf-8")
        self.assertIn('File "/conf.d/sm8250/PICO4-HiFi.conf"', card)
        verb = (d / "PICO4-HiFi.conf").read_text(encoding="utf-8")
        for name in ("WSA_CODEC_DMA_RX_0 Audio Mixer MultiMedia1", "MultiMedia2 Mixer TX_CODEC_DMA_TX_3",
                     "SpkrLeft DAC Switch", "SpkrRight DAC Switch", "ADC1 Switch"):
            self.assertIn(f"name='{name}'", verb)
        # The stock kernel's names have no place in the mainline profile.
        self.assertNotIn("CDC_DMA", verb)
        self.assertNotIn("DAC_Port", verb)
        # PICO OS's levels (mixer_paths.xml).
        for line in ("cset \"name='WSA_RX0 Digital Volume' 84\"", "cset \"name='WSA_RX1 Digital Volume' 84\"",
                     "cset \"name='TX_DEC0 Volume' 84\"", "cset \"name='ADC1 Volume' 12\""):
            self.assertIn(line, card)


if __name__ == "__main__":
    unittest.main()
