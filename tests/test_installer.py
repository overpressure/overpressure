# SPDX-License-Identifier: Unlicense
"""The window's installer view: its event model, its engines, the gui command and a smoke test of the widgets."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import time
import unittest
from typing import Callable
from unittest import mock

from overpressure import __main__ as cli
from overpressure import install, window
from overpressure.headset import Registry
from overpressure.window import advanced, app, installer

PLAN = {
    "event": "plan",
    "headset": {"codename": "phoenix", "name": "PICO 4"},
    "stages": [
        {"id": "computer", "title": "Check this computer", "summary": "s1", "tasks": [{"id": "tools", "title": "T"}]},
        {
            "id": "install",
            "title": "Install",
            "summary": "s2",
            "tasks": [
                {"id": "wifi", "title": "Wi-Fi", "estimate": "optional", "writes": True},
                {"id": "system", "title": "System", "estimate": "about 15 min", "writes": True},
            ],
        },
    ],
}
WIFI = {
    "event": "ask",
    "id": "wifi",
    "kind": "form",
    "title": "Wi-Fi",
    "text": "t",
    "fields": [{"name": "ssid", "label": "Network name"}, {"name": "password", "label": "Password", "secret": True}],
}


def feed(model: installer.Model, *events: dict) -> None:
    """Give MODEL the EVENTS as the engine's lines."""
    for event in events:
        model.feed(json.dumps(event) + "\n")


def task(stage: str, name: str, status: str) -> dict:
    """Return a task event."""
    return {"event": "task", "stage": stage, "task": name, "status": status}


def get(model: installer.Model, name: str) -> installer.Task:
    """Return the plan's task NAME, which must be there."""
    found = model.task(name)
    assert found is not None
    return found


def close(shown: app.Window) -> None:
    """Destroy a test's window and the callbacks it has scheduled, which would run after it is gone."""
    for after in shown.root.tk.call("after", "info"):
        shown.root.tk.call("after", "cancel", after)
    shown.root.destroy()


def ended(model: installer.Model) -> dict:
    """Return the finished event, which must be there."""
    assert model.finished is not None
    return model.finished


class ModelTests(unittest.TestCase):
    def test_the_plan_gives_stages_tasks_and_the_first_stage(self):
        m = installer.Model()
        feed(m, PLAN)
        self.assertEqual([s.id for s in m.stages], ["computer", "install"])
        self.assertEqual(get(m, "system").estimate, "about 15 min")
        self.assertTrue(get(m, "wifi").writes)
        self.assertEqual((m.current, m.view(), m.button()), ("computer", "stage", "Start"))
        self.assertEqual(m.headset["name"], "PICO 4")

    def test_tasks_move_the_current_stage_and_the_stage_states(self):
        m = installer.Model()
        feed(m, PLAN)
        m.begin()
        feed(m, task("computer", "tools", "done"), task("install", "wifi", "running"))
        self.assertEqual(m.current, "install")
        self.assertEqual([s.status for s in m.stages], ["done", "running"])
        self.assertEqual(m.button(), "")  # the engine works: no main button
        feed(m, task("install", "wifi", "skipped"), task("install", "system", "failed"))
        self.assertEqual(m.stages[1].status, "failed")

    def test_progress_notes_and_log_lines(self):
        m = installer.Model()
        feed(m, PLAN, task("computer", "tools", "running"))
        feed(m, {"event": "progress", "task": "tools", "fraction": 1.7, "text": "30 of 30 MB"})
        self.assertEqual((get(m, "tools").fraction, get(m, "tools").progress), (1.0, "30 of 30 MB"))
        feed(m, {"event": "progress", "task": "tools", "fraction": None})
        self.assertIsNone(get(m, "tools").fraction)
        feed(m, task("computer", "tools", "done"))
        self.assertEqual((get(m, "tools").fraction, get(m, "tools").progress), (None, ""))
        feed(m, {"event": "note", "task": "system", "level": "warning", "text": "Keep the cable in."})
        self.assertEqual(m.stages[1].notes, [("warning", "Keep the cable in.")])
        m.log.clear()
        feed(m, {"event": "log", "task": "tools", "line": "fetched"})
        m.feed("Traceback (most recent call last):\r\n")  # not an event: a log line
        m.feed('["not", "an", "event"]')
        self.assertEqual(
            list(m.log), ["tools: fetched", "Traceback (most recent call last):", '["not", "an", "event"]']
        )

    def test_the_details_tell_every_status_change_note_ask_and_end(self):
        # A run whose steps were all done before, or are skipped, still has details to show.
        m = installer.Model()
        feed(
            m,
            PLAN,
            task("computer", "tools", "done"),
            task("computer", "tools", "running"),
            task("computer", "tools", "done"),
            task("computer", "tools", "skipped"),
            task("install", "system", "failed"),
            task("install", "system", "pending"),
            {"event": "note", "task": "system", "level": "warning", "text": "Keep the cable in."},
            WIFI,
            {"event": "finished", "status": "failed", "task": "system", "error": "No headset.", "where": "Plug it in."},
        )
        self.assertEqual(
            list(m.log),
            [
                "T: done earlier",
                "T: started",
                "T: done",
                "T: already there, skipped",
                f"{get(m, 'system').title}: failed",
                "Note: Keep the cable in.",
                f"Waiting for you: {WIFI['title']}",
                "Failed: No headset. Plug it in.",
            ],
        )
        feed(m, {"event": "finished", "status": "done"})
        self.assertEqual(m.log[-1], "Finished.")

    def test_the_log_is_bounded(self):
        m = installer.Model()
        for n in range(installer.LOG_LINES + 5):
            m.feed(f"line {n}")
        self.assertEqual(len(m.log), installer.LOG_LINES)
        self.assertEqual(m.log[-1], f"line {installer.LOG_LINES + 4}")

    def test_a_form_answer_carries_its_values_once_and_clears_the_ask(self):
        m = installer.Model()
        feed(m, PLAN, WIFI)
        self.assertEqual(m.view(), "ask")
        line = m.answer(accepted=True, values={"ssid": "net", "password": "pw"})
        self.assertEqual(
            json.loads(line), {"answer": "wifi", "accepted": True, "values": {"ssid": "net", "password": "pw"}}
        )
        self.assertIsNone(m.ask)
        self.assertNotIn("pw", "\n".join(m.log))
        with self.assertRaises(RuntimeError):
            m.answer(accepted=True)

    def test_a_declined_confirm_sends_no_values(self):
        m = installer.Model()
        feed(m, {"event": "ask", "id": "consent", "kind": "confirm", "title": "Erase?", "text": "t"})
        self.assertEqual(json.loads(m.answer(accepted=False)), {"answer": "consent", "accepted": False, "values": {}})

    def test_the_end_of_a_run(self):
        m = installer.Model()
        feed(m, PLAN)
        m.begin()
        feed(m, task("computer", "tools", "running"))
        feed(m, {"event": "finished", "status": "failed", "task": "tools", "error": "e", "where": "w"})
        m.exited(1)
        self.assertEqual((m.view(), get(m, "tools").status), ("failed", "failed"))
        m.begin()  # Retry
        self.assertEqual((m.view(), get(m, "tools").status), ("stage", "pending"))
        feed(m, {"event": "finished", "status": "stopped", "task": "tools", "error": "", "where": ""})
        m.exited(3)
        self.assertEqual((m.view(), m.button()), ("stage", "Continue"))
        m.begin()
        feed(m, {"event": "finished", "status": "done", "task": None})
        m.exited(0)
        self.assertEqual(m.view(), "done")

    def test_an_engine_that_exits_without_finishing_has_failed(self):
        m = installer.Model()
        m.begin()
        m.exited(1)
        self.assertEqual(m.view(), "failed")
        self.assertIn("exit status 1", ended(m)["error"])

    def test_a_new_plan_keeps_what_was_finished(self):
        m = installer.Model()
        feed(m, PLAN, task("computer", "tools", "done"), task("install", "wifi", "failed"))
        feed(m, PLAN)
        self.assertEqual((get(m, "tools").status, get(m, "wifi").status), ("done", "pending"))
        self.assertEqual(m.current, "install")

    def test_a_plan_with_a_failed_task_offers_retry(self):
        m = installer.Model()
        feed(m, PLAN, task("computer", "tools", "failed"))  # what install --plan reports
        self.assertEqual(m.button(), "Retry")


class StartPageTests(unittest.TestCase):
    """What the window shows first, from the plan probe (install --plan)."""

    def test_without_a_recorded_run_the_first_stage_starts(self):
        m = installer.Model()
        feed(m, PLAN, task("computer", "tools", "pending"), task("install", "wifi", "pending"))
        m.probed()
        self.assertFalse(m.start_page)
        self.assertEqual((m.view(), m.current, m.button()), ("stage", "computer", "Start"))

    def test_a_recorded_run_shows_the_start_page_and_its_choices(self):
        m = installer.Model()
        feed(m, PLAN, task("computer", "tools", "done"), task("install", "wifi", "skipped"))
        m.probed()
        self.assertEqual((m.view(), m.current, m.button()), ("start", "install", "Continue where you left off"))
        self.assertEqual(m.progress_text(), "Done so far: Check this computer. It stopped in Install.")
        self.assertEqual([(s.id, s.writes) for s in m.restart_stages()], [("computer", False), ("install", True)])
        title, text, warning = m.restart_question("computer")
        self.assertEqual(title, "Start over from Check this computer?")
        self.assertIn("forgets what was done in Check this computer and Install and does them again", text)
        self.assertIn("Install writes to the headset", warning)
        self.assertIn("doing it again repeats its backup", warning)
        m.begin()  # either choice starts the engine: the start page goes
        self.assertEqual((m.start_page, m.view()), (False, "stage"))
        m.exited(3)
        m.probed()  # a later probe in the same window shows no start page again
        self.assertFalse(m.start_page)

    def test_a_stage_without_writes_starts_over_without_a_warning(self):
        m = installer.Model()
        feed(m, PLAN, task("computer", "tools", "done"))
        m.probed()
        self.assertEqual(m.restart_question("computer")[2], "")
        self.assertIn("does it again from its start", m.restart_question("computer")[1])
        self.assertEqual(m.restart_question("install")[0], "Start over from Install?")

    def test_a_failure_or_a_run_that_never_ended_counts_as_recorded(self):
        m = installer.Model()
        feed(m, PLAN, task("install", "system", "failed"))
        m.probed()
        self.assertEqual((m.view(), m.current), ("start", "install"))
        self.assertIn("A step of Install failed", m.progress_text())
        m = installer.Model()
        feed(m, PLAN, task("computer", "tools", "running"))  # the engine was killed
        m.probed()
        self.assertEqual((get(m, "tools").status, m.view()), ("pending", "start"))
        self.assertIn("Nothing has been finished yet. It stopped in Check this computer.", m.progress_text())

    def test_everything_done(self):
        m = installer.Model()
        feed(
            m,
            PLAN,
            task("computer", "tools", "done"),
            task("install", "wifi", "done"),
            task("install", "system", "done"),
        )
        m.probed()
        self.assertIn("Every stage is done", m.progress_text())

    def test_prose_lists(self):
        self.assertEqual(
            [installer.joined(w) for w in (["a"], ["a", "b"], ["a", "b", "c"])], ["a", "a and b", "a, b and c"]
        )


class StopTests(unittest.TestCase):
    """The window's stop: running, stopping until the finished event, stopped with Continue."""

    def running(self, name: str) -> installer.Model:
        m = installer.Model()
        feed(m, PLAN)
        m.begin()
        feed(m, task("install" if name != "tools" else "computer", name, "running"))
        return m

    def test_a_task_that_does_not_write_stops_now(self):
        m = self.running("tools")
        self.assertEqual((m.phase(), m.stop_label()), ("running", "Stop"))
        self.assertEqual(m.stop_question(), (installer.STOP_TITLE, installer.STOP_NOW))
        m.request_stop()
        self.assertEqual(
            (m.phase(), m.stop_label(), m.stop_after_write), ("stopping", "Stopping\N{HORIZONTAL ELLIPSIS}", False)
        )
        self.assertEqual(m.button(), "")
        feed(m, {"event": "finished", "status": "stopped", "task": "tools", "error": "", "where": "Nothing changed."})
        self.assertEqual((m.phase(), m.stop_label()), ("stopped", ""))
        m.exited(3)
        self.assertEqual((m.view(), m.button(), m.phase()), ("stage", "Continue", "stopped"))

    def test_a_task_that_writes_finishes_first(self):
        m = self.running("system")
        self.assertEqual(m.stop_question()[1], installer.STOP_AFTER_WRITE)
        m.request_stop()
        self.assertTrue(m.stop_after_write)

    def test_while_an_ask_waits_it_stops_now_and_the_ask_goes(self):
        m = self.running("wifi")  # it writes, but it waits for the owner: nothing is being written
        feed(m, WIFI)
        self.assertEqual(m.stop_question()[1], installer.STOP_NOW)
        m.request_stop()
        self.assertEqual((m.ask, m.view(), m.phase()), (None, "stage", "stopping"))

    def test_no_stop_when_nothing_runs(self):
        m = installer.Model()
        feed(m, PLAN)
        m.request_stop()
        self.assertEqual((m.phase(), m.stop_label()), ("idle", ""))


def drained(engine: installer.DemoEngine, extra: list[str] | None, model: installer.Model) -> list[dict]:
    """Start ENGINE with EXTRA (None: it runs already), feed MODEL its lines until it exits; return the events."""
    if extra is not None:
        engine.start(extra)
    events = []
    while (item := engine.items.get(timeout=30))[0] == "line":
        events.append(model.feed(str(item[1])))
    return events


class Answerer:
    """Drive an engine like the window: apply its lines and answer every ask, until it exits.

    STOP_AT: the task whose start stops the run, as the window's Stop does.
    """

    def __init__(self, engine: installer.DemoEngine | installer.ChildEngine, stop_at: str = "") -> None:
        self.engine, self.model, self.asks, self.stop_at = engine, installer.Model(), [], stop_at

    def run(self, extra: list[str], values: dict[str, str]) -> int:
        self.model.begin()
        self.engine.start(extra)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            kind, value = self.engine.items.get(timeout=30)
            if kind == "exit":
                self.model.exited(int(value))
                return int(value)
            event = self.model.feed(str(value))
            if event["event"] == "task" and (event["task"], event["status"]) == (self.stop_at, "running"):
                self.model.request_stop()
                self.engine.stop()
            if event["event"] == "ask" and not self.model.stopping:
                self.asks.append(event["id"])
                self.engine.send(self.model.answer(accepted=True, values=values if event["kind"] == "form" else None))
        msg = "the engine did not exit"
        raise AssertionError(msg)


class DemoTests(unittest.TestCase):
    def test_the_demo_fails_once_then_finishes(self):
        demo = installer.DemoEngine(pace=0)
        driver = Answerer(demo)
        driver.run([], {"ssid": "net", "password": "pw"})
        self.assertEqual(driver.model.view(), "failed")
        self.assertEqual(ended(driver.model)["task"], "unlock")
        self.assertEqual(driver.asks, ["plug-in", "consent"])
        self.assertTrue(any(s.notes for s in driver.model.stages))
        driver.run([], {"ssid": "net", "password": "pw"})
        self.assertEqual(driver.model.view(), "done")
        self.assertEqual(driver.asks, ["plug-in", "consent", "wifi"])
        self.assertNotIn("pw", "\n".join(driver.model.log))
        self.assertTrue(all(t.status in installer.FINISHED for s in driver.model.stages for t in s.tasks))

    def test_the_demos_plan_only(self):
        for recorded in (False, True):
            demo = installer.DemoEngine(pace=0, recorded=recorded)
            model = installer.Model()
            events = drained(demo, ["--plan"], model)
            model.probed()
            self.assertEqual(events[0]["event"], "plan")
            self.assertEqual({e["event"] for e in events[1:]}, {"task"})
            self.assertEqual(model.start_page, recorded)
            self.assertEqual(
                [s.status for s in model.stages], ["done"] * 4 + ["pending"] if recorded else ["pending"] * 5
            )

    def test_the_demo_starts_over_from_a_stage(self):
        demo = installer.DemoEngine(pace=0, recorded=True)
        demo.start(["--restart", "build"])
        while (event := json.loads(str(demo.items.get(timeout=10)[1])))["event"] != "task" or event["status"] != "done":
            pass
        while event["status"] != "running":
            event = json.loads(str(demo.items.get(timeout=10)[1]))
        first = event
        demo.stop()
        self.assertEqual(first["task"], "kernel")  # the build stage again, the computer's stays done

    def test_a_stop_ends_a_task_that_does_not_write_at_once(self):
        demo, model = installer.DemoEngine(pace=0.2), installer.Model()
        demo.start([])
        while (event := model.feed(str(demo.items.get(timeout=10)[1])))["event"] != "task":
            pass
        self.assertEqual((event["task"], event["status"]), ("doctor", "running"))
        demo.stop()
        drained(demo, None, model)
        self.assertEqual(ended(model)["status"], "stopped")
        self.assertEqual(ended(model)["where"], install.STOPPED_NOTHING)
        self.assertEqual(get(model, "doctor").status, "pending")

    def test_a_stop_lets_a_task_that_writes_finish(self):
        driver = Answerer(installer.DemoEngine(pace=0.1), stop_at="abl")
        driver.run([], {})
        self.assertEqual(get(driver.model, "abl").status, "done")  # it finished, then the run stopped
        self.assertEqual(
            (ended(driver.model)["task"], ended(driver.model)["where"]), ("unlock", install.STOPPED_BETWEEN)
        )

    def test_a_stop_does_not_answer_the_next_runs_first_ask(self):
        # The stop's leftover belonged to the stopped run: continuing must still ask, not take it as a decline.
        driver = Answerer(installer.DemoEngine(pace=0.1), stop_at="abl")
        driver.run([], {})
        self.assertEqual(ended(driver.model)["status"], "stopped")
        driver.stop_at = ""
        driver.run([], {"ssid": "net", "password": "pw"})  # the demo's one failure, at unlock
        self.assertEqual((ended(driver.model)["status"], ended(driver.model)["task"]), ("failed", "unlock"))
        driver.run([], {"ssid": "net", "password": "pw"})
        self.assertEqual(ended(driver.model)["status"], "done")
        self.assertIn("wifi", driver.asks)

    def test_a_stopped_demo_finishes_as_stopped(self):
        demo = installer.DemoEngine(pace=0)
        model = installer.Model()
        demo.start([])
        demo.stop()
        while (item := demo.items.get(timeout=10))[0] == "line":
            model.feed(str(item[1]))
        self.assertEqual(ended(model)["status"], "stopped")


class ChildEngineTests(unittest.TestCase):
    def test_its_command_line(self):
        self.assertEqual(
            installer.ChildEngine("phoenix").argv(["--plan"]),
            [sys.executable, "-m", "overpressure", "--device", "phoenix", "install", "--json", "--plan"],
        )
        self.assertEqual(installer.ChildEngine(None).argv([])[3:], ["install", "--json"])

    def test_lines_answers_and_the_exit_go_through_the_pipes(self):
        # A stand-in engine: one ask, then the answer it read back as a log line.
        script = (
            "import json, sys\n"
            'print(json.dumps({"event": "ask", "id": "a", "kind": "confirm", "title": "t", "text": "x"}), flush=True)\n'
            'print(json.dumps({"event": "log", "line": sys.stdin.readline().strip()}), flush=True)\n'
            'print(json.dumps({"event": "finished", "status": "done"}), flush=True)\n'
        )
        engine = installer.ChildEngine(None)
        with mock.patch.object(installer.ChildEngine, "argv", return_value=[sys.executable, "-c", script]):
            driver = Answerer(engine)
            code = driver.run([], {})
        self.assertEqual(code, 0)
        self.assertEqual(driver.model.view(), "done")
        # The echoed answer, then the run's end as the details tell it.
        self.assertEqual(json.loads(driver.model.log[-2]), {"answer": "a", "accepted": True, "values": {}})
        self.assertEqual(driver.model.log[-1], "Finished.")

    def test_stop_closes_its_stdin(self):
        script = "import sys\nsys.stdin.read()\nprint('stopped')\n"
        engine = installer.ChildEngine(None)
        with mock.patch.object(installer.ChildEngine, "argv", return_value=[sys.executable, "-c", script]):
            engine.start([])
        engine.stop()
        self.assertEqual(engine.items.get(timeout=30), ("line", "stopped\n"))
        self.assertEqual(engine.items.get(timeout=30), ("exit", 0))
        engine.end()  # it has exited: nothing to end


class GuiCommandTests(unittest.TestCase):
    def run_gui(self, args: list[str]) -> tuple[int, str]:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = cli._gui(Registry({}), None, args)  # noqa: SLF001  # the command's own function
        return code, err.getvalue()

    def test_usage(self):
        self.assertEqual(self.run_gui(["--help"])[0], 0)
        self.assertEqual(self.run_gui(["--other"])[0], 2)

    def test_without_customtkinter_it_says_how_to_get_it_and_opens_nothing(self):
        with mock.patch.object(window, "available", return_value="CustomTkinter is not installed"):
            with mock.patch.object(window, "main") as opened:
                code, err = self.run_gui(["--advanced"])
        self.assertEqual(code, 1)
        self.assertIn("uv sync --extra gui", err)
        self.assertIn("Install.cmd", err)
        opened.assert_not_called()

    def test_the_window_opens_for_the_headset_in_the_view_asked_for(self):
        for args, demo, fresh, view in (
            ([], False, False, False),
            (["--demo"], True, False, False),
            (["--demo-fresh"], True, True, False),
            (["--advanced", "--demo"], True, False, True),
        ):
            with mock.patch.object(window, "available", return_value=""):
                with mock.patch.object(window, "main", return_value=0) as opened:
                    self.assertEqual(self.run_gui(args)[0], 0)
            self.assertEqual(opened.call_args.args[1:], (None,))
            self.assertEqual(opened.call_args.kwargs, {"demo": demo, "fresh": fresh, "advanced": view})

    def test_available_names_what_is_missing(self):
        with mock.patch("importlib.util.find_spec", return_value=None):
            self.assertEqual(window.available(), "this Python has no Tk")


def panel_widgets(shown: app.Window) -> list:
    """Return the widgets of the panel shown, depth first."""
    found, todo = [], [shown.panel]
    while todo:
        widget = todo.pop(0)
        found.append(widget)
        todo.extend(widget.winfo_children())
    return found


def panel_texts(shown: app.Window) -> list[str]:
    """Return the texts of the panel's labels, top down: its title first."""
    return [w.cget("text") for w in panel_widgets(shown) if type(w).__name__ == "CTkLabel"]


def press_panel(shown: app.Window, label: str) -> None:
    """Press the panel's button LABEL."""
    next(w for w in panel_widgets(shown) if type(w).__name__ == "CTkButton" and w.cget("text") == label).invoke()


class WindowTests(unittest.TestCase):
    """The widgets, where Tk, a display and CustomTkinter are there."""

    def test_the_demo_runs_through_the_window(self):
        if window.available():
            self.skipTest(window.available())
        import tkinter as tk  # noqa: PLC0415  # the same

        import customtkinter  # noqa: PLC0415  # optional: checked above

        side = advanced.Advanced(Registry({}), None, process=advanced.DemoProcess(pace=0), checks=advanced.demo_checks)
        try:
            shown = app.Window(customtkinter, installer.DemoEngine(pace=0), side)
        except tk.TclError as exc:  # no display
            self.skipTest(f"no Tk display: {exc}")
        self.drive(shown)

    def window(self, engine: installer.DemoEngine) -> app.Window:
        if window.available():
            self.skipTest(window.available())
        import tkinter as tk  # noqa: PLC0415  # only where it is there

        import customtkinter  # noqa: PLC0415  # optional: checked above

        side = advanced.Advanced(Registry({}), None, process=advanced.DemoProcess(pace=0), checks=advanced.demo_checks)
        try:
            return app.Window(customtkinter, engine, side)
        except tk.TclError as exc:  # no display
            self.skipTest(f"no Tk display: {exc}")

    def test_the_start_page_then_a_stop(self):
        shown = self.window(installer.DemoEngine(pace=1, recorded=True))
        try:
            self.pump(shown, lambda: not shown.probing)
            for _ in range(5):  # the probe has ended: nothing starts by itself
                shown.receive()
                shown.root.update()
            self.assertFalse(shown.model.running)
            self.assertEqual(
                (shown.title.cget("text"), shown.primary.cget("text")), ("Welcome back", "Continue where you left off")
            )
            shown.ask_restart("prepare")
            self.assertEqual(panel_texts(shown)[0], "Start over from Prepare your headset?")
            self.assertTrue(any("Prepare your headset writes to the headset" in t for t in panel_texts(shown)))
            press_panel(shown, "Cancel")
            self.assertFalse(shown.model.running)
            self.assertEqual(shown.title.cget("text"), "Welcome back")
            shown.ask_restart("build")
            press_panel(shown, "Start over")
            self.pump(shown, lambda: (t := shown.model.task("kernel")) is not None and t.status == "running")
            self.assertTrue(shown.stop_button.winfo_ismapped())
            self.assertEqual(shown.stop_button.cget("text"), "Stop")
            shown.ask_to_stop()
            self.assertEqual(panel_texts(shown)[:2], [installer.STOP_TITLE, installer.STOP_NOW])
            press_panel(shown, "Stop")
            self.assertEqual(shown.model.phase(), "stopping")
            self.pump(shown, lambda: not shown.model.running)
            self.assertEqual((shown.model.phase(), shown.primary.cget("text")), ("stopped", "Continue"))
            self.assertFalse(shown.stop_button.winfo_ismapped())
            self.assertIn(install.STOPPED_NOTHING, shown.note.text.cget("text"))
        finally:
            close(shown)

    def drive(self, shown: app.Window) -> None:
        try:
            self.pump(shown, lambda: not shown.probing)
            self.assertEqual(shown.title.cget("text"), "Check this computer")
            self.assertEqual(shown.primary.cget("text"), "Start")
            self.assertFalse(shown.model.running)  # nothing starts by itself
            shown.press()
            self.pump(shown, lambda: shown.model.ask is not None and shown.panel is not None)
            self.assertEqual((shown.model.ask or {}).get("id"), "plug-in")
            shown.close()  # while it runs: asks first
            self.assertIsNone(shown.panel_ask)
            self.assertFalse(shown.closing)
            shown.hide_panel()
            shown.render()  # declined: the engine's ask comes back
            self.assertIs(shown.panel_ask, shown.model.ask)
            shown.engine.send(shown.model.answer(accepted=True))
            shown.render()
            self.pump(shown, lambda: shown.model.ask is not None and shown.model.ask["id"] == "consent")
            shown.engine.send(shown.model.answer(accepted=True))
            shown.render()
            self.pump(shown, lambda: shown.model.view() == "failed")
            self.assertEqual(shown.primary.cget("text"), "Retry")
            shown.toggle_details()
            self.assertIn("unlock: demo output", shown.details.get("1.0", "end"))
        finally:
            close(shown)

    @staticmethod
    def pump(shown: app.Window, until: Callable[[], bool]) -> None:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            changed, _ = shown.receive()
            if changed:
                shown.render()
            shown.root.update()
            if until():
                return
            time.sleep(0.01)
        msg = "the window did not get there"
        raise AssertionError(msg)


if __name__ == "__main__":
    unittest.main()
