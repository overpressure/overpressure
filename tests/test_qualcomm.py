# SPDX-License-Identifier: Unlicense
"""overpressure.qualcomm: the SoC ID, EDL's partition maps, backups, verified writes and the EDL steps."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from overpressure import qualcomm


class QualcommTests(unittest.TestCase):
    def test_chip_input(self):
        self.assertEqual(qualcomm.chip_id(" 00123\r\n"), 123)
        self.assertEqual(qualcomm.chip_id("0xFFFFFFFF"), 0xFFFFFFFF)
        for value in ("-1", "4294967296", "123abc", "", "1; reboot", "+1"):
            with self.assertRaises(ValueError):
                qualcomm.chip_id(value)

    def test_partition_size_from_the_rawprogram_maps(self):
        with tempfile.TemporaryDirectory() as temp:
            backup = Path(temp)
            (backup / "lun4").mkdir()
            (backup / "lun4" / "rawprogram4.xml").write_text(
                '<data><program label="boot" num_partition_sectors="16" SECTOR_SIZE_IN_BYTES="4096" />'
                '<program label="misc" num_partition_sectors="1" SECTOR_SIZE_IN_BYTES="4096" /></data>'
            )
            self.assertEqual(qualcomm.partition_size(backup, "boot"), 65536)
            with self.assertRaises(ValueError):
                qualcomm.partition_size(backup, "userdata")


#: The shape of macOS's `ioreg -p IOUSB -l`: a node per device, its properties below it.
IOREG = """+-o Root  <class IORegistryEntry, id 0x100000100, retain 31>
  +-o AppleT8112USBXHCI@00000000  <class AppleT8112USBXHCI, id 0x100000331, registered, matched, active>
  | +-o QUSB_BULK_CID:0402_SN:ABCD@00100000  <class IOUSBHostDevice, id 0x100001a2c, registered>
  | |   {
  | |     "sessionID" = 15190871203
  | |     "idProduct" = 36872
  | |     "USB Product Name" = "QUSB_BULK_CID:0402_SN:ABCD"
  | |     "idVendor" = 1478
  | |   }
  | |
  | +-o Keyboard@00200000  <class IOUSBHostDevice, id 0x100001b11, registered>
  |     {
  |       "idProduct" = 36872
  |       "idVendor" = 1452
  |     }
"""


class EdlCountTests(unittest.TestCase):
    """EDL (05c6:9008) devices: pyusb everywhere, else the system's own listing."""

    @staticmethod
    def no_usb(_vendor: int, _product: int) -> None:
        return None

    def test_pyusb_first_on_every_system(self):
        asked = []

        def usb(vendor: int, product: int) -> int:
            asked.append((vendor, product))
            return 2

        def no_command(argv: list[str], _timeout: float) -> str:
            raise AssertionError(argv)

        for platform in ("win32", "darwin", "linux"):
            self.assertEqual(qualcomm.edl_devices(platform=platform, usb=usb, command=no_command), 2)
        self.assertEqual(asked, [(0x05C6, 0x9008)] * 3)

    def test_windows_falls_back_to_powershell(self):
        calls = []

        def command(argv: list[str], _timeout: float) -> str:
            calls.append(argv)
            return "1"

        self.assertEqual(qualcomm.edl_devices(platform="win32", usb=self.no_usb, command=command), 1)
        self.assertIn("USB" + chr(92) + "VID_05C6&PID_9008*", calls[0][-1])

    def test_macos_falls_back_to_ioreg(self):
        calls = []

        def command(argv: list[str], _timeout: float) -> str:
            calls.append(argv)
            return IOREG

        self.assertEqual(qualcomm.edl_devices(platform="darwin", usb=self.no_usb, command=command), 1)
        self.assertEqual(calls, [["ioreg", "-p", "IOUSB", "-l"]])
        self.assertEqual(qualcomm.ioreg_count("", 0x05C6, 0x9008), 0)

    def test_linux_falls_back_to_sysfs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, ids in (("1-1", ("05c6", "9008")), ("1-2", ("05c6", "901d")), ("usb1", ("1d6b", "0002"))):
                (root / name).mkdir()
                (root / name / "idVendor").write_text(ids[0] + "\n")
                (root / name / "idProduct").write_text(ids[1] + "\n")
            (root / "1-0.1").mkdir()  # an interface: no ids
            self.assertEqual(qualcomm.edl_devices(platform="linux", usb=self.no_usb, sysfs=root), 1)

    def test_pyusb_missing_counts_as_unknown(self):
        # A module that is None in sys.modules cannot be imported: as without the packages.
        with mock.patch.dict(sys.modules, {"usb": None, "usb.core": None, "libusb_package": None}):
            self.assertIsNone(qualcomm.usb_count(0x05C6, 0x9008))


class FakeClock:
    """Virtual time: a wait's sleeps pass at once."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class DrainTests(unittest.TestCase):
    def test_reads_until_the_device_leaves(self):
        clock, answers = FakeClock(), iter([False, False, True])
        self.assertTrue(qualcomm.drain_until_gone(clock, reader=lambda: next(answers), platform="linux"))
        self.assertLess(clock.now, 1)

    def test_a_device_that_stays_is_reported_after_the_time(self):
        clock = FakeClock()
        self.assertFalse(qualcomm.drain_until_gone(clock, seconds=5, reader=lambda: False, platform="linux"))
        self.assertGreaterEqual(clock.now, 5)

    def test_windows_driver_reads_by_itself(self):
        clock = FakeClock()
        self.assertTrue(qualcomm.drain_until_gone(clock, platform="win32"))
        self.assertEqual(clock.now, 0)


class UnansweredTests(unittest.TestCase):
    def test_edl_ngs_words_for_a_device_that_does_not_answer(self):
        for mode in ("Unknown", "Error"):
            with self.subTest(mode=mode):
                line = (
                    "[ERROR] An unexpected error occurred in 'printgpt': "
                    f"Cannot proceed: Device mode is {mode} or could not be determined."
                )
                self.assertTrue(qualcomm.unanswered("Passive read timed out\n" + line))
        self.assertFalse(qualcomm.unanswered("Serial Number: 0A1B2C3D\n--- Partitions LUN 0 ---"))
        self.assertIn("only right after it enters EDL mode", qualcomm.NO_ANSWER)


class EdlTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_native_dump_rejects_missing_truncated_and_escaping_files(self):
        directory = self.root / "lun4"
        directory.mkdir()
        xml = directory / "rawprogram4.xml"

        def map_file(name: str = "abl.bin") -> None:
            xml.write_text(
                '<data><program filename="' + name + '" label="abl" '
                'num_partition_sectors="1" SECTOR_SIZE_IN_BYTES="4096" '
                'physical_partition_number="4" /></data>'
            )

        map_file()
        with self.assertRaises(ValueError):
            qualcomm.validate_native_dump(self.root, [4], include_userdata=True)
        (directory / "abl.bin").write_bytes(b"x" * 4095)
        with self.assertRaises(ValueError):
            qualcomm.validate_native_dump(self.root, [4], include_userdata=True)
        (directory / "abl.bin").write_bytes(b"x" * 4096)
        qualcomm.validate_native_dump(self.root, [4], include_userdata=True)
        map_file("../abl.bin")
        with self.assertRaises(ValueError):
            qualcomm.validate_native_dump(self.root, [4], include_userdata=True)

    def capture(
        self, folder: Path, names: tuple[str, ...], required: tuple[str, ...] = ("abl", "boot", "devinfo")
    ) -> dict:
        def runner(command: list[str], timeout: float = 60) -> str:
            self.assertEqual(timeout, 86400)
            for name in names:
                (folder / name).write_bytes(b"data of " + name.encode())
            return "ran " + " ".join(command)

        return qualcomm.capture_backup(
            folder,
            [["edl", "rl", str(folder)]],
            {"serial": "A"},
            backend="bkerler",
            luns=None,
            include_userdata=False,
            runner=runner,
            required=required,
        )

    def test_a_backup_holds_the_required_partitions_and_its_metadata(self):
        complete = ("abl_a.bin", "boot.img", "devinfo.bin", "gpt_main0.bin", "rawprogram0.xml")
        manifest = self.capture(self.root / "good", complete)
        self.assertEqual(sorted(manifest["files"]), sorted(complete))
        self.assertEqual(manifest["identity"], {"serial": "A"})
        self.assertIn("ran edl rl", (self.root / "good" / "edl.log").read_text())
        self.assertEqual(json.loads((self.root / "good" / "manifest.json").read_text()), manifest)
        with self.assertRaisesRegex(ValueError, "new backup directory"):
            self.capture(self.root / "good", complete)
        with self.assertRaisesRegex(ValueError, "devinfo"):
            self.capture(self.root / "partial", ("abl.bin", "boot.bin", "gpt_main0.bin", "rawprogram0.xml"))
        self.assertFalse((self.root / "partial" / "manifest.json").exists())
        with self.assertRaisesRegex(ValueError, "GPT"):
            self.capture(self.root / "no-gpt", ("abl.bin", "boot.bin", "devinfo.bin"))
        other = ("vbmeta.bin", "gpt_main0.bin", "rawprogram0.xml")
        self.assertIn("vbmeta.bin", self.capture(self.root / "other", other, required=("vbmeta",))["files"])

    def test_a_write_is_read_back_outside_the_backup(self):
        image = self.root / "abl.elf"
        image.write_bytes(b"engineering" * 1000)
        backup = self.root / "backup"
        backup.mkdir()
        commands = []

        def runner(command: list[str], timeout: float = 60) -> str:
            commands.append((command, timeout))
            if command[0] == "read":
                Path(command[1]).write_bytes(readback_data)
            return "OK"

        readback_data = image.read_bytes()
        qualcomm.write_verified(
            ["write"], lambda path: ["read", path], image, readback=self.root / "rb.bin", backup=backup, runner=runner
        )
        self.assertEqual(commands, [(["write"], 600), (["read", str((self.root / "rb.bin").resolve())], 600)])
        for readback in (self.root / "rb.bin", backup / "rb.bin"):  # exists; inside the backup
            commands.clear()
            with self.assertRaisesRegex(ValueError, "new path outside"):
                qualcomm.write_verified(
                    ["write"], lambda path: ["read", path], image, readback=readback, backup=backup, runner=runner
                )
            self.assertEqual(commands, [])
        readback_data = b"other"
        with self.assertRaisesRegex(ValueError, "readback mismatch"):
            qualcomm.write_verified(
                ["write"],
                lambda path: ["read", path],
                image,
                readback=self.root / "bad.bin",
                backup=backup,
                runner=runner,
            )

    def test_entering_edl_waits_for_exactly_one_device(self):
        commands, counts = [], [0, 0, 2, 1]

        def runner(command: list[str], timeout: float) -> str:
            commands.append((command, timeout))
            return ""

        qualcomm.enter_edl("adb", "A1", runner, FakeClock(), edl_count=lambda: counts.pop(0))
        self.assertEqual(commands, [(["adb", "-s", "A1", "reboot", "edl"], 30)])
        commands.clear()
        qualcomm.enter_edl("adb", "A1", runner, FakeClock(), edl_count=lambda: 1)
        self.assertEqual(commands, [])
        with self.assertRaisesRegex(RuntimeError, "timed out waiting for EDL: expected exactly one EDL device"):
            qualcomm.enter_edl("adb", "A1", runner, FakeClock(), edl_count=lambda: 0)

    def test_edl_ng_commands_and_the_serial_check(self):
        self.assertEqual(
            qualcomm.edl_ng("edl-ng", "loader.elf", "read-part", "boot", "out.bin"),
            ["edl-ng", "--loader", "loader.elf", "--memory", "Ufs", "read-part", "boot", "out.bin"],
        )
        qualcomm.check_edl_serial("Connected.\nSerial Number: 0x1A2b\nok", "0x1a2B")
        for output in ("Serial Number: 0x1A2b3", "no serial", "Serial Number: 0x9999"):
            with self.assertRaisesRegex(RuntimeError, "not the configured headset"):
                qualcomm.check_edl_serial(output, "0x1A2b")


if __name__ == "__main__":
    unittest.main()
