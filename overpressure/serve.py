# SPDX-License-Identifier: Unlicense
"""The engine as a server for the app: python -m overpressure serve --json [--demo].

One long-lived process, JSON lines both ways. The app sends requests, ``{"id": N, "request": NAME, ...}``;
each gets one answer, ``{"id": N, "result": {...}}`` or ``{"id": N, "error": TEXT}``. Events flow
freely: a run's are install --json's events (docs/tool.md, "The installer"), each with ``"run"`` set to
the run's id, then ``{"event": "exited", "run": ID, "code": N}`` once its engine has exited.

The protocol is versioned (PROTOCOL, in hello's answer): an app refuses a newer major. Version 0 is
the app's first spike: hello, the settings, the disclaimer, the headsets and runs.

Requests:
  hello                          the protocol, this overpressure, the platform, the disclaimer and whether it is due
  settings                       the app's settings (appearance, the disclaimer on every start)
  set_settings {appearance}      change them
  accept_disclaimer {every_start}
                                 accepted: for this server and every program it starts
  headsets                       the catalogue and the registry: each headset, what is downloaded of it
  start {codename, workflow, restart}
                                 a run of the headset's workflow (one at a time); its id
  answer {run, answer, accepted, values}
                                 the reply to the run's pending ask
  stop {run}                     ask the run to stop: at once, or after a write under way
"""

from __future__ import annotations

import contextlib
import itertools
import json
import os
import sys
import threading
from typing import IO, TYPE_CHECKING, Any, Callable, ClassVar, Union

from . import catalogue, disclaimer, localconfig, version
from .headset import INSTALL, Headset, Registry, discover
from .window.installer import ChildEngine, DemoEngine

if TYPE_CHECKING:
    from pathlib import Path

#: The protocol's major and minor version: a new request or field raises the minor, a change the major.
PROTOCOL = (0, 1)
#: The app's settings, beside the window's (out/window.json).
SETTINGS = "out/app.json"
APPEARANCES = ("system", "light", "dark")
DEFAULTS: dict[str, Any] = {"appearance": "system", "disclaimer_every_start": True, "disclaimer_seen": ""}
#: How a run's engine is made: (codename, name, workflow) -> an engine (ChildEngine, DemoEngine, a test's fake).
Engines = Callable[[str, str, str], Union[ChildEngine, DemoEngine]]


class RefusedError(Exception):
    """A request that cannot be answered; its message is the answer's error."""


def child_engines(codename: str, _name: str, workflow: str) -> ChildEngine:
    """Return the real engine: install --json as a child."""
    return ChildEngine(codename, workflow)


def demo_engines(pace: float = 1.0) -> Engines:
    """Return the demo's engines at PACE (0 in tests): nothing runs."""

    def make(codename: str, name: str, workflow: str) -> DemoEngine:
        return DemoEngine(pace=pace, codename=codename, name=name, workflow=workflow)

    return make


class Server:
    """Answers the app's requests and passes a run's events on, one JSON object per line on OUT."""

    def __init__(  # noqa: PLR0913  # what it serves, where it writes, and where it keeps what
        self,
        registry: Registry,
        out: IO[str],
        *,
        demo: bool = False,
        engines: Engines | None = None,
        root: Path | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        """Serve REGISTRY's headsets on OUT; DEMO runs nothing; ROOT holds the settings and devices/ (tests)."""
        self.registry = registry
        self.out = out
        self.demo = demo
        self.engines = engines or (demo_engines() if demo else child_engines)
        self.root = root or localconfig.ROOT
        self.environ = os.environ if environ is None else environ
        self.lock = threading.Lock()
        self.ids = itertools.count(1)
        self.run: str | None = None  # the run under way: its id
        self.engine: ChildEngine | DemoEngine | None = None
        self.after: list[Callable[[], None]] = []  # what a request does once its answer is out (a run's start)

    # -- the wire ---------------------------------------------------------------------------
    def write(self, message: dict[str, Any]) -> None:
        """Write one message as a line; the lock keeps a run's events and answers from interleaving."""
        line = json.dumps(message, ensure_ascii=False)
        with self.lock, contextlib.suppress(OSError, ValueError):  # the app has gone: nothing to tell
            self.out.write(line + "\n")
            self.out.flush()

    def serve(self, inp: IO[str]) -> int:
        """Answer requests from INP until it ends; a run under way is asked to stop then."""
        for line in inp:
            if line.strip():
                self.respond(line)
        if self.engine is not None:
            self.engine.stop()
        return 0

    def respond(self, line: str) -> None:
        """Answer one request line, then do what has to follow its answer: a run's events come after its id."""
        self.write(self.handle(line))
        after, self.after = self.after, []
        for action in after:
            action()

    def handle(self, line: str) -> dict[str, Any]:
        """Return the answer to one request line."""
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
            return {"id": ident, "result": handler(self, message)}
        except RefusedError as exc:
            return {"id": ident, "error": str(exc)}

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

    # -- headsets ---------------------------------------------------------------------------
    def _headsets(self, _message: dict[str, Any]) -> dict[str, Any]:
        found = []
        codenames = list(catalogue.CATALOGUE) + [c for c in self.registry.headsets if c not in catalogue.CATALOGUE]
        for codename in codenames:
            known = catalogue.CATALOGUE.get(codename)
            headset = self.registry.headsets.get(codename)
            info = headset.info if headset is not None else None
            record = catalogue.record_of(codename, self.root)
            found.append(
                {
                    "codename": codename,
                    "name": info.name if info else known.name if known else codename,
                    "soc": info.soc if info else "",
                    "repository": known.repository if known else "",
                    "downloaded": catalogue.installed(codename, self.root),
                    "channel": str(record.get("channel", "")),
                    "pinned": bool(record.get("pinned")),
                    "refused": self.registry.refused.get(codename, ""),
                    "workflows": self.workflows(headset) if headset is not None else [],
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

    # -- runs -------------------------------------------------------------------------------
    def _start(self, message: dict[str, Any]) -> dict[str, Any]:
        if self.run is not None:
            msg = "a run is under way: one at a time"
            raise RefusedError(msg)
        if not disclaimer.accepted(self.environ):
            msg = "the disclaimer is not accepted"
            raise RefusedError(msg)
        codename = str(message.get("codename", ""))
        headset = self.registry.headsets.get(codename)
        if headset is None:
            msg = f"no headset {codename!r} on this computer"
            raise RefusedError(msg)
        workflow = str(message.get("workflow") or INSTALL)
        if workflow not in {w.key for w in headset.workflows()}:
            msg = f"the {headset.info.name} has no workflow {workflow!r}"
            raise RefusedError(msg)
        problem = headset.workflow_problem(workflow)
        if problem:
            raise RefusedError(problem)
        extra = ["--restart", str(message["restart"])] if message.get("restart") else []
        run = f"run-{next(self.ids)}"
        engine = self.engines(codename, headset.info.name, workflow)
        self.run, self.engine = run, engine

        def begin() -> None:
            threading.Thread(target=self._pump, args=(run, engine), daemon=True).start()
            engine.start(extra)

        self.after.append(begin)
        return {"run": run}

    def _pump(self, run: str, engine: ChildEngine | DemoEngine) -> None:
        """Pass RUN's events on until its engine exits; then the next run may start."""
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
        engine.send(
            json.dumps({"answer": message.get("answer"), "accepted": message.get("accepted") is True, "values": values})
        )
        return {}

    def _stop(self, message: dict[str, Any]) -> dict[str, Any]:
        self._current(message).stop()
        return {}

    REQUESTS: ClassVar[dict[str, Callable[[Server, dict[str, Any]], dict[str, Any]]]] = {
        "hello": _hello,
        "settings": _settings,
        "set_settings": _set_settings,
        "accept_disclaimer": _accept_disclaimer,
        "headsets": _headsets,
        "start": _start,
        "answer": _answer,
        "stop": _stop,
    }


USAGE = """usage: python -m overpressure serve --json [--demo]

  --json   JSON lines both ways (the only mode): the app's requests on stdin, answers and events on stdout
  --demo   a fake engine: no headset, nothing runs"""


def main(args: list[str], registry: Registry | None = None) -> int:
    """Run the server on stdin and stdout until stdin ends."""
    if args[:1] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if "--json" not in args or any(a not in ("--json", "--demo") for a in args):
        print(USAGE, file=sys.stderr)
        return 2
    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    server = Server(registry if registry is not None else discover(), sys.stdout, demo="--demo" in args)
    return server.serve(sys.stdin)
