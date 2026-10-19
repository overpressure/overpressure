import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pico4 import device


class ProtocolTests(unittest.TestCase):
    def test_vectors(self):
        # 1234567 = 0x12d687, masked to 0x12d206; max ID masks to 0xf7f3f37f.
        for number, token in ((0, "pico0"), (1, "picoX"), (1234567, "picoXDHD0B"),
                              (0xFFFFFFFF, "picoMBM9M9BM")):
            self.assertEqual(device.unlock_token(number), token)

    def test_chip_input(self):
        self.assertEqual(device.chip_id(" 00123\r\n"), 123)
        self.assertEqual(device.chip_id("0xFFFFFFFF"), 0xFFFFFFFF)
        for value in ("-1", "4294967296", "123abc", "", "1; reboot", "+1"):
            with self.assertRaises(ValueError):
                device.chip_id(value)

    def test_unlock_is_fail_closed(self):
        self.assertTrue(device.unlocked("(bootloader) unlocked: yes"))
        self.assertTrue(device.unlocked("(bootloader) Device unlocked: true\r\n"))
        self.assertTrue(device.unlocked("(bootloader) Device critical unlocked: true", True))
        for output in ("", "FAILED (unknown command)", "unlocked: no", "unlocked: yes\nunlocked: no",
                       "Device critical unlocked: true", "unlocked: yesterday"):
            self.assertFalse(device.unlocked(output))

    def test_external_failures(self):
        for code, output in ((1, ""), (0, "FAILED (remote error)"), (0, "ERROR: missing partition")):
            with patch.object(device.subprocess, "run", return_value=subprocess.CompletedProcess([], code, "", output)):
                with self.assertRaises(ValueError):
                    device.run(["fake"])
        with patch.object(device.subprocess, "run", side_effect=subprocess.TimeoutExpired("fake", 60)):
            with self.assertRaises(subprocess.TimeoutExpired):
                device.run(["fake"])

    def test_device_selection(self):
        with patch.object(device, "run", return_value="List of devices attached\na device\nb unauthorized"):
            with self.assertRaises(ValueError):
                device.select_device("adb", None)
            with self.assertRaises(ValueError):
                device.select_device("adb", "b")
            self.assertEqual(device.select_device("adb", "a"), "a")


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.identity = self.root / "device.json"
        self.record = {"schema": 1, "serial": "ABC", "chip_id": 1234567,
                       "fingerprint": "Pico/Phoenix_ovs/PICOA8110:10/5.13.8/build:user/dev-keys",
                       "oem_state": "false"}
        self.identity.write_text(json.dumps(self.record))
        self.backup = self.root / "backup"
        self.backup.mkdir()
        (self.backup / "abl.bin").write_bytes(b"original stock ABL")
        self.manifest = {"schema": 1, "identity": self.record,
                         "files": {"abl.bin": device.digest(self.backup / "abl.bin")}}
        (self.backup / "manifest.json").write_text(json.dumps(self.manifest))
        self.loader = self.root / "loader.elf"
        self.loader.write_bytes(b"test loader")
        self.image = self.root / "engineering.elf"
        self.image.write_bytes(b"engineering")
        self.readback = self.root / "readback.bin"

    def args(self, command):
        args = [command, "--identity", str(self.identity), "--backup", str(self.backup)]
        if command != "unlock":
            args += ["--loader", str(self.loader)]
        if command in ("stage", "restore-abl"):
            args += ["--partition", "abl", "--readback", str(self.readback)]
        if command == "stage":
            args += ["--image", str(self.image), "--sha256", device.digest(self.image)["sha256"]]
        return args

    def call(self, args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return device.main(args)

    def test_previews_never_contact_tools(self):
        with patch.object(device, "run") as run:
            for command in ("backup", "stage", "unlock", "restore-abl"):
                self.assertEqual(self.call(self.args(command)), 0)
            run.assert_not_called()

    def test_python_edl_entrypoint_keeps_arguments_separate(self):
        script = self.root / "tool with spaces" / "edl.py"
        args = device.parser().parse_args(self.args("backup") + [
            "--edl-script", str(script), "--edl-python", "venv/python.exe"])
        command = device.edl_command(args, "rl", str(self.backup), "--genxml")
        # Relative tool paths become absolute: CreateProcess rejects "venv/python.exe".
        self.assertEqual(command[:3], [str(Path("venv/python.exe").resolve()), str(script.resolve()), "rl"])
        self.assertIn("--memory=ufs", command)

    def test_bare_edl_name_stays_a_path_lookup(self):
        self.assertEqual(device.executable("edl-ng"), "edl-ng")
        self.assertEqual(device.executable("cache/edl-ng/edl-ng.exe"), str(Path("cache/edl-ng/edl-ng.exe").resolve()))

    def test_native_backend_requires_explicit_luns_and_keeps_commands_read_only(self):
        base = self.args("backup") + ["--edl-backend", "edl-ng", "--edl", "native tool.exe"]
        self.assertEqual(self.call(base), 1)
        self.assertEqual(self.call(base + ["--edl-luns", "0", "0"]), 1)
        args = device.parser().parse_args(base + ["--edl-luns", "4", "0"])
        commands = device.backup_commands(args)
        self.assertEqual(len(commands), 2)
        for command, lun in zip(commands, (4, 0)):
            self.assertEqual(command[:3], ["native tool.exe", "--memory", "Ufs"])
            self.assertEqual(command[5], "dump-rawprogram")
            self.assertEqual(Path(command[6]), (self.backup / f"lun{lun}").resolve())
            self.assertEqual(command[7:], ["--lun", str(lun), "--skip", "userdata"])
        args.include_userdata = True
        self.assertNotIn("--skip", device.backup_commands(args)[0])
        self.assertEqual(device.edl_command(args, "r", "abl", "read.bin")[5:],
                         ["read-part", "abl", "read.bin"])
        self.assertEqual(device.edl_command(args, "w", "abl", "write.bin")[5:],
                         ["write-part", "abl", "write.bin"])

    def test_native_dump_rejects_missing_truncated_and_escaping_files(self):
        directory = self.backup / "lun4"
        directory.mkdir()
        xml = directory / "rawprogram4.xml"
        def map_file(name="abl.bin"):
            xml.write_text('<data><program filename="' + name + '" label="abl" '
                           'num_partition_sectors="1" SECTOR_SIZE_IN_BYTES="4096" '
                           'physical_partition_number="4" /></data>')
        map_file()
        with self.assertRaises(ValueError):
            device.validate_native_dump(self.backup, [4], True)
        (directory / "abl.bin").write_bytes(b"x" * 4095)
        with self.assertRaises(ValueError):
            device.validate_native_dump(self.backup, [4], True)
        (directory / "abl.bin").write_bytes(b"x" * 4096)
        device.validate_native_dump(self.backup, [4], True)
        map_file("../abl.bin")
        with self.assertRaises(ValueError):
            device.validate_native_dump(self.backup, [4], True)

    def test_backup_requires_recovery_files_and_does_not_overwrite(self):
        args = self.args("backup") + ["--execute", "--confirm-device", "ABC"]
        with patch.object(device, "run") as run:
            self.assertEqual(self.call(args), 1)
            run.assert_not_called()
        fresh = self.root / "fresh"
        args[args.index("--backup") + 1] = str(fresh)
        def fake(command, timeout=60):
            for name in ("abl.bin", "boot.bin", "devinfo.bin", "gpt_main0.bin", "rawprogram0.xml"):
                (fresh / name).write_bytes(b"test recovery data")
            return "OK"
        with patch.object(device, "run", side_effect=fake):
            self.assertEqual(self.call(args), 0)
        self.assertEqual(len(device.verified_backup(fresh)["files"]), 5)
        incomplete = self.root / "incomplete"
        args[args.index("--backup") + 1] = str(incomplete)
        with patch.object(device, "run", return_value="OK"):
            self.assertEqual(self.call(args), 1)
        self.assertFalse((incomplete / "manifest.json").exists())

    def test_stage_validates_image_partition_and_readback_before_write(self):
        base = self.args("stage") + ["--execute", "--confirm-device", "ABC"]
        with patch.object(device, "run") as run:
            for flag, value in (("--sha256", "0" * 64), ("--partition", "boot"),
                                ("--partition", "abl_a"), ("--readback", str(self.backup / "new.bin"))):
                args = base.copy()
                args[args.index(flag) + 1] = value
                self.assertEqual(self.call(args), 1)
            self.image.write_bytes(b"oversize" * 100)
            self.assertEqual(self.call(base), 1)
            run.assert_not_called()

    def test_tampered_backup_blocks_every_write(self):
        (self.backup / "abl.bin").write_bytes(b"tampered")
        with patch.object(device, "run") as run:
            for command in ("stage", "unlock", "restore-abl"):
                self.assertEqual(self.call(self.args(command) + ["--execute", "--confirm-device", "ABC"]), 1)
            run.assert_not_called()

    def test_manifest_cannot_escape_backup(self):
        self.manifest["files"] = {"../loader.elf": device.digest(self.loader)}
        (self.backup / "manifest.json").write_text(json.dumps(self.manifest))
        with self.assertRaises(ValueError):
            device.verified_backup(self.backup)

    def test_wrong_device_and_missing_wipe_ack(self):
        with patch.object(device, "run") as run:
            self.assertEqual(self.call(self.args("unlock") + ["--execute", "--confirm-device", "OTHER"]), 1)
            self.assertEqual(self.call(self.args("unlock") + ["--execute", "--confirm-device", "ABC"]), 1)
            run.assert_not_called()

    def test_unknown_firmware_blocks_execution(self):
        self.record["fingerprint"] = self.record["fingerprint"].replace("5.13.8", "5.14.0")
        self.identity.write_text(json.dumps(self.record))
        with patch.object(device, "run") as run:
            self.assertEqual(self.call(self.args("unlock")), 1)
            run.assert_not_called()

    def test_write_and_readback(self):
        commands = []
        def fake(command, timeout=60):
            commands.append(command)
            if command[1] == "r":
                Path(command[3]).write_bytes(self.image.read_bytes() + b"padding")
            return "OK"
        with patch.object(device, "run", side_effect=fake):
            self.assertEqual(self.call(self.args("stage") + ["--execute", "--confirm-device", "ABC"]), 0)
        self.assertEqual([c[1] for c in commands], ["w", "r"])
        self.assertEqual((self.backup / "abl.bin").read_bytes(), b"original stock ABL")

    def test_bad_readback_is_failure(self):
        def fake(command, timeout=60):
            if command[1] == "r":
                Path(command[3]).write_bytes(b"bad")
            return "OK"
        with patch.object(device, "run", side_effect=fake):
            self.assertEqual(self.call(self.args("stage") + ["--execute", "--confirm-device", "ABC"]), 1)

    def test_restore_uses_original(self):
        commands = []
        def fake(command, timeout=60):
            commands.append(command)
            if command[1] == "r":
                Path(command[3]).write_bytes((self.backup / "abl.bin").read_bytes())
            return "OK"
        with patch.object(device, "run", side_effect=fake):
            self.assertEqual(self.call(self.args("restore-abl") + ["--execute", "--confirm-device", "ABC"]), 0)
        self.assertEqual(Path(commands[0][3]), (self.backup / "abl.bin").resolve())

    def test_unlock_checks_after_reboot_and_stops_on_failure(self):
        commands = []
        def fake(command, timeout=60):
            commands.append(command)
            if command[-1] == "devices":
                return "ABC fastboot"
            if command[-1] == "device-info":
                return "Device unlocked: true\nDevice critical unlocked: true"
            return "OKAY"
        args = self.args("unlock") + ["--execute", "--confirm-device", "ABC", "--accept-data-loss"]
        with patch.object(device, "run", side_effect=fake):
            self.assertEqual(self.call(args), 0)
        # Real engineering ABL rejected persistent unlock writes until permissive
        # mode was enabled. Normal unlock must also precede critical unlock.
        self.assertEqual([command[3:] for command in commands[2:5]], [
            ["oem", "setenforce", "0"], ["flashing", "unlock"],
            ["flashing", "unlock_critical"],
        ])
        self.assertEqual(commands[-2][-2:], ["reboot", "bootloader"])
        self.assertEqual(commands[-1][-2:], ["oem", "device-info"])
        with patch.object(device, "run", side_effect=["ABC fastboot", ValueError("failure")]) as run:
            self.assertEqual(self.call(args), 1)
            self.assertEqual(run.call_count, 2)
        with patch.object(device, "run", side_effect=lambda c, timeout=60: "ABC fastboot" if c[-1] == "devices" else "Device unlocked: false"):
            self.assertEqual(self.call(args), 1)


if __name__ == "__main__":
    unittest.main()
