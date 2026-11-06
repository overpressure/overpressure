# SPDX-License-Identifier: Unlicense
"""The window's headset page: always first, the last choice in out/window.json, the way back, the demo."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest import mock

from overpressure import __main__ as cli
from overpressure import window
from overpressure.headset import Action, AfterInstall, Command, Headset, Info, Registry, Step
from overpressure.install import Stage, Task
from overpressure.window import advanced, app, headsets, installer
from tests.test_installer import PLAN, close, feed, panel_texts, task


class Planned(Headset):
    """A headset with an installer and a finished page of its own."""

    info = Info("planned", "Planned headset", "SoC A", "has an installer")

    def commands(self):
        return {"x": Command("x", "")}

    def steps(self):
        return (
            Step("install", "Install", "Headset", ("x",), writes=True),
            Step("help", "Help", "Headset", ("x", "help")),
        )

    def install_plan(self):
        return (Stage("s", "Stage", "", (Task("t", "Task", lambda _ctx: None),)),)

    def after_install(self):
        return AfterInstall(
            "Put it on",
            "Ready to play.",
            (Action("Update", "install"), Action("Not a step", "nope"), Action("Get help", "help")),
        )


class Bare(Headset):
    """A headset without an installer: Advanced mode only, the default finished page."""

    info = Info("bare", "Bare headset", "SoC B")

    def commands(self):
        return {}

    def steps(self):
        return (Step("tools", "Tools", "Setup", ("tools",)),)


def two() -> Registry:
    """Return a registry of the two headsets."""
    return Registry({"planned": Planned(), "bare": Bare()})


def one() -> Registry:
    """Return a registry of one headset, as devices/ is today."""
    return Registry({"planned": Planned()})


class Folder(unittest.TestCase):
    """A repository root of its own: out/window.json and an owner's .env."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.prefs = self.root / headsets.PREFS
        self.env = self.root / ".env"
        self.env.write_text("# the owner's settings\nPICO4_ADB_SERIAL=abc\n", encoding="utf-8")
        self.env_before = self.env.read_text(encoding="utf-8")

    def remember(self, data: object) -> None:
        """Write DATA as out/window.json (a string as it is)."""
        self.prefs.parent.mkdir(parents=True, exist_ok=True)
        self.prefs.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")

    def assert_env_untouched(self) -> None:
        self.assertEqual(self.env.read_text(encoding="utf-8"), self.env_before)


class ChoiceTests(Folder):
    def test_one_headset_is_asked_for_too(self):
        choice = headsets.Choice(one(), None, prefs=self.prefs)
        self.assertTrue(choice.asking())
        self.assertEqual([(e.codename, e.last) for e in choice.entries()], [("planned", False)])

    def test_several_are_listed_by_name_with_what_each_opens(self):
        choice = headsets.Choice(two(), None, prefs=self.prefs)
        self.assertTrue(choice.asking())
        self.assertEqual(
            [(e.codename, e.name, e.about, e.installer, e.last) for e in choice.entries()],
            [
                ("bare", "Bare headset", "SoC B", False, False),
                ("planned", "Planned headset", "SoC A · has an installer", True, False),
            ],
        )
        self.assertIn("Advanced mode only", choice.entries()[0].mode)
        self.assertEqual(choice.entries()[1].mode, "Installer and Advanced mode")

    def test_the_headset_used_last_is_marked_and_first(self):
        self.remember({"last_headset": "planned", "other": 1})
        entries = headsets.Choice(two(), None, prefs=self.prefs).entries()
        self.assertEqual([(e.codename, e.last) for e in entries], [("planned", True), ("bare", False)])

    def test_a_missing_broken_or_unknown_last_choice_marks_nothing(self):
        for data in (None, "{not json", "[1, 2]", {"last_headset": 7}, {"last_headset": "gone"}, {}):
            if data is not None:
                self.remember(data)
            choice = headsets.Choice(two(), None, prefs=self.prefs)
            self.assertEqual((choice.last, [e.last for e in choice.entries()]), ("", [False, False]), data)
        self.assertEqual(headsets.Choice(two(), None).last, "")  # no file at all (tests)

    def test_a_choice_goes_to_window_json_not_to_env(self):
        self.remember({"other": 1})
        choice = headsets.Choice(two(), None, prefs=self.prefs)
        self.assertIsInstance(choice.choose("planned"), Planned)
        self.assertFalse(choice.asking())
        choice.choose("bare")
        self.assertEqual(json.loads(self.prefs.read_text(encoding="utf-8")), {"other": 1, "last_headset": "bare"})
        self.assertEqual(choice.entries()[0].codename, "bare")
        self.assert_env_untouched()
        with self.assertRaises(KeyError):
            choice.choose("nope")

    def test_a_choice_starts_a_missing_or_broken_file_afresh(self):
        headsets.Choice(one(), None, prefs=self.prefs).choose("planned")  # out/ is made
        self.assertEqual(json.loads(self.prefs.read_text(encoding="utf-8")), {"last_headset": "planned"})
        self.remember("{broken")
        headsets.Choice(one(), None, prefs=self.prefs).choose("planned")
        self.assertEqual(json.loads(self.prefs.read_text(encoding="utf-8")), {"last_headset": "planned"})

    def test_the_demo_reads_but_writes_nothing(self):
        self.remember({"last_headset": "bare"})
        choice = headsets.Choice(two(), None, prefs=self.prefs, save=False)
        self.assertEqual(choice.last, "bare")
        choice.choose("planned")
        self.assertEqual(choice.last, "planned")  # marked in this window
        self.assertEqual(json.loads(self.prefs.read_text(encoding="utf-8")), {"last_headset": "bare"})
        self.assert_env_untouched()

    def test_a_headset_chosen_by_device_asks_nothing(self):
        self.assertFalse(headsets.Choice(two(), Bare()).asking())
        self.assertFalse(headsets.Choice(Registry({}), None).asking())  # nothing to choose from
        self.assertTrue(headsets.Choice(Registry({}), None).installer())  # nothing chosen: the view stays

    def test_changing_is_refused_while_something_runs(self):
        refusal = headsets.Choice.refusal
        self.assertEqual(refusal(installer_running=False, step_running=False), "")
        self.assertIn("The installer is running", refusal(installer_running=True, step_running=False))
        self.assertIn("A step of Advanced mode is running", refusal(installer_running=False, step_running=True))
        self.assertIn("change the headset", refusal(installer_running=True, step_running=False))

    def test_each_headset_says_what_its_finished_page_offers(self):
        self.assertEqual(Bare().after_install(), AfterInstall("Installed", "Your headset is ready.", ()))
        self.assertEqual([a.step for a in Planned().after_install().actions], ["install", "nope", "help"])


class LastTimeTests(unittest.TestCase):
    """The headset page's line on where a headset stopped, from its plan probe."""

    def probed(self, *events: dict) -> installer.Model:
        m = installer.Model()
        feed(m, PLAN, *events)
        m.probed()
        return m

    def test_where_it_stopped_failed_or_finished(self):
        self.assertEqual(headsets.last_time(self.probed()), "")  # nothing recorded
        self.assertEqual(
            headsets.last_time(self.probed(task("computer", "tools", "done"))), "Last time: stopped in Install"
        )
        self.assertEqual(
            headsets.last_time(self.probed(task("install", "system", "failed"))), "Last time: a step of Install failed"
        )
        everything = [task("computer", "tools", "done")] + [task("install", t, "done") for t in ("wifi", "system")]
        self.assertEqual(headsets.last_time(self.probed(*everything)), "Last time: every stage was done")

    def test_a_probe_reads_the_plan_once(self):
        for recorded, line in ((False, ""), (True, "Last time: stopped in Install")):
            probe = headsets.Probe(installer.DemoEngine(pace=0, recorded=recorded)).start()
            deadline = time.monotonic() + 10
            while not probe.receive():
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            self.assertTrue(probe.done)
            self.assertEqual((probe.line(), probe.model.start_page), (line, recorded))
            self.assertFalse(probe.receive())  # ended: nothing more


class CommandLineTests(Folder):
    """The gui command: only --device skips the headset page."""

    def gui(self, argv: list[str], registry: Registry, environ: dict[str, str] | None = None) -> Any:  # noqa: ANN401  # the mock's call
        with contextlib.ExitStack() as stack:  # Python 3.9: no with (...)
            stack.enter_context(mock.patch.object(cli.localconfig, "ROOT", self.root))
            stack.enter_context(mock.patch.dict("os.environ", environ or {}, clear=True))
            stack.enter_context(mock.patch.object(cli, "discover", return_value=registry))
            stack.enter_context(mock.patch.object(window, "available", return_value=""))
            opened = stack.enter_context(mock.patch.object(window, "main", return_value=0))
            self.assertEqual(cli.main(argv), 0)
        return opened.call_args

    def test_one_headset_opens_the_headset_page(self):
        self.assertIsNone(self.gui(["gui"], one()).args[1])

    def test_device_skips_it(self):
        self.assertIsInstance(self.gui(["--device", "planned", "gui"], two()).args[1], Planned)
        self.assertIsInstance(self.gui(["--device=bare", "gui", "--advanced"], two()).args[1], Bare)

    def test_overpressure_device_does_not_skip_it(self):
        self.env.write_text("OVERPRESSURE_DEVICE=bare\n", encoding="utf-8")
        self.assertIsNone(self.gui(["gui"], two()).args[1])
        self.assertIsNone(self.gui(["gui"], two(), {"OVERPRESSURE_DEVICE": "planned"}).args[1])
        self.env.write_text("OVERPRESSURE_DEVICE=gone\n", encoding="utf-8")  # a stale setting does not stop it
        self.assertIsNone(self.gui(["gui"], two()).args[1])
        # The command line's own setting stays the owner's.
        with mock.patch.object(cli.localconfig, "ROOT", self.root), mock.patch.dict("os.environ", clear=True):
            self.env.write_text("OVERPRESSURE_DEVICE=bare\n", encoding="utf-8")
            self.assertIsInstance(cli.select(two(), None), Bare)

    def test_demo_headsets_registers_the_second_one_before(self):
        registry, headset = self.gui(["gui", "--demo-headsets"], one()).args
        self.assertEqual(sorted(registry.headsets), ["example", "planned"])
        self.assertIsNone(headset)

    def test_the_window_keeps_the_choice_in_out_unless_the_demo(self):
        if window.available():
            self.skipTest(window.available())
        for demo in (False, True):
            opened = mock.MagicMock()
            with mock.patch.object(window, "Window", opened), mock.patch.object(window.localconfig, "ROOT", self.root):
                self.assertEqual(window.main(one(), None, demo=demo), 0)
            choice: headsets.Choice = opened.call_args.kwargs["choice"]
            self.assertTrue(choice.asking())
            self.assertEqual((choice.prefs, choice.save), (self.prefs, not demo))
        engine = opened.call_args.kwargs["engines"](Planned())
        self.assertIsInstance(engine, installer.DemoEngine)
        self.assertEqual(engine.headset, {"codename": "planned", "name": "Planned headset"})


class DemoTests(unittest.TestCase):
    def test_the_demo_engine_names_the_chosen_headset(self):
        demo = installer.DemoEngine(pace=0, codename="planned", name="Planned headset")
        demo.start(["--plan"])
        plan = json.loads(str(demo.items.get(timeout=10)[1]))
        self.assertEqual(plan["headset"], {"codename": "planned", "name": "Planned headset"})
        while demo.items.get(timeout=10)[0] != "exit":
            pass

    def test_demo_headsets_adds_a_second_headset_to_the_demo_only(self):
        registry = cli.demo_headsets(one())
        self.assertEqual(sorted(registry.headsets), ["example", "planned"])
        self.assertFalse(headsets.Choice(registry).installer(registry.get("example")))
        with mock.patch.object(window, "available", return_value=""), mock.patch.object(window, "main") as opened:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli._gui(registry, None, ["--demo-headsets"]), opened.return_value)  # noqa: SLF001  # the command's own function
        self.assertEqual(opened.call_args.kwargs, {"demo": True, "fresh": False, "advanced": False})


def texts(widget: Any) -> list[str]:  # noqa: ANN401  # a Tk widget
    """Return the texts of WIDGET's labels and buttons, depth first."""
    found, todo = [], [widget]
    while todo:
        w = todo.pop(0)
        if type(w).__name__ in ("CTkLabel", "CTkButton"):
            found.append(w.cget("text"))
        todo.extend(w.winfo_children())
    return found


class WindowTests(Folder):
    """The headset page and the way back in the widgets, where Tk, a display and CustomTkinter are there."""

    def setUp(self):
        super().setUp()
        self.made: list[installer.DemoEngine] = []
        self.recorded = True

    def engines(self, headset: Headset) -> installer.DemoEngine:
        engine = installer.DemoEngine(
            pace=0, recorded=self.recorded, codename=headset.info.codename, name=headset.info.name
        )
        self.made.append(engine)
        return engine

    def open(self, registry: Registry, headset: Headset | None, view: str = app.INSTALLER_VIEW) -> app.Window:
        if window.available():
            self.skipTest(window.available())
        import tkinter as tk  # noqa: PLC0415  # optional: checked above

        import customtkinter  # noqa: PLC0415  # the same

        side = advanced.Advanced(registry, headset, process=advanced.DemoProcess(pace=0), checks=advanced.demo_checks)
        choice = headsets.Choice(registry, headset, prefs=self.prefs)
        first = self.engines(headset) if headset is not None else installer.DemoEngine(pace=0)
        try:
            shown = app.Window(customtkinter, first, side, view=view, choice=choice, engines=self.engines)
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
            for probe in shown.probes.values():
                if probe.receive() and shown.view == app.CHOOSER_VIEW:
                    shown.render_chooser()
            shown.advanced_view.tick()
            shown.root.update()
            if until():
                return
            time.sleep(0.01)
        msg = "the window did not get there"
        raise AssertionError(msg)

    def links(self, shown: app.Window) -> tuple[str, str]:
        """Return how the header's mode link and back link are placed ("" when hidden)."""
        return shown.mode_link.winfo_manager(), shown.back_link.winfo_manager()

    def test_one_headset_opens_on_the_headset_page(self):
        shown = self.open(one(), None)
        self.assertEqual(shown.view, app.CHOOSER_VIEW)
        self.assertEqual(list(shown.headset_cards), ["planned"])
        self.assertEqual(self.links(shown), ("", ""))  # no way past it but a choice
        self.assertEqual(shown.subtitle.cget("text"), "")
        self.assertNotIn(headsets.LAST_USED, texts(shown.headset_cards["planned"]))
        self.pump(shown, lambda: shown.probes["planned"].done)
        self.assertIn("Last time: stopped in Install", texts(shown.headset_cards["planned"]))

        self.assertTrue(shown.choose("planned"))
        self.assertEqual(shown.view, app.INSTALLER_VIEW)
        self.pump(shown, lambda: not shown.probing)
        self.assertEqual(shown.title.cget("text"), "Welcome back")
        self.assertEqual(self.links(shown), ("pack", "pack"))
        self.assertEqual(shown.back_link.cget("text"), app.BACK)
        self.assertIn("Planned headset", shown.subtitle.cget("text"))
        self.assertEqual(json.loads(self.prefs.read_text(encoding="utf-8")), {"last_headset": "planned"})
        self.assert_env_untouched()
        self.assertEqual(len(self.made), 1)  # the page's plan probe became the installer's: no second one

    def test_the_headset_used_last_is_marked_first_with_the_main_button(self):
        self.remember({"last_headset": "planned"})
        shown = self.open(two(), None)
        self.assertEqual(list(shown.headset_cards), ["planned", "bare"])
        self.assertIn(headsets.LAST_USED, texts(shown.headset_cards["planned"]))
        self.assertNotIn(headsets.LAST_USED, texts(shown.headset_cards["bare"]))
        self.assertNotIn("Last time", " ".join(texts(shown.headset_cards["bare"])))  # no installer, no plan
        buttons = {
            codename: next(w for w in card.winfo_children() if type(w).__name__ == "CTkButton")
            for codename, card in shown.headset_cards.items()
        }
        self.assertEqual(buttons["planned"].cget("fg_color"), app.ACCENT)
        self.assertEqual(buttons["bare"].cget("fg_color"), "transparent")

    def test_device_skips_the_page(self):
        shown = self.open(two(), Planned())
        self.assertEqual(shown.view, app.INSTALLER_VIEW)
        self.pump(shown, lambda: not shown.probing)
        self.assertEqual(self.links(shown), ("pack", "pack"))
        self.assertFalse(self.prefs.exists())  # nothing was chosen in the window

    def test_the_back_link_is_on_every_page(self):
        shown = self.open(two(), None)
        shown.choose("planned")
        self.pump(shown, lambda: not shown.probing)
        pages = {"start": self.links(shown)}
        shown.open_advanced()
        pages["advanced"] = self.links(shown)
        shown.switch()
        shown.model.start_page = False  # the first stage, as without an earlier run
        shown.render()
        pages["stage"] = self.links(shown)
        shown.model.feed(json.dumps({"event": "finished", "status": "failed", "task": None, "error": "e"}))
        shown.render()
        pages["failed"] = self.links(shown)
        shown.model.feed(json.dumps({"event": "finished", "status": "done", "task": None}))
        shown.render()
        self.assertEqual((shown.title.cget("text"), shown.summary.cget("text")), ("Put it on", "Ready to play."))
        self.assertEqual([b.cget("text") for b in shown.quiet.winfo_children()], ["Update", "Get help"])
        pages["done"] = self.links(shown)
        self.assertEqual(pages, dict.fromkeys(pages, ("pack", "pack")))

        # A headset without an installer: Advanced mode only, the back link and no way to the installer.
        self.assertTrue(shown.to_headsets())
        self.assertEqual((shown.view, self.links(shown)), (app.CHOOSER_VIEW, ("", "")))
        self.assertTrue(shown.choose("bare"))
        self.assertEqual((shown.view, shown.probing, self.links(shown)), (app.ADVANCED_VIEW, False, ("", "pack")))
        self.assertEqual(shown.advanced_view.headset_name.cget("text"), "Bare headset")
        self.assertIn("tools", shown.advanced_view.entries)
        self.assertEqual(shown.after_install(), AfterInstall())

    def test_going_back_is_refused_while_something_runs(self):
        shown = self.open(two(), None)
        shown.choose("planned")
        self.pump(shown, lambda: not shown.probing)
        shown.model.running = True
        self.assertFalse(shown.to_headsets())
        self.assertEqual(panel_texts(shown)[0], "One thing at a time")
        self.assertIn("The installer is running", panel_texts(shown)[1])
        self.assertFalse(shown.choose("bare"))
        self.assertEqual(shown.view, app.INSTALLER_VIEW)
        shown.hide_panel()
        shown.model.running = False

        shown.open_advanced("help")
        shown.advanced.start()  # a step runs
        self.assertFalse(shown.to_headsets())
        self.assertIsNotNone(shown.advanced_view.panel)
        self.assertEqual(shown.view, app.ADVANCED_VIEW)

    def test_going_back_and_choosing_again_shows_the_start_page_with_the_progress_kept(self):
        shown = self.open(two(), None)
        shown.choose("planned")
        self.pump(shown, lambda: not shown.probing)
        self.assertEqual(shown.primary.cget("text"), "Continue where you left off")
        shown.press()  # the last stage: its Wi-Fi asks, then it is done
        while shown.model.view() != "done":
            self.pump(shown, lambda: shown.model.ask is not None or shown.model.view() == "done")
            if shown.model.ask is not None:
                shown.engine.send(shown.model.answer(accepted=True, values={}))
        self.pump(shown, lambda: not shown.model.running)

        self.assertTrue(shown.to_headsets())
        self.pump(shown, lambda: shown.probes["planned"].done)
        self.assertIn("Last time: every stage was done", texts(shown.headset_cards["planned"]))
        self.assertTrue(shown.choose("planned"))
        self.assertFalse(shown.probing)  # read on the page already
        self.assertEqual(shown.title.cget("text"), "Welcome back")
        self.assertIn("Every stage is done", shown.summary.cget("text"))
        self.assertIn("Finished.", shown.model.log)  # its details from before stay
        self.assertEqual(len(self.made), 1)  # one engine for the headset, its progress in it

    def test_advanced_first_opens_after_the_choice(self):
        shown = self.open(one(), None, view=app.ADVANCED_VIEW)
        self.assertEqual(shown.view, app.CHOOSER_VIEW)
        shown.choose("planned")
        self.assertEqual((shown.view, self.links(shown)), (app.ADVANCED_VIEW, ("pack", "pack")))
        shown.to_headsets()
        shown.choose("planned")
        self.assertEqual(shown.view, app.INSTALLER_VIEW)  # choosing again: its start page


if __name__ == "__main__":
    unittest.main()
