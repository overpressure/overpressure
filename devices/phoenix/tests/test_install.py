# SPDX-License-Identifier: Unlicense
"""The PICO 4's installer plan against fakes: tasks, done checks, serials, refusals and the Wi-Fi secret."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

from devices.phoenix.tooling import HEADSET, localconfig
from devices.phoenix.tooling import install as plan
from overpressure import doctor, tools
from overpressure.doctor import Check
from overpressure.install import Context, Engine, Stage, Task

if TYPE_CHECKING:
    from collections.abc import Callable

#: Made-up values: no real headset's.
ADB, FASTBOOT, EDL = "ADBFAKE1", "FBFAKE1", "0A1B2C3D"
PASSWORD = "correct horse battery"  # noqa: S105  # a made-up secret the tests look for
FINGERPRINT = "Pico/Phoenix_ovs/PICOA8110:10/{}/test:user/dev-keys"
SHARP = "console=ttyMSM0 msm_drm.dsi_display0=qcom,mdss_dsi_sharp_ls026b3sa_2160_video: other=1"
INNOLUX = "msm_drm.dsi_display0=qcom,mdss_dsi_nt57900_innolux_2160_video:"
PRINTGPT = f"""[INFO]  Serial Number: {EDL}
[INFO]  --- GPT Header LUN 0 ---
[INFO]  --- Partitions LUN 0 ---
[INFO]  --- GPT Header LUN 1 ---
[INFO]  --- Partitions LUN 1 ---
[INFO]  --- GPT Header LUN 2 ---
"""


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class Recorder:
    def __init__(self, answers: dict[str, dict]):
        self.events: list[dict] = []
        self.answers = answers

    def start(self, stop: Callable[[], None]) -> None:
        pass

    def emit(self, event: dict) -> None:
        self.events.append(event)

    def answer(self, ask: plan.Ask) -> dict | None:
        return self.answers.get(ask.id)

    def finished(self):
        return next(e for e in self.events if e["event"] == "finished")


class PlanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patcher = mock.patch.object(tools, "CACHE", self.root / "cache" / "tools")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.env = self.root / ".env"
        self.identity = self.root / "backup" / "device.json"
        self.backup = self.root / "backup" / "stock"
        self.env.write_text(
            "\n".join(
                [
                    f"PICO4_IDENTITY={self.identity.as_posix()}",
                    f"PICO4_BACKUP={self.backup.as_posix()}",
                    f"PICO4_LOADER={(self.root / 'firehose.elf').as_posix()}",
                    f"PICO4_ENGINEERING_ABL={(self.root / 'abl.elf').as_posix()}",
                    f"PICO4_EDL={(self.root / 'edl-ng').as_posix()}",
                    f"PICO4_PLATFORM_TOOLS={(self.root / 'pt').as_posix()}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        mine = {k: v for k, v in os.environ.items() if not k.startswith("PICO4_")}  # .env's, not this host's
        environ = mock.patch.dict(os.environ, mine, clear=True)
        environ.start()
        self.addCleanup(environ.stop)
        self.version = "5.13.7"
        self.model = "PICO 4"
        self.cmdline = SHARP
        self.edl = 0
        self.fastboot = False
        self.spawned: list[tuple[list[str], bytes | None, bool]] = []
        self.captured: list[list[str]] = []
        self.checks: list[Check] = []
        self.image = False
        self.plan = plan.Plan(
            HEADSET,
            root=self.root,
            work=self.root / "out" / "dev",
            computer=lambda: self.checks,
            edl_count=lambda: self.edl,
            image_present=lambda: self.image,
        )

    # -- the fakes -----------------------------------------------------------------------------
    def spawn(self, argv: list[str], stdin: bytes | None, writes: bool, line: Callable[[str], None]) -> int:  # noqa: FBT001  # the Spawn signature
        self.spawned.append((argv, stdin, writes))
        if "probe" in argv:
            output = Path(argv[argv.index("--output") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "schema": 1,
                "serial": argv[argv.index("--serial") + 1],
                "fingerprint": FINGERPRINT.format(self.version),
                "oem_state": "true",
                "chip_id": 1234567,
                "model": self.model,
                "ddr_type": "DDR4",
            }
            output.write_text(json.dumps(record), encoding="utf-8")
        line("ok")
        return 0

    def capture(self, argv: list[str], _timeout: float) -> tuple[int, str]:  # noqa: PLR0911  # one answer per command
        self.captured.append(argv)
        text = " ".join([Path(argv[0]).stem, *argv[1:]])
        if text == "adb devices":
            return 0, f"List of devices attached\n{ADB}\tdevice\n"
        if "cat /proc/cmdline" in text:
            return 0, self.cmdline
        if text.endswith("reboot edl"):
            self.edl = 1
            return 0, ""
        if "printgpt" in text:
            return 0, PRINTGPT
        if text.endswith("reboot bootloader"):
            self.fastboot = True
            return 0, ""
        if text == "fastboot devices":
            return 0, f"{FASTBOOT}\tfastboot\n" if self.fastboot else ""
        return 0, ""

    def run_stages(self, ids: tuple[str, ...], answers: dict[str, dict]) -> Recorder:
        stages = [s for s in self.plan.stages() if s.id in ids]
        ui = Recorder(answers)
        engine = Engine(
            HEADSET.info,
            stages,
            ui,
            root=self.root / "out" / "install",
            env=self.env,
            spawn=self.spawn,
            capture=self.capture,
            clock=FakeClock(),
        )
        engine.run()
        return ui

    def ctx(self) -> Context:
        engine = Engine(HEADSET.info, (), Recorder({}), root=self.root / "out" / "install", env=self.env)
        task = Task("t", "T", lambda _ctx: None)
        return Context(engine, Stage("s", "S", "", (task,)), task)

    def settings(self) -> localconfig.Settings:
        return localconfig.load(self.env, {})

    def commands(self) -> list[str]:
        return [" ".join(argv[argv.index("phoenix") + 1 :][:2]) for argv, _, _ in self.spawned if "phoenix" in argv]

    @staticmethod
    def yes(*ids: str) -> dict[str, dict]:
        return {i: {"answer": i, "accepted": True, "values": {}} for i in ids}

    # -- the tests -------------------------------------------------------------------------------
    def test_the_stages_and_their_tasks(self):
        stages = self.plan.stages()
        self.assertEqual([s.id for s in stages], ["computer", "build", "connect", "prepare", "install"])
        self.assertEqual(
            [[t.id for t in s.tasks] for s in stages],
            [
                ["tools", "unlock-files", "doctor"],
                ["assets", "stage1", "container", "userspace", "system", "dev-image", "kernel", "boot-image"],
                ["find", "identity", "panel"],
                ["consent", "backup", "stage-abl", "unlock", "flash", "maintenance"],
                ["wifi", "install", "installed", "wifi-profile", "check", "ready"],
            ],
        )
        writes = [t.id for s in stages for t in s.tasks if t.writes]
        self.assertEqual(writes, ["stage-abl", "unlock", "flash", "install", "wifi-profile"])
        self.assertTrue(all(t.done is not None for t in stages[1].tasks))
        self.assertTrue(all(t.where for s in stages[1:] for t in s.tasks if t.id not in {"ready", "wifi"}))
        reset = plan.Plan(HEADSET, factory_reset=True).stages()[3]
        self.assertEqual([t.id for t in reset.tasks][3:5], ["unlock", "reset"])
        self.assertTrue(HEADSET.install_plan())

    def test_the_builds_run_the_steps_command_lines(self):
        build = next(s for s in self.plan.stages() if s.id == "build")
        argv = self.plan.argv(self.ctx(), "stage1")
        self.assertEqual(argv[-4:], ["phoenix", "build", "--firmware", "5.13.7-global-seko"])
        self.assertEqual(len(build.tasks), 8)
        # The Linux side runs in the build container, whose image a task builds before the first Linux step.
        ids = [t.id for t in build.tasks]
        linux_root = [i for i in ids if i in self.plan.steps and self.plan.steps[i].linux_root]
        self.assertLess(ids.index("container"), ids.index(linux_root[0]))
        for key in linux_root:
            self.assertEqual(self.plan.argv(self.ctx(), key)[1:6], ["-m", "overpressure", "container", "run", "--"])
        task = build.tasks[ids.index("container")]
        self.assertEqual((task.title, task.estimate), ("Build environment", "a few minutes the first time"))

    def test_the_build_environment_is_built_unless_its_image_is_there(self):
        ui = self.run_stages(("build",), {})
        self.assertEqual(ui.finished()["status"], "done", ui.finished())
        built = [argv for argv, _, _ in self.spawned if argv[1:5] == ["-m", "overpressure", "container", "build"]]
        self.assertEqual(len(built), 1)
        self.spawned.clear()
        self.image = True
        (self.root / "out" / "install" / "phoenix" / "state.json").unlink()
        self.run_stages(("build",), {})
        self.assertFalse([argv for argv, _, _ in self.spawned if "container" in argv and "build" in argv])

    def test_build_done_checks_look_at_the_outputs(self):
        ctx = self.ctx()
        out = self.root / "out"
        done = {t.id: t.done for t in self.plan.stages()[1].tasks}
        self.assertFalse(any(check(ctx) for check in done.values() if check))
        self.image = True
        self.assertTrue(done["container"](ctx))  # ty: ignore[call-non-callable]  # every build has one
        for kind in ("graphics-firmware", "sensor-data", "wlan", "audio", "stock-slam"):
            (self.root / "assets" / f"{kind}-5.13.7").mkdir(parents=True)
            (self.root / "assets" / f"{kind}-5.13.7" / "manifest.json").write_text("{}")
        for name in ("pico4-stage1-5.13.7-global-seko.img", "userspace/pico4-userspace.tar.zst"):
            (out / name).parent.mkdir(parents=True, exist_ok=True)
            (out / name).write_text("x")
        self.assertTrue(done["assets"](ctx) and done["stage1"](ctx) and done["userspace"](ctx))  # ty: ignore[call-non-callable]  # every build has one
        self.assertFalse(done["system"](ctx))  # ty: ignore[call-non-callable]  # every build has one
        (out / "userspace/pico4-system.img").write_text("x")
        self.assertTrue(done["system"](ctx))  # ty: ignore[call-non-callable]  # every build has one
        archive = out / "userspace/pico4-userspace.tar.zst"
        mtime = (out / "userspace/pico4-system.img").stat().st_mtime
        os.utime(archive, (mtime + 10, mtime + 10))  # a newer archive: the system image is built again
        self.assertFalse(done["system"](ctx))  # ty: ignore[call-non-callable]  # every build has one

    def test_connect_and_prepare_find_the_serials_and_write_them_to_env(self):
        ui = self.run_stages(("connect", "prepare"), self.yes("plug-in", "consent"))
        self.assertEqual(ui.finished()["status"], "done", ui.finished())
        s = self.settings()
        self.assertEqual((s.adb_serial, s.edl_serial, s.fastboot_serial), (ADB, EDL, FASTBOOT))
        self.assertEqual(
            self.commands(),
            [
                "device probe",
                "device backup",
                "device verify-backup",
                "dev stage-abl",
                "device unlock",
                "dev flash",
                "dev connect",
            ],
        )
        backup = next(argv for argv, _, _ in self.spawned if "backup" in argv and "--execute" in argv)
        luns = backup[backup.index("--edl-luns") + 1 : backup.index("--execute")]
        self.assertEqual(luns, ["1", "2", "0"])
        self.assertEqual(backup[backup.index("--confirm-device") + 1], ADB)
        unlock = next(argv for argv, _, _ in self.spawned if "unlock" in argv)
        self.assertIn("--accept-data-loss", unlock)
        self.assertEqual(unlock[unlock.index("--serial") + 1], FASTBOOT)
        self.assertIn(
            ["pt/adb", "-s", ADB, "reboot", "edl"], [[_tail(a) for a in c][:1] + c[1:] for c in self.captured]
        )
        text = self.env.read_text(encoding="utf-8")
        self.assertEqual(text.count("PICO4_ADB_SERIAL="), 1)
        self.assertIn(f"PICO4_IDENTITY={self.identity.as_posix()}", text)  # the other lines stay

    def test_an_unverified_firmware_is_refused_before_any_write(self):
        self.version = "5.13.8"
        ui = self.run_stages(("connect", "prepare"), self.yes("plug-in", "consent"))
        finished = ui.finished()
        self.assertEqual((finished["status"], finished["task"]), ("failed", "identity"))
        self.assertIn("5.13.8", finished["error"])
        self.assertIn("Nothing was changed", finished["where"])
        self.assertEqual(self.commands(), ["device probe"])
        self.assertFalse(any("reboot" in " ".join(c) for c in self.captured))
        self.assertNotIn("consent", [e.get("id") for e in ui.events if e["event"] == "ask"])

    def test_a_pico_4_pro_is_refused_before_any_write(self):
        self.model = "PICO 4 Pro"
        ui = self.run_stages(("connect", "prepare"), self.yes("plug-in", "consent"))
        finished = ui.finished()
        self.assertEqual((finished["status"], finished["task"]), ("failed", "identity"))
        self.assertIn("PICO 4 Pro", finished["error"])
        self.assertEqual(self.commands(), ["device probe"])
        self.assertNotIn("consent", [e.get("id") for e in ui.events if e["event"] == "ask"])

    def test_an_unverified_panel_is_refused_before_any_write(self):
        self.cmdline = INNOLUX
        ui = self.run_stages(("connect", "prepare"), self.yes("plug-in", "consent"))
        self.assertEqual((ui.finished()["status"], ui.finished()["task"]), ("failed", "panel"))
        self.assertIn("nt57900", ui.finished()["error"])
        self.assertEqual(self.commands(), ["device probe"])
        self.cmdline = "cat: /proc/cmdline: Permission denied"
        self.spawned.clear()
        ui = self.run_stages(("connect",), self.yes("plug-in"))
        self.assertEqual(ui.finished()["task"], "panel")  # a panel that cannot be read is refused too

    def test_the_panel_from_surfaceflinger_where_the_command_line_is_closed(self):
        # PICO OS's adb shell may not read /proc/cmdline; SurfaceFlinger names the panel.
        self.cmdline = 'cat: /proc/cmdline: Permission denied\nDisplay 1 (HWC display 0): displayName="sharp ls026b3"'
        ui = self.run_stages(("connect",), self.yes("plug-in"))
        self.assertEqual(ui.finished()["status"], "done")
        self.assertTrue(any("panel sharp_ls026b3sa, verified" in e.get("line", "") for e in ui.events))

    def test_another_headsets_identity_is_not_overwritten(self):
        self.identity.parent.mkdir(parents=True)
        self.identity.write_text(json.dumps({"schema": 1, "serial": "SOMEONE"}))
        ui = self.run_stages(("connect",), self.yes("plug-in"))
        self.assertEqual(ui.finished()["task"], "identity")
        self.assertIn("another headset", ui.finished()["error"])
        self.assertEqual(self.commands(), [])

    def test_a_saved_identity_of_this_headset_is_not_probed_again(self):
        self.identity.parent.mkdir(parents=True)
        self.identity.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "serial": ADB,
                    "fingerprint": FINGERPRINT.format("5.13.7"),
                    "oem_state": "true",
                    "chip_id": 7,
                }
            )
        )
        ui = self.run_stages(("connect",), self.yes("plug-in"))
        self.assertEqual(ui.finished()["status"], "done")
        self.assertEqual(self.commands(), [])
        statuses = [(e["task"], e["status"]) for e in ui.events if e["event"] == "task"]
        self.assertIn(("identity", "skipped"), statuses)

    def test_several_devices_or_none_end_in_words(self):
        original = self.capture

        def crowded(argv: list[str], timeout: float) -> tuple[int, str]:
            if Path(argv[0]).stem == "adb" and argv[1:] == ["devices"]:
                return 0, f"{ADB}\tdevice\nOTHER\tdevice\n"
            return original(argv, timeout)

        self.capture = crowded  # ty: ignore[invalid-assignment]  # a method replaced by a fake
        ui = self.run_stages(("connect",), self.yes("plug-in"))
        self.assertEqual(ui.finished()["task"], "find")
        self.assertIn("More than one Android device", ui.finished()["error"])
        notes = [e["text"] for e in ui.events if e["event"] == "note"]
        self.assertEqual(notes, ["More than one Android device is connected: unplug the others."])  # said once

    def test_a_stop_ends_the_wait_for_a_headset_that_is_never_plugged_in(self):
        original, polls, clock = self.capture, [], FakeClock()

        class Stoppable(Recorder):
            def start(self, stop: Callable[[], None]) -> None:
                self.stop = stop

        ui = Stoppable(self.yes("plug-in"))

        def empty(argv: list[str], timeout: float) -> tuple[int, str]:
            if Path(argv[0]).stem == "adb" and argv[1:] == ["devices"]:
                polls.append(clock.now)
                if len(polls) == 3:  # the window's Stop, while it waits
                    ui.stop()
                return 0, "List of devices attached\n"
            return original(argv, timeout)

        stages = [s for s in self.plan.stages() if s.id == "connect"]
        engine = Engine(
            HEADSET.info, stages, ui, root=self.root / "out" / "install", env=self.env, capture=empty, clock=clock
        )
        self.assertEqual(engine.run(), "stopped")
        self.assertEqual(len(polls), 3)
        self.assertLess(clock.now, plan.PLUG_IN_SECONDS / 10)  # not the ten minutes it waits at most
        self.assertEqual((ui.finished()["task"], ui.finished()["where"]), ("find", plan.NOTHING_CHANGED))
        self.assertNotIn("PICO4_ADB_SERIAL", self.env.read_text(encoding="utf-8"))

    def test_an_unfinished_backup_folder_stays_and_the_new_one_goes_elsewhere(self):
        self.backup.mkdir(parents=True)
        (self.backup / "edl.log").write_text("half a dump")
        self.run_stages(("connect", "prepare"), self.yes("plug-in", "consent"))
        new = self.settings().backup
        self.assertTrue(new.startswith("backup/stock-"), new)
        self.assertTrue((self.backup / "edl.log").exists())

    def test_declining_the_consent_writes_nothing(self):
        answers = self.yes("plug-in")
        answers["consent"] = {"answer": "consent", "accepted": False, "values": {}}
        ui = self.run_stages(("connect", "prepare"), answers)
        self.assertEqual(ui.finished()["status"], "stopped")
        self.assertEqual(self.commands(), ["device probe"])

    def test_the_wifi_password_reaches_the_headset_on_stdin_only(self):
        answers = {"wifi": {"answer": "wifi", "accepted": True, "values": {"ssid": "Home Net", "password": PASSWORD}}}
        ui = self.run_stages(("install",), answers)
        self.assertEqual(ui.finished()["status"], "done", ui.finished())
        self.assertEqual(self.commands(), ["dev install", "dev installed", "dev check"])
        ssh = [(argv, data) for argv, data, _ in self.spawned if data is not None]
        self.assertEqual(len(ssh), 1)
        argv, data = ssh[0]
        self.assertIn(plan.WIFI_FILE, argv[-1])
        self.assertTrue(all(PASSWORD not in word and "Home Net" not in word for word in argv))
        self.assertIn(f"psk={PASSWORD}".encode(), data or b"")
        self.assertIn(b"ssid=Home Net", data or b"")
        self.assertNotIn(PASSWORD, json.dumps(ui.events))
        self.assertNotIn("Home Net", json.dumps(ui.events))
        for path in (self.root / "out" / "install" / "phoenix").iterdir():
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(PASSWORD, text, path.name)
            self.assertNotIn("Home Net", text, path.name)
        self.assertNotIn(PASSWORD, self.env.read_text(encoding="utf-8"))
        self.assertIn(plan.READY, [e["text"] for e in ui.events if e["event"] == "note"])

    def test_no_wifi_writes_no_profile(self):
        ui = self.run_stages(("install",), {"wifi": {"answer": "wifi", "accepted": True, "values": {}}})
        self.assertEqual(ui.finished()["status"], "done")
        self.assertFalse([1 for _, data, _ in self.spawned if data is not None])

    def test_the_doctor_ignores_what_the_installer_provides(self):
        self.checks = [
            Check("PICO4_ADB_SERIAL", doctor.HEADSET, doctor.MISSING, "empty"),
            Check("EDL backup", doctor.HEADSET, doctor.MISSING, "none"),
            Check("assets/wlan-5.13.7", plan.checks.ASSETS, doctor.MISSING, "not extracted"),
            Check(
                "build container image",
                doctor.BUILDS,
                doctor.MISSING,
                "not built",
                "python -m overpressure container build",
            ),
            Check("git config", "build", doctor.WARNING, "none", "git config"),
        ]
        ui = self.run_stages(("computer",), {})
        doctor_task = [e for e in ui.events if e["event"] == "task" and e["task"] == "doctor"]
        self.assertEqual(doctor_task[-1]["status"], "done")
        self.assertIn("git config: none (git config)", [e["text"] for e in ui.events if e["event"] == "note"])
        # Every check it looked at is in the details, also the ones that passed.
        logged = [e["line"] for e in ui.events if e["event"] == "log" and e.get("task") == "doctor"]
        self.assertIn("warning: git config (build): none", logged)
        self.assertFalse(any("PICO4_ADB_SERIAL" in line for line in logged))
        self.checks.append(Check("ssh", doctor.HEADSET, doctor.MISSING, "not on PATH", "OpenSSH"))
        (self.root / "out" / "install" / "phoenix" / "state.json").unlink()  # a fresh start: the doctor runs again
        ui = self.run_stages(("computer",), {})
        self.assertEqual(ui.finished()["task"], "doctor")
        self.assertIn("- ssh (working on the headset): not on PATH\n  How: OpenSSH", ui.finished()["error"])

    def test_a_missing_or_silent_engine_is_explained_first_in_one_message(self):
        missing = [Check(doctor.ENGINE, doctor.BUILDS, doctor.MISSING, "neither docker nor podman", "install one")]
        text = plan.computer_problems(missing)
        self.assertEqual(text.splitlines()[1], plan.ENGINE_MISSING)
        self.assertIn("Docker or Podman", plan.ENGINE_MISSING)
        self.assertIn("start the installer again", plan.ENGINE_MISSING)
        other = [Check("ssh", doctor.HEADSET, doctor.MISSING, "not on PATH", "OpenSSH")]
        self.assertEqual(plan.engine_problem(other), "")
        self.assertNotIn(plan.ENGINE_MISSING, plan.computer_problems(other))


class ParserTests(unittest.TestCase):
    def test_edl_ng_output(self):
        self.assertEqual(plan.edl_serial(PRINTGPT), EDL)
        self.assertEqual(plan.edl_serial("no device"), "")
        self.assertEqual(plan.edl_luns(PRINTGPT), [1, 2, 0])
        self.assertEqual(plan.dump_progress("Dumping aop: 6.2% (0.03 / 0.50 MiB) [N/A]   "), (None, "Reading aop"))
        self.assertIsNone(plan.dump_progress("Saved main GPT"))

    def test_device_lists(self):
        adb = "* daemon started successfully\nList of devices attached\nA\tdevice\nB\tunauthorized\n\n"
        self.assertEqual(plan.device_rows(adb), {"A": "device", "B": "unauthorized"})
        self.assertEqual(plan.device_rows("F\tfastboot\n", ("fastboot",)), {"F": "fastboot"})

    def test_wifi_answers(self):
        self.assertEqual(plan.wifi_problem("", ""), "")
        self.assertEqual(plan.wifi_problem("net", ""), "")  # an open network
        self.assertEqual(plan.wifi_problem("net", "12345678"), "")
        self.assertEqual(plan.wifi_problem("net", "ab" * 32), "")  # the hexadecimal key
        self.assertIn("network name too", plan.wifi_problem("", "12345678"))
        self.assertIn("8 to 63", plan.wifi_problem("net", "short"))
        self.assertIn("32 bytes", plan.wifi_problem("n" * 33, ""))
        self.assertIn("control", plan.wifi_problem("net\n", "12345678"))

    def test_keyfile(self):
        text = plan.keyfile(" back\\slash", "pass word!", "uuid-1").decode()
        self.assertIn("id=\\sback\\\\slash\n", text)
        self.assertIn("[wifi-security]\nkey-mgmt=wpa-psk\npsk=pass word!\n", text)
        self.assertIn("uuid=uuid-1\n", text)
        self.assertNotIn("wifi-security", plan.keyfile("open", "", "u").decode())


class AfterInstallTests(unittest.TestCase):
    def test_the_finished_page_offers_update_pico_os_and_help(self):
        after = HEADSET.after_install()
        self.assertEqual(after.title, "Unplug it and put it on")
        self.assertIn("sign in to Steam", after.text)
        self.assertEqual(
            [(a.label, a.step) for a in after.actions],
            [("Update", "install"), ("Back to PICO OS", "restore"), ("Get help", "support-bundle")],
        )
        keys = {step.key for step in HEADSET.steps()}
        self.assertTrue(all(a.step in keys for a in after.actions))


def _tail(word: str) -> str:
    """Return the last two parts of a tool path (pt/adb), whatever the host's separators and suffix."""
    path = Path(word)
    return f"{path.parent.name}/{path.stem}"


if __name__ == "__main__":
    unittest.main()
