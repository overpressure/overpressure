import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOTFS = ROOT / "userspace/rootfs"
UNITS = ROOTFS / "usr/lib/systemd/system"
UCM = ROOTFS / "usr/share/alsa/ucm2/conf.d/kona-mtp-snd-ca"


def unit(name: str) -> str:
    return (UNITS / name).read_text(encoding="utf-8")


class AudioUnitTests(unittest.TestCase):
    def test_modules_load_before_the_protection_domain_mapper(self):
        # The audio notifier misses the service locator coming up otherwise (docs/userspace.md#audio).
        modules = unit("pico4-audio-modules.service")
        self.assertIn("Before=pico4-pd-mapper.service", modules)
        self.assertIn("DefaultDependencies=no", modules)
        self.assertIn("ExecStart=/usr/libexec/pico4-poc/audio modules", modules)

    def test_dsp_boots_after_the_modules_and_the_mapper(self):
        adsp = unit("pico4-adsp.service")
        self.assertIn("Requires=pico4-audio-modules.service", adsp)
        for dep in ("pico4-audio-modules.service", "pico4-qrtr-ns.service", "pico4-pd-mapper.service"):
            self.assertRegex(adsp, rf"(?m)^After=.*{re.escape(dep)}")
        self.assertIn("ExecStart=/usr/libexec/pico4-poc/audio boot", adsp)

    def test_audio_process_is_started_as_on_pico_os(self):
        pd = unit("pico4-audiopd.service")
        self.assertIn("Requires=pico4-adsp.service", pd)
        self.assertIn("ExecStart=/usr/libexec/pico4-poc/audiopd", pd)
        self.assertIn("Restart=always", pd)
        script = (ROOTFS / "usr/libexec/pico4-poc/audiopd").read_text(encoding="utf-8")
        # The stock kernel's secure channel (downstream ABI), or a mainline kernel's node.
        self.assertIn("node=/dev/adsprpc-smd-secure", script)
        self.assertIn("node=/dev/fastrpc-adsp", script)
        self.assertIn('-f "$node" -d adsp -P audiopd -R /usr/share/qcom/pico4-audio', script)

    def test_dsp_boots_on_either_kernel(self):
        adsp = unit("pico4-adsp.service")
        self.assertIn("ConditionPathExists=|/sys/kernel/boot_adsp", adsp)
        self.assertIn("ConditionPathExists=|/sys/class/remoteproc", adsp)
        script = (ROOTFS / "usr/libexec/pico4-poc/audio").read_text(encoding="utf-8")
        self.assertIn("[ -e /sys/kernel/boot_adsp ] || exec /usr/libexec/pico4-poc/remoteproc-boot adsp", script)

    def test_target_pulls_in_all_units(self):
        target = unit("pico4-audio.target")
        for name in ("pico4-audio-modules.service", "pico4-adsp.service", "pico4-audiopd.service",
                     "pico4-audiocal.service"):
            self.assertIn(name, target)
            self.assertIn("WantedBy=pico4-audio.target", unit(name))
        build = (ROOT / "scripts/build-userspace.sh").read_text(encoding="utf-8")
        self.assertRegex(build, r"systemctl enable [^\n]*(\\\n[^\n]*)*pico4-audio\.target")

    def test_calibration_follows_the_audio_process_and_stays(self):
        cal = unit("pico4-audiocal.service")
        self.assertIn("After=pico4-audiopd.service", cal)
        # Sent again after the ADSP restarts; the kernel drops the blocks when the device closes.
        self.assertIn("PartOf=pico4-adsp.service", cal)
        self.assertIn("ExecStart=/usr/bin/pico4-audiocal run", cal)
        self.assertNotIn("--once", cal)

    def test_no_cpu_controller_settings(self):
        for name in ("pico4-audio-modules.service", "pico4-adsp.service", "pico4-audiopd.service",
                     "pico4-audiocal.service"):
            self.assertNotIn("CPUWeight", unit(name))
            self.assertNotIn("CPUQuota", unit(name))

    def test_script_sets_the_restart_level_before_booting(self):
        script = (ROOTFS / "usr/libexec/pico4-poc/audio").read_text(encoding="utf-8")
        boot = script[script.index("\nboot)"):script.index("\n*)")]
        self.assertLess(boot.index("RELATED"), boot.index("boot_adsp/boot"))
        self.assertNotIn("rmmod", script)

    def test_script_loads_pico_os_list_with_dependencies_first(self):
        script = (ROOTFS / "usr/libexec/pico4-poc/audio").read_text(encoding="utf-8")
        listed = script[script.index('LIST="') + 6:].split('"', 1)[0].split()
        self.assertEqual(listed[0], "audio_q6_pdr")
        self.assertEqual(listed[-1], "audio_machine_kona")
        self.assertEqual(len(listed), 24)
        extract = (ROOT / "scripts/extract-audio.sh").read_text(encoding="utf-8")
        self.assertEqual(extract[extract.index('wanted = """') + 12:].split('"""', 1)[0].split(), listed)
        # Dependencies before the module that needs them (modules.dep lists the deepest last).
        load = script[script.index("load() {"):script.index("subsystem()")]
        self.assertLess(load.index("tac"), load.index("insmod"))


class AudioNodeTests(unittest.TestCase):
    def test_only_multimedia1_gets_device_nodes(self):
        # Opening other PCMs of this card can crash the stock kernel (docs/userspace.md#audio).
        rules = (ROOTFS / "usr/lib/udev/rules.d/00-pico4-devnodes.rules").read_text(encoding="utf-8")
        skip = next(l for l in rules.splitlines() if "q6core-audio:sound" in l and not l.startswith("#"))
        self.assertIn('KERNEL!="pcmC*D0[pc]"', skip)
        self.assertIn('GOTO="pico4_devnodes_end"', skip)
        self.assertLess(rules.index(skip), rules.index('PROGRAM="/usr/libexec/pico4-poc/devnode add"'))


class AudioUcmTests(unittest.TestCase):
    def test_card_file_names_the_verb(self):
        card = (UCM / "kona-mtp-snd-ca.conf").read_text(encoding="utf-8")
        self.assertIn('File "/conf.d/kona-mtp-snd-ca/HiFi.conf"', card)
        self.assertTrue((UCM / "HiFi.conf").exists())

    def test_devices_use_multimedia1_both_ways(self):
        hifi = (UCM / "HiFi.conf").read_text(encoding="utf-8")
        self.assertIn('PlaybackPCM "hw:${CardId},0"', hifi)
        self.assertIn('CapturePCM "hw:${CardId},0"', hifi)
        self.assertIn("'WSA_CDC_DMA_RX_0 Audio Mixer MultiMedia1' 1", hifi)
        self.assertIn("'MultiMedia1 Mixer TX_CDC_DMA_TX_3' 1", hifi)

    def test_every_enabled_control_is_reset(self):
        hifi = (UCM / "HiFi.conf").read_text(encoding="utf-8")
        verb = hifi[hifi.index("SectionVerb"):hifi.index("SectionDevice")]
        enable = verb[verb.index("EnableSequence"):verb.index("DisableSequence")]
        disable = verb[verb.index("DisableSequence"):verb.index("Value")]
        names = lambda text: re.findall(r"name='([^']+)'", text)
        self.assertEqual(sorted(names(enable)), sorted(names(disable)))
        # The paths are complete before ACP probes the PCM: both live in the verb.
        self.assertNotIn("EnableSequence", hifi[hifi.index("SectionDevice"):])


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
        self.assertIn('node.name = "~alsa_output.platform-soc_qcom_msm-audio-apr.*"', rule)
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
        script = (ROOTFS / "usr/libexec/pico4-poc/lpass-macros").read_text(encoding="utf-8")
        self.assertIn("DEV=3370000.codec", script)
        self.assertIn('echo "$DEV" > /sys/bus/platform/drivers_probe', script)

    def test_calibration_binds_once_the_card_is_up(self):
        # The ADSP maps the calibration's buffer only once the audio process runs; the card shows it.
        script = (ROOTFS / "usr/libexec/pico4-poc/lpass-macros").read_text(encoding="utf-8")
        up = script.index('log "lpass-macros: sound card up"')
        self.assertLess(up, script.index("\t\tcalibration\n", up))
        self.assertIn('echo "${dev##*/}" > /sys/bus/platform/drivers_probe', script)

    def test_calibration_store_is_the_stock_kernels(self):
        self.assertIn("ConditionPathExists=/sys/class/kgsl\n", unit("pico4-audiocal.service"))

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
        # PICO OS's levels, as in the stock profile.
        stock = UCM / "kona-mtp-snd-ca.conf"
        for line in stock.read_text(encoding="utf-8").splitlines():
            if "Volume" in line and "cset" in line:
                self.assertIn(line.strip(), card)


if __name__ == "__main__":
    unittest.main()
