import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devices.phoenix.tooling import cycle, devloop, localconfig


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class SettingsTests(unittest.TestCase):
    def test_parse_skips_comments_and_strips_quotes(self):
        values = localconfig.parse("# headset\nPICO4_ADB_SERIAL=ABC  # the ADB serial\n\n"
                                   "PICO4_EDL='C:/tools/edl ng/edl-ng.exe'\nPICO4_FIRMWARE=\"5.13.7-global-seko\"\n")
        self.assertEqual(values, {"PICO4_ADB_SERIAL": "ABC", "PICO4_EDL": "C:/tools/edl ng/edl-ng.exe",
                                  "PICO4_FIRMWARE": "5.13.7-global-seko"})
        with self.assertRaisesRegex(ValueError, "line 1"):
            localconfig.parse("no assignment\n")

    def test_environment_overrides_the_file_and_paths_are_rooted(self):
        with tempfile.TemporaryDirectory() as temp:
            env = Path(temp) / ".env"
            env.write_text("PICO4_ADB_SERIAL=FILE\nPICO4_BACKUP=backup/stock-1\n", encoding="utf-8")
            settings = localconfig.load(env, {"PICO4_ADB_SERIAL": "ENV"})
        self.assertEqual(settings.adb_serial, "ENV")
        self.assertEqual(settings.path("backup"), localconfig.ROOT / "backup/stock-1")
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
        (backup / "manifest.json").write_text(json.dumps({"schema": 1, "files": {
            "lun4/boot.bin": {"size": len(self.boot), "sha256": hashlib.sha256(self.boot).hexdigest()}}}))
        (self.root / "device.json").write_text(json.dumps({
            "schema": 1, "serial": "ADB1", "oem_state": "true", "chip_id": 1234567,
            "fingerprint": "Pico/Phoenix_ovs/PICOA8110:10/5.13.7/test:user/dev-keys"}))
        self.settings = localconfig.Settings(
            adb_serial="ADB1", fastboot_serial="FB1", edl_serial="EDL1", identity=str(self.root / "device.json"),
            backup=str(backup), loader=str(self.root / "firehose.elf"), engineering_abl_sha256="ab" * 32,
            edl=str(self.root / "edl-ng"), firmware="test")
        self.calls = []
        self.edl = 0
        self.readback = self.boot
        self.edl_output = "Serial Number: EDL1"
        self.run_result = {"status": "passed", "boot_restore_required": False}
        self.fastboot_listed = True
        self.unlocked = "true"
        self.partitions = [("boot", len(self.boot)), ("abl", 1 << 20)]
        self.clock = FakeClock()
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def texts(self):
        return [" ".join(c) for c in self.calls]

    def fake(self, argv, timeout=60, stdin=None, check=True, log=None):
        words = [str(a) for a in argv]
        self.calls.append(words)
        text = " ".join(words)
        output = ""
        if "reboot edl" in text:
            self.edl = 1
        elif "getprop sys.boot_completed" in text:
            output = "1\nPico/Phoenix_ovs/PICOA8110:10/5.13.7/test:user/dev-keys"
        elif "hwtest flash-run" in text:
            run = Path(words[words.index("--out") + 1])
            run.mkdir()
            (run / "result.json").write_text(json.dumps(self.run_result))
        elif "read-part boot" in text:
            Path(words[words.index("boot") + 1]).write_bytes(self.readback)
            output = self.edl_output
        elif "printgpt" in text:
            output = self.edl_output
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
            for marker, name in (("reboot edl", "adb-edl"), ("printgpt", "identify"), ("device stage", "stage"),
                                 ("reset", "edl-reset"),
                                 ("hwtest flash-run", "flash-run"), ("read-part boot", "readback"),
                                 ("device restore-abl", "restore-abl"), ("getprop sys.boot_completed", "pico-os"),
                                 (" flash boot ", "flash-boot")):
                if marker in text:
                    names.append(name)
        return names


class CycleTests(HeadsetFixture):
    def make(self):
        return cycle.Cycle(self.settings, self.folder, self.fake, self.clock, lambda: self.edl)

    def run_cycle(self, c):
        with patch.object(cycle, "ROOT", self.root):
            (self.root / "out").mkdir(exist_ok=True)
            (self.root / "out/pico4-poc-test.img").write_bytes(b"image")
            c.stage_abl()
            self.edl = 0
            code = c.flash_run(self.root / "userspace.tar.zst")
            c.restore_abl()
        return code

    def test_full_cycle_order_ends_on_stock(self):
        self.assertEqual(self.run_cycle(self.make()), 0)
        self.assertEqual(self.steps(), ["adb-edl", "identify", "stage", "edl-reset", "pico-os", "flash-run", "pico-os",
                                        "adb-edl", "readback", "restore-abl", "edl-reset", "pico-os"])
        stage = next(w for w in self.calls if "stage" in w)
        self.assertEqual(stage[stage.index("--confirm-device") + 1], "ADB1")
        run = next(w for w in self.calls if "flash-run" in w)
        self.assertIn("--userspace", run)
        self.assertEqual(run[run.index("--fastboot-serial") + 1], "FB1")

    def test_boot_that_differs_from_the_backup_keeps_the_engineering_abl(self):
        self.readback = b"something else"
        with self.assertRaisesRegex(RuntimeError, "differs from boot.bin"):
            self.run_cycle(self.make())
        self.assertNotIn("restore-abl", self.steps())

    def test_another_edl_device_is_never_written(self):
        self.edl_output = "Serial Number: OTHER"
        with self.assertRaisesRegex(RuntimeError, "not the configured headset"):
            self.run_cycle(self.make())
        self.assertEqual(self.steps(), ["adb-edl", "identify"])

    def test_pending_boot_restore_flashes_the_pinned_stock_image(self):
        stock = self.root / "stock-boot.img"
        stock.write_bytes(self.boot)
        self.run_result = {"status": "failed", "boot_restore_required": True,
                           "stock_boot": {"path": str(stock), "size": len(self.boot),
                                          "sha256": hashlib.sha256(self.boot).hexdigest()},
                           "manual_restore_commands": [["fastboot", "-s", "FB1", "flash", "boot", str(stock)],
                                                       ["fastboot", "-s", "FB1", "reboot"]]}
        self.run_cycle(self.make())
        self.assertIn(["fastboot", "-s", "FB1", "flash", "boot", str(stock)], self.calls)
        self.assertEqual(self.steps()[-1], "pico-os")

    def test_changed_restore_image_is_never_flashed(self):
        stock = self.root / "stock-boot.img"
        stock.write_bytes(b"tampered")
        self.run_result = {"status": "failed", "boot_restore_required": True,
                           "stock_boot": {"path": str(stock), "size": len(self.boot),
                                          "sha256": hashlib.sha256(self.boot).hexdigest()},
                           "manual_restore_commands": [["fastboot", "-s", "FB1", "flash", "boot", str(stock)]]}
        with self.assertRaisesRegex(RuntimeError, "changed"):
            self.run_cycle(self.make())
        self.assertNotIn(["fastboot", "-s", "FB1", "flash", "boot", str(stock)], self.calls)

    def test_headset_that_stays_off_after_the_final_reset_needs_a_hard_reset(self):
        c = self.make()
        fake = self.fake

        def off_after_reset(argv, timeout=60, stdin=None, check=True, log=None):
            text = " ".join(str(a) for a in argv)
            if "reset --mode Reset" in text:
                self.edl = 0
            if "getprop sys.boot_completed" in text and Path(self.folder / "reset-final.log").exists():
                self.calls.append([str(a) for a in argv])
                raise RuntimeError("adb: no devices/emulators found")
            return fake(argv, timeout, stdin, check, log)
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


class DevTests(HeadsetFixture):
    def make(self, responses=None):
        responses = responses or {}

        def fake(argv, timeout=60, stdin=None, check=True, log=None):
            text = " ".join(str(a) for a in argv)
            for marker, reply in responses.items():
                if marker in text:
                    if stdin is not None:
                        stdin.read()
                    self.calls.append([str(a) for a in argv])
                    return reply() if callable(reply) else reply
            return self.fake(argv, timeout, stdin, check, log)
        return devloop.Dev(self.settings, self.root / "dev", fake, self.clock)

    def archive(self):
        archive = self.root / "pico4-userspace.tar.zst"
        archive.write_bytes(b"userspace")
        digest = hashlib.sha256(b"userspace").hexdigest()
        (self.root / "pico4-userspace.manifest.json").write_text(json.dumps({
            "archive": {"sha256": digest}, "packages": {"systemd": "257.7", "pico4-monado": "25.1.0-3", "deckard-steamvr-rel": "1"},
            "gpu_firmware": [{}], "sensor_data": []}))
        return archive, digest

    def test_check_knows_the_stage1_of_a_kernel_test_image(self):
        stock = {"kernel": "Linux version 4.19.81-perf+", "stock": "5.13.7"}
        valve = {"kernel": "Linux version 7.2.0-pico4", "stock": "5.13.7"}
        (self.root / "kernel").mkdir()
        (self.root / "kernel" / "test-recovery.img.json").write_text(json.dumps({"build_info": valve}))
        running = {"info": stock}
        dev = self.make({"cat /etc/pico4-poc/build-info": lambda: json.dumps(running["info"])})
        self.assertEqual(dev.known_build_info(stock), stock)
        running["info"] = valve
        self.assertEqual(dev.known_build_info(stock), valve)
        # An unknown stage 1 keeps the dev image's, which the check then refuses.
        running["info"] = {"kernel": "Linux version 9"}
        self.assertEqual(dev.known_build_info(stock), stock)

    def test_boot_streams_switches_and_records_the_archive(self):
        archive, digest = self.archive()
        comm = iter(["init", "systemd"])
        dev = self.make({"pico4-userspace receive": f"unpacking\nreceived {digest}",
                         "cat /proc/1/comm": lambda: next(comm), "pico4-userspace switch": "switching"})
        state = dev.boot(archive)
        self.assertEqual((state["sha256"], state["steamvr"], state["gpu_firmware"]), (digest, True, True))
        self.assertTrue(any(f"receive --sha256 {digest}" in c for c in self.texts()))
        self.assertTrue(any("userspace-authenticated" in c for c in self.texts()))
        saved = json.loads((self.root / "dev/state.json").read_text())
        self.assertEqual(saved["archive"], str(archive.resolve()))

    def test_every_command_renews_the_host_contact(self):
        argv = self.make().ssh_argv("pico4-valve-session-check --seconds 30")
        self.assertEqual(argv[-1], "cut -d. -f1 /proc/uptime > /run/pico4-poc/host-contact 2>/dev/null; "
                                   "pico4-valve-session-check --seconds 30")

    def test_unconfirmed_archive_never_switches(self):
        archive, _ = self.archive()
        dev = self.make({"pico4-userspace receive": "received 0000"})
        with self.assertRaisesRegex(RuntimeError, "did not confirm"):
            dev.boot(archive)
        self.assertFalse(any("switch" in c for c in self.texts()))

    def test_login_requires_the_dev_image_on_this_headset(self):
        self.make({"cat /etc/pico4-poc/test-run": "dev-loop\n1234567"}).authenticate()
        with self.assertRaisesRegex(RuntimeError, "not this headset"):
            self.make({"cat /etc/pico4-poc/test-run": "other-run\n1234567"}).authenticate()
        with self.assertRaisesRegex(RuntimeError, "not this headset"):
            self.make({"cat /etc/pico4-poc/test-run": "dev-loop\n7654321"}).authenticate()

    def test_flash_needs_an_unlocked_single_boot_partition_of_the_backup_size(self):
        image = self.root / "dev/dev-boot.img"
        for unlocked, partitions, message in (
                ("false", [("boot", len(self.boot))], "locked"),
                ("true", [("boot_a", len(self.boot)), ("boot_b", len(self.boot))], "non-slotted"),
                ("true", [("boot", 4096)], "size")):
            self.calls.clear()
            self.unlocked, self.partitions = unlocked, partitions
            dev = self.make()
            image.write_bytes(b"dev image")
            with self.assertRaisesRegex(RuntimeError, message):
                dev.flash()
            self.assertNotIn("flash-boot", self.steps())

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
        self.make().flash("recovery")
        flash = self.calls.index(["fastboot", "-s", "FB1", "flash", "recovery", str(image)])
        self.assertIn("unlock", self.texts()[flash - 1])
        self.assertEqual(self.calls[flash + 1], ["fastboot", "-s", "FB1", "reboot", "recovery"])
        # The backup's size counts, not the boot partition's.
        self.calls.clear()
        self.partitions = [("boot", len(self.boot)), ("recovery", 4096)]
        with self.assertRaisesRegex(RuntimeError, "recovery partition"):
            self.make().flash("recovery")
        self.assertFalse(any(" flash recovery " in t for t in self.texts()))
        # Any other partition is refused before fastboot is asked anything.
        with self.assertRaisesRegex(ValueError, "not a boot image partition"):
            self.make().check_boot_partition(image, "super")

    def test_connect_reads_back_the_partition_that_started(self):
        dev = self.make({"cat /etc/pico4-poc/test-run": "dev-loop\n1234567", "cat /proc/cmdline":
                         "console=x pico4poc.part=recovery", "sha256sum": "e" * 64 + "  -"})
        (self.root / "dev/dev-recovery.img").write_bytes(b"dev image")
        (self.root / "dev/dev-image.json").write_text(json.dumps({"boot_sha256": "f" * 64, "recovery_sha256": "e" * 64}))
        dev.connect()
        self.assertTrue(any("/dev/block/by-name/recovery | sha256sum" in t for t in self.texts()))
        self.assertFalse(any("/dev/block/by-name/boot | sha256sum" in t for t in self.texts()))

    def install_setup(self, cmdline="console=x pico4poc.part=recovery", data="none", held=None,
                      confirmed_boot=b"install boot"):
        backup = Path(self.settings.backup)
        (backup / "lun0").mkdir(exist_ok=True)
        rows = "".join(f'<program label="{n}" num_partition_sectors="{s}" SECTOR_SIZE_IN_BYTES="4096" '
                       f'physical_partition_number="0" filename="{n}.bin"/>'
                       for n, s in (("boot", 24576), ("super", 2097152), ("userdata", 27804115)))
        (backup / "lun0/rawprogram0.xml").write_text(f"<data>{rows}</data>")
        dev_dir = self.root / "dev"
        dev_dir.mkdir(exist_ok=True)
        kernel_dir = self.root / "kernel"
        kernel_dir.mkdir(exist_ok=True)
        (kernel_dir / "install-boot-valve-rgb.img").write_bytes(b"install boot")
        system = self.root / "pico4-system.img"
        system.write_bytes(b"system image")
        boot_sha = hashlib.sha256(confirmed_boot).hexdigest()
        system_sha = hashlib.sha256(b"system image").hexdigest()
        responses = {f"by-name/{name} | sha256sum": f"{sha}  -" for name, sha in
                     (("boot", boot_sha), ("super", system_sha)) if name in (held or ())}
        dev = self.make({**responses, "cat /etc/pico4-poc/test-run": "dev-loop\n1234567", "cat /proc/cmdline": cmdline,
                         "pico4-install write boot": f"written boot {boot_sha}",
                         "pico4-install write super": f"written super {system_sha}",
                         "pico4-install status": f"bootmode: boot-recovery\nsystem: pico4-system\ndata: {data}",
                         "pico4-install format-data": "formatted userdata",
                         "pico4-install bootmode normal": "bootmode normal",
                         "cat /proc/1/comm /etc/pico4-poc/install-id": "systemd\ndev-loop\n1234567"})
        return dev, system

    def test_install_writes_boot_and_super_formats_new_data_and_starts_it(self):
        dev, system = self.install_setup()
        dev.install(system)
        steps = [t for t in self.texts() if "pico4-install" in t or t.endswith("pico4-reboot")]
        self.assertEqual([s.split("; ")[-1].split(" --")[0] for s in steps],
                         ["pico4-install write boot", "pico4-install write super", "pico4-install status",
                          "pico4-install format-data", "pico4-install bootmode normal", "pico4-reboot"])
        # Sizes from the backup's partition maps, not from the headset.
        self.assertIn("--size 100663296 --bytes 12 ", steps[0])
        self.assertIn(f"--size {2097152 * 4096} ", steps[1])
        self.assertIn(f"--size {27804115 * 4096}", steps[3])

    def test_install_takes_another_boot_image(self):
        # Such as the installed system's boot image with Valve's kernel (recovery_image.py --boot).
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
        manifest = json.loads((backup / "manifest.json").read_text())
        for name in ("super", "recovery"):
            data = f"stock {name}".encode().ljust(8192, b"\0")
            (backup / f"lun0/{name}.bin").write_bytes(data)
            manifest["files"][f"lun0/{name}.bin"] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        (backup / "manifest.json").write_text(json.dumps(manifest))
        rows = (backup / "lun0/rawprogram0.xml").read_text().replace(
            "</data>", '<program label="metadata" num_partition_sectors="4096" SECTOR_SIZE_IN_BYTES="4096" '
                       'physical_partition_number="0" filename="metadata.bin"/>'
                       '<program label="recovery" num_partition_sectors="2" SECTOR_SIZE_IN_BYTES="4096" '
                       'physical_partition_number="0" filename="recovery.bin"/></data>')
        (backup / "lun0/rawprogram0.xml").write_text(rows)
        self.partitions = [("boot", len(self.boot)), ("recovery", 8192)]
        shas = {n: hashlib.sha256(d).hexdigest() for n, d in
                (("super", b"stock super".ljust(8192, b"\0")), ("boot", self.boot))}
        dev = self.make({"cat /etc/pico4-poc/test-run": "dev-loop\n1234567",
                         "cat /proc/cmdline": "console=x pico4poc.part=recovery",
                         "pico4-install write super": f"written super {shas['super']}",
                         "pico4-install write boot": f"written boot {shas['boot']}",
                         "pico4-install wipe userdata": "wiped userdata",
                         "pico4-install wipe metadata": "wiped metadata",
                         "pico4-install bootmode normal": "bootmode normal"})
        (self.root / "dev/installed.json").write_text("{}")
        dev.restore()
        steps = [t.split("; ")[-1].split(" --")[0] for t in self.texts()
                 if "pico4-install" in t or " flash " in t]
        self.assertEqual(steps[:5], ["pico4-install write super", "pico4-install write boot",
                                     "pico4-install wipe userdata", "pico4-install wipe metadata",
                                     "pico4-install bootmode normal"])
        self.assertTrue(steps[5].endswith(" flash recovery " + str(self.root / "dev/restore-recovery.simg")))
        self.assertIn(f"--size {4096 * 4096}", next(t for t in self.texts() if "wipe metadata" in t))
        self.assertFalse((self.root / "dev/installed.json").exists())

    def test_connect_verifies_the_flashed_dev_image(self):
        dev = self.make({"cat /etc/pico4-poc/test-run": "dev-loop\n1234567", "sha256sum": "f" * 64 + "  -"})
        (self.root / "dev/dev-boot.img").write_bytes(b"dev image")
        (self.root / "dev/dev-image.json").write_text(json.dumps({"boot_sha256": "f" * 64}))
        dev.connect()
        (self.root / "dev/dev-image.json").write_text(json.dumps({"boot_sha256": "0" * 64}))
        with self.assertRaisesRegex(RuntimeError, "readback differs"):
            dev.connect()

    def test_reboot_from_fastboot_restarts_stage_1_without_a_flash(self):
        dev = self.make({"cat /etc/pico4-poc/test-run": "dev-loop\n1234567"})
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
        dev = self.make({"cat /proc/1/comm /proc/cmdline": "systemd\nconsole=x",
                         "cat /proc/1/comm /etc/pico4-poc/install-id": "systemd\ndev-loop\n1234567",
                         "userspace-archive.sha256": "abc"})
        dev.reboot()
        self.assertTrue(any(devloop.INSTALL_ID in t for t in self.texts()))
        self.assertFalse(any("cat /etc/pico4-poc/test-run" in t for t in self.texts()))
        # With the request in misc, the maintenance stage 1 starts from recovery.
        self.calls.clear()
        dev = self.make({"cat /proc/1/comm /proc/cmdline": "pico4-init\nconsole=x pico4poc.part=recovery",
                         "cat /etc/pico4-poc/test-run": "dev-loop\n1234567",
                         "cat /proc/cmdline": "console=x pico4poc.part=recovery"})
        dev.reboot()
        self.assertTrue(any("cat /etc/pico4-poc/test-run" in t for t in self.texts()))
        self.assertFalse(any(devloop.INSTALL_ID in t for t in self.texts()))

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
