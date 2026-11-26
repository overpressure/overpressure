# SPDX-License-Identifier: Unlicense
"""overpressure.qualcomm: the SoC ID, EDL's partition maps, backups, verified writes and the EDL steps."""

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable, ClassVar
from unittest import mock

from overpressure import process, qualcomm


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

    def test_a_sahara_handshake_that_fails_again_after_the_reset(self):
        # edl-ng 1.6 on macOS, a session that no longer answers: libusb fails the handshake with a stalled pipe.
        first = (
            "[ERROR] Handshake failed: LibUsbDotNet error: Pipe\n"
            "[WARN] Initial Sahara handshake failed, attempting reset and retry...\n"
        )
        for last in (
            "[ERROR] An unexpected error occurred in 'printgpt': Sahara handshake failed even after reset.",
            (
                "[ERROR] Sahara reset/retry failed: Pipe\n"
                "[ERROR] An unexpected error occurred in 'reset': Sahara handshake failed."
            ),
        ):
            with self.subTest(last=last):
                self.assertTrue(qualcomm.unanswered(first + last))
        # The first try's failure alone is not one: the retry answered.
        answered = first + "[INFO] Sahara handshake successful after reset.\nSerial Number: 0A1B2C3D"
        self.assertFalse(qualcomm.unanswered(answered))


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
        # A partition the backup leaves out (Android's cache) is in the map without an image.
        xml.write_text(
            xml.read_text().replace(
                "</data>",
                '<program filename="cache.bin" label="cache" num_partition_sectors="9" SECTOR_SIZE_IN_BYTES="4096" '
                'physical_partition_number="4" /></data>',
            )
        )
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
        self.assertEqual(manifest["skipped"], ["cache", "rawdump", "logdump", "userdata"])
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

    def test_a_stopped_dump_counts_the_partitions_it_read_whole(self):
        directory = self.root / "lun4"
        directory.mkdir()
        rows = "".join(
            f'<program filename="{name}" label="{label}" num_partition_sectors="1" SECTOR_SIZE_IN_BYTES="4096" '
            'physical_partition_number="4" />'
            for label, name in (("abl", "abl.bin"), ("xbl", "xbl.bin"), ("tz", "tz.bin"), ("out", "../out.bin"))
        )
        (directory / "rawprogram4.xml").write_text(f"<data>{rows}</data>")
        (directory / "abl.bin").write_bytes(b"x" * 4096)
        (directory / "xbl.bin").write_bytes(b"x" * 100)  # cut short
        (self.root / "out.bin").write_bytes(b"x" * 4096)  # outside the LUN's folder
        self.assertEqual(qualcomm.dumped(self.root, 4), ["abl"])
        self.assertEqual(qualcomm.dumped(self.root, 5), [])  # no map: nothing counts

    def test_the_commands_skip_what_a_stopped_backup_holds(self):
        (self.root / "loader.elf").write_bytes(b"loader")
        args = argparse.Namespace(
            edl_backend="edl-ng",
            edl="edl-ng",
            edl_script=None,
            edl_python=None,
            loader=self.root / "loader.elf",
            backup=self.root / "backup",
            edl_luns=[0, 4],
            include_userdata=False,
        )
        commands = qualcomm.backup_commands(args, {4: ["abl", "xbl"]})
        skips = [command[command.index("--skip") + 1] for command in commands]
        self.assertEqual(skips, ["cache,rawdump,logdump,userdata", "cache,rawdump,logdump,userdata,abl,xbl"])
        self.assertEqual(qualcomm.backup_commands(args), qualcomm.backup_commands(args, {}))

    def test_an_unfinished_backup_goes_on_and_another_is_never_written_to(self):
        folder = self.root / "backup"
        complete = ("abl_a.bin", "boot.img", "devinfo.bin", "gpt_main0.bin", "rawprogram0.xml")

        def stops(command: list[str], timeout: float = 60) -> str:  # noqa: ARG001  # the runner's signature
            (folder / "abl_a.bin").write_bytes(b"abl")
            msg = r"Command failed: \.\COM5 timed out."
            raise ValueError(msg)

        def capture(runner: Callable[..., str], identity: dict, *, resume: bool) -> dict:
            return qualcomm.capture_backup(
                folder,
                [["edl", "rl", str(folder)]],
                identity,
                backend="bkerler",
                luns=None,
                include_userdata=False,
                runner=runner,
                resume=resume,
            )

        with self.assertRaisesRegex(ValueError, "timed out"):
            capture(stops, {"serial": "A"}, resume=True)
        self.assertIn(r"COM5 timed out", (folder / "edl.log").read_text())  # the failure is in the log too
        self.assertEqual(qualcomm.unfinished(folder), {"serial": "A"})
        self.assertFalse((folder / "manifest.json").exists())
        with self.assertRaisesRegex(ValueError, "new backup directory"):
            capture(stops, {"serial": "A"}, resume=False)
        with self.assertRaisesRegex(ValueError, "not an unfinished backup of this headset"):
            capture(stops, {"serial": "B"}, resume=True)

        def finishes(command: list[str], timeout: float = 60) -> str:  # noqa: ARG001  # the runner's signature
            for name in complete[1:]:
                (folder / name).write_bytes(b"data of " + name.encode())
            return "went on"

        manifest = capture(finishes, {"serial": "A"}, resume=True)
        self.assertEqual(sorted(manifest["files"]), sorted(complete))
        self.assertIsNone(qualcomm.unfinished(folder))
        self.assertFalse((folder / qualcomm.UNFINISHED).exists())
        self.assertIn("went on", (folder / "edl.log").read_text())
        with self.assertRaisesRegex(ValueError, "new backup directory"):  # a complete backup stays as it is
            capture(finishes, {"serial": "A"}, resume=True)

    def test_a_link_that_broke_mid_session_is_told_apart(self):
        for output in (
            r"09:24:52.672 [ERROR] Error dumping partition 'metadata': QUD write to \\.\COM5 timed out.",
            r"error: Command failed: \.\COM5 timed out.",
            "LibUsb bulk read timed out.",
            # A garbled reply: the loader's XML came back as zeros (Windows, a resumed backup's partition table).
            (
                "20:17:51.626 [ERROR] Error reading/saving main GPT: '.', hexadecimal value 0x00, is an invalid "
                "character. Line 11, position 47."
            ),
        ):
            self.assertTrue(qualcomm.link_lost(output), output)
        self.assertFalse(qualcomm.link_lost("Handshake failed: Expected Sahara HELLO"))
        self.assertIn("has to restart", qualcomm.LINK_LOST)
        self.assertIn("port or the cable", qualcomm.LINK_LOST)

    def test_a_failed_dump_goes_into_the_log_whole(self):
        folder = self.root / "backup"
        whole = "first error line\n" + "x" * 2000

        def fails(command: list[str], timeout: float = 60) -> str:  # noqa: ARG001  # the runner's signature
            # A headset's runner turns the tool's error into its own (raise ... from exc).
            cause = process.CommandFailed("edl-ng failed (1): " + whole[-800:], whole)
            error = ValueError(str(cause))
            error.__cause__ = cause
            raise error

        with self.assertRaises(ValueError):
            qualcomm.capture_backup(
                folder,
                [["edl", "rl", str(folder)]],
                {"serial": "A"},
                backend="bkerler",
                luns=None,
                include_userdata=False,
                runner=fails,
            )
        log = (folder / "edl.log").read_text()
        self.assertIn("first error line", log)
        self.assertIn("ValueError: edl-ng failed (1)", log)
        self.assertEqual(qualcomm.failed_output(ValueError("only text")), "only text")

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


SECTOR = 4096


def gpt(total: int) -> bytes:
    """Return a primary GPT of 6 sectors whose header names the backup header's position (the LUN's last sector)."""
    header = b"EFI PART" + bytes(24) + (total - 1).to_bytes(8, "little")
    return bytes(SECTOR) + header.ljust(SECTOR, b"\0") + bytes(4 * SECTOR)


class WriteBackTests(unittest.TestCase):
    """A two-LUN backup written back through a fake edl-ng that holds each LUN's bytes."""

    LUNS: ClassVar[dict[int, int]] = {1: 64, 0: 256}  # sectors, in the backup's order

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.backup = self.root / "backup"
        self.files: dict[str, dict] = {}
        self.lun(
            1,
            [
                ("PrimaryGPT", "gpt_main1.bin", "0", 6),
                ("BackupGPT", "gpt_backup1.bin", "NUM_DISK_SECTORS-5.", 5),
                ("xbl", "xbl.bin", "6", 8),
            ],
        )
        self.lun(
            0,
            [
                ("PrimaryGPT", "gpt_main0.bin", "0", 6),
                ("BackupGPT", "gpt_backup0.bin", "NUM_DISK_SECTORS-5.", 5),
                ("persist", "persist.bin", "6", 4),
                ("metadata", "metadata.bin", "10", 2),
                ("cache", "cache.bin", "12", 8),
                ("userdata", "userdata.bin", "20", 200),
            ],
            left_out=("cache", "userdata"),
        )
        self.manifest = {"luns": list(self.LUNS), "files": self.files}
        self.disk = {lun: bytearray(b"\xff" * sectors * SECTOR) for lun, sectors in self.LUNS.items()}
        self.commands: list[list[str]] = []

    def lun(self, lun: int, rows: list[tuple[str, str, str, int]], left_out: tuple[str, ...] = ()) -> None:
        folder = self.backup / f"lun{lun}"
        folder.mkdir(parents=True)
        xml = "".join(
            f'<program filename="{name}" label="{label}" start_sector="{start}" num_partition_sectors="{sectors}" '
            f'SECTOR_SIZE_IN_BYTES="{SECTOR}" physical_partition_number="{lun}" />'
            for label, name, start, sectors in rows
        )
        (folder / f"rawprogram{lun}.xml").write_text(f"<data>{xml}</data>")
        for label, name, _start, sectors in rows:
            if label in left_out:
                continue
            data = gpt(self.LUNS[lun]) if label == "PrimaryGPT" else bytes([len(label)]) * sectors * SECTOR
            (folder / name).write_bytes(data)
            self.files[f"lun{lun}/{name}"] = {"size": len(data)}

    def edl(self, argv: list, _timeout: float, *, corrupt: str = "") -> None:
        words = [str(a) for a in argv]
        self.commands.append(words)
        lun = int(words[words.index("--lun") + 1])
        disk = self.disk[lun]
        if "write-sector" in words:
            at = words.index("write-sector")
            start, data = int(words[at + 1]), Path(words[at + 2]).read_bytes()
            disk[start * SECTOR : start * SECTOR + len(data)] = data
        else:
            at = words.index("read-sector")
            start, count, path = int(words[at + 1]), int(words[at + 2]), Path(words[at + 3])
            data = bytes(disk[start * SECTOR : (start + count) * SECTOR])
            if corrupt and corrupt in str(self.commands[-2]):
                data = b"x" + data[1:]
            path.write_bytes(data)

    def test_the_plan_goes_lun_by_lun_in_map_order_and_wipes_instead_of_writing_data(self):
        plan = qualcomm.write_back_plan(self.backup, self.manifest)
        self.assertEqual(
            [(w.lun, w.label, w.start, w.sectors, w.image is None) for w in plan],
            [
                (1, "PrimaryGPT", 0, 6, False),
                (1, "BackupGPT", 64 - 5, 5, False),  # NUM_DISK_SECTORS-5. from the backup's own GPT
                (1, "xbl", 6, 8, False),  # the boot chain too
                (0, "PrimaryGPT", 0, 6, False),
                (0, "BackupGPT", 256 - 5, 5, False),
                (0, "persist", 6, 4, False),
                (0, "metadata", 10, 2, True),  # zeros, not the backup's metadata
                (0, "userdata", 20, min(200, qualcomm.WIPE_BYTES // SECTOR), True),
            ],
        )  # cache, which a backup leaves out, is not written

    def test_the_plan_refuses_a_missing_image_a_wrong_size_and_a_partition_outside_its_lun(self):
        (self.backup / "lun0/persist.bin").unlink()
        del self.files["lun0/persist.bin"]
        with self.assertRaisesRegex(ValueError, "no image of persist"):
            qualcomm.write_back_plan(self.backup, self.manifest)
        self.files["lun0/persist.bin"] = {"size": 3}
        with self.assertRaisesRegex(ValueError, "partition's size"):
            qualcomm.write_back_plan(self.backup, self.manifest)
        self.files["lun0/persist.bin"] = {"size": 4 * SECTOR}
        (self.backup / "lun1/gpt_main1.bin").write_bytes(gpt(10))  # a LUN too small for xbl
        with self.assertRaisesRegex(ValueError, "outside LUN 1"):
            qualcomm.write_back_plan(self.backup, self.manifest)

    def test_the_luns_own_sizes_come_from_the_loader_and_must_be_the_backups(self):
        lines = (
            '{"storage_info": {"total_blocks":256, "block_size":4096}}',
            "UFS Current LUN Number: = 0x0",
            '{"storage_info": {"total_blocks":256, "block_size":4096}}',
            "UFS Current LUN Number: = 0x0",  # the first answer per LUN counts
            '{"storage_info": {"total_blocks":64, "block_size":4096}}',
            "UFS Current LUN Number: = 0x1",
        )
        output = "\n".join(lines)
        device = qualcomm.storage_sectors(output)
        self.assertEqual(device, {0: 256, 1: 64})
        backup = {lun: qualcomm.backup_lun_sectors(self.backup, lun) for lun in self.LUNS}
        qualcomm.check_storage(backup, device)
        with self.assertRaisesRegex(ValueError, "LUNs \\[0\\], the backup"):
            qualcomm.check_storage(backup, {0: 256})
        with self.assertRaisesRegex(ValueError, "LUN 1: 128 sectors, the backup's 64"):
            qualcomm.check_storage(backup, {0: 256, 1: 128})

    def test_each_write_is_read_back_before_the_next(self):
        plan = qualcomm.write_back_plan(self.backup, self.manifest)
        with contextlib.redirect_stdout(io.StringIO()):
            qualcomm.write_back(plan, "edl-ng", "firehose.elf", self.root / "work", self.edl)
        self.assertEqual(len(self.commands), 2 * len(plan))
        for n, write in enumerate(plan):
            written, read = self.commands[2 * n], self.commands[2 * n + 1]
            self.assertIn("write-sector", written)
            self.assertEqual(written[written.index("--lun") + 1], str(write.lun))
            self.assertIn("read-sector", read)
            self.assertEqual(read[read.index("read-sector") + 2], str(write.sectors))
        disk = self.disk[0]
        self.assertEqual(disk[6 * SECTOR : 10 * SECTOR], (self.backup / "lun0/persist.bin").read_bytes())
        self.assertEqual(disk[10 * SECTOR : 12 * SECTOR], bytes(2 * SECTOR))  # metadata zeroed
        self.assertEqual(disk[12 * SECTOR : 20 * SECTOR], b"\xff" * 8 * SECTOR)  # cache left alone
        self.assertFalse((self.root / "work/readback.bin").exists())

    def test_a_readback_that_differs_stops_before_the_next_write(self):
        plan = qualcomm.write_back_plan(self.backup, self.manifest)
        quiet = contextlib.redirect_stdout(io.StringIO())
        with quiet, self.assertRaisesRegex(RuntimeError, "xbl \\(LUN 1\\) reads back other bytes.*stays in EDL mode"):
            qualcomm.write_back(
                plan, "edl-ng", "firehose.elf", self.root / "work", lambda a, t: self.edl(a, t, corrupt="xbl.bin")
            )
        self.assertEqual(len(self.commands), 6)  # the GPTs and xbl of LUN 1, nothing of LUN 0


if __name__ == "__main__":
    unittest.main()
