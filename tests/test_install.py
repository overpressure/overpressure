# SPDX-License-Identifier: Unlicense
"""overpressure.install: the engine's events, state, resume, done checks, asks, secrets, lock and modes."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

from overpressure import install, localconfig
from overpressure.headset import Command, Headset, Info
from overpressure.install import Ask, Context, Engine, Failed, Field, Stage, Task
from overpressure.process import until

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

SECRET = "hunter2-very-secret"  # noqa: S105  # a made-up secret the tests look for
INFO = Info("fake", "Fake headset")


@dataclass
class Example(localconfig.Settings):
    PREFIX = "EXAMPLE_"

    serial: str = ""


class FakeClock:
    """A clock whose sleeps pass at once; it keeps each sleep's length."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class Recorder:
    """A UI that records events and answers asks from a script."""

    def __init__(self, answers: dict[str, dict] | None = None):
        self.events: list[dict] = []
        self.answers = answers or {}
        self.stop: Callable[[], None] | None = None

    def start(self, stop: Callable[[], None]) -> None:
        self.stop = stop

    def emit(self, event: dict) -> None:
        self.events.append(event)

    def answer(self, ask: Ask) -> dict | None:
        return self.answers.get(ask.id)

    def kinds(self):
        return [e["event"] for e in self.events]

    def statuses(self):
        return [(e["task"], e["status"]) for e in self.events if e["event"] == "task"]

    def finished(self):
        return next(e for e in self.events if e["event"] == "finished")


class EngineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.out = self.root / "out" / "install"
        self.folder = self.out / INFO.codename  # the headset's state and logs
        self.env = self.root / ".env"
        self.ran: list[str] = []
        self.spawned: list[tuple[list[str], bytes | None, bool]] = []
        self.output = {"there": False}

    def spawn(self, argv: list[str], stdin: bytes | None, writes: bool, line: Callable[[str], None]) -> int:  # noqa: FBT001  # the Spawn signature
        self.spawned.append((argv, stdin, writes))
        for text in ("first line", "error: it broke" if "fail" in argv else "last line"):
            line(text)
        return 1 if "fail" in argv else 0

    def capture(self, argv: list[str], _timeout: float) -> tuple[int, str]:
        return 0, "captured " + " ".join(argv)

    def engine(self, stages: Sequence[Stage], ui: Recorder) -> Engine:
        return Engine(INFO, stages, ui, root=self.out, env=self.env, spawn=self.spawn, capture=self.capture)

    def task(self, name: str, **kwargs: object) -> Task:
        def run(ctx: Context) -> None:
            self.ran.append(name)
            ctx.log(f"{name} works")
            ctx.progress(0.5, "half")

        return Task(name, name.title(), kwargs.pop("run", run), **kwargs)  # ty: ignore[invalid-argument-type]  # the tests pass Task fields by name

    def plan(self, **kwargs: object) -> tuple[Stage, ...]:
        return (
            Stage(
                "a", "Stage A", "the first", (self.task("one"), self.task("two", done=lambda _c: self.output["there"]))
            ),
            Stage("b", "Stage B", "the second", (self.task("three", **kwargs),)),
        )

    def test_events_in_order_and_the_state_file(self):
        ui = Recorder()
        self.assertEqual(self.engine(self.plan(), ui).run(), "done")
        self.assertEqual(ui.kinds()[0], "plan")
        self.assertEqual(ui.events[0]["headset"], {"codename": "fake", "name": "Fake headset"})
        self.assertEqual(
            ui.events[0]["stages"][0]["tasks"][0], {"id": "one", "title": "One", "estimate": "", "writes": False}
        )
        self.assertEqual(
            ui.statuses(),
            [
                ("one", "running"),
                ("one", "done"),
                ("two", "running"),
                ("two", "done"),
                ("three", "running"),
                ("three", "done"),
            ],
        )
        self.assertEqual(ui.kinds()[1:5], ["task", "log", "progress", "task"])
        self.assertEqual(ui.events[3], {"event": "progress", "task": "one", "fraction": 0.5, "text": "half"})
        self.assertEqual(ui.events[-1], {"event": "finished", "status": "done", "task": None, "error": "", "where": ""})
        state = json.loads((self.folder / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["schema"], 1)
        self.assertEqual(
            {k: v["status"] for k, v in state["tasks"].items()}, dict.fromkeys(("one", "two", "three"), "done")
        )
        self.assertTrue(all("finished" in v for v in state["tasks"].values()))
        self.assertIn("one works", (self.folder / "one.log").read_text(encoding="utf-8"))

    def test_a_done_task_is_skipped_on_the_next_run_unless_its_check_says_otherwise(self):
        self.engine(self.plan(), Recorder()).run()
        self.ran.clear()
        ui = Recorder()
        self.engine(self.plan(), ui).run()
        self.assertEqual(self.ran, ["two"])  # done before, but its check says its output is gone
        self.assertEqual(ui.statuses()[0], ("one", "done"))

    def test_a_done_check_skips_a_task_on_its_first_run(self):
        self.output["there"] = True
        ui = Recorder()
        self.engine(self.plan(), ui).run()
        self.assertEqual(self.ran, ["one", "three"])
        self.assertIn(("two", "skipped"), ui.statuses())
        self.assertEqual(json.loads((self.folder / "state.json").read_text())["tasks"]["two"]["status"], "skipped")

    def test_a_done_check_that_raises_means_run(self):
        def broken(_ctx: Context) -> NoReturn:
            raise OSError

        stages = (Stage("a", "A", "", (self.task("one", done=broken),)),)
        self.engine(stages, Recorder()).run()
        self.assertEqual(self.ran, ["one"])

    def test_the_first_failure_stops_the_run_and_says_where(self):
        def fail(_ctx: Context) -> None:
            msg = "the cable is out"
            raise Failed(msg, "Plug it in again.")

        stages = (
            Stage("a", "A", "", (self.task("one"), self.task("two", run=fail, where="ignored"))),
            Stage("b", "B", "", (self.task("three"),)),
        )
        ui = Recorder()
        self.assertEqual(self.engine(stages, ui).run(), "failed")
        self.assertEqual(self.ran, ["one"])
        self.assertEqual(
            ui.finished(),
            {
                "event": "finished",
                "status": "failed",
                "task": "two",
                "error": "the cable is out",
                "where": "Plug it in again.",
            },
        )
        self.assertEqual(json.loads((self.folder / "state.json").read_text())["tasks"]["two"]["status"], "failed")

    def test_any_exception_fails_in_words_and_the_task_runs_again_next_time(self):
        calls = []

        def flaky(_ctx: Context) -> None:
            calls.append(1)
            if len(calls) == 1:
                msg = "no answer"
                raise RuntimeError(msg)

        stages = (Stage("a", "A", "", (self.task("one", run=flaky, where="Try again."),)),)
        ui = Recorder()
        self.engine(stages, ui).run()
        self.assertEqual((ui.finished()["error"], ui.finished()["where"]), ("no answer", "Try again."))
        self.assertIn("Traceback", (self.folder / "one.log").read_text(encoding="utf-8"))
        self.assertEqual(self.engine(stages, Recorder()).run(), "done")
        self.assertEqual(len(calls), 2)

    def test_a_child_program_streams_its_lines_and_fails_with_its_error_line(self):
        def child(ctx: Context) -> None:
            ctx.run(["prog", "fail"])

        ui = Recorder()
        self.engine((Stage("a", "A", "", (Task("one", "One", child, writes=True),)),), ui).run()
        self.assertEqual(self.spawned, [(["prog", "fail"], None, True)])
        self.assertEqual(
            [e["line"] for e in ui.events if e["event"] == "log"], ["$ prog fail", "first line", "error: it broke"]
        )
        self.assertEqual(ui.finished()["error"], "it broke")
        self.assertIn("first line", (self.folder / "one.log").read_text(encoding="utf-8"))

    def test_a_declined_ask_stops_the_run(self):
        def consent(ctx: Context) -> None:
            ctx.ask(Ask("consent", "confirm", "Erase?", "It erases.", accept="Erase"))
            self.ran.append("after")

        stages = (Stage("a", "A", "", (Task("one", "One", consent), self.task("two"))),)
        ui = Recorder({"consent": {"answer": "consent", "accepted": False, "values": {}}})
        self.assertEqual(self.engine(stages, ui).run(), "stopped")
        ask = next(e for e in ui.events if e["event"] == "ask")
        self.assertEqual(
            ask,
            {
                "event": "ask",
                "id": "consent",
                "kind": "confirm",
                "title": "Erase?",
                "text": "It erases.",
                "fields": [],
                "accept": "Erase",
                "decline": "Cancel",
            },
        )
        self.assertEqual(self.ran, [])
        self.assertEqual(ui.finished()["status"], "stopped")
        self.assertEqual(ui.statuses()[-1], ("one", "pending"))
        self.assertNotIn("one", json.loads((self.folder / "state.json").read_text())["tasks"])

    def test_no_answer_stops_too(self):
        stages = (Stage("a", "A", "", (Task("one", "One", lambda ctx: ctx.ask(Ask("x", "action", "Plug in", ""))),)),)  # ty: ignore[invalid-argument-type]  # the ask's values are dropped
        self.assertEqual(self.engine(stages, Recorder()).run(), "stopped")

    def test_secrets_never_leave_the_engine(self):
        form = Ask("wifi", "form", "Wi-Fi", "", (Field("ssid", "Network"), Field("password", "Password", secret=True)))
        refused = []

        def task(ctx: Context) -> None:
            values = ctx.ask(form)
            ctx.log(f"the password is {values['password']}")
            ctx.note(f"note {values['password']}")
            ctx.progress(None, values["password"])
            for attempt in (
                lambda: ctx.run(["prog", "--psk", values["password"]]),
                lambda: ctx.output(["prog", values["password"]]),
                lambda: ctx.set_setting("EXAMPLE_SERIAL", values["password"]),
            ):
                try:
                    attempt()
                except Failed as exc:  # noqa: PERF203  # each attempt is refused on its own
                    refused.append(str(exc))
            ctx.run(["prog", "write"], stdin=values["password"].encode())
            msg = f"failed with {values['password']}"
            raise Failed(msg, f"where {values['password']}")

        ui = Recorder({"wifi": {"answer": "wifi", "accepted": True, "values": {"ssid": "home", "password": SECRET}}})
        self.engine((Stage("a", "A", "", (Task("one", "One", task),)),), ui).run()
        self.assertEqual(len(refused), 3)
        self.assertEqual(self.spawned, [(["prog", "write"], SECRET.encode(), False)])  # on stdin only
        text = json.dumps(ui.events)
        self.assertNotIn(SECRET, text)
        self.assertIn(install.MASK, ui.finished()["error"])
        for path in self.folder.iterdir():
            self.assertNotIn(SECRET, path.read_text(encoding="utf-8"), path.name)
        self.assertFalse(self.env.exists())

    def test_a_required_field_is_asked_again(self):
        answers = iter([{"accepted": True, "values": {"name": " "}}, {"accepted": True, "values": {"name": "x"}}])
        got = []

        class Script(Recorder):
            def answer(self, ask: Ask) -> dict | None:  # noqa: ARG002  # Recorder.answer's signature
                return next(answers)

        ui = Script()
        form = Ask("f", "form", "Name", "", (Field("name", "Name"),))
        self.engine((Stage("a", "A", "", (Task("one", "One", lambda ctx: got.append(ctx.ask(form))),)),), ui).run()
        self.assertEqual(got, [{"name": "x"}])
        self.assertEqual(sum(e["event"] == "ask" for e in ui.events), 2)

    def test_settings_go_to_env_and_are_read_back(self):
        def task(ctx: Context) -> None:
            ctx.set_setting("EXAMPLE_SERIAL", "S1")
            self.ran.append(ctx.settings(Example).serial)

        self.engine((Stage("a", "A", "", (Task("one", "One", task),)),), Recorder()).run()
        self.assertEqual(self.ran, ["S1"])
        self.assertIn("EXAMPLE_SERIAL=S1", self.env.read_text(encoding="utf-8"))

    def test_in_process_output_becomes_log_lines(self):
        def task(_ctx: Context) -> None:
            print("printed")  # noqa: T201  # what the engine turns into log lines
            print("to stderr", file=sys.stderr)  # noqa: T201  # what the engine turns into log lines

        ui = Recorder()
        self.engine((Stage("a", "A", "", (Task("one", "One", task),)),), ui).run()
        self.assertEqual([e["line"] for e in ui.events if e["event"] == "log"], ["printed", "to stderr"])

    def test_one_engine_at_a_time(self):
        lock = install.Lock(self.out / "lock")
        self.assertTrue(lock.acquire())
        try:
            ui = Recorder()
            self.assertEqual(self.engine(self.plan(), ui).run(), "failed")
            self.assertIn("Another installer", ui.finished()["error"])
            self.assertEqual(self.ran, [])
        finally:
            lock.release()
        self.assertEqual(self.engine(self.plan(), Recorder()).run(), "done")

    def test_restart_forgets_from_a_stage_on(self):
        self.output["there"] = False
        self.engine(self.plan(), Recorder()).run()
        self.ran.clear()
        self.engine(self.plan(), Recorder()).run("b")
        self.assertEqual(self.ran, ["two", "three"])  # "two": its check; "three": forgotten
        with self.assertRaisesRegex(ValueError, "no stage"):
            self.engine(self.plan(), Recorder()).forget("z")

    def test_a_stop_request_ends_the_run_between_tasks(self):
        def stop(ctx: Context) -> None:
            ctx.engine.request_stop()

        ui = Recorder()
        stages = (Stage("a", "A", "", (Task("one", "One", stop), self.task("two"))),)
        self.assertEqual(self.engine(stages, ui).run(), "stopped")
        self.assertEqual(self.ran, [])
        self.assertEqual(ui.statuses()[-1], ("two", "pending"))

    def test_a_stop_request_ends_a_wait_at_once(self):
        # As find() waits for the headset: until() through ctx.clock, ten minutes; the window's stop
        # arrives (the stdin thread) after the third try.
        clock = FakeClock()
        tries: list[float] = []

        def wait(ctx: Context) -> None:
            def headset() -> str:
                tries.append(clock.now)
                if len(tries) == 3:  # the try the stop comes after
                    ctx.engine.request_stop()
                msg = "no headset yet"
                raise RuntimeError(msg)

            until("the headset", 600, headset, ctx.clock)

        ui = Recorder()
        stages = (Stage("a", "A", "", (Task("find", "Find", wait), self.task("two"))),)
        engine = Engine(INFO, stages, ui, root=self.out, env=self.env, capture=self.capture, clock=clock)
        self.assertEqual(engine.run(), "stopped")
        self.assertEqual(len(tries), 3)
        self.assertLess(clock.now, 7)  # two retries of 3 s, not ten minutes
        self.assertTrue(all(s <= install.STOP_SLICE for s in clock.slept))
        self.assertEqual(self.ran, [])
        self.assertEqual(ui.statuses()[-1], ("find", "pending"))
        self.assertEqual(ui.finished()["where"], install.STOPPED_NOTHING)
        # Its own where says the headset's state when it has one.
        ui = Recorder()
        stages = (Stage("a", "A", "", (Task("find", "Find", wait, where="Nothing changed here."),)),)
        tries.clear()
        Engine(INFO, stages, ui, root=self.out, env=self.env, clock=FakeClock()).run()
        self.assertEqual(ui.finished()["where"], "Nothing changed here.")

    def test_a_stop_clock_sleeps_in_slices_and_raises_stopped(self):
        clock, stop = FakeClock(), []
        wait = install.StopClock(clock, lambda: bool(stop))
        wait.sleep(1)
        self.assertEqual((round(clock.now, 6), len(clock.slept)), (1.0, 5))
        self.assertEqual(wait.monotonic(), clock.now)
        stop.append(1)
        with self.assertRaises(install.Stopped):
            wait.sleep(0)

    def test_ctrl_c_at_a_wait_stops_the_run(self):
        class Interrupted(FakeClock):
            def sleep(self, seconds: float) -> None:  # noqa: ARG002  # FakeClock.sleep's signature
                raise KeyboardInterrupt

        def wait(ctx: Context) -> None:
            ctx.clock.sleep(5)

        ui = Recorder()
        engine = Engine(
            INFO, (Stage("a", "A", "", (Task("one", "One", wait),)),), ui, root=self.out, clock=Interrupted()
        )
        self.assertEqual(engine.run(), "stopped")

    def test_after_a_stop_nothing_more_starts_in_the_task(self):
        def task(ctx: Context) -> None:
            ctx.engine.request_stop()
            ctx.output(["poll"])

        ui = Recorder()
        self.assertEqual(self.engine((Stage("a", "A", "", (Task("one", "One", task),)),), ui).run(), "stopped")
        self.assertNotIn("$ poll", (self.folder / "one.log").read_text(encoding="utf-8"))

    def test_repeated_ids_are_refused(self):
        with self.assertRaisesRegex(ValueError, "task id 'one' repeats"):
            self.engine(
                (Stage("a", "A", "", (self.task("one"),)), Stage("b", "B", "", (self.task("one"),))), Recorder()
            )


class StateLayoutTests(unittest.TestCase):
    """Each headset's state and logs in its own folder, one lock for the computer, the old state moved over."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.out = Path(temp.name) / "out" / "install"

    @staticmethod
    def stages(*ids: str) -> tuple[Stage, ...]:
        return (Stage("s", "Stage", "", tuple(Task(i, i.title(), lambda ctx: ctx.log("ran")) for i in ids)),)

    def engine(self, info: Info, stages: tuple[Stage, ...], ui: Recorder | None = None) -> Engine:
        return Engine(info, stages, ui or Recorder(), root=self.out, env=self.out / ".env")

    def legacy(self, *done: str) -> dict:
        """Write a state and logs as the engine before the headsets' folders did; return the state."""
        self.out.mkdir(parents=True, exist_ok=True)
        state = {"schema": 1, "tasks": {i: {"status": "done", "finished": "2026-11-01T00:00:00+00:00"} for i in done}}
        (self.out / "state.json").write_text(json.dumps(state), encoding="utf-8")
        for task in done:
            (self.out / f"{task}.log").write_text(f"old {task}\n", encoding="utf-8")
        return state

    def test_two_headsets_keep_their_own_state_and_share_the_lock(self):
        other = Info("other", "Other headset")
        self.assertEqual(self.engine(INFO, self.stages("one")).run(), "done")
        self.assertEqual(self.engine(other, self.stages("one", "two")).run(), "done")
        for codename, tasks in (("fake", {"one"}), ("other", {"one", "two"})):
            state = json.loads((self.out / codename / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(set(state["tasks"]), tasks)
            self.assertTrue((self.out / codename / "one.log").is_file())
        self.assertFalse((self.out / "state.json").exists())
        lock = install.Lock(self.out / "lock")  # another engine, of either headset
        self.assertTrue(lock.acquire())
        try:
            ui = Recorder()
            self.assertEqual(self.engine(other, self.stages("one"), ui).run(), "failed")
            self.assertIn("Another installer", ui.finished()["error"])
        finally:
            lock.release()

    def test_a_run_moves_the_old_state_and_its_logs_into_the_headsets_folder(self):
        state = self.legacy("one")
        (self.out / "elsewhere.log").write_text("not this plan's\n", encoding="utf-8")
        ui = Recorder()
        self.assertEqual(self.engine(INFO, self.stages("one", "two"), ui).run(), "done")
        self.assertEqual(ui.statuses()[0], ("one", "done"))  # resumed: done before, not run again
        self.assertFalse((self.out / "state.json").exists())
        moved = json.loads((self.out / "fake" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(moved["tasks"]["one"], state["tasks"]["one"])
        self.assertEqual((self.out / "fake" / "one.log").read_text(encoding="utf-8"), "old one\n")
        self.assertTrue((self.out / "elsewhere.log").exists())  # no task of this plan: it stays

    def test_another_plans_old_state_stays_where_it_is(self):
        self.legacy("one", "flash")
        other = Info("other", "Other headset")
        self.assertEqual(self.engine(other, self.stages("one", "two")).run(), "done")
        self.assertTrue((self.out / "state.json").exists())  # it records "flash", which this plan has not
        self.assertTrue((self.out / "one.log").exists())
        self.assertEqual(self.engine(INFO, self.stages("one", "flash")).run(), "done")
        self.assertFalse((self.out / "state.json").exists())

    def test_a_headset_with_its_own_state_leaves_the_old_one_alone(self):
        self.engine(INFO, self.stages("one")).run()
        self.legacy("one")
        self.engine(INFO, self.stages("one")).run()
        self.assertTrue((self.out / "state.json").exists())

    def test_the_plan_moves_it_under_the_lock_and_reads_it_in_place_while_another_engine_runs(self):
        self.legacy("one")
        lock = install.Lock(self.out / "lock")
        self.assertTrue(lock.acquire())  # the owner's installer runs
        try:
            ui = Recorder()
            self.engine(INFO, self.stages("one", "two"), ui).show_plan()
            self.assertEqual(ui.statuses(), [("one", "done"), ("two", "pending")])
            self.assertTrue((self.out / "state.json").exists())  # not moved under the running engine
            self.assertFalse((self.out / "fake").exists())
        finally:
            lock.release()
        ui = Recorder()
        self.engine(INFO, self.stages("one", "two"), ui).show_plan()
        self.assertEqual(ui.statuses(), [("one", "done"), ("two", "pending")])
        self.assertFalse((self.out / "state.json").exists())
        self.assertTrue((self.out / "fake" / "state.json").exists())
        self.assertTrue((self.out / "fake" / "one.log").exists())

    def test_a_move_cut_off_midway_is_finished_by_the_next_one(self):
        self.legacy("one", "two")
        (self.out / "fake").mkdir()
        (self.out / "one.log").replace(self.out / "fake" / "one.log")  # the logs had begun to move
        self.engine(INFO, self.stages("one", "two")).show_plan()
        self.assertEqual(sorted(p.name for p in (self.out / "fake").iterdir()), ["one.log", "state.json", "two.log"])


class ChildTests(unittest.TestCase):
    """The real child runner: lines streamed (carriage returns too), stdin fed, the exit status."""

    def test_spawn(self):
        with tempfile.TemporaryDirectory() as temp:
            engine = Engine(INFO, (), Recorder(), root=Path(temp))
            lines = []
            code = engine.spawn(
                [sys.executable, "-c", "import sys; print('a'); print('b\\rc'); print(sys.stdin.read()); sys.exit(3)"],
                b"from stdin",
                True,  # noqa: FBT003  # the Spawn signature
                lines.append,
            )
        self.assertEqual((code, lines), (3, ["a", "b", "c", "from stdin"]))

    def stop_in_child(self, *, writes: bool) -> tuple[str, Recorder, list[str], float]:
        """Run a task whose child prints "up", then is asked to stop; return the end, the UI, lines, seconds."""
        script = "import time; print('up', flush=True); time.sleep(%s); print('end', flush=True)"
        seen: list[str] = []

        def child(ctx: Context) -> None:
            def line(text: str) -> None:
                seen.append(text)
                if text == "up":
                    ctx.engine.request_stop()  # as the window's stop arrives

            ctx.run([sys.executable, "-c", script % (1 if writes else 60)], progress=line)
            ctx.run([sys.executable, "-c", "print('second')"])

        ui = Recorder()
        stages = (Stage("a", "A", "", (Task("one", "One", child, writes=writes, where="In fastboot."),)),)
        with tempfile.TemporaryDirectory() as temp:
            began = time.monotonic()
            end = Engine(INFO, stages, ui, root=Path(temp)).run()
            return end, ui, seen, time.monotonic() - began

    def test_a_stop_ends_a_child_that_does_not_write(self):
        end, ui, seen, seconds = self.stop_in_child(writes=False)
        self.assertEqual(end, "stopped")
        self.assertEqual(seen, ["up"])  # ended: no "end", no second child
        self.assertLess(seconds, 30)
        self.assertEqual(ui.finished()["where"], "In fastboot.")

    def test_a_child_that_writes_runs_to_its_end_then_the_run_stops(self):
        end, ui, seen, _ = self.stop_in_child(writes=True)
        self.assertEqual(end, "stopped")
        self.assertEqual(seen, ["up", "end"])  # not cut off; the second child never starts
        self.assertEqual(ui.finished()["where"], f"{install.STOPPED_WROTE} In fastboot.")
        self.assertEqual(ui.statuses()[-1], ("one", "pending"))


class ModeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)

    def plan(self):
        def wifi(ctx: Context) -> None:
            values = ctx.ask(
                Ask(
                    "wifi",
                    "form",
                    "Wi-Fi",
                    "Optional.",
                    (Field("ssid", "Network name"), Field("password", "Password", secret=True)),
                )
            )
            ctx.log(f"network {values['ssid']}")

        def plug(ctx: Context) -> None:
            ctx.ask(Ask("plug", "action", "Plug in", "Plug it in."))

        return (
            Stage(
                "s",
                "Stage",
                "a stage",
                (Task("plug", "Plug in", plug, "a minute", writes=True), Task("wifi", "Wi-Fi", wifi)),
            ),
        )

    def test_text_mode(self):
        out, typed, hidden = io.StringIO(), iter(["", "home"]), []

        def secret(_prompt: str) -> str:
            hidden.append(1)
            return SECRET

        ui = install.TextUI(out, lambda _p: next(typed), secret)
        self.assertEqual(Engine(INFO, self.plan(), ui, root=self.folder).run(), "done")
        text = out.getvalue()
        self.assertIn("overpressure installer: Fake headset (fake)", text)
        self.assertIn("== Stage ==", text)
        self.assertIn("[x] Wi-Fi", text)
        self.assertIn("      network home", text)
        self.assertIn("Finished.", text)
        self.assertEqual(hidden, [1])
        self.assertNotIn(SECRET, text)

    def test_text_mode_declines_and_ends_of_input(self):
        out = io.StringIO()
        ui = install.TextUI(out, lambda _p: "q")
        self.assertEqual(Engine(INFO, self.plan(), ui, root=self.folder).run(), "stopped")
        self.assertIn("Stopped.", out.getvalue())

        def eof(_prompt: str) -> NoReturn:
            raise EOFError

        self.assertEqual(
            Engine(INFO, self.plan(), install.TextUI(io.StringIO(), eof), root=self.folder).run(), "stopped"
        )

    def test_json_mode(self):
        lines = [
            "not json",
            json.dumps({"answer": "plug", "accepted": True, "values": {}}),
            json.dumps({"answer": "wifi", "accepted": True, "values": {"ssid": "home", "password": SECRET}}),
        ]
        read, write = os.pipe()
        os.write(write, ("\n".join(lines) + "\n").encode())
        out = io.StringIO()
        with os.fdopen(read, encoding="utf-8") as answers:  # the window keeps stdin open while the run goes on
            try:
                status = Engine(INFO, self.plan(), install.JsonUI(out, answers), root=self.folder).run()
            finally:
                os.close(write)
        self.assertEqual(status, "done")
        events = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(events[0]["event"], "plan")
        self.assertEqual([e["id"] for e in events if e["event"] == "ask"], ["plug", "wifi"])
        self.assertEqual(events[-1]["status"], "done")
        self.assertNotIn(SECRET, out.getvalue())

    def test_json_mode_stops_when_stdin_closes(self):
        out = io.StringIO()
        self.assertEqual(
            Engine(INFO, self.plan(), install.JsonUI(out, io.StringIO("")), root=self.folder).run(), "stopped"
        )
        self.assertEqual(json.loads(out.getvalue().splitlines()[-1])["status"], "stopped")


class Fake(Headset):
    info = INFO

    def __init__(self, stages: tuple[Stage, ...] = ()) -> None:
        self.stages = stages

    def commands(self):
        return {"x": Command("x", "")}

    def install_plan(self):
        return self.stages


class MainTests(unittest.TestCase):
    def test_plan_and_usage(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            stages = (Stage("s", "Stage", "", (Task("t", "Task", lambda _ctx: None),)),)
            out = io.StringIO()
            self.assertEqual(install.main(Fake(stages), ["--json", "--plan"], stdout=out, root=folder), 0)
            events = [json.loads(line) for line in out.getvalue().splitlines()]
            self.assertEqual([e["event"] for e in events], ["plan", "task"])
            self.assertEqual(events[1]["status"], "pending")
            self.assertFalse((folder / "lock").exists())  # --plan runs nothing
            out = io.StringIO()
            self.assertEqual(install.main(Fake(), ["--json"], stdout=out, root=folder), 2)
            self.assertEqual(json.loads(out.getvalue())["status"], "failed")
            out = io.StringIO()
            self.assertEqual(install.main(Fake(stages), ["--restart", "nope"], stdout=out, root=folder), 2)
            self.assertIn("No stage 'nope'", out.getvalue())
            self.assertEqual(install.main(Fake(stages), [], stdout=io.StringIO(), root=folder), 0)

    def test_the_command_line_reaches_the_engine(self):
        from overpressure import __main__ as cli  # noqa: PLC0415  # the command line, as the other tests use it

        self.assertIs(cli.ON_HEADSET["install"], install.main)


if __name__ == "__main__":
    unittest.main()
