# SPDX-License-Identifier: Unlicense
"""The engine as a server for the app: python -m overpressure serve --json [--demo | --demo-fresh].

One long-lived process, JSON lines both ways. The app sends requests, ``{"id": N, "request": NAME, ...}``;
each gets one answer, ``{"id": N, "result": {...}}`` or ``{"id": N, "error": TEXT}``. An answer that takes
time (the network, the checks) comes later, from a thread; the other requests are answered meanwhile.

Events flow freely. A run's are install --json's events (docs/tool.md, "The installer"), each with
``"run"`` set to the run's id, then ``{"event": "exited", "run": ID, "code": N}``. A job (a download, a
backup's export or import, the logs, a step of Advanced mode) sends ``job-line``, ``job-progress`` and
last ``job-ended``, each with ``"job"`` set to its id.

One thing at a time: a run, a download, a backup's export or import, the logs or a step; the checks, the
releases and the plan only look, and may come any time.

The protocol is versioned (PROTOCOL, in hello's answer): an app refuses a newer major. Its requests are
listed in docs/tool.md, "The app's server". --demo runs nothing: fake engines, steps, checks, downloads
and backups (the window's demo fakes), as if an earlier install had done all but its last stage;
--demo-fresh starts with nothing done. The demo treats a development checkout as a downloaded release.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Callable, ClassVar, Union

from . import carryover, catalogue, disclaimer, doctor, localconfig, progress, version
from .headset import INSTALL, Headset, Registry, discover
from .window.advanced import DemoProcess, StepProcess, Steps, browse, demo_checks
from .window.installer import BackupJob, ChildEngine, DemoBackupJob, DemoEngine

#: The protocol's major and minor version: a new request or field raises the minor, a change the major.
PROTOCOL = (0, 2)
#: The app's settings, beside the window's (out/window.json).
SETTINGS = "out/app.json"
APPEARANCES = ("system", "light", "dark")
DEFAULTS: dict[str, Any] = {"appearance": "system", "disclaimer_every_start": True, "disclaimer_seen": ""}
#: How a run's engine is made: (codename, name, workflow) -> an engine (ChildEngine, DemoEngine, a test's fake).
Engines = Callable[[str, str, str], Union[ChildEngine, DemoEngine]]
#: What the demo offers as published releases, newest first: (version, prerelease).
DEMO_RELEASES = (("2026.11.0b1", True), ("2026.11.0b0", True), ("1.0-beta.5", True), ("1.0-beta.4", True))
#: The demo's earlier overpressure folder, offered with OVERPRESSURE_DEMO_CARRY_OVER=1.
DEMO_EARLIER = "overpressure-2026.11.0b0"
#: What a job is, in the words of the refusal while it runs.
JOB_WORDS = {
    "download": "A download",
    "export": "The backup's export",
    "import": "The backup's import",
    "logs": "Saving the logs",
    "step": "A step of Advanced mode",
}
#: A job's item: ("line", text) or ("exit", code).
Item = tuple[str, Union[str, int]]


class RefusedError(Exception):
    """A request that cannot be answered; its message is the answer's error."""


@dataclass
class Later:
    """An answer that takes time: WORK runs on a thread, and its result (or RefusedError) is the answer."""

    work: Callable[[], dict[str, Any]]


def child_engines(codename: str, _name: str, workflow: str) -> ChildEngine:
    """Return the real engine: install --json as a child."""
    return ChildEngine(codename, workflow)


def demo_engines(pace: float = 1.0, *, recorded: bool = True) -> Engines:
    """Return the demo's engines at PACE (0 in tests), one per headset and workflow, so a retry goes on.

    RECORDED: the install starts as if an earlier run had done every stage but the last.
    """
    made: dict[tuple[str, str], DemoEngine] = {}

    def make(codename: str, name: str, workflow: str) -> DemoEngine:
        key = (codename, workflow)
        if key not in made:
            made[key] = DemoEngine(pace=pace, codename=codename, name=name, workflow=workflow, recorded=recorded)
        return made[key]

    return make


class Download:
    """A headset's release downloaded into devices/ (catalogue.add) on a thread: its lines, then its exit."""

    def __init__(self, codename: str, channel: str, wanted: str, root: Path) -> None:
        """Download CODENAME's release WANTED, else the newest on CHANNEL, into ROOT/devices."""
        self.items: queue.Queue[Item] = queue.Queue()
        self.codename, self.channel, self.wanted, self.root = codename, channel, wanted, root

    def start(self) -> None:
        """Start it."""
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        def log(line: str) -> None:
            self.items.put(("line", line))

        try:
            catalogue.add(self.codename, channel=self.channel, version=self.wanted, root=self.root, log=log)
        except (LookupError, RuntimeError, ValueError, OSError) as exc:
            log(f"error: {exc or type(exc).__name__}")
            self.items.put(("exit", 1))
            return
        self.items.put(("exit", 0))


class DemoDownload:
    """--demo's download: a few progress lines; nothing is fetched or written."""

    def __init__(self, name: str, wanted: str, *, pace: float = 1.0) -> None:
        """Pretend to download NAME's release WANTED; PACE scales the pauses (0 in tests)."""
        self.items: queue.Queue[Item] = queue.Queue()
        self.name, self.wanted, self.pace = name, wanted, pace

    def start(self) -> None:
        """Send the demo's lines on a thread."""

        def work() -> None:
            self.items.put(("line", f"downloading {self.name} {self.wanted} (the demo fetches nothing)"))
            for n in range(1, 11):
                time.sleep(0.15 * self.pace)
                self.items.put(("line", progress.line(f"{n * 4} of 40 MB", n / 10)))
            self.items.put(("line", "checked against its SHA-256"))
            self.items.put(("exit", 0))

        threading.Thread(target=work, daemon=True).start()


@dataclass
class Job:
    """Something running besides a run: its id, what it is, and what reads its lines."""

    id: str
    kind: str
    codename: str
    source: Any  # Download, DemoDownload, BackupJob, StepProcess, DemoProcess: items, and for a step stop()
    done: Callable[[int], None] | None = None  # what follows its end (a download: the headsets found anew)


class Server:
    """Answers the app's requests and passes runs' and jobs' events on, one JSON object per line on OUT."""

    def __init__(  # noqa: PLR0913  # what it serves, where it writes, and where it keeps what
        self,
        registry: Registry,
        out: IO[str],
        *,
        demo: bool = False,
        engines: Engines | None = None,
        root: Path | None = None,
        environ: dict[str, str] | None = None,
        pace: float = 1.0,
        rediscover: Callable[[], Registry] = discover,
    ) -> None:
        """Serve REGISTRY's headsets on OUT; DEMO runs nothing; ROOT holds the settings and devices/ (tests).

        PACE scales the demo's pauses (0 in tests); REDISCOVER finds the headsets again after a download.
        """
        self.registry = registry
        self.out = out
        self.demo = demo
        self.pace = pace
        self.engines = engines or (demo_engines(pace) if demo else child_engines)
        self.root = root or localconfig.ROOT
        self.environ = os.environ if environ is None else environ
        self.rediscover = rediscover
        self.lock = threading.Lock()
        self.ids = itertools.count(1)
        self.run: str | None = None  # the run under way: its id
        self.engine: ChildEngine | DemoEngine | None = None
        self.job: Job | None = None  # the job under way
        self.after: list[Callable[[], None]] = []  # what a request does once its answer is out (a run's start)
        self.values: dict[tuple[str, str], dict[str, str]] = {}  # (codename, step) -> its parameters' values
        self.demo_downloaded: dict[str, dict[str, Any]] = {}  # the demo's downloads: codename -> its record

    # -- the wire ---------------------------------------------------------------------------
    def write(self, message: dict[str, Any]) -> None:
        """Write one message as a line; the lock keeps events and answers from interleaving."""
        line = json.dumps(message, ensure_ascii=False)
        with self.lock, contextlib.suppress(OSError, ValueError):  # the app has gone: nothing to tell
            self.out.write(line + "\n")
            self.out.flush()

    def serve(self, inp: IO[str]) -> int:
        """Answer requests from INP until it ends; a run or a step under way is asked to stop then."""
        for line in inp:
            if line.strip():
                self.respond(line)
        if self.engine is not None:
            self.engine.stop()
        if self.job is not None and hasattr(self.job.source, "stop"):
            self.job.source.stop()
        return 0

    def respond(self, line: str) -> None:
        """Answer one request line, then do what has to follow its answer: a run's events come after its id."""
        answer = self.handle(line)
        if answer is not None:
            self.write(answer)
        after, self.after = self.after, []
        for action in after:
            action()

    def handle(self, line: str) -> dict[str, Any] | None:
        """Return the answer to one request line; None when it comes later, from a thread."""
        try:
            message = json.loads(line)
        except ValueError:
            return {"id": None, "error": "not JSON"}
        if not isinstance(message, dict):
            return {"id": None, "error": "not a JSON object"}
        ident = message.get("id")
        handler = self.REQUESTS.get(str(message.get("request")))
        if handler is None:
            return {"id": ident, "error": f"unknown request: {message.get('request')}"}
        try:
            result = handler(self, message)
        except RefusedError as exc:
            self.after.clear()  # a refused request starts nothing
            return {"id": ident, "error": str(exc)}
        if isinstance(result, Later):
            threading.Thread(target=self._later, args=(ident, result), daemon=True).start()
            return None
        return {"id": ident, "result": result}

    def _later(self, ident: object, later: Later) -> None:
        try:
            self.write({"id": ident, "result": later.work()})
        except RefusedError as exc:
            self.write({"id": ident, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001  # the app is told instead of waiting for ever
            self.write({"id": ident, "error": f"{type(exc).__name__}: {exc}"})

    # -- what a request names, and what runs --------------------------------------------------
    def headset(self, message: dict[str, Any]) -> Headset:
        """Return the headset the request names (codename), or refuse."""
        codename = str(message.get("codename", ""))
        headset = self.registry.headsets.get(codename)
        if headset is None:
            msg = f"no headset {codename!r} on this computer"
            raise RefusedError(msg)
        return headset

    def busy(self) -> str:
        """Return what runs now, in words, or "" when nothing does: one thing at a time."""
        if self.run is not None:
            return "The installer is running. Let it finish, or stop it, first."
        if self.job is not None:
            return f"{JOB_WORDS[self.job.kind]} is running. Let it finish first."
        return ""

    def refuse_if_busy(self) -> None:
        """Refuse when something runs."""
        problem = self.busy()
        if problem:
            raise RefusedError(problem)

    # -- settings and the disclaimer --------------------------------------------------------
    def settings(self) -> dict[str, Any]:
        """Return the app's settings, the defaults for what the file lacks."""
        try:
            data = json.loads((self.root / SETTINGS).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        found = data if isinstance(data, dict) else {}
        return {key: found.get(key, value) for key, value in DEFAULTS.items()}

    def save(self, settings: dict[str, Any]) -> None:
        """Keep SETTINGS in the settings file."""
        path = self.root / SETTINGS
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=1) + "\n", encoding="utf-8", newline="\n")

    def disclaimer_due(self) -> bool:
        """Tell whether the app shows the disclaimer on this start: on every start, else once per version."""
        settings = self.settings()
        return bool(settings["disclaimer_every_start"]) or settings["disclaimer_seen"] != version.current()

    def _hello(self, _message: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocol": "{}.{}".format(*PROTOCOL),
            "version": version.describe(),
            "platform": sys.platform,
            "demo": self.demo,
            "disclaimer": {
                "title": disclaimer.TITLE,
                "text": disclaimer.TEXT,
                "accept": disclaimer.ACCEPT,
                "due": self.disclaimer_due(),
                "accepted": disclaimer.accepted(self.environ),
            },
        }

    def _settings(self, _message: dict[str, Any]) -> dict[str, Any]:
        return self.settings()

    def _set_settings(self, message: dict[str, Any]) -> dict[str, Any]:
        settings = self.settings()
        appearance = message.get("appearance", settings["appearance"])
        if appearance not in APPEARANCES:
            msg = f"appearance is one of {', '.join(APPEARANCES)}"
            raise RefusedError(msg)
        settings["appearance"] = appearance
        self.save(settings)
        return settings

    def _accept_disclaimer(self, message: dict[str, Any]) -> dict[str, Any]:
        settings = self.settings()
        settings["disclaimer_every_start"] = bool(message.get("every_start", settings["disclaimer_every_start"]))
        settings["disclaimer_seen"] = version.current()
        self.save(settings)
        disclaimer.accept(self.environ)  # the engines it starts inherit it, as with --accept-disclaimer
        return settings

    # -- headsets: the catalogue, what is here, releases, downloads, updates -----------------
    def record(self, codename: str) -> dict[str, Any]:
        """Return what the download of CODENAME recorded (the demo's own downloads first)."""
        return self.demo_downloaded.get(codename) or catalogue.record_of(codename, self.root)

    def downloaded(self, codename: str) -> str:
        """Return what is downloaded of CODENAME in words, or ""; in the demo its downloads, a checkout a release."""
        demo = self.demo_downloaded.get(codename)
        if demo is not None:
            return f"release {demo['tag']}" if demo["tag"] != "main" else "preview (main)"
        found = catalogue.installed(codename, self.root)
        if self.demo and found == catalogue.CHECKOUT:
            return f"release {DEMO_RELEASES[1][0]}"
        return found

    def checkout(self, codename: str) -> bool:
        """Tell whether devices/CODENAME is a development checkout; the demo treats one as a download."""
        return not self.demo and (self.root / "devices" / codename / ".git").exists()

    def _headsets(self, _message: dict[str, Any]) -> dict[str, Any]:
        known = {**catalogue.CATALOGUE, **catalogue.IN_DEVELOPMENT}
        codenames = list(known) + [c for c in self.registry.headsets if c not in known]
        found = []
        for codename in codenames:
            entry = known.get(codename)
            headset = self.registry.headsets.get(codename)
            info = headset.info if headset is not None else None
            record = self.record(codename)
            found.append(
                {
                    "codename": codename,
                    "name": info.name if info else entry.name if entry else codename,
                    "soc": (info.soc if info else "") or (entry.soc if entry else ""),
                    "year": entry.year if entry else 0,
                    "repository": entry.repository if entry else "",
                    "development": codename in catalogue.IN_DEVELOPMENT,
                    "downloaded": self.downloaded(codename),
                    "checkout": self.checkout(codename),
                    "channel": str(record.get("channel") or ""),
                    "pinned": bool(record.get("pinned")),
                    "refused": self.registry.refused.get(codename, ""),
                    "workflows": self.workflows(headset) if headset is not None else [],
                    "has_steps": bool(headset is not None and list(headset.steps())),
                }
            )
        return {"headsets": found}

    @staticmethod
    def workflows(headset: Headset) -> list[dict[str, Any]]:
        """Return HEADSET's workflows for the app, each with why it cannot run now ("" when it can)."""
        return [
            {
                "key": w.key,
                "title": w.title,
                "summary": w.summary,
                "erases": w.erases,
                "changes": w.changes,
                "problem": headset.workflow_problem(w.key),
            }
            for w in headset.workflows()
        ]

    def _releases(self, message: dict[str, Any]) -> Later:
        codename = str(message.get("codename", ""))
        known = catalogue.CATALOGUE.get(codename)
        if known is None:
            msg = f"{codename!r} has no releases to download"
            raise RefusedError(msg)

        def work() -> dict[str, Any]:
            if self.demo:
                found = [{"version": v, "prerelease": p, "problem": ""} for v, p in DEMO_RELEASES]
            else:
                found = [
                    {"version": r.version, "prerelease": r.prerelease, "problem": r.problem}
                    for r in catalogue.releases(known)
                ]
            record = self.record(codename)
            return {"releases": found, "record": {k: record.get(k) for k in ("tag", "channel", "pinned")}}

        return Later(work)

    def _download(self, message: dict[str, Any]) -> dict[str, Any]:
        codename = str(message.get("codename", ""))
        known = catalogue.CATALOGUE.get(codename)
        if known is None:
            msg = f"{codename!r} has no release to download yet"
            raise RefusedError(msg)
        if self.checkout(codename):
            msg = f"devices/{codename} is a development checkout: update it with git, it is not replaced"
            raise RefusedError(msg)
        self.refuse_if_busy()
        channel = str(message.get("channel") or catalogue.STABLE)
        if channel not in catalogue.CHANNELS:
            msg = f"channel is one of {', '.join(catalogue.CHANNELS)}"
            raise RefusedError(msg)
        wanted = str(message.get("version") or "")
        source: DemoDownload | Download
        if self.demo:
            tag = wanted or ("main" if channel == catalogue.PREVIEW else DEMO_RELEASES[0][0])
            source = DemoDownload(known.name, tag, pace=self.pace)

            def done(code: int) -> None:
                if code == 0:
                    self.demo_downloaded[codename] = {"tag": tag, "channel": channel, "pinned": bool(wanted)}

        else:
            source = Download(codename, channel, wanted, self.root)

            def done(_code: int) -> None:
                self.registry = self.rediscover()

        self.after.append(source.start)
        return self.start_job("download", codename, source, done)

    def _updates(self, _message: dict[str, Any]) -> Later:
        def work() -> dict[str, Any]:
            found: dict[str, str] = {}
            for codename in catalogue.CATALOGUE:
                if self.demo:
                    if codename in self.registry.headsets and codename not in self.demo_downloaded:
                        found[codename] = DEMO_RELEASES[0][0]
                    continue
                with contextlib.suppress(LookupError, ValueError, OSError):  # offline: no updates known
                    newer = catalogue.update(codename, self.root)
                    if newer is not None:
                        found[codename] = newer.version
            return {"updates": found}

        return Later(work)

    # -- a headset's workflows: the plan, runs, the finished page ----------------------------
    def _plan(self, message: dict[str, Any]) -> Later:
        headset = self.headset(message)
        workflow = str(message.get("workflow") or INSTALL)
        self.refuse_if_busy()
        engine = self.engines(headset.info.codename, headset.info.name, workflow)

        def work() -> dict[str, Any]:
            engine.start(["--plan"])
            stages: list[dict[str, Any]] = []
            status: dict[str, str] = {}
            while True:
                kind, value = engine.items.get(timeout=120)
                if kind == "exit":
                    break
                with contextlib.suppress(ValueError):
                    event = json.loads(str(value))
                    if event.get("event") == "plan":
                        stages = event["stages"]
                    elif event.get("event") == "task":
                        status[event["task"]] = event["status"]
            for stage in stages:
                for task in stage["tasks"]:
                    task["status"] = status.get(task["id"], "pending")
            return {"stages": stages}

        return Later(work)

    def _start(self, message: dict[str, Any]) -> dict[str, Any]:
        self.refuse_if_busy()
        if not disclaimer.accepted(self.environ):
            msg = "the disclaimer is not accepted"
            raise RefusedError(msg)
        headset = self.headset(message)
        workflow = str(message.get("workflow") or INSTALL)
        if workflow not in {w.key for w in headset.workflows()}:
            msg = f"the {headset.info.name} has no workflow {workflow!r}"
            raise RefusedError(msg)
        problem = headset.workflow_problem(workflow)
        if problem:
            raise RefusedError(problem)
        extra = ["--restart", str(message["restart"])] if message.get("restart") else []
        run = f"run-{next(self.ids)}"
        engine = self.engines(headset.info.codename, headset.info.name, workflow)
        self.run, self.engine = run, engine

        def begin() -> None:
            threading.Thread(target=self._pump_run, args=(run, engine), daemon=True).start()
            engine.start(extra)

        self.after.append(begin)
        return {"run": run}

    def _pump_run(self, run: str, engine: ChildEngine | DemoEngine) -> None:
        """Pass RUN's events on until its engine exits; then the next thing may start."""
        while True:
            kind, value = engine.items.get()
            if kind == "exit":
                with self.lock:
                    self.run, self.engine = None, None
                self.write({"event": "exited", "run": run, "code": value})
                return
            text = str(value).rstrip("\n")
            try:
                event = json.loads(text)
            except ValueError:
                event = None
            if isinstance(event, dict) and "event" in event:
                self.write({**event, "run": run})
            elif text:
                self.write({"event": "output", "run": run, "line": text})  # not an event: the engine's own words

    def _current(self, message: dict[str, Any]) -> ChildEngine | DemoEngine:
        if self.engine is None or message.get("run") != self.run:
            msg = f"no run {message.get('run')!r} under way"
            raise RefusedError(msg)
        return self.engine

    def _answer(self, message: dict[str, Any]) -> dict[str, Any]:
        engine = self._current(message)
        values = message.get("values") if isinstance(message.get("values"), dict) else {}
        reply = {"answer": message.get("answer"), "accepted": message.get("accepted") is True, "values": values}
        engine.send(json.dumps(reply))
        return {}

    def _stop(self, message: dict[str, Any]) -> dict[str, Any]:
        self._current(message).stop()
        return {}

    def _finished_page(self, message: dict[str, Any]) -> dict[str, Any]:
        headset = self.headset(message)
        page = headset.finished_page(str(message.get("workflow") or INSTALL))
        workflows = {w.key for w in headset.workflows()}
        steps = {s.key for s in headset.steps()}
        actions = [
            {"label": a.label, "workflow": a.workflow if a.workflow in workflows else "", "step": a.step}
            for a in page.actions
            if a.workflow in workflows or a.step in steps
        ]
        return {"title": page.title, "text": page.text, "actions": actions}

    # -- jobs: what runs besides a run -------------------------------------------------------
    def start_job(
        self,
        kind: str,
        codename: str,
        source: Any,  # noqa: ANN401  # one of the sources Job names
        done: Callable[[int], None] | None = None,
    ) -> dict[str, Any]:
        """Make SOURCE the job KIND for CODENAME, read once its answer is out; return its id."""
        job = Job(f"job-{next(self.ids)}", kind, codename, source, done)
        self.job = job
        self.after.append(lambda: threading.Thread(target=self._pump_job, args=(job,), daemon=True).start())
        return {"job": job.id}

    def _pump_job(self, job: Job) -> None:
        """Pass JOB's lines on as events until it exits."""
        last_error = ""
        while True:
            kind, value = job.source.items.get()
            if kind == "exit":
                code = int(value)
                if job.done is not None:
                    job.done(code)
                with self.lock:
                    self.job = None
                self.write({"event": "job-ended", "job": job.id, "kind": job.kind, "code": code, "error": last_error})
                return
            text = str(value).rstrip("\n")
            found = progress.parse(text)
            if found is not None:
                fraction, what = found
                self.write({"event": "job-progress", "job": job.id, "fraction": fraction, "text": what})
            elif text.strip():
                if text.startswith("error:"):
                    last_error = text.removeprefix("error:").strip()
                self.write({"event": "job-line", "job": job.id, "line": text})

    def _stop_job(self, message: dict[str, Any]) -> dict[str, Any]:
        job = self.job
        if job is None or message.get("job") != job.id:
            msg = f"no job {message.get('job')!r} under way"
            raise RefusedError(msg)
        if not hasattr(job.source, "stop"):
            msg = "this job finishes by itself"
            raise RefusedError(msg)
        job.source.stop()
        return {}

    # -- the backup and the logs -------------------------------------------------------------
    def _backup(self, message: dict[str, Any]) -> dict[str, Any]:
        headset = self.headset(message)
        if self.demo:
            return {"supported": True, "present": True, "folder": "stock-demo"}
        here = headset.backup_set()
        if here is None:
            return {"supported": False, "present": False, "folder": ""}
        return {"supported": True, "present": (here.folder / "manifest.json").is_file(), "folder": here.folder.name}

    def _backup_job(self, message: dict[str, Any], action: str) -> dict[str, Any]:
        headset = self.headset(message)
        path = str(message.get("path") or "").strip()
        if not path:
            msg = "no file chosen"
            raise RefusedError(msg)
        self.refuse_if_busy()
        codename = headset.info.codename
        job = DemoBackupJob(codename, action, path, pace=self.pace) if self.demo else BackupJob(codename, action, path)
        self.after.append(job.start)
        return self.start_job(action, codename, job)

    def _backup_export(self, message: dict[str, Any]) -> dict[str, Any]:
        return self._backup_job(message, "export")

    def _backup_import(self, message: dict[str, Any]) -> dict[str, Any]:
        return self._backup_job(message, "import")

    def _save_logs(self, message: dict[str, Any]) -> dict[str, Any]:
        return self._backup_job(message, "logs")

    # -- Advanced mode: steps and checks -----------------------------------------------------
    def steps_of(self, headset: Headset, key: str, values: object) -> Steps:
        """Return the steps model at HEADSET's step KEY with VALUES set (they stay, per headset and step)."""
        model = Steps(self.registry, headset)
        model.values = self.values
        if not model.select(key) or model.step() is None:
            msg = f"the {headset.info.name} has no step {key!r}"
            raise RefusedError(msg)
        for name, value in (values if isinstance(values, dict) else {}).items():
            model.set_value(str(name), str(value))
        return model

    def _steps(self, message: dict[str, Any]) -> dict[str, Any]:
        headset = self.headset(message)
        model = Steps(self.registry, headset)
        model.values = self.values
        by_key = {s.key: s for s in model.steps()}
        groups = []
        for group, entries in model.groups():
            items: list[dict[str, Any]] = []
            for entry in entries:
                step = by_key.get(entry.key)
                if step is None:  # the dependency checks
                    items.append({"key": entry.key, "title": entry.title, "checks": True})
                    continue
                values = model.values_of(step)
                params = [
                    {
                        "name": p.name,
                        "label": p.label,
                        "kind": p.kind,
                        "choices": list(p.choices),
                        "help": p.help,
                        "value": values.get(p.name, p.default),
                        "browse": browse(step, p),
                    }
                    for p in step.params
                ]
                items.append(
                    {
                        "key": step.key,
                        "title": step.title,
                        "help": model.help(step),
                        "writes": step.writes,
                        "linux_root": step.linux_root,
                        "params": params,
                        "checks": False,
                    }
                )
            groups.append({"group": group, "steps": items})
        return {"groups": groups}

    def _command(self, message: dict[str, Any]) -> dict[str, Any]:
        model = self.steps_of(self.headset(message), str(message.get("step", "")), message.get("values"))
        return {"command": model.command(), "problem": model.problem()}

    def _run_step(self, message: dict[str, Any]) -> dict[str, Any]:
        headset = self.headset(message)
        model = self.steps_of(headset, str(message.get("step", "")), message.get("values"))
        problem = model.problem()
        if problem:
            raise RefusedError(problem)
        self.refuse_if_busy()
        step = model.step()
        if step is not None and step.writes and not disclaimer.accepted(self.environ):
            msg = "the disclaimer is not accepted"
            raise RefusedError(msg)
        argv = model.argv()
        process: DemoProcess | StepProcess = DemoProcess(pace=self.pace) if self.demo else StepProcess()
        answer = self.start_job("step", headset.info.codename, process)
        command = "$ " + model.command()
        self.after.append(lambda: self.write({"event": "job-line", "job": answer["job"], "line": command}))
        self.after.append(lambda: process.start(argv))
        return answer

    def _checks(self, message: dict[str, Any]) -> Later:
        headset = self.headset(message)

        def work() -> dict[str, Any]:
            checks = demo_checks(headset) if self.demo else doctor.run(headset)
            return {
                "checks": [
                    {"name": c.name, "needed_for": c.needed_for, "status": c.status, "detail": c.detail, "fix": c.fix}
                    for c in checks
                ]
            }

        return Later(work)

    # -- carry-over from an earlier folder ---------------------------------------------------
    def _carry_over(self, _message: dict[str, Any]) -> dict[str, Any]:
        if self.demo:
            offered = self.environ.get("OVERPRESSURE_DEMO_CARRY_OVER") == "1"
            old = self.root.parent / DEMO_EARLIER
            return {"from": str(old) if offered else "", "question": carryover.question(old) if offered else ""}
        found = carryover.previous(self.root)
        return {"from": str(found) if found else "", "question": carryover.question(found) if found else ""}

    def _carry(self, message: dict[str, Any]) -> Later:
        old = Path(str(message.get("from", "")))
        self.refuse_if_busy()

        def work() -> dict[str, Any]:
            if self.demo:
                return {"brought": ["the backup (backup/, moved)", "the settings (.env)"]}
            try:
                brought = carryover.carry(old, self.root, say=lambda _line: None)
            except ValueError as exc:
                raise RefusedError(str(exc)) from exc
            self.registry = self.rediscover()
            return {"brought": brought}

        return Later(work)

    def _decline_carry_over(self, message: dict[str, Any]) -> dict[str, Any]:
        if not self.demo:
            carryover.decline(Path(str(message.get("from", ""))), self.root)
        return {}

    REQUESTS: ClassVar[dict[str, Callable[[Server, dict[str, Any]], Any]]] = {
        "hello": _hello,
        "settings": _settings,
        "set_settings": _set_settings,
        "accept_disclaimer": _accept_disclaimer,
        "headsets": _headsets,
        "releases": _releases,
        "download": _download,
        "updates": _updates,
        "plan": _plan,
        "start": _start,
        "answer": _answer,
        "stop": _stop,
        "finished_page": _finished_page,
        "stop_job": _stop_job,
        "backup": _backup,
        "backup_export": _backup_export,
        "backup_import": _backup_import,
        "save_logs": _save_logs,
        "steps": _steps,
        "command": _command,
        "run_step": _run_step,
        "checks": _checks,
        "carry_over": _carry_over,
        "carry": _carry,
        "decline_carry_over": _decline_carry_over,
    }


USAGE = """usage: python -m overpressure serve --json [--demo | --demo-fresh]

  --json         JSON lines both ways (the only mode): the app's requests on stdin, answers and events on stdout
  --demo         nothing runs: fake engines, steps, checks, downloads and backups; the install starts as if an
                 earlier run had done all but its last stage
  --demo-fresh   the same, with nothing done before"""


def main(args: list[str], registry: Registry | None = None) -> int:
    """Run the server on stdin and stdout until stdin ends."""
    if args[:1] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if "--json" not in args or any(a not in ("--json", "--demo", "--demo-fresh") for a in args):
        print(USAGE, file=sys.stderr)
        return 2
    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    fresh = "--demo-fresh" in args
    demo = fresh or "--demo" in args
    server = Server(
        registry if registry is not None else discover(),
        sys.stdout,
        demo=demo,
        engines=demo_engines(recorded=not fresh) if demo else None,
    )
    return server.serve(sys.stdin)
