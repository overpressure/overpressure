"""The PICO 4's RAM boot target: the dev image, its unlock check, entering fastboot, the login."""
import tempfile
import unittest
from pathlib import Path

from overpressure import bootimg, fastboot

from devices.phoenix.tooling import HEADSET, devloop, localconfig
from devices.phoenix.tooling.ramboot import PicoRamBoot


class FakeDev:
    def __init__(self, work, adb="", boot="OKAY", device_info="(bootloader) Device unlocked: true"):
        self.work = work
        self.adb, self.boot, self.device_info = adb, boot, device_info
        self.calls, self.logins = [], 0

    def run(self, argv, timeout=60, stdin=None, check=True, log=None):
        words = [str(a) for a in argv]
        self.calls.append(words)
        if words[1:] == ["devices"]:
            return self.adb if "adb" in words[0] else "F\tfastboot"
        if words[3:5] == ["oem", "device-info"]:
            return self.device_info
        if words[3:4] == ["boot"]:
            if self.boot.startswith("FAILED"):
                raise RuntimeError("fastboot boot failed: " + self.boot)
            return self.boot
        return ""

    def ssh(self, command, timeout=30, stdin=None, check=True):
        self.calls.append(["ssh", command])
        return ""

    def authenticate(self):
        self.logins += 1
        if self.logins < 2:
            raise RuntimeError("no SSH yet")


class PicoRamBootTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        self.image = self.work / "dev-boot.img"
        self.image.write_bytes(bootimg.BootImage(2, 4096, b"kernel", b"ramdisk", dtb=b"dtb").pack())
        self.settings = localconfig.Settings(adb_serial="A", fastboot_serial="F", ncm_python="ncm-python",
                                             platform_tools="")

    def target(self, **fake):
        self.dev = FakeDev(self.work, **fake)
        return PicoRamBoot(self.settings, self.dev)

    def boot(self, target):
        clock = fastboot.Clock(monotonic=lambda: 0.0, sleep=lambda _s: None)
        return fastboot.ram_boot(target, self.image, log=lambda _line: None, clock=clock)

    def test_the_pico_4_offers_it_with_the_dev_image(self):
        self.assertIsInstance(HEADSET.ram_boot(), PicoRamBoot)
        self.assertEqual(PicoRamBoot(self.settings).image(), devloop.WORK / "dev-boot.img")
        keys = [s.key for s in HEADSET.steps()]
        self.assertLess(keys.index("stage-abl"), keys.index("ram-boot"))
        self.assertLess(keys.index("ram-boot"), keys.index("flash"))

    def test_boot_then_the_ncm_link_once_and_the_dev_image_login(self):
        self.boot(self.target())
        self.assertIn(["fastboot", "-s", "F", "boot", str(self.image)], self.dev.calls)
        ncm = [c for c in self.dev.calls if c[0] == "ncm-python" or c[0].endswith("ncm-python")]
        self.assertEqual(len(ncm), 1)
        self.assertEqual(ncm[0][-2:], ["--serial", "F"])
        self.assertEqual(self.dev.logins, 2)
        self.assertFalse(any("flash" in c for c in self.dev.calls))

    def test_the_engineering_abls_refusal_names_the_partition_route(self):
        with self.assertRaises(fastboot.Refused) as raised:
            self.boot(self.target(boot="FAILED (remote: 'unknown command')"))
        self.assertIn("dev flash", str(raised.exception))
        self.assertEqual(self.dev.logins, 0)

    def test_the_stock_abls_unlock_form_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "engineering ABL"):
            self.boot(self.target(device_info="DeviceUnlocked=true"))
        self.assertFalse(any(c[3:4] == ["boot"] for c in self.dev.calls))

    def test_enters_fastboot_from_pico_os_or_from_linux(self):
        target = self.target(adb="List of devices attached\nA\tdevice")
        target.enter()
        self.assertIn(["adb", "-s", "A", "reboot", "bootloader"], self.dev.calls)
        target = self.target(adb="List of devices attached")
        target.enter()
        self.assertIn(["ssh", "pico4-reboot bootloader"], self.dev.calls)


if __name__ == "__main__":
    unittest.main()
