"""The power key as on PICO OS (docs/install.md#power-key)."""
import configparser
import importlib.machinery
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DROPIN = ROOT / "userspace/rootfs/usr/lib/systemd/logind.conf.d/50-pico4-power-key.conf"
PATH = ROOT / "userspace/rootfs/usr/libexec/pico4-poc/powerkey"
_loader = importlib.machinery.SourceFileLoader("powerkey", str(PATH))
_spec = importlib.util.spec_from_loader("powerkey", _loader)
tool = importlib.util.module_from_spec(_spec)
_loader.exec_module(tool)


class LogindTests(unittest.TestCase):
    def test_logind_leaves_the_key_to_pico4_powerkey(self):
        config = configparser.ConfigParser()
        config.read_string(DROPIN.read_text(encoding="utf-8"))
        self.assertEqual(config["Login"]["HandlePowerKey"], "ignore")
        self.assertEqual(config["Login"]["HandlePowerKeyLongPress"], "ignore")
        unit = (ROOT / "userspace/rootfs/usr/lib/systemd/system/pico4-powerkey.service").read_text(encoding="utf-8")
        self.assertIn("ExecStart=/usr/libexec/pico4-poc/powerkey\n", unit)
        build = (ROOT / "scripts/build-userspace.sh").read_text(encoding="utf-8")
        self.assertIn("pico4-powerkey.service", build[build.index("in_t systemctl enable"):])
        self.assertNotIn(b"\r", PATH.read_bytes())


class DeviceTests(unittest.TestCase):
    def test_the_pmic_key(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for event, device in (("event0", "gpio-keys"), ("event2", "pm8941_pwrkey"), ("event3", "pm8941_resin")):
                (Path(tmp) / event / "device").mkdir(parents=True)
                (Path(tmp) / event / "device/name").write_text(device + "\n")
            self.assertEqual(tool.key_device(f"{tmp}/event*/device/name"), "/dev/input/event2")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(tool.key_device(f"{tmp}/event*/device/name"))


class KeyTests(unittest.TestCase):
    """PICO OS: a short press goes to sleep, 2 s held shuts down without asking."""

    def test_short_press_suspends(self):
        key = tool.Key()
        self.assertIsNone(key.event(1, 10.0))
        self.assertEqual(key.timeout(10.5), (1.5, None))
        self.assertEqual(key.event(0, 10.4), "suspend")
        self.assertEqual(key.timeout(11.0), (None, None))

    def test_two_seconds_held_powers_off_once(self):
        key = tool.Key()
        key.event(1, 10.0)
        self.assertEqual(key.timeout(12.0), (None, "poweroff"))
        self.assertIsNone(key.event(0, 12.5))  # the release after the power-off does nothing

    def test_release_without_press_does_nothing(self):
        self.assertIsNone(tool.Key().event(0, 1.0))

    def test_wake_up_press_is_ignored(self):
        # Woken by the key: its events arrive with the resume and belong to the wake-up press.
        waking = tool.Waking(asleep=0.0)
        self.assertTrue(waking.update(100.0, asleep=12.0))
        self.assertTrue(waking.quiet(100.2))
        self.assertFalse(waking.quiet(101.5))

    def test_press_after_another_wake_source_counts(self):
        # Woken by the RTC: the idle poll notices the resume; a press a minute later suspends.
        waking = tool.Waking(asleep=0.0)
        self.assertTrue(waking.update(200.0, asleep=20.0))   # the poll after the resume
        self.assertFalse(waking.update(260.0, asleep=20.0))  # the press: no new sleep
        self.assertFalse(waking.quiet(260.0))
        self.assertLessEqual(tool.POLL_S, tool.SETTLE_S)

    def test_charger_mode_knows_the_same_keys(self):
        # Stage 1's charger waits for the power key on whichever kernel boot holds.
        charger = (ROOT / "initramfs/usr/libexec/pico4-poc/charger").read_text(encoding="utf-8")
        self.assertIn(f'[ "$name" = {tool.KEY_DEVICE} ]', charger)

    def test_event_layout(self):
        # struct input_event on aarch64: two longs of time, type, code, value.
        self.assertEqual(tool.EVENT.size, 24)


if __name__ == "__main__":
    unittest.main()
