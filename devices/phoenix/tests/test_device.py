# SPDX-License-Identifier: Unlicense
"""The PICO 4's device tool: the unlock token, EDL backups, the ABL stage and restore, the unlock, against fakes."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from devices.phoenix.tooling import device, unlockfiles


class ProtocolTests(unittest.TestCase):
    def test_vectors(self):
        # 1234567 = 0x12d687, masked to 0x12d206; max ID masks to 0xf7f3f37f.
        for number, token in ((0, "pico0"), (1, "picoX"), (1234567, "picoXDHD0B"), (0xFFFFFFFF, "picoMBM9M9BM")):
            self.assertEqual(device.unlock_token(number), token)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.identity = self.root / "device.json"
        self.record: dict[str, Any] = {
            "schema": 1,
            "serial": "ABC",
            "chip_id": 1234567,
            "fingerprint": "Pico/Phoenix_ovs/PICOA8110:10/5.13.7/build:user/dev-keys",
            "oem_state": "true",
        }
        self.identity.write_text(json.dumps(self.record))
        self.backup = self.root / "backup"
        self.backup.mkdir()
        (self.backup / "abl.bin").write_bytes(b"original stock ABL")
        self.manifest = {
            "schema": 1,
            "identity": self.record,
            "files": {"abl.bin": device.digest(self.backup / "abl.bin")},
        }
        (self.backup / "manifest.json").write_text(json.dumps(self.manifest))
        self.loader = self.root / "loader.elf"
        self.loader.write_bytes(b"test loader")
        self.image = self.root / "engineering.elf"
        self.image.write_bytes(b"engineering")
        self.readback = self.root / "readback.bin"

    def args(self, command: str) -> list[str]:
        args = [command, "--identity", str(self.identity), "--backup", str(self.backup)]
        if command != "unlock":
            args += ["--loader", str(self.loader)]
        if command in ("stage", "restore-abl"):
            args += ["--partition", "abl", "--readback", str(self.readback)]
        if command == "stage":
            args += ["--image", str(self.image), "--sha256", device.digest(self.image)["sha256"]]
        return args

    def call(self, args: list[str]) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return device.main(args)

    def test_previews_never_contact_tools(self):
        with patch.object(device, "run") as run:
            for command in ("backup", "stage", "unlock", "restore-abl"):
                self.assertEqual(self.call(self.args(command)), 0)
            run.assert_not_called()

    def test_python_edl_entrypoint_keeps_arguments_separate(self):
        script = self.root / "tool with spaces" / "edl.py"
        args = device.parser().parse_args(
            [*self.args("backup"), "--edl-script", str(script), "--edl-python", "venv/python.exe"]
        )
        command = device.edl_command(args, "rl", str(self.backup), "--genxml")
        # Relative tool paths become absolute: CreateProcess rejects "venv/python.exe".
        self.assertEqual(command[:3], [str(Path("venv/python.exe").resolve()), str(script.resolve()), "rl"])
        self.assertIn("--memory=ufs", command)

    def test_native_backend_requires_explicit_luns_and_keeps_commands_read_only(self):
        base = [*self.args("backup"), "--edl-backend", "edl-ng", "--edl", "native tool.exe"]
        self.assertEqual(self.call(base), 1)
        self.assertEqual(self.call([*base, "--edl-luns", "0", "0"]), 1)
        args = device.parser().parse_args([*base, "--edl-luns", "4", "0"])
        commands = device.backup_commands(args)
        self.assertEqual(len(commands), 2)
        for command, lun in zip(commands, (4, 0)):
            self.assertEqual(command[:3], ["native tool.exe", "--memory", "Ufs"])
            self.assertEqual(command[5], "dump-rawprogram")
            self.assertEqual(Path(command[6]), (self.backup / f"lun{lun}").resolve())
            self.assertEqual(command[7:], ["--lun", str(lun), "--skip", "userdata"])
        args.include_userdata = True
        self.assertNotIn("--skip", device.backup_commands(args)[0])
        self.assertEqual(device.edl_command(args, "r", "abl", "read.bin")[5:], ["read-part", "abl", "read.bin"])
        self.assertEqual(device.edl_command(args, "w", "abl", "write.bin")[5:], ["write-part", "abl", "write.bin"])

    def test_backup_requires_recovery_files_and_does_not_overwrite(self):
        args = [*self.args("backup"), "--execute", "--confirm-device", "ABC"]
        with patch.object(device, "run") as run:
            self.assertEqual(self.call(args), 1)
            run.assert_not_called()
        fresh = self.root / "fresh"
        args[args.index("--backup") + 1] = str(fresh)

        def fake(command: list[str], timeout: float = 60) -> str:  # noqa: ARG001  # device.run's signature
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
        base = [*self.args("stage"), "--execute", "--confirm-device", "ABC"]
        with patch.object(device, "run") as run:
            for flag, value in (
                ("--sha256", "0" * 64),
                ("--partition", "boot"),
                ("--partition", "abl_a"),
                ("--readback", str(self.backup / "new.bin")),
            ):
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
                self.assertEqual(self.call([*self.args(command), "--execute", "--confirm-device", "ABC"]), 1)
            run.assert_not_called()

    def test_manifest_cannot_escape_backup(self):
        self.manifest["files"] = {"../loader.elf": device.digest(self.loader)}
        (self.backup / "manifest.json").write_text(json.dumps(self.manifest))
        with self.assertRaises(ValueError):
            device.verified_backup(self.backup)

    def test_wrong_device_and_missing_wipe_ack(self):
        with patch.object(device, "run") as run:
            self.assertEqual(self.call([*self.args("unlock"), "--execute", "--confirm-device", "OTHER"]), 1)
            self.assertEqual(self.call([*self.args("unlock"), "--execute", "--confirm-device", "ABC"]), 1)
            run.assert_not_called()

    def test_unknown_firmware_blocks_execution(self):
        self.record["fingerprint"] = self.record["fingerprint"].replace("5.13.7", "5.14.0")
        self.identity.write_text(json.dumps(self.record))
        with patch.object(device, "run") as run:
            self.assertEqual(self.call(self.args("unlock")), 1)
            run.assert_not_called()

    def test_the_global_seko_builds_with_the_verified_code_pass(self):
        for version, support in (
            ("5.13.7", "verified"),
            ("5.13.3", "the verified build's code before the kernel"),
            ("5.13.8", "the verified build's code before the kernel"),
        ):
            self.record["fingerprint"] = f"Pico/Phoenix_ovs/PICOA8110:10/{version}/build:user/dev-keys"
            self.identity.write_text(json.dumps(self.record))
            # The saved record stays as it is: backups compare it as a whole.
            self.assertEqual(device.read_identity(self.identity), self.record)
            self.assertEqual(device.firmware_support(self.record), support)

    def test_every_other_build_is_refused_with_its_reason(self):
        for product, version, state, reason in (
            ("Phoenix_ovs", "5.13.7", "false", "another key"),  # sek: another AVB key
            ("Phoenix_ovs", "5.13.8", "", "another key"),
            ("Phoenix", "5.13.7", "true", "China builds run another XBL and AOP"),
            ("phoenix", "5.13.7", "false", "China builds"),
            ("Phoenix_ovs", "5.13.9", "true", "has not been compared"),
            ("Phoenix_ovs", "5.12.0", "true", "has not been compared"),
        ):
            self.record["fingerprint"] = f"Pico/{product}/PICOA8110:10/{version}/build:user/dev-keys"
            self.record["oem_state"] = state
            self.identity.write_text(json.dumps(self.record))
            with self.subTest(product=product, version=version, state=state):
                with self.assertRaisesRegex(ValueError, reason):
                    device.read_identity(self.identity)
                with self.assertRaisesRegex(ValueError, "Nothing was changed"):
                    device.read_identity(self.identity)

    def test_other_pico_headsets_are_refused(self):
        for model in (
            "Pico/Phoenix_ovs/PICOA9210:10/5.13.7/x:user/dev-keys",  # another model number
            "Pico/Sparrow/PICOA8110:10/5.13.7/x:user/dev-keys",  # another codename
            "Pico/Hermes/A7H10:10/5.13.7/x:user/dev-keys",
        ):
            self.record["fingerprint"] = model
            self.identity.write_text(json.dumps(self.record))
            with self.assertRaisesRegex(ValueError, "original PICO 4"):
                device.read_identity(self.identity)

    def test_the_pico_4_pro_and_enterprise_are_refused(self):
        # They share the PICO 4's fingerprint; PICO OS tells them apart by model and memory.
        for model, ddr in (
            ("PICO 4 Pro", "DDR5"),
            ("PICO 4 Enterprise", "DDR5"),
            ("PICO 4 Pro", "DDR4"),
            ("PICO 4", "DDR5"),
        ):
            self.record.update(model=model, ddr_type=ddr)
            self.identity.write_text(json.dumps(self.record))
            with self.assertRaisesRegex(ValueError, "Pro and Enterprise are not verified"):
                device.read_identity(self.identity)
        # A probe that got no answer is refused too; the PICO 4 itself passes.
        self.record.update(model="", ddr_type="DDR4")
        self.identity.write_text(json.dumps(self.record))
        with self.assertRaisesRegex(ValueError, "as nothing"):
            device.read_identity(self.identity)
        self.record.update(model="PICO 4", ddr_type="DDR4")
        self.identity.write_text(json.dumps(self.record))
        self.assertEqual(device.read_identity(self.identity)["model"], "PICO 4")
        # An identity saved before the probe read them stays valid.
        del self.record["model"], self.record["ddr_type"]
        self.identity.write_text(json.dumps(self.record))
        device.read_identity(self.identity)

    def test_the_probe_records_model_and_memory(self):
        answers = {
            "ro.build.fingerprint": "Pico/Phoenix_ovs/PICOA8110:10/5.13.7/x:user/dev-keys",
            "ro.oem.state": "true",
            "pxr.vendorhw.product.model": "PICO 4",
            "ro.vendor.ddr_type": "DDR4",
        }

        def run(argv: list[str], *_: object, **__: object) -> str:
            return answers.get(argv[-1], "0x1234")

        output = self.identity.with_name("probed.json")
        with patch.object(device, "run", side_effect=run), patch.object(device, "select_device", return_value="ABC"):
            self.call(["probe", "--adb", "adb", "--output", str(output)])
        record = json.loads(output.read_text())
        self.assertEqual((record["model"], record["ddr_type"]), ("PICO 4", "DDR4"))

    def test_the_panel_decides(self):
        sharp = "console=x msm_drm.dsi_display0=qcom,mdss_dsi_sharp_ls026b3sa_90_video: pico4.part=recovery"
        self.assertEqual(device.check_panel(sharp), "sharp_ls026b3sa")
        with self.assertRaisesRegex(ValueError, "nt57900.*not verified"):
            device.check_panel("msm_drm.dsi_display0=qcom,mdss_dsi_innolux_nt57900_90_video:")
        with self.assertRaisesRegex(ValueError, "names no panel"):
            device.check_panel("console=x")

    def test_the_panel_in_pico_os(self):
        # PANEL_PROBE's output on PICO OS 5.13.7, where the shell may not read the command line.
        denied = "cat: /proc/cmdline: Permission denied\n"
        log = (
            "11-09 11:07:36.430   927  1623 I Dpps    : DppsFactoryImpSDM::GetLtmXmlParser():842 "
            "panel_name {}_90hz_video_mode_dsi_panel\n"
        )
        shown = 'Display 1234 (HWC display 0): port=129 pnpId=QCM displayName="{}"\n'
        sharp = denied + log.format("sharp_ls026b3sa") + shown.format("sharp ls026b3")
        self.assertEqual(device.check_panel_in_pico_os(sharp), "sharp_ls026b3sa")
        # The log has rotated past the boot: SurfaceFlinger's short name.
        self.assertEqual(device.check_panel_in_pico_os(denied + shown.format("sharp ls026b3")), "sharp_ls026b3sa")
        # The other panel, by either source.
        with self.assertRaisesRegex(ValueError, "nt57900_innolux is not verified"):
            device.check_panel_in_pico_os(denied + log.format("nt57900_innolux"))
        with self.assertRaisesRegex(ValueError, "innolux_nt57 is not verified"):
            device.check_panel_in_pico_os(denied + shown.format("innolux nt57"))
        # Nothing readable: refused.
        for output in (denied, denied + shown.format("")):
            with self.assertRaisesRegex(ValueError, "names no display panel"):
                device.check_panel_in_pico_os(output)
        # A readable command line decides, as before.
        cmdline = "msm_drm.dsi_display0=qcom,mdss_dsi_innolux_nt57900_90_video:\n" + shown.format("sharp ls026b3")
        with self.assertRaisesRegex(ValueError, "nt57900 is not verified"):
            device.check_panel_in_pico_os(cmdline)

    def test_the_unlock_files_are_checked_before_they_are_written(self):
        root = self.root

        class Settings:
            def path(self, name: str) -> Path:
                return root / "assets" / f"{name}.elf"

        with self.assertRaisesRegex(ValueError, "not the known file"):
            unlockfiles.fetch(Settings(), log=lambda _: None, read=lambda _: b"other")
        self.assertFalse((self.root / "assets" / "engineering_abl.elf").exists())
        self.assertEqual({f.setting for f in unlockfiles.FILES}, {"engineering_abl", "loader"})
        for item in unlockfiles.FILES:
            self.assertTrue(item.url.startswith("https://"))
            self.assertRegex(item.sha256, "^[0-9a-f]{64}$")

    def test_write_and_readback(self):
        commands = []

        def fake(command: list[str], timeout: float = 60) -> str:  # noqa: ARG001  # device.run's signature
            commands.append(command)
            if command[1] == "r":
                Path(command[3]).write_bytes(self.image.read_bytes() + b"padding")
            return "OK"

        with patch.object(device, "run", side_effect=fake):
            self.assertEqual(self.call([*self.args("stage"), "--execute", "--confirm-device", "ABC"]), 0)
        self.assertEqual([c[1] for c in commands], ["w", "r"])
        self.assertEqual((self.backup / "abl.bin").read_bytes(), b"original stock ABL")

    def test_bad_readback_is_failure(self):
        def fake(command: list[str], timeout: float = 60) -> str:  # noqa: ARG001  # device.run's signature
            if command[1] == "r":
                Path(command[3]).write_bytes(b"bad")
            return "OK"

        with patch.object(device, "run", side_effect=fake):
            self.assertEqual(self.call([*self.args("stage"), "--execute", "--confirm-device", "ABC"]), 1)

    def test_restore_uses_original(self):
        commands = []

        def fake(command: list[str], timeout: float = 60) -> str:  # noqa: ARG001  # device.run's signature
            commands.append(command)
            if command[1] == "r":
                Path(command[3]).write_bytes((self.backup / "abl.bin").read_bytes())
            return "OK"

        with patch.object(device, "run", side_effect=fake):
            self.assertEqual(self.call([*self.args("restore-abl"), "--execute", "--confirm-device", "ABC"]), 0)
        self.assertEqual(Path(commands[0][3]), (self.backup / "abl.bin").resolve())

    def test_unlock_checks_after_reboot_and_stops_on_failure(self):
        commands = []

        def fake(command: list[str], timeout: float = 60) -> str:  # noqa: ARG001  # device.run's signature
            commands.append(command)
            if command[-1] == "devices":
                return "ABC fastboot"
            if command[-1] == "device-info":
                return "Device unlocked: true\nDevice critical unlocked: true"
            return "OKAY"

        args = [*self.args("unlock"), "--execute", "--confirm-device", "ABC", "--accept-data-loss"]
        with patch.object(device, "run", side_effect=fake):
            self.assertEqual(self.call(args), 0)
        # Real engineering ABL rejected persistent unlock writes until permissive
        # mode was enabled. Normal unlock must also precede critical unlock.
        self.assertEqual(
            [command[3:] for command in commands[2:5]],
            [
                ["oem", "setenforce", "0"],
                ["flashing", "unlock"],
                ["flashing", "unlock_critical"],
            ],
        )
        self.assertEqual(commands[-2][-2:], ["reboot", "bootloader"])
        self.assertEqual(commands[-1][-2:], ["oem", "device-info"])
        with patch.object(device, "run", side_effect=["ABC fastboot", ValueError("failure")]) as run:
            self.assertEqual(self.call(args), 1)
            self.assertEqual(run.call_count, 2)

        # Unlocked before (a new install after the way back): the bootloader refuses the unlock as
        # done, as the PICO 4's does; the check after the reboot still decides.
        def unlocked_before(command: list[str], timeout: float = 60) -> str:  # noqa: ARG001  # device.run's signature
            if command[-2:] in (["flashing", "unlock"], ["flashing", "unlock_critical"]):
                msg = "Command failed (1): FAILED (remote: '\\tDevice already : unlocked!')"
                raise ValueError(msg)
            return fake(command)

        commands.clear()
        with patch.object(device, "run", side_effect=unlocked_before):
            self.assertEqual(self.call(args), 0)
        self.assertEqual(commands[-1][-2:], ["oem", "device-info"])
        # Any other refusal still stops it.
        with patch.object(
            device,
            "run",
            side_effect=lambda c, **_: (
                "ABC fastboot" if c[-1] == "devices" else (_ for _ in ()).throw(ValueError("FAILED (remote: 'locked')"))
            ),
        ):
            self.assertEqual(self.call(args), 1)
        with patch.object(
            device,
            "run",
            side_effect=lambda c, **_: "ABC fastboot" if c[-1] == "devices" else "Device unlocked: false",
        ):
            self.assertEqual(self.call(args), 1)


if __name__ == "__main__":
    unittest.main()
