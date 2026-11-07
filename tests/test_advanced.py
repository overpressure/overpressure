# SPDX-License-Identifier: Unlicense
"""The window's Advanced mode: steps and command lines, the run, one thing at a time, the checks, the widgets."""

from __future__ import annotations

import queue
import sys
import time
import unittest
from typing import TYPE_CHECKING, Callable, NoReturn

from overpressure import REPO, doctor, runner, window
from overpressure.headset import Command, Headset, Info, Param, Registry, Step
from overpressure.install import Stage, Task
from overpressure.window import advanced, app, installer
from tests.test_installer import close

if TYPE_CHECKING:
    from overpressure.window.installer import Item

CHECKS = [
    doctor.Check("thing", "building", doctor.MISSING, "not there", "install thing"),
    doctor.Check("tool", "building", doctor.OK, "1.0"),
    doctor.Check("ssh", "working on the headset", doctor.WARNING, "old", "update it"),
]


class Fake(Headset):
    info = Info("fake", "Fake headset", "SoC", "a summary")

    def commands(self):
        return {"build": Command("image", "builds", keep=True), "flash": Command("flash", "flashes")}

    def steps(self):
        return (
            Step(
                "img",
                "Image",
                "Image",
                ("build", "--firmware", "{firmware}", "{fast}"),
                "Builds the image.",
                params=(Param("firmware", "Firmware", "a"), Param("fast", "Fast", "0", "flag", option="--fast")),
            ),
            Step(
                "us",
                "Userspace",
                "Image",
                ("scripts/build.sh", "{root}", "assets/fw-{version}", "{out}"),
                params=(
                    Param("root", "Root", "/var/tmp/b", "path"),  # noqa: S108  # a default word, nothing is written
                    Param("version", "Version", "1.2"),
                    Param("out", "Out", ""),
                ),
                script=True,
                linux_root=True,
            ),
            Step(
                "fl",
                "Flash",
                "Headset",
                ("flash", "{image}", "{folder}"),
                "Writes the image.",
                params=(Param("image", "Image", "out/x.img", "path"), Param("folder", "Folder", "out/dev", "path")),
                writes=True,
            ),
        )

    def install_plan(self):
        return (Stage("s", "Stage", "", (Task("t", "Task", lambda _ctx: None),)),)


class Other(Headset):
    info = Info("other", "Other headset")

    def commands(self):
        return {}

    def steps(self):
        return (Step("tools", "Tools", "Setup", ("tools",)),)


class FakeProcess:
    """A step's program that runs nothing: the test puts its lines and its exit."""

    def __init__(self) -> None:
        self.items: queue.Queue[Item] = queue.Queue()
        self.argv: list[str] | None = None
        self.stops = 0

    def start(self, argv: list[str]) -> None:
        self.argv = argv

    def stop(self) -> None:
        self.stops += 1


def fake_advanced(*headsets: Headset) -> tuple[advanced.Advanced, FakeProcess]:
    """Return Advanced mode for HEADSETS (else Fake) with a fake program and fixed checks."""
    process = FakeProcess()
    registry = Registry({h.info.codename: h for h in headsets or (Fake(),)})
    side = advanced.Advanced(registry, None, process=process, checks=lambda _h: CHECKS)
    return side, process


def receive_checks(checks: advanced.Checks) -> list[str]:
    """Wait for the checks' thread and return what it updated."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        updated = checks.receive()
        if updated:
            return updated
        time.sleep(0.01)
    msg = "the checks did not finish"
    raise AssertionError(msg)


class StepsTests(unittest.TestCase):
    def test_the_checks_come_first_and_the_steps_follow_their_groups(self):
        steps = advanced.Steps(Registry({"fake": Fake()}))
        groups = [(g, [e.key for e in entries]) for g, entries in steps.groups()]
        self.assertEqual(groups, [("Setup", ["doctor"]), ("Image", ["img", "us"]), ("Headset", ["fl"])])
        self.assertEqual(steps.selected, advanced.DOCTOR)
        self.assertIsNone(steps.step())
        self.assertEqual((steps.command(), steps.about()), ("", "SoC · a summary"))

    def test_the_command_line_follows_the_parameters(self):
        h = Fake()
        steps = advanced.Steps(Registry({"fake": h}))
        self.assertTrue(steps.select("img"))
        self.assertIn("--device fake build --firmware a", steps.command())
        steps.set_value("fast", "1")
        steps.set_value("firmware", "b")
        self.assertEqual(steps.argv(), runner.process(h, h.steps()[0], {"firmware": "b", "fast": "1"}))
        self.assertTrue(steps.command().endswith("build --firmware b --fast"))
        self.assertEqual(steps.command(), runner.display(steps.argv()))

    def test_values_are_kept_per_headset_and_step(self):
        steps = advanced.Steps(Registry({"fake": Fake(), "other": Other()}))
        steps.select("img")
        steps.set_value("firmware", "b")
        steps.choose("other")
        self.assertEqual(steps.selected, advanced.DOCTOR)  # it has no img
        self.assertFalse(steps.select("img"))
        self.assertTrue(steps.select("tools"))
        steps.choose("fake")
        steps.select("img")
        self.assertIn("--firmware b", steps.command())

    def test_help_says_where_it_runs_and_that_it_writes(self):
        steps = advanced.Steps(Registry({"fake": Fake()}))
        _, us, fl = Fake().steps()
        self.assertEqual(steps.help(fl), "Writes the image. Writes to the headset: asks before it starts.")
        self.assertEqual(steps.help(us), "Runs on Linux as root, in the build container.")

    def test_browse_picks_files_and_folders_on_this_host_only(self):
        _, us, fl = Fake().steps()
        self.assertEqual([advanced.browse(fl, p) for p in fl.params], ["file", "folder"])
        self.assertEqual(advanced.browse(us, us.params[0]), "")  # in the container's /var/tmp volume
        inside = Param("out", "Out", "out/userspace", "path")
        self.assertEqual(advanced.browse(us, inside), "folder")  # in the repository: the container sees it
        self.assertEqual(advanced.browse(us, us.params[1]), "")  # not a path
        self.assertEqual(advanced.relative(str(REPO / "out" / "x.img")), "out/x.img")
        outside = str(REPO.parent / "elsewhere.img")
        self.assertEqual(advanced.relative(outside), outside)


class ContainerPathTests(unittest.TestCase):
    STEP = Step(
        "b",
        "Build",
        "Image",
        ("scripts/build.sh", "{root}", "{out}"),
        params=(Param("root", "Build folder", "/var/tmp/b", "path"), Param("out", "Output", "out/b", "path")),  # noqa: S108  # a default word, nothing is written
        script=True,
        linux_root=True,
    )

    def test_the_container_sees_the_repository_and_var_tmp_only(self):
        self.assertEqual(advanced.container_path("/var/tmp/pico4-build"), "/var/tmp/pico4-build")  # noqa: S108  # a path word
        self.assertEqual(advanced.container_path("/work/out"), "/work/out")
        self.assertEqual(advanced.container_path("out/userspace"), "out/userspace")
        self.assertEqual(advanced.container_path(str(REPO / "out" / "x")), "out/x")
        self.assertIsNone(advanced.container_path(str(REPO.parent / "elsewhere")))
        self.assertIsNone(advanced.container_path("../elsewhere"))
        self.assertIsNone(advanced.container_path("/etc/passwd"))
        self.assertIsNone(advanced.container_path("/var/tmpfoo"))  # noqa: S108  # a path word

    def test_a_path_outside_is_refused_before_the_run_and_one_inside_made_relative(self):
        class One(Fake):
            def steps(self) -> tuple[Step, ...]:
                return (ContainerPathTests.STEP,)

        model = advanced.Advanced(Registry({"fake": One()}), process=FakeProcess(), checks=lambda _h: [])
        model.steps.select("b")
        self.assertEqual(model.press(installer_running=False)[0], advanced.START)
        model.steps.set_value("out", str(REPO / "out" / "elsewhere"))
        self.assertIn("out/elsewhere", model.steps.argv())  # relative, as the container sees it
        self.assertEqual(model.press(installer_running=False)[0], advanced.START)
        model.steps.set_value("out", str(REPO.parent / "outside"))
        kind, title, text = model.press(installer_running=False)
        self.assertEqual((kind, title), (advanced.REFUSED, "Path outside the repository"))
        self.assertIn("The build container sees only the repository and /var/tmp", text)
        model.steps.set_value("out", "out/b")
        model.steps.set_value("root", "/srv/build")
        self.assertEqual(model.press(installer_running=False)[0], advanced.REFUSED)


class RunTests(unittest.TestCase):
    def test_a_run_ends_done_failed_or_stopped(self):
        img = Fake().steps()[0]
        run = advanced.Run()
        self.assertEqual((run.status(), run.running), ("Nothing is running.", False))
        self.assertFalse(run.stop())
        for code, state, status in ((0, "done", "Image: done"), (3, "failed", "Image: failed (exit status 3)")):
            run.begin(img, "python build")
            self.assertEqual((run.running, run.status()), (True, "Running: Image"))
            run.ended(code)
            self.assertEqual((run.state, run.status(), run.running), (state, status, False))
        run.begin(img, "python build")
        self.assertTrue(run.stop())
        self.assertEqual((run.status(), run.running), ("Stopping: Image", True))
        run.ended(1)
        self.assertEqual(run.status(), "Image: stopped")
        lines, whole = run.take()
        self.assertEqual((lines[0], lines[-1], whole), ("$ python build", "[exit 1]", False))
        self.assertEqual(run.take(), ([], False))

    def test_the_log_is_bounded_and_a_backlog_replaces_what_is_shown(self):
        run = advanced.Run()
        for n in range(advanced.LOG_LINES + 5):
            run.add(f"line {n}")
        self.assertEqual(len(run.log), advanced.LOG_LINES)
        lines, whole = run.take()
        self.assertTrue(whole)
        self.assertEqual((len(lines), lines[-1]), (advanced.LOG_LINES, f"line {advanced.LOG_LINES + 4}"))


class OneThingAtATimeTests(unittest.TestCase):
    def test_a_step_waits_for_the_installer_and_the_installer_for_a_step(self):
        busy = advanced.busy
        self.assertEqual(busy(advanced.STEP, installer_running=False, step_running=False), "")
        self.assertIn("The installer is running", busy(advanced.STEP, installer_running=True, step_running=False))
        self.assertIn("A step is running", busy(advanced.STEP, installer_running=False, step_running=True))
        self.assertIn("A step of Advanced mode", busy(advanced.INSTALLER, installer_running=False, step_running=True))
        self.assertEqual(busy(advanced.INSTALLER, installer_running=False, step_running=False), "")


class AdvancedTests(unittest.TestCase):
    def test_a_step_that_does_not_write_starts_at_once_and_streams_its_output(self):
        side, process = fake_advanced()
        self.assertEqual(side.press(installer_running=False)[0], advanced.REFUSED)  # the checks are selected
        side.steps.select("img")
        self.assertEqual(side.press(installer_running=False), (advanced.START, "", ""))
        self.assertTrue(side.start())
        self.assertEqual(process.argv, side.steps.argv())
        self.assertFalse(side.start())  # one at a time
        process.items.put(("line", "building"))
        self.assertFalse(side.receive())
        process.items.put(("exit", 0))
        self.assertTrue(side.receive())
        self.assertEqual(side.run.status(), "Image: done")
        self.assertEqual(list(side.run.log)[1:], ["building", "[exit 0]"])
        self.assertTrue(side.run.log[0].startswith("$ ") and side.run.log[0].endswith("build --firmware a"))

    def test_a_step_that_writes_asks_first_with_its_command_line(self):
        side, process = fake_advanced()
        side.steps.select("fl")
        kind, title, text = side.press(installer_running=False)
        self.assertEqual((kind, title), (advanced.ASK, "Write to the headset?"))
        self.assertIn("Flash: Writes the image.", text)
        self.assertIn(side.steps.command(), text)
        self.assertIsNone(process.argv)  # nothing runs before the answer

    def test_nothing_starts_while_something_runs(self):
        side, _ = fake_advanced()
        side.steps.select("img")
        kind, title, text = side.press(installer_running=True)
        self.assertEqual((kind, title), (advanced.REFUSED, "One thing at a time"))
        self.assertIn("The installer is running", text)
        side.start()
        side.steps.select("us")
        self.assertIn("A step is running", side.press(installer_running=False)[2])
        self.assertEqual(side.run.key, "img")

    def test_stop_ends_the_program_and_the_run_says_stopped(self):
        side, process = fake_advanced()
        side.stop()
        self.assertEqual(process.stops, 0)  # nothing runs
        side.steps.select("img")
        side.start()
        side.stop()
        side.stop()
        self.assertEqual(process.stops, 1)
        process.items.put(("exit", 1))
        side.receive()
        self.assertEqual(side.run.status(), "Image: stopped")


class ProcessTests(unittest.TestCase):
    def items(self, process: advanced.StepProcess | advanced.DemoProcess) -> list[Item]:
        found = []
        while True:
            item = process.items.get(timeout=30)
            found.append(item)
            if item[0] == "exit":
                return found

    def test_a_steps_program_streams_its_lines_and_its_exit_status(self):
        process = advanced.StepProcess()
        process.start([sys.executable, "-c", "print('a'); print('b'); raise SystemExit(3)"])
        self.assertEqual(self.items(process), [("line", "a"), ("line", "b"), ("exit", 3)])

    def test_stop_ends_the_program(self):
        process = advanced.StepProcess()
        process.start([sys.executable, "-c", "import time; print('up', flush=True); time.sleep(60)"])
        self.assertEqual(process.items.get(timeout=30), ("line", "up"))
        process.stop()
        kind, code = process.items.get(timeout=30)
        self.assertEqual(kind, "exit")
        self.assertNotEqual(code, 0)

    def test_a_program_that_cannot_start_fails(self):
        process = advanced.StepProcess()
        process.start(["overpressure-no-such-program"])
        found = self.items(process)
        self.assertTrue(found[0][1].startswith("error: "))  # ty: ignore[unresolved-attribute]  # a line
        self.assertEqual(found[-1], ("exit", -1))

    def test_the_demo_runs_nothing(self):
        demo = advanced.DemoProcess(pace=0)
        demo.start(["python", "-m", "overpressure", "tools"])
        found = self.items(demo)
        self.assertEqual((len(found), found[-1]), (advanced.DemoProcess.LINES + 1, ("exit", 0)))
        self.assertIn("nothing runs", str(found[0][1]))
        demo = advanced.DemoProcess(pace=40)
        demo.start(["tools"])
        demo.stop()
        self.assertEqual(self.items(demo), [("exit", 1)])


class ChecksTests(unittest.TestCase):
    def test_the_checks_are_grouped_summed_up_and_the_first_one_to_fix_is_selected(self):
        checks = advanced.Checks(lambda _h: CHECKS)
        self.assertEqual(
            (checks.summary("fake"), checks.label("fake"), checks.fix("fake")), ("", "Check dependencies", "")
        )
        self.assertTrue(checks.start(Fake()))
        self.assertFalse(checks.start(Fake()))  # already checking
        self.assertEqual(checks.summary("fake"), "Checking ...")
        self.assertEqual(receive_checks(checks), ["fake"])
        groups = [(needed_for, [i for i, _ in group]) for needed_for, group in checks.groups("fake")]
        self.assertEqual(groups, [("building", [0, 1]), ("working on the headset", [2])])
        self.assertEqual(checks.label("fake"), "Check dependencies (1 missing)")
        self.assertEqual(
            checks.summary("fake"), "1 missing: thing. 1 warning. The checks only look; they install nothing."
        )
        self.assertEqual(checks.fix("fake"), "install thing")
        checks.select("fake", 1)
        self.assertEqual(checks.fix("fake"), "")  # it is fine
        checks.select("fake", 2)
        self.assertEqual(checks.fix("fake"), "update it")
        checks.select("fake", 7)  # no such check: the selection stays
        self.assertEqual(checks.fix("fake"), "update it")

    def test_all_there_and_a_broken_run(self):
        checks = advanced.Checks(lambda _h: [CHECKS[1]])
        checks.start(None)
        receive_checks(checks)
        self.assertEqual(checks.label(""), "Check dependencies (all there)")
        self.assertEqual(checks.summary(""), "Everything needed is there. The checks only look; they install nothing.")

        def broken(_h: Headset | None) -> NoReturn:
            msg = "probe broke"
            raise RuntimeError(msg)

        checks = advanced.Checks(broken)
        checks.start(None)
        receive_checks(checks)
        (check,) = checks.results[""]
        self.assertEqual((check.status, check.detail), (doctor.WARNING, "the checks failed: probe broke"))


class ViewTests(unittest.TestCase):
    """Advanced mode's widgets, where Tk, a display and CustomTkinter are there."""

    def open(self, side: advanced.Advanced) -> app.Window:
        if window.available():
            self.skipTest(window.available())
        import tkinter as tk  # noqa: PLC0415  # optional: checked above

        import customtkinter  # noqa: PLC0415  # the same

        try:
            shown = app.Window(customtkinter, installer.DemoEngine(pace=0), side, view=app.ADVANCED_VIEW)
        except tk.TclError as exc:  # no display
            self.skipTest(f"no Tk display: {exc}")
        self.addCleanup(close, shown)
        return shown

    @staticmethod
    def pump(shown: app.Window, until: Callable[[], bool]) -> None:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            changed, _ = shown.receive()
            if changed:
                shown.render()
            shown.advanced_view.tick()
            shown.root.update()
            if until():
                return
            time.sleep(0.01)
        msg = "the window did not get there"
        raise AssertionError(msg)

    def test_the_checks_are_listed_with_the_selected_ones_fix(self):
        side, _ = fake_advanced()
        shown = self.open(side)
        view = shown.advanced_view
        self.pump(shown, lambda: "fake" in side.checks.results)
        self.assertEqual(len(view.check_rows), 3)
        self.assertEqual(view.entries["doctor"][1].cget("text"), "Check dependencies (1 missing)")
        self.assertEqual(view.fix.get("1.0", "end").strip(), "install thing")
        view.select_check(2)
        self.assertEqual(view.fix.get("1.0", "end").strip(), "update it")

    def test_a_steps_parameters_change_its_command_line(self):
        side, _ = fake_advanced()
        shown = self.open(side)
        view = shown.advanced_view
        view.select("img")
        shown.root.update()
        view.vars["fast"].set("1")
        shown.root.update()
        self.assertIn("build --firmware a --fast", view.command.get("1.0", "end"))
        view.select("us")
        shown.root.update()
        self.assertEqual(sorted(view.vars), ["out", "root", "version"])

    def test_a_run_asks_first_when_it_writes_and_streams_into_the_log(self):
        side, process = fake_advanced()
        shown = self.open(side)
        view = shown.advanced_view
        view.select("fl")
        view.press_run()
        self.assertIsNotNone(view.panel)
        self.assertIsNone(process.argv)
        view.hide_panel()
        view.confirmed(accepted=True)  # what Run in the panel does
        self.assertEqual(process.argv, side.steps.argv())
        self.assertEqual(view.status.cget("text"), "Running: Flash")
        process.items.put(("line", "writing"))
        process.items.put(("exit", 0))
        self.pump(shown, lambda: not side.run.running)
        self.assertIn("writing", view.log.get("1.0", "end"))
        self.assertEqual(view.status.cget("text"), "Flash: done")

    def test_one_thing_at_a_time_in_the_window(self):
        side, process = fake_advanced()
        shown = self.open(side)
        self.pump(shown, lambda: not shown.probing)
        view = shown.advanced_view
        view.select("img")
        shown.model.running = True  # as while an install runs
        view.press_run()
        self.assertIsNotNone(view.panel)
        self.assertIsNone(process.argv)
        view.hide_panel()
        shown.model.running = False
        view.press_run()
        self.assertIsNotNone(process.argv)
        shown.start()  # the installer, while the step runs
        self.assertFalse(shown.model.running)
        self.assertIsNotNone(shown.panel)

    def test_the_list_follows_the_windows_headset(self):
        side, _ = fake_advanced(Fake(), Other())
        shown = self.open(side)
        view = shown.advanced_view
        self.assertEqual(view.headset_name.cget("text"), "Fake headset")
        self.assertIn("img", view.entries)
        self.assertTrue(shown.to_headsets())  # the header's way back to the headset page
        self.assertTrue(shown.choose("other"))
        self.assertEqual(sorted(view.entries), ["doctor", "tools"])
        self.assertEqual((side.steps.codename(), view.headset_name.cget("text")), ("other", "Other headset"))

    def test_the_header_switches_views_and_the_after_install_choices_open_a_step(self):
        side, _ = fake_advanced()
        shown = self.open(side)
        self.assertEqual(shown.mode_link.cget("text"), "Installer")
        shown.switch()
        self.assertEqual((shown.view, shown.mode_link.cget("text")), (app.INSTALLER_VIEW, "Advanced mode"))
        shown.open_advanced("fl")
        self.assertEqual((shown.view, side.steps.selected), (app.ADVANCED_VIEW, "fl"))
        self.assertEqual(shown.advanced_view.title.cget("text"), "Flash")


if __name__ == "__main__":
    unittest.main()
