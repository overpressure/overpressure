# SPDX-License-Identifier: Unlicense
"""overpressure.usbaccess: what the installed udev rules grant, the fix per distribution, connected devices."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from overpressure import doctor, process, usbaccess
from overpressure.usbaccess import UsbMode

ADB = UsbMode("PICO OS with USB debugging", "2d40", "00b7")
FASTBOOT = UsbMode("fastboot mode", "18d1", "d00d")
EDL = UsbMode("EDL mode", "05c6", "9008")
MODES = (ADB, FASTBOOT, EDL)

#: A rules file in the jump style of the packaged Android rules: a block per vendor, labels that set
#: what the device is and jump to the common end, which grants every device marked as a user's.
JUMPS = """\
ACTION!="add", ACTION!="bind", GOTO="end"
SUBSYSTEM!="usb", GOTO="end"
ATTR{bDeviceClass}=="09", GOTO="end"

# a vendor whose listed products only count
ATTR{idVendor}!="2d40", GOTO="not_first"
ATTR{idProduct}=="00b7", GOTO="user"
GOTO="end"
LABEL="not_first"

# a vendor with products of its own and every other one as a user's device
ATTR{idVendor}!="05c6", GOTO="not_second"
ATTR{idProduct}=="9025", GOTO="adb"
ENV{adb_user}="yes"
GOTO="match"
LABEL="not_second"

ATTR{idVendor}!="18d1", GOTO="not_third"
ATTR{idProduct}=="d00d", GOTO="fast"
GOTO="end"
LABEL="not_third"
GOTO="end"

LABEL="adb", ENV{adb_adb}="yes", GOTO="match"
LABEL="fast", ENV{adb_fast}="yes", \\
    GOTO="match"
LABEL="user", ENV{adb_user}="yes"

LABEL="match"
ENV{adb_adb}=="yes", ENV{adb_user}="yes"
ENV{adb_fast}=="yes", ENV{adb_user}="yes"
ENV{adb_user}=="yes", MODE="0660", GROUP="phones", TAG+="uaccess"
LABEL="end"
"""
#: The older line style: one line per vendor, then the grant.
LINES = """\
SUBSYSTEM=="usb", ATTR{idVendor}=="05c6", ENV{adb_user}="yes"
SUBSYSTEM=="usb", ATTR{idVendor}=="18d1", ENV{adb_user}="yes"
ENV{adb_user}=="yes", MODE="0660", GROUP="plugdev", TAG+="uaccess"
"""


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class Folder(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.etc = self.root / "etc"
        self.lib = self.root / "lib"
        self.etc.mkdir()
        self.lib.mkdir()

    def rules(self, folder: Path, name: str, text: str) -> Path:
        path = folder / name
        path.write_text(text, encoding="utf-8")
        return path

    def granted(self, *, groups: tuple[str, ...] = ()) -> list[str]:
        files = usbaccess.rule_files((self.etc, self.lib))
        return [m.name for m in MODES if usbaccess.granted(m, files, groups)]


class GrantTests(Folder):
    def test_the_jump_style_grants_listed_products_and_whole_vendors(self) -> None:
        self.rules(self.lib, "51-android.rules", JUMPS)
        self.assertEqual(self.granted(), [ADB.name, FASTBOOT.name, EDL.name])
        files = usbaccess.rule_files((self.etc, self.lib))
        self.assertFalse(usbaccess.granted(UsbMode("x", "2d40", "00b1"), files))  # not listed under its vendor
        self.assertTrue(usbaccess.granted(UsbMode("x", "05c6", "f000"), files))  # its vendor's catch-all

    def test_the_line_style(self) -> None:
        self.rules(self.lib, "51-android.rules", LINES)
        self.assertEqual(self.granted(), [FASTBOOT.name, EDL.name])

    def test_no_rules_grant_nothing(self) -> None:
        self.rules(self.lib, "50-udev-default.rules", 'SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", MODE="0664"\n')
        self.assertEqual(self.granted(), [])

    def test_uaccess_counts_only_before_the_seat_rules(self) -> None:
        self.rules(self.lib, "80-late.rules", LINES)
        self.assertEqual(self.granted(), [])

    def test_a_group_counts_for_its_members_and_a_mode_for_everyone(self) -> None:
        self.rules(self.lib, "90-group.rules", 'ATTR{idVendor}=="05c6", MODE="0660", GROUP="dialout"\n')
        self.assertEqual(self.granted(), [])
        self.assertEqual(self.granted(groups=("dialout",)), [EDL.name])
        self.rules(self.lib, "91-all.rules", 'ATTR{idVendor}=="18d1", MODE="0666"\n')
        self.assertEqual(self.granted(), [FASTBOOT.name])

    def test_a_match_it_cannot_evaluate_never_grants(self) -> None:
        self.rules(self.lib, "51-x.rules", 'KERNEL=="bus*", ATTR{idVendor}=="05c6", TAG+="uaccess"\n')
        self.assertEqual(self.granted(), [])

    def test_a_file_in_etc_hides_the_packaged_one(self) -> None:
        self.rules(self.lib, "51-android.rules", JUMPS)
        self.rules(self.etc, "51-android.rules", "# emptied by the owner\n")
        self.assertEqual(self.granted(), [])
        self.assertEqual(usbaccess.rule_files((self.etc, self.lib)), [self.etc / "51-android.rules"])

    def test_the_own_rule_grants_its_mode(self) -> None:
        self.rules(self.etc, "70-overpressure.rules", ADB.rule() + "\n")
        self.assertEqual(self.granted(), [ADB.name])


class FixTests(Folder):
    def release(self, text: str) -> Path:
        return self.rules(self.root, "os-release", text)

    def test_the_distributions_package_first(self) -> None:
        cases = {
            "ID=arch\n": "sudo pacman -S android-udev",
            "ID=manjaro\nID_LIKE=arch\n": "sudo pacman -S android-udev",
            'ID=debian\nVERSION_ID="13"\n': "sudo apt install android-udev-rules",
            'ID=linuxmint\nID_LIKE="ubuntu debian"\n': "sudo apt install android-sdk-platform-tools-common",
        }
        for text, package in cases.items():
            with self.subTest(text=text):
                fix = usbaccess.fix(MODES, release=self.release(text))
                self.assertTrue(fix.startswith(package), fix)
                self.assertIn(usbaccess.TRIGGER, fix)

    def test_the_rules_to_add_once_the_package_is_there_or_none_is_known(self) -> None:
        installed = [self.rules(self.lib, "51-android.rules", LINES)]
        for text in ("ID=ubuntu\nID_LIKE=debian\n", "ID=fedora\n"):
            with self.subTest(text=text):
                fix = usbaccess.fix([ADB], release=self.release(text), files=installed)
                self.assertIn(f"'{ADB.rule()}'", fix)
                self.assertNotIn(EDL.vendor, fix)
                self.assertIn(f"sudo tee -a {usbaccess.OWN_RULES}", fix)
                self.assertTrue(fix.endswith(usbaccess.TRIGGER))

    def test_the_doctor_check(self) -> None:
        release = self.release("ID=arch\n")
        linux, windows = doctor.Host(platform="linux"), doctor.Host(platform="win32")
        self.assertEqual(doctor.usb_check(windows, MODES), [])
        (check,) = doctor.usb_check(linux, MODES, dirs=(self.etc, self.lib), release=release, groups=())
        self.assertEqual(check.status, doctor.MISSING)
        self.assertIn(EDL.name, check.detail)
        self.assertTrue(check.fix.startswith("sudo pacman -S android-udev"))
        self.rules(self.lib, "51-android.rules", JUMPS)
        (check,) = doctor.usb_check(linux, MODES, dirs=(self.etc, self.lib), release=release, groups=())
        self.assertEqual((check.status, check.fix), (doctor.OK, ""))


class DeviceTests(Folder):
    def device(self, name: str, vendor: str, product: str, bus: int, number: int) -> None:
        folder = self.root / "sys" / name
        folder.mkdir(parents=True)
        for key, value in (("idVendor", vendor), ("idProduct", product), ("busnum", bus), ("devnum", number)):
            (folder / key).write_text(f"{value}\n")

    def test_the_connected_devices_this_user_may_not_write(self) -> None:
        self.device("1-2", "05c6", "9008", 1, 5)
        self.device("1-3", "046d", "c52b", 1, 6)
        sysfs, dev = self.root / "sys", self.root / "dev"
        self.assertEqual(usbaccess.device_files("05c6", "9008", sysfs, dev), [dev / "001" / "005"])
        self.assertEqual(usbaccess.denied(EDL, sysfs=sysfs, dev=dev, access=lambda _p: False), [dev / "001" / "005"])
        self.assertEqual(usbaccess.denied(EDL, sysfs=sysfs, dev=dev, access=lambda _p: True), [])
        self.assertEqual(usbaccess.denied(ADB, sysfs=sysfs, dev=dev, access=lambda _p: False), [])

    def test_require_gives_udev_its_moment_then_names_the_fix(self) -> None:
        clock, answers = FakeClock(), iter([True, True, False])
        usbaccess.require(EDL, MODES, clock, denied_now=lambda: next(answers))
        self.assertLess(clock.now, usbaccess.GRACE)
        clock = FakeClock()
        with self.assertRaisesRegex(usbaccess.NoAccessError, "in EDL mode over USB"):
            usbaccess.require(EDL, MODES, clock, denied_now=lambda: True)
        self.assertGreaterEqual(clock.now, usbaccess.GRACE)

    def test_a_denial_is_said_once_it_stayed(self) -> None:
        clock = FakeClock()
        denial = usbaccess.Denial(clock)
        denial.see(is_denied=True, text=lambda: "fix")
        clock.now = usbaccess.GRACE - 1
        denial.see(is_denied=False, text=lambda: "fix")  # it came back: the count starts again
        denial.see(is_denied=True, text=lambda: "fix")
        clock.now += usbaccess.GRACE
        with self.assertRaisesRegex(usbaccess.NoAccessError, "fix"):
            denial.see(is_denied=True, text=lambda: "fix")

    def test_guard_runs_adb_and_fastboot_again_then_names_the_fix(self) -> None:
        clock, calls = FakeClock(), []
        replies = iter(["X\tno permissions; see [udev]", "X\tfastboot"])

        def runner(argv: list[str], *_args: object, **_kwargs: object) -> str:
            calls.append(argv)
            return next(replies)

        run = usbaccess.guard(runner, MODES, {"fastboot": FASTBOOT}, clock)
        self.assertEqual(run(["/opt/fastboot", "devices"], 30), "X\tfastboot")
        self.assertEqual(len(calls), 2)

        def refused(_argv: list[str], *_args: object, **_kwargs: object) -> str:
            msg = "fastboot failed (1): no permissions (missing udev rules?)"
            raise RuntimeError(msg)

        run = usbaccess.guard(refused, MODES, {"fastboot.exe": FASTBOOT, "fastboot": FASTBOOT}, clock)
        with self.assertRaisesRegex(usbaccess.NoAccessError, "fastboot mode"):
            run(["fastboot", "flash", "boot", "boot.img"], 60)
        other = usbaccess.guard(lambda _argv, *_a, **_k: "no permissions", MODES, {"adb": ADB}, clock)
        self.assertEqual(other(["edl-ng", "printgpt"]), "no permissions")  # not adb's or fastboot's

    def test_until_ends_at_once_on_a_missing_rule(self) -> None:
        clock, calls = FakeClock(), []

        def denied() -> None:
            calls.append(clock.now)
            msg = "no rule"
            raise usbaccess.NoAccessError(msg)

        with self.assertRaises(usbaccess.NoAccessError):
            process.until("the headset", 600, denied, clock)
        self.assertEqual(calls, [0.0])

    def test_adb_and_fastboot_listings(self) -> None:
        self.assertTrue(usbaccess.listing_denied("List of devices attached\nX\tno permissions (missing udev rules?)"))
        self.assertFalse(usbaccess.listing_denied("List of devices attached\nX\tdevice\n"))


if __name__ == "__main__":
    unittest.main()
