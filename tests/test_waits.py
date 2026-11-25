# SPDX-License-Identifier: Unlicense
"""Waits for the headset in a new USB mode: a replug or the link's fix when they run out; device lists; its battery."""

import unittest
from pathlib import Path
from unittest import mock

from overpressure import android, install, power, usbaccess

FASTBOOT = usbaccess.UsbMode("fastboot mode", "18d1", "d00d")


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class ReplugTests(unittest.TestCase):
    def test_a_wait_that_runs_out_asks_for_a_replug_and_waits_again(self):
        clock = FakeClock()
        ctx = mock.Mock(clock=clock)

        def probe() -> str:
            if clock.now < 200.0:  # after the first wait's 180 s
                msg = "not yet"
                raise RuntimeError(msg)
            return "SERIAL"

        self.assertEqual(install.replug_until(ctx, "fastboot mode", 180, probe), "SERIAL")
        self.assertEqual([c.args[0].id for c in ctx.ask.call_args_list], ["replug"])
        self.assertIn("fastboot mode", ctx.ask.call_args.args[0].text)
        # Cancel stops the run (the ask raises Stopped); an error waiting does not cure ends it without an ask.
        clock.now = 0.0
        ctx.ask.side_effect = install.Stopped("declined")
        with self.assertRaises(install.Stopped):
            install.replug_until(ctx, "fastboot mode", 10, probe)
        ctx.ask.reset_mock()

        def refused() -> str:
            msg = "no access"
            raise usbaccess.NoAccessError(msg)

        with self.assertRaises(usbaccess.NoAccessError):
            install.replug_until(ctx, "fastboot mode", 10, refused)
        ctx.ask.assert_not_called()

    def test_a_link_without_the_headsets_address_asks_for_its_fix_not_a_replug(self):
        clock = FakeClock()
        ctx = mock.Mock(clock=clock)
        ctx.ask.side_effect = lambda _ask: setattr(clock, "now", 1000.0)

        def reach() -> str:
            if clock.now < 1000.0:  # until the owner fixed the link
                msg = "ssh: connect to host 172.16.42.1 port 22: No route to host"
                raise RuntimeError(msg)
            return "answered"

        problem = "The headset's USB link is up on this computer (enp6s0f3u4), ...\n\nnmcli connection up x"
        self.assertEqual(
            install.replug_until(ctx, "the maintenance system", 120, reach, link=lambda: problem), "answered"
        )
        (ask,) = [c.args[0] for c in ctx.ask.call_args_list]
        self.assertEqual((ask.id, ask.kind), ("usb-link", "action"))
        self.assertIn("nmcli connection up x\n\nThen press Done", ask.text)
        ctx.log.assert_any_call("The headset's USB link is up on this computer (enp6s0f3u4), ...")

    def test_the_log_names_what_the_usb_bus_holds(self):
        clock = FakeClock()
        ctx = mock.Mock(clock=clock)
        ctx.ask.side_effect = install.Stopped("declined")
        with (
            mock.patch.object(usbaccess, "seen", return_value="devices on USB: fastboot mode 0") as seen,
            self.assertRaises(install.Stopped),
        ):
            install.replug_until(ctx, "fastboot mode", 5, mock.Mock(side_effect=RuntimeError("x")), modes=(FASTBOOT,))
        seen.assert_called_once_with((FASTBOOT,))
        self.assertIn("devices on USB: fastboot mode 0", ctx.log.call_args_list[0].args[0])


class DeviceListTests(unittest.TestCase):
    def test_device_lists(self):
        adb = "* daemon started successfully\nList of devices attached\nA\tdevice\nB\tunauthorized\n\n"
        self.assertEqual(android.device_rows(adb), {"A": "device", "B": "unauthorized"})
        self.assertEqual(android.device_rows("F\tfastboot\n", ("fastboot",)), {"F": "fastboot"})
        self.assertEqual(list(android.device_rows("B\tdevice\nA\tdevice\n", ("device",))), ["B", "A"])  # adb's order

    def test_a_listing_is_unusable_only_without_a_usable_device(self):
        with mock.patch.object(usbaccess, "denied", return_value=[Path("/dev/bus/usb/001/006")]):
            self.assertTrue(usbaccess.unusable("", FASTBOOT))
            self.assertFalse(usbaccess.unusable("F\tfastboot\n", FASTBOOT))
        self.assertTrue(usbaccess.unusable("F\tno permissions; see [x]\n", FASTBOOT))
        with mock.patch.object(usbaccess, "denied", return_value=[]):
            self.assertFalse(usbaccess.unusable("", FASTBOOT))

    def test_the_usb_bus_is_read_on_linux_only(self):
        self.assertEqual(usbaccess.seen((FASTBOOT,), platform="win32"), "")
        with mock.patch.object(usbaccess, "device_files", return_value=[Path("/dev/bus/usb/001/002")]):
            self.assertEqual(usbaccess.seen((FASTBOOT,), platform="linux"), "devices on USB: fastboot mode 1")


class HeadsetBatteryTests(unittest.TestCase):
    def test_writes_wait_for_a_battery_that_lasts(self):
        self.assertEqual(power.headset_refusal("54 Discharging"), "")
        self.assertEqual(power.headset_refusal("5 Charging"), "")
        self.assertEqual(power.headset_refusal("5 Full"), "")
        self.assertIn("at 10 % (Discharging)", power.headset_refusal("10 Discharging"))
        self.assertIn("status unknown", power.headset_refusal("10"))
        self.assertEqual(power.headset_refusal(""), "")  # unreadable: the caller warns
        self.assertEqual(power.headset_refusal("cat: no such file\n"), "")
        # As cat prints the two files, one per line.
        self.assertEqual(power.headset_refusal("20\nDischarging\n"), "")
        self.assertIn("at 19 % (Discharging)", power.headset_refusal("19\nDischarging\n"))
        self.assertTrue(power.headset_refusal("5\nNot charging\n"))
        self.assertEqual(power.headset_refusal("54 Discharging", minimum=60)[:21], "the battery is at 54 ")


if __name__ == "__main__":
    unittest.main()
