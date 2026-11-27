# SPDX-License-Identifier: Unlicense
"""overpressure.serve: the app's requests, the settings, the disclaimer, runs and jobs with their events."""

from __future__ import annotations

import contextlib
import io
import json
import queue
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

from overpressure import disclaimer, serve
from overpressure import headset as headsets
from overpressure.headset import Action, AfterInstall, Found, Headset, Info, Param, Registry, Step
from overpressure.install import Stage, Task

if TYPE_CHECKING:
    from collections.abc import Callable

TIMEOUT = 10.0  # seconds a test waits for an event before it fails


class Planned(Headset):
    """A headset with an installer of one stage."""

    info = Info("planned", "Planned headset", "SoC A")

    def commands(self):
        return {}

    def install_plan(self):
        return (Stage("s", "Stage", "", (Task("t", "Task", lambda _ctx: None),)),)

    def steps(self):
        return (
            Step("build", "Build", "Image", ("x", "{target}"), "Builds.", params=(Param("target", "Target", "a"),)),
        )

    def after_install(self):
        return AfterInstall(
            "Put it on",
            "Ready.",
            (Action("Again", workflow="install"), Action("Build", step="build"), Action("Gone", step="nope")),
        )


class Blocked(Planned):
    """A headset whose install cannot run now."""

    info = Info("blocked", "Blocked headset")

    def workflow_problem(self, workflow: str) -> str:
        return f"{workflow} cannot run now"


class Looks(Planned):
    """A headset that looks at itself: in its maker's system, the install fitting it and a workflow it lacks."""

    info = Info("looks", "Looking headset")

    def assess(self) -> Found:
        return Found(headsets.MAKER, "Maker OS", "1.2", (("Bootloader", "Locked"),), ("install", "nope"))


class Lines(io.StringIO):
    """A stream whose written lines arrive in a queue, for a test to wait on."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: queue.Queue[dict] = queue.Queue()
        self.kept: list[dict] = []  # messages a wait passed over, for the next waits

    def write(self, text: str) -> int:
        for line in text.splitlines():
            self.lines.put(json.loads(line))
        return len(text)

    def next(self, predicate: Callable[[dict], bool] = lambda _m: True) -> dict:
        """Return the first message PREDICATE accepts, in order; the others stay for the next waits."""
        for i, message in enumerate(self.kept):
            if predicate(message):
                return self.kept.pop(i)
        while True:
            message = self.lines.get(timeout=TIMEOUT)
            if predicate(message):
                return message
            self.kept.append(message)


class Base(unittest.TestCase):
    """A demo server on a queue of lines, with a fresh settings folder."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.out = Lines()
        self.environ: dict[str, str] = {}
        self.server = serve.Server(
            Registry({"planned": Planned(), "blocked": Blocked()}),
            self.out,
            demo=True,
            engines=serve.demo_engines(pace=0),
            pace=0,
            root=self.root,
            environ=self.environ,
        )
        self.ids = 0

    def request(self, name: str, **fields: object) -> dict:
        """Send one request and return its answer."""
        self.ids += 1
        self.server.respond(json.dumps({"id": self.ids, "request": name, **fields}))
        return self.out.next(lambda m: m.get("id") == self.ids)


class AssessTest(Base):
    def test_the_demo_finds_the_headset_after_its_plug_in_in_the_state_asked_for(self):
        found = self.request("assess", codename="planned", first=True)["result"]
        self.assertEqual((found["state"], found["system"]), (headsets.MAKER, "PICO OS"))
        self.assertEqual(found["workflows"], ["install"])  # the demo's other workflows are not the headset's
        self.environ["OVERPRESSURE_DEMO_STATE"] = headsets.EDL
        self.assertEqual(self.request("assess", codename="planned")["result"]["state"], headsets.EDL)
        self.server.pace = 100.0  # a plug-in that takes time: nothing there yet
        self.assertEqual(self.request("assess", codename="planned", first=True)["result"]["state"], headsets.ABSENT)

    def test_a_headset_looks_at_itself_and_one_that_cannot_says_so(self):
        self.server.demo = False
        self.server.registry = Registry({"looks": Looks(), "planned": Planned()})
        found = {h["codename"]: h for h in self.request("headsets")["result"]["headsets"]}
        self.assertTrue(found["looks"]["assesses"])
        self.assertFalse(found["planned"]["assesses"])
        looked = self.request("assess", codename="looks")["result"]
        self.assertEqual(looked["facts"], [["Bootloader", "Locked"]])
        self.assertEqual(looked["workflows"], ["install"])
        self.assertFalse(self.request("assess", codename="planned")["result"]["assesses"])


class ServeTest(Base):
    def test_hello_names_the_protocol_and_the_disclaimer_due(self):
        result = self.request("hello")["result"]
        self.assertEqual(result["protocol"], "{}.{}".format(*serve.PROTOCOL))
        self.assertTrue(result["demo"])
        self.assertEqual(result["disclaimer"]["text"], disclaimer.TEXT)
        self.assertTrue(result["disclaimer"]["due"])
        self.assertFalse(result["disclaimer"]["accepted"])

    def test_disclaimer_unticked_shows_once_per_version(self):
        self.request("accept_disclaimer", every_start=False)
        self.assertEqual(self.environ.get(disclaimer.ENV), "1")
        self.assertFalse(self.request("hello")["result"]["disclaimer"]["due"])
        settings = json.loads((self.root / serve.SETTINGS).read_text(encoding="utf-8"))
        settings["disclaimer_seen"] = "an older version"
        (self.root / serve.SETTINGS).write_text(json.dumps(settings), encoding="utf-8")
        self.assertTrue(self.request("hello")["result"]["disclaimer"]["due"])

    def test_disclaimer_ticked_shows_on_every_start(self):
        self.request("accept_disclaimer", every_start=True)
        self.assertTrue(self.request("hello")["result"]["disclaimer"]["due"])

    def test_settings_keep_the_appearance_and_refuse_another(self):
        self.assertEqual(self.request("settings")["result"]["appearance"], "system")
        self.assertEqual(self.request("set_settings", appearance="dark")["result"]["appearance"], "dark")
        self.assertEqual(self.request("settings")["result"]["appearance"], "dark")
        self.assertIn("error", self.request("set_settings", appearance="pink"))

    def test_headsets_list_the_catalogue_and_the_registry(self):
        found = {h["codename"]: h for h in self.request("headsets")["result"]["headsets"]}
        self.assertIn("phoenix", found)  # the catalogue's, downloaded or not
        self.assertEqual(found["planned"]["soc"], "SoC A")
        self.assertEqual([w["key"] for w in found["planned"]["workflows"]], ["install"])
        self.assertEqual(found["planned"]["workflows"][0]["problem"], "")
        self.assertEqual(found["blocked"]["workflows"][0]["problem"], "install cannot run now")

    def test_unknown_requests_and_bad_lines_are_answered(self):
        self.assertIn("error", self.request("nonsense"))
        self.server.respond("not json")
        self.assertEqual(self.out.next(), {"id": None, "error": "not JSON"})

    def test_a_run_needs_the_disclaimer_a_headset_and_a_runnable_workflow(self):
        self.assertIn("disclaimer", self.request("start", codename="planned")["error"])
        self.request("accept_disclaimer", every_start=True)
        self.assertIn("no headset", self.request("start", codename="nope")["error"])
        self.assertIn("no workflow", self.request("start", codename="planned", workflow="nope")["error"])
        self.assertEqual(self.request("start", codename="blocked")["error"], "install cannot run now")

    def test_a_run_streams_its_events_with_its_id_after_its_answer(self):
        self.request("accept_disclaimer", every_start=True)
        run = self.request("start", codename="planned")["result"]["run"]
        self.assertIn("error", self.request("start", codename="planned"))  # one at a time
        self.assertEqual(self.out.next(lambda m: "event" in m)["event"], "plan")
        while True:
            message = self.out.next(lambda m: "event" in m)
            self.assertEqual(message["run"], run)
            if message["event"] == "ask":
                self.request("answer", run=run, answer=message["id"], accepted=True, values={})
            if message["event"] == "exited":
                break
        self.assertEqual(self.request("start", codename="planned")["result"]["run"], "run-2")  # free again

    def test_stop_ends_the_run(self):
        self.request("accept_disclaimer", every_start=True)
        run = self.request("start", codename="planned")["result"]["run"]
        self.assertIn("error", self.request("stop", run="another"))
        self.out.next(lambda m: m.get("event") == "ask")  # the demo waits for the headset
        self.assertEqual(self.request("stop", run=run), {"id": self.ids, "result": {}})
        finished = self.out.next(lambda m: m.get("event") == "finished")
        self.assertEqual(finished["status"], "stopped")
        self.out.next(lambda m: m.get("event") == "exited")

    def test_the_end_of_input_stops_a_run(self):
        self.request("accept_disclaimer", every_start=True)
        self.server.respond(json.dumps({"id": 99, "request": "start", "codename": "planned"}))
        self.out.next(lambda m: m.get("event") == "ask")
        done = threading.Event()
        threading.Thread(target=lambda: (self.server.serve(io.StringIO("")), done.set()), daemon=True).start()
        self.assertEqual(self.out.next(lambda m: m.get("event") == "finished")["status"], "stopped")
        self.assertTrue(done.wait(TIMEOUT))


class JobsTest(Base):
    """The requests besides a run, in the demo: nothing is fetched, written or run."""

    def job(self, answer: dict) -> tuple[list[dict], dict]:
        """Return a job's events up to its end, and its end."""
        job = answer["result"]["job"]
        seen = []
        while True:
            message = self.out.next(lambda m: m.get("job") == job)
            seen.append(message)
            if message["event"] == "job-ended":
                return seen, message

    def test_the_catalogue_names_headsets_in_development(self):
        found = {h["codename"]: h for h in self.request("headsets")["result"]["headsets"]}
        self.assertTrue(found["hollywood"]["development"])
        self.assertEqual(found["phoenix"]["year"], 2022)
        self.assertIn("error", self.request("download", codename="hollywood"))  # stable: it has no releases
        self.assertEqual(self.request("releases", codename="hollywood")["result"]["releases"], [])
        _events, end = self.job(self.request("download", codename="hollywood", channel="preview"))
        self.assertEqual(end["code"], 0)
        found = {h["codename"]: h for h in self.request("headsets")["result"]["headsets"]}
        self.assertEqual(found["hollywood"]["downloaded"], "preview (main)")

    def test_releases_come_later_from_a_thread(self):
        result = self.request("releases", codename="phoenix")["result"]
        self.assertEqual(result["releases"][0]["version"], serve.DEMO_RELEASES[0][0])

    def test_a_download_is_a_job_and_the_headset_has_it_after(self):
        answer = self.request("download", codename="phoenix", channel="beta")
        events, end = self.job(answer)
        self.assertEqual(end["code"], 0)
        self.assertIn("job-progress", {e["event"] for e in events})
        found = {h["codename"]: h for h in self.request("headsets")["result"]["headsets"]}
        self.assertEqual(found["phoenix"]["downloaded"], f"release {serve.DEMO_RELEASES[0][0]}")
        self.assertEqual(found["phoenix"]["channel"], "beta")
        self.assertEqual(self.request("updates")["result"]["updates"], {})

    def test_one_thing_at_a_time(self):
        self.request("accept_disclaimer", every_start=True)
        self.request("start", codename="planned")
        self.out.next(lambda m: m.get("event") == "ask")  # the run waits
        self.assertIn("installer is running", self.request("download", codename="phoenix")["error"])
        self.assertIn("error", self.request("run_step", codename="planned", step="build"))
        self.assertIn("error", self.request("save_logs", codename="planned", path="logs.zip"))

    def test_the_plan_tells_what_an_earlier_run_did(self):
        stages = self.request("plan", codename="planned")["result"]["stages"]
        statuses = [t["status"] for s in stages for t in s["tasks"]]
        self.assertIn("done", statuses)  # the demo starts as if all but the last stage were done
        self.assertEqual(statuses[-1], "pending")

    def test_the_finished_page_keeps_the_actions_it_can_offer(self):
        page = self.request("finished_page", codename="planned", workflow="install")["result"]
        self.assertEqual(page["title"], "Put it on")
        self.assertEqual([a["label"] for a in page["actions"]], ["Again", "Build"])

    def test_the_backup_export_and_the_logs_are_jobs(self):
        self.assertTrue(self.request("backup", codename="planned")["result"]["present"])
        self.assertIn("error", self.request("backup_export", codename="planned", path=""))
        _events, end = self.job(self.request("backup_export", codename="planned", path="b.tar"))
        self.assertEqual(end["code"], 0)
        _events, end = self.job(self.request("save_logs", codename="planned", path="logs.zip"))
        self.assertEqual(end["kind"], "logs")

    def test_steps_their_command_and_a_run(self):
        groups = self.request("steps", codename="planned")["result"]["groups"]
        self.assertEqual(groups[0]["steps"][0]["checks"], True)  # the dependency checks first
        build = next(s for g in groups for s in g["steps"] if s["key"] == "build")
        self.assertEqual(build["params"][0]["value"], "a")
        command = self.request("command", codename="planned", step="build", values={"target": "b"})["result"]
        self.assertTrue(command["command"].endswith("x b"))
        self.assertIn("error", self.request("command", codename="planned", step="nope"))
        events, end = self.job(self.request("run_step", codename="planned", step="build", values={}))
        self.assertTrue(events[0]["line"].startswith("$ "))
        self.assertTrue(events[0]["line"].endswith("x b"))  # the values stay
        self.assertEqual(end["code"], 0)

    def test_a_step_can_be_stopped(self):
        answer = self.request("run_step", codename="planned", step="build")
        self.assertIn("error", self.request("stop_job", job="another"))
        self.assertEqual(self.request("stop_job", job=answer["result"]["job"])["result"], {})
        self.job(answer)

    def test_checks_come_later(self):
        checks = self.request("checks", codename="planned")["result"]["checks"]
        self.assertIn("missing", {c["status"] for c in checks})

    def test_carry_over_is_offered_in_the_demo_only_when_asked(self):
        self.assertEqual(self.request("carry_over")["result"]["from"], "")
        self.environ["OVERPRESSURE_DEMO_CARRY_OVER"] = "1"
        offered = self.request("carry_over")["result"]
        self.assertTrue(offered["from"].endswith(serve.DEMO_EARLIER))
        self.assertTrue(self.request("carry", **{"from": offered["from"]})["result"]["brought"])
        self.environ["OVERPRESSURE_DEMO_CARRY_OVER"] = "ask"
        self.assertEqual(self.request("carry_over")["result"]["ask"], True)  # the app asks for the folder


class AppCarryOverTest(Base):
    """The app's data folder (a root apart from overpressure's own files): it asks for the earlier folder."""

    def setUp(self) -> None:
        super().setUp()
        self.server.demo = False

    def test_a_fresh_data_folder_asks_and_a_chosen_folder_is_checked(self):
        with mock.patch.object(serve.carryover, "portable_places", return_value=[]):
            answer = self.request("carry_over")["result"]
        self.assertEqual((answer["from"], answer["ask"]), ("", True))
        refused = self.request("carry_check", **{"from": str(self.root)})
        self.assertIn("already", refused["error"])
        old = self.root.parent / "overpressure-earlier"
        (old / "overpressure").mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(old, ignore_errors=True))
        (old / "overpressure" / "__init__.py").write_text("", encoding="utf-8")
        (old / "pyproject.toml").write_text("", encoding="utf-8")
        (old / ".env").write_text("x=1\n", encoding="utf-8")
        checked = self.request("carry_check", **{"from": str(old)})["result"]
        self.assertIn(str(old), checked["question"])


class MainTest(unittest.TestCase):
    def test_json_is_required(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(serve.main([], Registry()), 2)
            self.assertEqual(serve.main(["--json", "--other"], Registry()), 2)


if __name__ == "__main__":
    unittest.main()
