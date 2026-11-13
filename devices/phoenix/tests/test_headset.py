# SPDX-License-Identifier: Unlicense
"""The PICO 4's host workflow: settings, the flash-and-run cycle and the development loop, against fake tools."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import IO, TYPE_CHECKING
from unittest.mock import patch

from devices.phoenix.tooling import cycle, devloop, localconfig
from overpressure import REPO, progress, qualcomm
from overpressure.localconfig import parse

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


def drain(stdin: int | IO[bytes] | None) -> bytes:
    """Read a fake child's stdin to its end, as the real one would: a file, or a pipe's read end."""
    if stdin is None:
        return b""
    if isinstance(stdin, int):
        return b"".join(iter(lambda: os.read(stdin, 1 << 20), b""))
    return stdin.read()


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class SettingsTests(unittest.TestCase):
    def test_parse_skips_comments_and_strips_quotes(self):
        values = parse(
            "# headset\nPICO4_ADB_SERIAL=ABC  # the ADB serial\n\n"
            "PICO4_EDL='C:/tools/edl ng/edl-ng.exe'\nPICO4_FIRMWARE=\"5.13.7-global-seko\"\n"
        )
        self.assertEqual(
            values,
            {
                "PICO4_ADB_SERIAL": "ABC",
                "PICO4_EDL": "C:/tools/edl ng/edl-ng.exe",
                "PICO4_FIRMWARE": "5.13.7-global-seko",
            },
        )
        with self.assertRaisesRegex(ValueError, "line 1"):
            parse("no assignment\n")

    def test_environment_overrides_the_file_and_paths_are_rooted(self):
        with tempfile.TemporaryDirectory() as temp:
            env = Path(temp) / ".env"
            env.write_text("PICO4_ADB_SERIAL=FILE\nPICO4_BACKUP=backup/stock-1\n", encoding="utf-8")
            settings = localconfig.load(env, {"PICO4_ADB_SERIAL": "ENV"})
        self.assertEqual(settings.adb_serial, "ENV")
        self.assertEqual(settings.path("backup"), REPO / "backup/stock-1")
        self.assertEqual(settings.tool("adb"), "adb")
        settings.platform_tools = "/opt/platform-tools"
        self.assertEqual(Path(settings.tool("fastboot")).name.split(".")[0], "fastboot")
        with self.assertRaisesRegex(ValueError, "PICO4_EDL_SERIAL"):
            settings.require("adb_serial", "edl_serial")


class HeadsetFixture(unittest.TestCase):
    """A fake headset behind the commands the cycle and the dev loop run."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.folder = self.root / "evidence"
        self.folder.mkdir()
        backup = self.root / "backup"
        (backup / "lun4").mkdir(parents=True)
        self.boot = b"stock boot partition"
        (backup / "lun4/boot.bin").write_bytes(self.boot)
        (backup / "manifest.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "files": {
                        "lun4/boot.bin": {"size": len(self.boot), "sha256": hashlib.sha256(self.boot).hexdigest()}
                    },
                }
            )
        )
        (self.root / "device.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "serial": "ADB1",
                    "oem_state": "true",
                    "chip_id": 1234567,
                    "fingerprint": "Pico/Phoenix_ovs/PICOA8110:10/5.13.7/test:user/dev-keys",
                }
            )
        )
        self.settings = localconfig.Settings(
            adb_serial="ADB1",
            fastboot_serial="FB1",
            edl_serial="EDL1",
            identity=str(self.root / "device.json"),
            backup=str(backup),
            loader=str(self.root / "firehose.elf"),
            engineering_abl_sha256="ab" * 32,
            edl=str(self.root / "edl-ng"),
            firmware="test",
        )
        self.calls = []
        self.edl = 0
        self.readback = self.boot
        self.edl_output = "Serial Number: EDL1"
        self.edl_outputs: list[str] = []  # the next printgpt sessions' outputs, before edl_output
        self.restarting: list[int] = []  # the EDL count's next answers while a restart into EDL runs
        self.run_result = {"status": "passed", "boot_restore_required": False}
        self.fastboot_listed = True
        self.unlocked = "true"
        self.partitions = [("boot", len(self.boot)), ("abl", 1 << 20)]
        self.clock = FakeClock()
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def edl_count(self) -> int:
        return self.restarting.pop(0) if self.restarting else self.edl

    def texts(self):
        return [" ".join(c) for c in self.calls]

    def fake(  # noqa: C901  # one answer per command the steps run
        self,
        argv: Sequence[object],
        _timeout: float = 60,
        *,
        stdin: int | IO[bytes] | None = None,
        check: bool = True,  # noqa: ARG002  # process.run's keyword
        log: Path | None = None,
    ) -> str:
        drain(stdin)  # a writer feeding the child waits for it to read
        words = [str(a) for a in argv]
        self.calls.append(words)
        text = " ".join(words)
        output = ""
        if "reboot edl" in text:
            self.edl = 1
        elif "reset --mode Edl" in text:
            self.restarting = [1, 1, 1, 0, 0]  # still there a while, then gone, then back (as the PICO 4)
        elif "getprop sys.boot_completed" in text:
            output = "1\nPico/Phoenix_ovs/PICOA8110:10/5.13.7/test:user/dev-keys"
        elif "getprop sys.pxr.init.status" in text:
            output = "1\n"  # PICO OS is through with the MCU
        elif "hwtest flash-run" in text:
            run = Path(words[words.index("--out") + 1])
            run.mkdir()
            (run / "result.json").write_text(json.dumps(self.run_result))
        elif "read-part boot" in text:
            Path(words[words.index("boot") + 1]).write_bytes(self.readback)
            output = self.edl_output
        elif "printgpt" in text:
            output = self.edl_outputs.pop(0) if self.edl_outputs else self.edl_output
        elif "oem device-info" in text:
            output = f"(bootloader) Device unlocked: {self.unlocked}"
        elif "getvar all" in text:
            output = "\n".join(f"(bootloader) partition-size:{name}: {hex(size)}" for name, size in self.partitions)
        elif words[0].endswith("fastboot") and words[-1] == "devices":
            output = "FB1\tfastboot" if self.fastboot_listed else ""
        if log is not None:
            Path(log).write_text(output)
        return output

    def steps(self):
        names = []
        for words in self.calls:
            text = " ".join(words)
            for marker, name in (
                ("getprop sys.pxr.init.status", "mcu"),
                ("reboot edl", "adb-edl"),
                ("printgpt", "identify"),
                ("device stage", "stage"),
                ("reset", "edl-reset"),
                ("hwtest flash-run", "flash-run"),
                ("read-part boot", "readback"),
                ("device restore-abl", "restore-abl"),
                ("getprop sys.boot_completed", "pico-os"),
                (" flash boot ", "flash-boot"),
            ):
                if marker in text:
                    names.append(name)
        return names


class CycleTests(HeadsetFixture):
    def make(self):
        return cycle.Cycle(self.settings, self.folder, self.fake, self.clock, self.edl_count)

    def run_cycle(self, c: cycle.Cycle) -> int:
        with patch.object(cycle, "ROOT", self.root):
            (self.root / "out").mkdir(exist_ok=True)
            (self.root / "out/pico4-stage1-test.img").write_bytes(b"image")
            c.stage_abl()
            self.edl = 0
            code = c.flash_run()
            c.restore_abl()
        return code

    def test_full_cycle_order_ends_on_stock(self):
        self.assertEqual(self.run_cycle(self.make()), 0)
        self.assertEqual(
            self.steps(),
            [
                "mcu",
                "adb-edl",
                "identify",
                "stage",
                "edl-reset",
                "pico-os",
                "flash-run",
                "pico-os",
                "mcu",
                "adb-edl",
                "identify",
                "readback",
                "restore-abl",
                "edl-reset",
                "pico-os",
            ],
        )
        stage = next(w for w in self.calls if "stage" in w)
        self.assertEqual(stage[stage.index("--confirm-device") + 1], "ADB1")
        run = next(w for w in self.calls if "flash-run" in w)
        self.assertEqual(run[run.index("--fastboot-serial") + 1], "FB1")

    def test_boot_that_differs_from_the_backup_keeps_the_engineering_abl(self):
        self.readback = b"something else"
        with self.assertRaisesRegex(RuntimeError, "differs from boot.bin"):
            self.run_cycle(self.make())
        self.assertNotIn("restore-abl", self.steps())

    def test_a_loader_left_running_is_restarted_into_a_fresh_edl_session_to_identify_it(self):
        # Linux: the backup's Firehose loader still runs; edl-ng's session then names no serial.
        self.edl_outputs = ["[INFO]  Detected device mode: Firehose\n--- Partitions LUN 0 ---"]
        c = self.make()
        self.edl = 1  # in EDL already, from the backup
        c.stage_abl()
        self.assertEqual(self.steps()[:4], ["identify", "edl-reset", "identify", "stage"])
        fresh = next(w for w in self.calls if "reset" in w)
        self.assertEqual(fresh[fresh.index("--mode") + 1], "Edl")

    def test_the_pipe_is_read_after_each_power_command(self):
        # Linux: the loader carries out a reset only once its last message is read.
        self.edl_outputs = ["[INFO]  Detected device mode: Firehose"]
        c = self.make()
        self.edl = 1
        reads: list[str] = []
        c.usb_reader = lambda: reads.append(self.steps()[-1]) is None  # read once; the device then left
        c.stage_abl()
        self.assertEqual(reads, ["edl-reset", "edl-reset"])  # after the fresh-session restart and the final reset
        self.assertEqual(self.steps(), ["identify", "edl-reset", "identify", "stage", "edl-reset", "pico-os"])

    def test_a_headset_that_does_not_restart_is_not_identified_in_its_old_session(self):
        self.edl_outputs = ["[INFO]  Detected device mode: Firehose"]
        c = self.make()
        c.edl_count = lambda: 1  # the restart command is sent, but the device never leaves USB
        with self.assertRaisesRegex(RuntimeError, "did not restart"):
            c.stage_abl()
        self.assertEqual(self.steps(), ["identify", "edl-reset"])  # no second session with the old loader

    def test_a_fresh_session_of_another_headset_is_still_refused(self):
        self.edl_outputs = ["[INFO]  Detected device mode: Firehose"]
        self.edl_output = "Serial Number: OTHER"
        c = self.make()
        self.edl = 1
        with self.assertRaisesRegex(RuntimeError, "not the configured headset"):
            c.stage_abl()
        self.assertNotIn("stage", self.steps())

    def test_another_edl_device_is_never_written(self):
        self.edl_output = "Serial Number: OTHER"
        with self.assertRaisesRegex(RuntimeError, "not the configured headset"):
            self.run_cycle(self.make())
        self.assertEqual(self.steps(), ["mcu", "adb-edl", "identify"])

    def test_pending_boot_restore_flashes_the_pinned_stock_image(self):
        stock = self.root / "stock-boot.img"
        stock.write_bytes(self.boot)
        self.run_result = {
            "status": "failed",
            "boot_restore_required": True,
            "stock_boot": {"path": str(stock), "size": len(self.boot), "sha256": hashlib.sha256(self.boot).hexdigest()},
            "manual_restore_commands": [
                ["fastboot", "-s", "FB1", "flash", "boot", str(stock)],
                ["fastboot", "-s", "FB1", "reboot"],
            ],
        }
        self.run_cycle(self.make())
        self.assertIn(["fastboot", "-s", "FB1", "flash", "boot", str(stock)], self.calls)
        self.assertEqual(self.steps()[-1], "pico-os")

    def test_changed_restore_image_is_never_flashed(self):
        stock = self.root / "stock-boot.img"
        stock.write_bytes(b"tampered")
        self.run_result = {
            "status": "failed",
            "boot_restore_required": True,
            "stock_boot": {"path": str(stock), "size": len(self.boot), "sha256": hashlib.sha256(self.boot).hexdigest()},
            "manual_restore_commands": [["fastboot", "-s", "FB1", "flash", "boot", str(stock)]],
        }
        with self.assertRaisesRegex(RuntimeError, "changed"):
            self.run_cycle(self.make())
        self.assertNotIn(["fastboot", "-s", "FB1", "flash", "boot", str(stock)], self.calls)

    def test_headset_that_stays_off_after_the_final_reset_needs_a_hard_reset(self):
        c = self.make()
        fake = self.fake

        def off_after_reset(
            argv: Sequence[object],
            timeout: float = 60,
            *,
            stdin: int | IO[bytes] | None = None,
            check: bool = True,
            log: Path | None = None,
        ) -> str:
            text = " ".join(str(a) for a in argv)
            if "reset --mode Reset" in text:
                self.edl = 0
            if "getprop sys.boot_completed" in text and Path(self.folder / "reset-final.log").exists():
                self.calls.append([str(a) for a in argv])
                msg = "adb: no devices/emulators found"
                raise RuntimeError(msg)
            return fake(argv, timeout, stdin=stdin, check=check, log=log)

        c.run = off_after_reset
        with self.assertRaisesRegex(RuntimeError, r"no ADB or EDL device\): hard reset needed"):
            self.run_cycle(c)
        resets = [t for t in self.texts() if " reset " in t]
        self.assertEqual(len(resets), 2)
        self.assertTrue(all("--mode Reset" in t for t in resets))
        self.assertEqual(self.steps()[-1], "pico-os")

    def test_missing_edl_device_times_out(self):
        c = cycle.Cycle(self.settings, self.folder, self.fake, self.clock, lambda: 0)
        with self.assertRaisesRegex(RuntimeError, "EDL"):
            c.enter_edl()

    def test_a_stale_edl_session_fails_in_plain_words(self):
        def stale(argv: Sequence[object], _timeout: float = 60, *, log: Path | None = None, **_kwargs: object) -> str:
            # edl-ng's output goes to its log; process.run's error then only names the log.
            assert log is not None
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(
                "Passive read timed out\nCannot proceed: Device mode is Unknown or could not be determined.\n"
            )
            msg = f"{Path(str(argv[0])).name} failed (1); see {log.name}"
            raise RuntimeError(msg)

        c = cycle.Cycle(self.settings, self.folder, stale, self.clock, lambda: 1)
        with self.assertRaises(RuntimeError) as caught:
            c.edl("read-part", "boot", "boot.bin", log="boot-readback")
        self.assertIn(qualcomm.NO_ANSWER, str(caught.exception))
        self.assertIn("power button", str(caught.exception))

        def broken(_argv: Sequence[object], _timeout: float = 60, **_kwargs: object) -> str:
            msg = "edl-ng failed (2); see other.log"
            raise RuntimeError(msg)

        c = cycle.Cycle(self.settings, self.folder, broken, self.clock, lambda: 1)
        with self.assertRaisesRegex(RuntimeError, r"failed \(2\)"):  # any other failure stays as it was
            c.edl("read-part", "boot", "boot.bin", log="other")


class DevTests(HeadsetFixture):
    def make(self, responses: dict[str, str | Callable[[], str]] | None = None) -> devloop.Dev:
        responses = responses or {}

        def fake(
            argv: Sequence[object],
            timeout: float = 60,
            *,
            stdin: IO[bytes] | None = None,
            check: bool = True,
            log: Path | None = None,
        ) -> str:
            text = " ".join(str(a) for a in argv)
            for marker, reply in responses.items():
                if marker in text:
                    drain(stdin)
                    self.calls.append([str(a) for a in argv])
                    return reply if isinstance(reply, str) else reply()
            return self.fake(argv, timeout, stdin=stdin, check=check, log=log)

        return devloop.Dev(self.settings, self.root / "dev", fake, self.clock)

    def test_check_knows_the_boot_image_whose_kernel_runs(self):
        valve = {"kernel": "Linux version 7.2.0-pico4 (overpressure@pico4) # SMP PREEMPT", "boot": "direct"}
        stage1 = {"kernel": "Linux version 7.2.0-pico4 (overpressure@pico4) # SMP PREEMPT", "stock": "5.13.7"}
        (self.root / "kernel").mkdir()
        (self.root / "kernel" / "a-test-recovery.img.json").write_text(json.dumps({"build_info": stage1}))
        (self.root / "kernel" / "valve-boot.img.json").write_text(json.dumps({"build_info": valve}))
        running = {"release": "7.2.0-pico4"}
        dev = self.make({"uname -r": lambda: running["release"] + "\n"})
        # A stage-1 test image's record is not the installed system's boot.
        self.assertEqual(dev.boot_build_info(), valve)
        # An unknown kernel has only its release, which the check compares.
        running["release"] = "9.0"
        self.assertEqual(dev.boot_build_info(), {"kernel": "Linux version 9.0 "})

    def test_every_command_renews_the_host_contact(self):
        argv = self.make().ssh_argv("pico4-valve-session-check --seconds 30")
        self.assertEqual(
            argv[-1],
            "cut -d. -f1 /proc/uptime > /run/pico4/host-contact 2>/dev/null; pico4-valve-session-check --seconds 30",
        )

    def test_login_requires_the_dev_image_on_this_headset(self):
        self.make({"cat /etc/pico4/test-run": "dev-loop\n1234567"}).authenticate()
        with self.assertRaisesRegex(RuntimeError, "not this headset"):
            self.make({"cat /etc/pico4/test-run": "other-run\n1234567"}).authenticate()
        with self.assertRaisesRegex(RuntimeError, "not this headset"):
            self.make({"cat /etc/pico4/test-run": "dev-loop\n7654321"}).authenticate()

    def test_flash_needs_an_unlocked_single_boot_partition_of_the_backup_size(self):
        image = self.root / "dev/dev-boot.img"
        for unlocked, partitions, message in (
            ("false", [("boot", len(self.boot))], "locked"),
            ("true", [("boot_a", len(self.boot)), ("boot_b", len(self.boot))], "non-slotted"),
            ("true", [("boot", 4096)], "size"),
        ):
            self.calls.clear()
            self.unlocked, self.partitions = unlocked, partitions
            dev = self.make()
            image.write_bytes(b"dev image")
            with self.assertRaisesRegex(RuntimeError, message):
                dev.flash_boot(image)
            self.assertNotIn("flash-boot", self.steps())

    def test_only_a_stage_1_on_the_stock_kernel_gets_the_windows_ncm_initialization(self):
        dev = self.make()
        record = self.root / "dev/dev-image.json"
        record.parent.mkdir(exist_ok=True)
        with patch.object(type(dev.s), "ncm", lambda _self: Path("ncm-python")):
            self.assertEqual(dev.stage1_ncm(), Path("ncm-python"))  # no record: an older dev image, stock kernel
            record.write_text(json.dumps({"recovery_kernel": "valve"}))
            self.assertIsNone(dev.stage1_ncm())  # Windows 11's own driver; the initialization disturbs it

    def test_recovery_gets_the_same_checks_and_is_started(self):
        backup = Path(self.settings.backup)
        (backup / "lun0").mkdir()
        recovery = b"stock recovery partition"
        (backup / "lun0/recovery.bin").write_bytes(recovery)
        manifest = json.loads((backup / "manifest.json").read_text())
        manifest["files"]["lun0/recovery.bin"] = {"size": len(recovery), "sha256": hashlib.sha256(recovery).hexdigest()}
        (backup / "manifest.json").write_text(json.dumps(manifest))
        self.partitions = [("boot", len(self.boot)), ("recovery", len(recovery))]
        image = self.root / "dev/dev-recovery.img"
        image.parent.mkdir(exist_ok=True)
        image.write_bytes(b"dev image")
        self.make().flash()
        flash = self.calls.index(["fastboot", "-s", "FB1", "flash", "recovery", str(image)])
        self.assertIn("unlock", self.texts()[flash - 1])
        self.assertEqual(self.calls[flash + 1], ["fastboot", "-s", "FB1", "reboot", "recovery"])
        # The backup's size counts, not the boot partition's.
        self.calls.clear()
        self.partitions = [("boot", len(self.boot)), ("recovery", 4096)]
        with self.assertRaisesRegex(RuntimeError, "recovery partition"):
            self.make().flash()
        self.assertFalse(any(" flash recovery " in t for t in self.texts()))
        # Any other partition is refused before fastboot is asked anything.
        with self.assertRaisesRegex(ValueError, "not a boot image partition"):
            self.make().check_boot_partition(image, "super")

    def test_connect_reads_back_the_partition_that_started(self):
        dev = self.make(
            {
                "cat /etc/pico4/test-run": "dev-loop\n1234567",
                "cat /proc/cmdline": "console=x pico4.part=recovery",
                "pico4-install hash": "e" * 64,
            }
        )
        (self.root / "dev/dev-recovery.img").write_bytes(b"dev image")
        (self.root / "dev/dev-image.json").write_text(
            json.dumps({"boot_sha256": "f" * 64, "recovery_sha256": "e" * 64})
        )
        dev.connect()
        self.assertTrue(any("pico4-install hash recovery " in t for t in self.texts()))
        self.assertFalse(any("pico4-install hash boot " in t for t in self.texts()))

    def install_setup(
        self,
        cmdline: str = "console=x pico4.part=recovery",
        data: str = "none",
        held: Sequence[str] | None = None,
        confirmed_boot: bytes = b"install boot",
        battery: str = "80\nDischarging\n",
    ) -> tuple[devloop.Dev, Path]:
        backup = Path(self.settings.backup)
        (backup / "lun0").mkdir(exist_ok=True)
        rows = "".join(
            f'<program label="{n}" num_partition_sectors="{s}" SECTOR_SIZE_IN_BYTES="4096" '
            f'physical_partition_number="0" filename="{n}.bin"/>'
            for n, s in (("boot", 24576), ("super", 2097152), ("userdata", 27804115))
        )
        (backup / "lun0/rawprogram0.xml").write_text(f"<data>{rows}</data>")
        dev_dir = self.root / "dev"
        dev_dir.mkdir(exist_ok=True)
        (dev_dir / "id_ed25519.pub").write_bytes(b"ssh-ed25519 PC\n")
        kernel_dir = self.root / "kernel"
        kernel_dir.mkdir(exist_ok=True)
        (kernel_dir / "valve-boot.img").write_bytes(b"install boot")
        system = self.root / "pico4-system.img"
        system.write_bytes(b"system image")
        boot_sha = hashlib.sha256(confirmed_boot).hexdigest()
        system_sha = hashlib.sha256(b"system image").hexdigest()
        # PICO OS's files from the backup (stockfiles.from_backup reads a real backup's partitions).
        stock = {"schema": 1, "firmware": "5.13.7", "files": {}}

        def from_backup(_backup: Path, archive: Path, *, log: Callable[[str], None] = print) -> dict:  # noqa: ARG001  # its signature
            archive.parent.mkdir(parents=True, exist_ok=True)
            archive.write_bytes(b"stock archive")
            return stock

        patcher = patch.object(devloop.stockfiles, "from_backup", from_backup)
        patcher.start()
        self.addCleanup(patcher.stop)
        stock_sha = devloop.stockfiles.manifest_sha256(stock)
        responses = {
            f"pico4-install hash {name} ": sha
            for name, sha in (("boot", boot_sha), ("super", system_sha))
            if name in (held or ())
        }
        dev = self.make(
            {
                **responses,
                f"cat {devloop.BATTERY}/capacity": battery,
                "cat /etc/pico4/test-run": "dev-loop\n1234567",
                "cat /proc/cmdline": cmdline + " msm_drm.dsi_display0=qcom,mdss_dsi_sharp_ls026b3sa_90_video:",
                "pico4-install write boot": f"written boot {boot_sha}",
                "pico4-install write super": f"written super {system_sha}",
                "pico4-install status": f"bootmode: boot-recovery\nsystem: pico4-system\ndata: {data}",
                "pico4-install format-data": "formatted userdata",
                "pico4-install keys": "keys 1",
                "pico4-install stock-hash": stock_sha if "stock" in (held or ()) else "none",
                "pico4-install stock --": f"stock {stock_sha}",
                "pico4-install bootmode normal": "bootmode normal",
                "cat /proc/1/comm /etc/pico4/install-id": "systemd\ndev-loop\n1234567",
            }
        )
        self.fastboot_listed = False  # stage 1 runs: the headset is not in fastboot
        return dev, system

    def test_install_writes_boot_and_super_formats_new_data_and_starts_it(self):
        dev, system = self.install_setup()
        dev.install(system)
        # The writes and the reboot; pico4-install hash only reads.
        steps = [
            t
            for t in self.texts()
            if ("pico4-install" in t and "pico4-install hash" not in t) or t.endswith("pico4-reboot")
        ]
        self.assertEqual(
            [s.split("; ")[-1].split(" --")[0] for s in steps],
            [
                "pico4-install write boot",
                "pico4-install write super",
                "pico4-install status",
                "pico4-install format-data",
                "pico4-install keys",
                "pico4-install stock-hash",
                "pico4-install stock",
                "pico4-install bootmode normal",
                "pico4-reboot",
            ],
        )
        # PICO OS's files from the backup go to the data partition: the archive's size and hash.
        self.assertIn(f"--bytes 13 --sha256 {hashlib.sha256(b'stock archive').hexdigest()}", steps[6])
        # Sizes from the backup's partition maps, not from the headset.
        self.assertIn("--size 100663296 --bytes 12 ", steps[0])
        self.assertIn(f"--size {2097152 * 4096} ", steps[1])
        self.assertIn(f"--size {27804115 * 4096}", steps[3])
        # The keys and the install ID go to the data partition, never into an image.
        self.assertIn(f"pico4-install keys --size {27804115 * 4096} --id dev-loop", steps[4])

    def test_install_reports_its_progress_the_writes_first(self):
        dev, system = self.install_setup()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            dev.install(system)
        shown = [found for found in map(progress.parse, out.getvalue().splitlines()) if found is not None]
        writes = [f for f, text in shown if text.startswith("writing ") and f is not None]
        self.assertEqual(writes, sorted(writes))  # boot, then super, on one meter
        self.assertAlmostEqual(writes[-1], devloop.INSTALL_WRITES)
        self.assertEqual(
            [text for _, text in shown if not text.startswith("writing ")],
            [
                "formatting userdata",
                "adding this computer's key",
                "PICO OS's files from the backup",
                "starting the installed system",
            ],
        )

    def test_install_refuses_keys_the_headset_did_not_confirm(self):
        dev, system = self.install_setup()
        real = dev.ssh
        dev.ssh = lambda command, *a, **k: "keys 0" if "pico4-install keys" in command else real(command, *a, **k)
        with self.assertRaisesRegex(RuntimeError, "did not confirm the keys"):
            dev.install(system)
        self.assertFalse(any("bootmode normal" in t for t in self.texts()))

    def test_install_refuses_another_panel_before_any_write(self):
        dev, system = self.install_setup()
        reads = {
            "cat /proc/cmdline": "console=x msm_drm.dsi_display0=qcom,mdss_dsi_innolux_nt57900_90_video:",
            "cat /proc/1/comm": "pico4-init",  # which system runs: stage 1
        }
        dev.ssh = lambda command, *_, **__: (
            reads[command] if command in reads else self.fail("wrote before reading the panel: " + command)
        )
        _real_connect, dev.connect = dev.connect, lambda: "recovery"
        with self.assertRaisesRegex(ValueError, "nt57900.*not verified"):
            dev.install(system)

    def test_a_repeated_install_on_the_running_system_goes_back_to_stage_1_first(self):
        # The last install got as far as starting the system; repeating it needs stage 1 again.
        dev, system = self.install_setup()
        order: list[str] = []
        dev.installed_running = lambda: order.append("probe") is None  # the installed system answers
        dev.recovery = lambda: order.append("recovery")
        dev.install(system)
        self.assertEqual(order, ["probe", "recovery"])
        self.assertTrue(any("pico4-install write boot" in t for t in self.texts()))

    def test_a_repeated_install_from_fastboot_goes_into_stage_1_first(self):
        # A repeat that stopped in fastboot on its way to stage 1.
        dev, system = self.install_setup()
        self.fastboot_listed = True
        order: list[str] = []
        dev.installed_running = lambda: order.append("probe") is None
        dev.recovery = lambda: order.append("recovery")
        dev.install(system)
        self.assertEqual(order, ["recovery"])  # no SSH probe of a headset in fastboot

    def test_linux_stops_the_adb_server_before_it_looks_for_fastboot(self):
        # The adb server can keep hold of the headset in fastboot: fastboot then does not list it.
        dev = self.make()
        with patch.object(sys, "platform", "linux"):
            dev.to_fastboot()
        texts = [t for t in self.texts() if "kill-server" in t or t.endswith("fastboot devices")]
        self.assertTrue(texts[0].endswith("adb kill-server"), texts)

    def test_from_the_installed_system_fastboot_looks_only_without_an_adb_server(self):
        # Looking for PICO OS (adb devices) starts the server again; on Linux it then holds the
        # headset in fastboot, and the wait for fastboot never sees it.
        self.fastboot_listed = False

        def into_fastboot() -> str:
            self.fastboot_listed = True
            return ""

        dev = self.make({"--reboot-argument=bootloader": into_fastboot})
        with patch.object(sys, "platform", "linux"):
            dev.to_fastboot()
        server = False
        for text in self.texts():
            if text.endswith("adb kill-server"):
                server = False
            elif text.split(" ")[0].endswith("adb"):
                server = True
            elif text.endswith("fastboot devices"):
                self.assertFalse(server, self.texts())
        self.assertTrue(self.fastboot_listed)

    def test_which_system_runs_by_its_first_process(self):
        self.assertTrue(self.make({"cat /proc/1/comm": "systemd\n"}).installed_running())
        self.assertFalse(self.make({"cat /proc/1/comm": "pico4-init\n"}).installed_running())

        def silent() -> str:
            msg = "ssh"
            raise subprocess.TimeoutExpired(msg, 10)

        self.assertFalse(self.make({"cat /proc/1/comm": silent}).installed_running())

    def test_a_restart_command_that_outlives_its_link_is_its_end(self):
        def vanished() -> str:
            msg = "ssh"
            raise subprocess.TimeoutExpired(msg, 15)  # Linux: the USB link went down with the restart

        dev = self.make({"pico4-reboot": vanished})
        dev.ssh_reboot("pico4-reboot")
        self.assertTrue(any("pico4-reboot" in t for t in self.texts()))

    def test_enrolled_keys_are_this_pcs_and_the_owners(self):
        dev, _ = self.install_setup()
        self.assertEqual(dev.enrolled_keys(), b"ssh-ed25519 PC\n")
        own = self.root / "own.pub"
        own.write_bytes(b"ssh-ed25519 OWN me\n\n")
        dev.s.ssh_public_key = str(own)
        self.assertEqual(dev.enrolled_keys(), b"ssh-ed25519 PC\nssh-ed25519 OWN me\n")
        own.write_bytes(b"-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n")
        with self.assertRaisesRegex(ValueError, "no public key"):
            dev.enrolled_keys()

    def test_release_check_records_every_result_and_fails_on_one(self):
        dev, _ = self.install_setup()
        dev.check = lambda: 0
        answers = {
            "pico4-listeners": "sshd tcp/22",
            "pico4-still-gate 60": "3000 poses, 0 lost",
            "cat /usr/share/pico4/version": "version=1.0b1",
            "systemd-analyze time": "Startup finished in 1.942s (kernel) + 9.027s (userspace) = 10.970s\n"
            "graphical.target reached after 9.026s in userspace.\n",
        }

        def ssh(command: str, *_: object, **__: object) -> str:
            if command not in answers:
                raise RuntimeError("failed: " + command)
            return answers[command]

        dev.ssh = ssh
        self.assertEqual(dev.release_check(), 0)
        results = sorted((dev.work.parent / "release").glob("*/result.json"))
        record = json.loads(results[-1].read_text(encoding="utf-8"))
        self.assertEqual(record["acceptance"], "passed")
        self.assertEqual(record["still gate"]["output"], "3000 poses, 0 lost")
        self.assertEqual(record["version"], "version=1.0b1")
        self.assertEqual(record["boot time"]["status"], "passed")
        self.assertIn("graphical target 11.0 s", record["boot time"]["output"])
        del answers["pico4-listeners"]
        with patch("time.sleep"):
            self.assertEqual(dev.release_check(), 1)

    def test_the_suspend_check_runs_detached_through_the_link_loss(self):
        dev, _ = self.install_setup()
        dev.check = lambda: 0
        sent: list[str] = []
        reads = [RuntimeError("ssh.exe failed (255)"), "", '{"status": "passed", "suspended": true}']

        def ssh(command: str, *_: object, **__: object) -> str:
            sent.append(command)
            if command.startswith("cat /run/pico4-suspend-check.json"):
                answer = reads.pop(0)
                if isinstance(answer, Exception):
                    raise answer
                return answer
            return {
                "pico4-listeners": "sshd tcp/22",
                "pico4-still-gate 60": "3000 poses, 0 lost",
                "cat /usr/share/pico4/version": "version=1.0b1",
                "systemd-analyze time": "graphical.target reached after 9s in userspace.\n"
                "Startup finished in 1.9s (kernel) + 9s (userspace) = 10.9s\n",
            }.get(command, "")

        dev.ssh = ssh
        self.assertEqual(dev.release_check(attended=True), 0)
        start = next(c for c in sent if "pico4-suspend-check" in c and "systemd-run" in c)
        self.assertIn("rm -f /run/pico4-suspend-check.json;", start)
        self.assertIn("--no-block pico4-suspend-check", start)
        record = json.loads(max((dev.work.parent / "release").glob("*/result.json")).read_text(encoding="utf-8"))
        self.assertEqual(record["suspend with sound"]["status"], "passed")
        # A failed check, and none within the deadline, fail the release check.
        reads[:] = ['{"status": "failed", "failures": ["no tone after"]}']
        self.assertEqual(dev.release_check(attended=True), 1)
        reads[:] = [""] * 100
        self.assertEqual(dev.release_check(attended=True), 1)
        record = json.loads(max((dev.work.parent / "release").glob("*/result.json")).read_text(encoding="utf-8"))
        self.assertIn("within 240 s", record["suspend with sound"]["output"])

    def test_release_check_fails_a_still_gate_that_lost_the_headset(self):
        dev, _ = self.install_setup()
        dev.check = lambda: 0
        answers = {
            "pico4-listeners": "sshd tcp/22",
            "pico4-still-gate 60": "54577 poses, 54577 lost, furthest 0.00 mm, 1.149 deg, longest gap 1.0 ms",
            devloop.SESSION_AGE: "600",
            "cat /usr/share/pico4/version": "version=1.0b1",
            "systemd-analyze time": "Startup finished in 1.9s (kernel) + 9s (userspace) = 10.9s\n"
            "graphical.target reached after 9s in userspace.\n",
        }
        dev.ssh = lambda command, *_, **__: answers[command]
        self.assertEqual(dev.release_check(), 1)
        record = json.loads(max((dev.work.parent / "release").glob("*/result.json")).read_text(encoding="utf-8"))
        self.assertEqual(record["still gate"]["status"], "failed")
        self.assertIn("lost the headset", record["still gate"]["output"])
        self.assertFalse(devloop.still_gate_lost("3000 poses, 0 lost, furthest 0.40 mm"))
        self.assertTrue(devloop.still_gate_lost("no pose"))

    def test_the_still_gate_waits_for_a_settled_session(self):
        dev, _ = self.install_setup()
        dev.check = lambda: 0
        order: list[str] = []
        start = dev.clock.monotonic()

        def ssh(command: str, *_: object, **__: object) -> str:
            order.append(command)
            return "45" if command == devloop.SESSION_AGE else ""

        dev.ssh = ssh
        dev.release_check()
        self.assertGreaterEqual(dev.clock.monotonic() - start, 75)  # the fake clock's waits add up
        self.assertLess(order.index(devloop.SESSION_AGE), order.index("pico4-still-gate 60"))
        start = dev.clock.monotonic()
        dev.ssh = lambda command, *_, **__: "600" if command == devloop.SESSION_AGE else ""
        dev.release_check()
        self.assertLess(dev.clock.monotonic() - start, 75)

    def test_release_check_fails_a_slow_boot(self):
        dev, _ = self.install_setup()
        dev.check = lambda: 0
        slow = "Startup finished in 2.1s (kernel) + 1min 2.5s (userspace) = 1min 4.6s\n"
        dev.ssh = lambda command, *_, **__: (
            slow + "graphical.target reached after 1min 2.4s in userspace.\n"
            if command == "systemd-analyze time"
            else ""
        )
        with patch("time.sleep"):
            self.assertEqual(dev.release_check(), 1)
        record = json.loads(max((dev.work.parent / "release").glob("*/result.json")).read_text(encoding="utf-8"))
        self.assertEqual(record["boot time"]["status"], "failed")
        self.assertIn("64.5 s", record["boot time"]["output"])
        self.assertAlmostEqual(
            devloop.graphical_seconds("in 823ms (kernel) + x\ngraphical.target reached after 9.5s in userspace."),
            10.323,
        )
        # Still starting: no graphical target yet.
        with self.assertRaisesRegex(RuntimeError, "no graphical target"):
            devloop.graphical_seconds("Startup finished in 1.9s (kernel) + 3s (userspace)")

    def test_install_takes_another_boot_image(self):
        # Such as a boot image of another kernel build (recovery_image.py --direct --boot).
        dev, system = self.install_setup(confirmed_boot=b"valve boot image")
        other = self.root / "install-boot-valve.img"
        other.write_bytes(b"valve boot image")
        dev.install(system, boot=other)
        write = next(t for t in self.texts() if "pico4-install write boot" in t)
        self.assertIn(f"--bytes {len(b'valve boot image')} ", write)
        self.assertIn(hashlib.sha256(b"valve boot image").hexdigest(), write)

    def test_install_skips_a_partition_that_holds_its_image(self):
        dev, system = self.install_setup(data="pico4-data", held=("super",))
        dev.install(system)
        self.assertTrue(any("pico4-install write boot" in t for t in self.texts()))
        self.assertFalse(any("pico4-install write super" in t for t in self.texts()))

    def test_write_partition_hashes_the_partition_only_when_its_start_matches(self):
        dev, _ = self.install_setup()
        image = self.root / "big.img"
        data = b"A" * (devloop.PREFIX_BYTES + 10)
        image.write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        answers = {
            f"pico4-install hash super {devloop.PREFIX_BYTES}": "0" * 64,
            "pico4-install write super": f"written super {sha}",
        }
        seen: list[str] = []

        def ssh(command: str, *_: object, **__: object) -> str:
            seen.append(command)
            return next(reply for marker, reply in answers.items() if marker in command)

        dev.ssh = ssh
        dev.write_partition("super", image, 60)
        # Another image's first MiB differs: the whole partition is never hashed for it.
        self.assertFalse(any(f"pico4-install hash super {len(data)}" in c for c in seen))
        self.assertFalse(seen[-1].endswith(" --zstd"))
        # The compressed copy goes when its record names this very image, else the image itself.
        (self.root / "big.img.zst").write_bytes(b"compressed")
        (self.root / "big.img.zst.sha256").write_text(sha + "\n")
        seen.clear()
        dev.write_partition("super", image, 60)
        self.assertTrue(seen[-1].endswith(f"--bytes {len(data)} --sha256 {sha} --zstd"))
        (self.root / "big.img.zst.sha256").write_text("f" * 64 + "\n")
        seen.clear()
        dev.write_partition("super", image, 60)
        self.assertFalse(seen[-1].endswith(" --zstd"))
        # Only the compressed copy kept (build-system-image.sh): its record stands in for the image.
        (self.root / "big.img.zst.sha256").write_text(sha + "\n")
        record = {"bytes": len(data), "sha256": sha, "prefix_bytes": devloop.PREFIX_BYTES, "prefix_sha256": "c" * 64}
        (self.root / "big.img.json").write_text(json.dumps(record))
        image.unlink()
        self.assertTrue(dev.image_there(image))
        answers[f"pico4-install hash super {devloop.PREFIX_BYTES}"] = "c" * 64  # the start matches the record's
        answers[f"pico4-install hash super {len(data)}"] = "0" * 64
        seen.clear()
        dev.write_partition("super", image, 60)
        self.assertIn(f"pico4-install hash super {len(data)}", seen[1])
        self.assertTrue(seen[-1].endswith(f"--bytes {len(data)} --sha256 {sha} --zstd"))

    def test_the_headset_builds_its_system_and_stage_1_writes_it_from_the_data_partition(self):
        dev, _ = self.install_setup()
        record = {"bytes": 4096, "sha256": "d" * 64, "prefix_bytes": 1024, "prefix_sha256": "e" * 64}
        seen: list[str] = []
        streamed: list[tuple[list, bytes]] = []

        def ssh(command: str, *_: object, **__: object) -> str:
            seen.append(command)
            if "pico4-system.img.json" in command:
                return json.dumps(record)
            if "manifest.json" in command:
                return '{"archive": {"sha256": "f"}}'
            if "pico4-install hash super 1024" in command:
                return "0" * 64
            if "pico4-install write super" in command:
                return f"written super {'d' * 64}"
            return ""

        def run_streamed(argv: list, *_: object, stdin: IO[bytes], **__: object) -> str:
            streamed.append((argv, stdin.read()))
            return ""

        dev.ssh = ssh
        with (
            patch.object(devloop.process, "run_streamed", run_streamed),
            patch.object(devloop, "ROOT", self.root),
            patch.object(devloop, "source_date_epoch", lambda: 1700000000),
            patch.object(devloop, "source_commit", lambda: "abc123"),
            patch.object(devloop.Dev, "build_tree", lambda _self, target: target.write(b"tree")),
        ):
            # From the installed system (its session stops), and from stage 1 (pico4-install build).
            installed = dev.build_on_headset(stage1=False)
            first = dev.build_on_headset(stage1=True)
        for built, (argv, tree) in zip((installed, first), streamed):
            self.assertEqual(built["path"], f"{devloop.HEADSET_BUILD}/out/pico4-system.img.zst")
            self.assertEqual(tree, b"tree")
            # The headset downloads through this PC: a SOCKS proxy on its loopback.
            self.assertEqual(argv[argv.index("-R") + 1], f"127.0.0.1:{devloop.HEADSET_PROXY_PORT}")
        self.assertIn("systemctl stop pico4-session.service", streamed[0][0][-1])
        self.assertIn("SOURCE_DATE_EPOCH=1700000000 OVERPRESSURE_COMMIT=abc123", streamed[0][0][-1])
        self.assertIn("pico4-install build --size 113885655040 --epoch 1700000000 --commit abc123", streamed[1][0][-1])
        self.assertIn("pico4-install build-file --size 113885655040 out/pico4-system.img.json", seen)
        self.assertTrue((self.root / "out/headset/pico4-userspace.manifest.json").is_file())  # for dev check
        seen.clear()
        dev.write_partition("super", self.root / "nowhere.img", 60, from_data=first)
        self.assertTrue(
            seen[-1].endswith(f"--bytes 4096 --sha256 {'d' * 64} --zstd --from-data {first['path']}"), seen[-1]
        )

    def test_writes_wait_for_a_battery_that_lasts(self):
        for reading, refused in (
            ("80\nDischarging\n", False),
            ("20\nDischarging\n", False),
            ("19\nDischarging\n", True),
            ("5\nNot charging\n", True),
            ("5\nCharging\n", False),
            ("5\n", True),
            ("", False),  # not readable: a warning, not a refusal
            ("cat: no such file\n", False),
        ):
            with self.subTest(reading=reading):
                self.assertEqual(bool(devloop.battery_refusal(reading)), refused)
        self.assertIn("at 19 % (Discharging)", devloop.battery_refusal("19\nDischarging"))

    def test_install_refuses_an_empty_battery_before_any_write(self):
        dev, system = self.install_setup(battery="7\nDischarging\n")
        with self.assertRaisesRegex(RuntimeError, "battery is at 7 %"):
            dev.install(system)
        self.assertFalse(any("pico4-install write" in t for t in self.texts()))
        self.assertFalse((dev.work / devloop.INSTALLED).exists())

    def test_install_keeps_existing_data(self):
        dev, system = self.install_setup(data="pico4-data")
        dev.install(system)
        self.assertFalse(any("format-data" in t for t in self.texts()))

    def test_install_runs_only_from_the_maintenance_stage_1(self):
        dev, system = self.install_setup(cmdline="console=x")
        with self.assertRaisesRegex(RuntimeError, "maintenance stage 1"):
            dev.install(system)
        self.assertFalse(any("pico4-install" in t for t in self.texts()))

    def test_restore_after_an_install_brings_back_system_boot_recovery_and_fresh_data(self):
        backup = Path(self.settings.backup)
        dev, _ = self.install_setup()
        self.fastboot_listed = True  # the restore's way into stage 1 passes through fastboot
        manifest = json.loads((backup / "manifest.json").read_text())
        for name in ("super", "recovery"):
            data = f"stock {name}".encode().ljust(8192, b"\0")
            (backup / f"lun0/{name}.bin").write_bytes(data)
            manifest["files"][f"lun0/{name}.bin"] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        (backup / "manifest.json").write_text(json.dumps(manifest))
        rows = (
            (backup / "lun0/rawprogram0.xml")
            .read_text()
            .replace(
                "</data>",
                '<program label="metadata" num_partition_sectors="4096" SECTOR_SIZE_IN_BYTES="4096" '
                'physical_partition_number="0" filename="metadata.bin"/>'
                '<program label="recovery" num_partition_sectors="2" SECTOR_SIZE_IN_BYTES="4096" '
                'physical_partition_number="0" filename="recovery.bin"/></data>',
            )
        )
        (backup / "lun0/rawprogram0.xml").write_text(rows)
        self.partitions = [("boot", len(self.boot)), ("recovery", 8192)]
        shas = {
            n: hashlib.sha256(d).hexdigest()
            for n, d in (("super", b"stock super".ljust(8192, b"\0")), ("boot", self.boot))
        }
        dev = self.make(
            {
                "cat /etc/pico4/test-run": "dev-loop\n1234567",
                "cat /proc/cmdline": "console=x pico4.part=recovery",
                "pico4-install write super": f"written super {shas['super']}",
                "pico4-install write boot": f"written boot {shas['boot']}",
                "pico4-install wipe userdata": "wiped userdata",
                "pico4-install wipe metadata": "wiped metadata",
                "pico4-install bootmode normal": "bootmode normal",
            }
        )
        (self.root / "dev/installed.json").write_text("{}")
        dev.restore()
        # From wherever the headset was, a fresh maintenance boot before the first write.
        reboot = self.calls.index(["fastboot", "-s", "FB1", "reboot", "recovery"])
        first_write = next(i for i, c in enumerate(self.calls) if "pico4-install write super" in " ".join(c))
        self.assertLess(reboot, first_write)
        steps = [
            t.split("; ")[-1].split(" --")[0]
            for t in self.texts()
            if ("pico4-install" in t and "pico4-install hash" not in t) or " flash " in t
        ]
        self.assertEqual(
            steps[:5],
            [
                "pico4-install write super",
                "pico4-install write boot",
                "pico4-install wipe userdata",
                "pico4-install wipe metadata",
                "pico4-install bootmode normal",
            ],
        )
        self.assertTrue(steps[5].endswith(" flash recovery " + str(self.root / "dev/restore-recovery.simg")))
        self.assertIn(f"--size {4096 * 4096}", next(t for t in self.texts() if "wipe metadata" in t))
        self.assertFalse((self.root / "dev/installed.json").exists())

    def test_connect_verifies_the_flashed_dev_image(self):
        dev = self.make({"cat /etc/pico4/test-run": "dev-loop\n1234567", "pico4-install hash": "f" * 64})
        (self.root / "dev/dev-boot.img").write_bytes(b"dev image")
        (self.root / "dev/dev-image.json").write_text(json.dumps({"boot_sha256": "f" * 64}))
        dev.connect()
        (self.root / "dev/dev-image.json").write_text(json.dumps({"boot_sha256": "0" * 64}))
        with self.assertRaisesRegex(RuntimeError, "readback differs"):
            dev.connect()

    def test_reboot_from_fastboot_restarts_stage_1_without_a_flash(self):
        dev = self.make({"cat /etc/pico4/test-run": "dev-loop\n1234567"})
        dev.reboot()
        self.assertIn(["fastboot", "-s", "FB1", "reboot"], self.calls)
        self.assertNotIn("flash-boot", self.steps())
        self.assertFalse(any("systemctl reboot" in c for c in self.texts()))
        self.calls.clear()
        self.fastboot_listed = False
        dev.reboot()
        self.assertTrue(any("systemctl reboot" in c for c in self.texts()))
        self.assertNotIn(["fastboot", "-s", "FB1", "reboot"], self.calls)

    def test_reboot_of_an_installed_headset_ends_where_the_bootloader_went(self):
        # boot holds the installed system: without the recovery request it comes back.
        (self.root / "dev").mkdir(exist_ok=True)
        (self.root / "dev/installed.json").write_text("{}")
        dev = self.make(
            {
                "cat /proc/1/comm /proc/cmdline": "systemd\nconsole=x",
                "cat /proc/1/comm /etc/pico4/install-id": "systemd\ndev-loop\n1234567",
                "userspace-archive.sha256": "abc",
            }
        )
        dev.reboot()
        self.assertTrue(any(devloop.INSTALL_ID in t for t in self.texts()))
        self.assertFalse(any("cat /etc/pico4/test-run" in t for t in self.texts()))
        # With the request in misc, the maintenance stage 1 starts from recovery.
        self.calls.clear()
        dev = self.make(
            {
                "cat /proc/1/comm /proc/cmdline": "pico4-init\nconsole=x pico4.part=recovery",
                "cat /etc/pico4/test-run": "dev-loop\n1234567",
                "cat /proc/cmdline": "console=x pico4.part=recovery",
            }
        )
        dev.reboot()
        self.assertTrue(any("cat /etc/pico4/test-run" in t for t in self.texts()))
        self.assertFalse(any(devloop.INSTALL_ID in t for t in self.texts()))

    def test_reboot_waits_for_another_boot_not_a_fixed_time(self):
        (self.root / "dev").mkdir(exist_ok=True)
        (self.root / "dev/installed.json").write_text("{}")
        self.fastboot_listed = False
        # Before the reboot, then twice the old boot still answering, then the new one.
        boots = iter(["old", "old", "old"])
        dev = self.make(
            {
                devloop.BOOT_ID: lambda: next(boots, "new") + "\n",
                "cat /proc/1/comm /proc/cmdline": "systemd\nconsole=x",
                "cat /proc/1/comm /etc/pico4/install-id": "systemd\ndev-loop\n1234567",
                "userspace-archive.sha256": "abc",
            }
        )
        dev.reboot()
        self.assertEqual(sum(devloop.BOOT_ID in t for t in self.texts()), 5)
        # 3 s, then a retry for each of the two answers of the old boot (every 3 s).
        self.assertEqual(self.clock.now, 9)

    def test_restore_flashes_the_verified_backup_boot_image(self):
        dev = self.make()
        dev.restore()
        flash = [c for c in self.calls if " flash boot " in " ".join(c)]
        self.assertEqual(flash[0][-1], str(Path(self.settings.backup) / "lun4/boot.bin"))
        self.assertIn("oem", " ".join(self.calls[self.calls.index(flash[0]) - 1]))
        (Path(self.settings.backup) / "lun4/boot.bin").write_bytes(b"changed")
        with self.assertRaisesRegex(RuntimeError, "differs"):
            self.make().restore()


if __name__ == "__main__":
    unittest.main()
