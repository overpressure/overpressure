# SPDX-License-Identifier: Unlicense
"""overpressure.serve: the app's requests, the settings, the disclaimer, and a run's events with its id."""

from __future__ import annotations

import contextlib
import io
import json
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from typing import TYPE_CHECKING

from overpressure import disclaimer, serve
from overpressure.headset import Headset, Info, Registry
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


class Blocked(Planned):
    """A headset whose install cannot run now."""

    info = Info("blocked", "Blocked headset")

    def workflow_problem(self, workflow: str) -> str:
        return f"{workflow} cannot run now"


class Lines(io.StringIO):
    """A stream whose written lines arrive in a queue, for a test to wait on."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: queue.Queue[dict] = queue.Queue()

    def write(self, text: str) -> int:
        for line in text.splitlines():
            self.lines.put(json.loads(line))
        return len(text)

    def next(self, predicate: Callable[[dict], bool] = lambda _m: True) -> dict:
        """Return the next message PREDICATE accepts, skipping the others."""
        while True:
            message = self.lines.get(timeout=TIMEOUT)
            if predicate(message):
                return message


class ServeTest(unittest.TestCase):
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
            root=self.root,
            environ=self.environ,
        )
        self.ids = 0

    def request(self, name: str, **fields: object) -> dict:
        """Send one request and return its answer."""
        self.ids += 1
        self.server.respond(json.dumps({"id": self.ids, "request": name, **fields}))
        return self.out.next(lambda m: m.get("id") == self.ids)

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


class MainTest(unittest.TestCase):
    def test_json_is_required(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(serve.main([], Registry()), 2)
            self.assertEqual(serve.main(["--json", "--other"], Registry()), 2)


if __name__ == "__main__":
    unittest.main()
