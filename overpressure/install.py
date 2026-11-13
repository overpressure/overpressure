# SPDX-License-Identifier: Unlicense
"""The install engine: a headset's plan of stages and tasks, run in order and resumable.

    python -m overpressure install [--workflow KEY] [--json] [--plan] [--restart STAGE]
    python -m overpressure upgrade|restore [--json] [--plan] [--restart STAGE]

A headset's plan (Headset.plan, for each of its workflows: install, and where it has them an
upgrade and the way back) is a sequence of stages, each a sequence of tasks. The engine runs the
tasks in order and stops at the first failure; a task done in an earlier run is skipped unless its
own done check says it must run again, and a done check may also skip a task on its first run (its
output is there already). Each headset has its own folder: the install's state is
out/install/<codename>/state.json and another workflow's out/install/<codename>/<workflow>/state.json,
the output of each task's child programs <task>.log beside it. A workflow that finishes forgets
what the others had done: each starts again from the headset as this one left it. One engine runs
at a time on this computer, whatever the headset (out/install/lock: builds share their caches).

The engine talks in events (docs/tool.md, "The installer"): with --json one JSON object per
line on stdout, answers to its asks one per line on stdin, for the window; without it
the same as text for a terminal, its asks through input() and getpass(). --plan only shows the
plan and what is done; --restart STAGE forgets what was done from that stage on.

Secret values (a form's secret fields) never reach a log, the state, .env, a command line, an
event or stderr: they live in the task's memory only. Stopping (Ctrl-C, or the window closing
the engine's stdin) ends the run at once wherever it waits: an ask, a wait through ctx.clock (every
until() a task runs), a child that does not write (it is ended). A child that writes to the headset
is waited for, never killed midway: the stop takes effect when it has ended.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import getpass
import io
import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Callable, ClassVar, Protocol, TypeVar

from . import REPO, localconfig, power, runner
from . import progress as progress_lines
from .headset import INSTALL

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence

    from .headset import Headset, Info
    from .process import Timing

S = TypeVar("S", bound=localconfig.Settings)

#: Where the lock lives, and each headset's folder with its state and its tasks' logs.
OUT = REPO / "out" / "install"
STATE = "state.json"
SCHEMA = 1
PENDING, RUNNING, DONE, SKIPPED, FAILED, STOPPED = "pending", "running", "done", "skipped", "failed", "stopped"
#: The statuses of a task that needs no run.
COMPLETE = (DONE, SKIPPED)
KINDS = ("confirm", "action", "form")
#: The exit statuses: finished, failed, a usage error (or no installer), stopped.
EXIT = {DONE: 0, FAILED: 1, STOPPED: 3}
EXIT_USAGE = 2
#: What a secret value becomes wherever text leaves the engine.
MASK = "***"
#: Seconds a stopped child that does not write gets to end before it is killed.
TERMINATE_GRACE = 10
#: Seconds a wait through ctx.clock sleeps at most before it looks for a stop request.
STOP_SLICE = 0.2
#: What a stopped run's finished event says (its where), in plain words.
STOPPED_BETWEEN = "Nothing was cut off: the next step had not begun. Start the installer again to continue."
STOPPED_NOTHING = "Nothing on the headset was changed. Start the installer again to continue where it stopped."
STOPPED_AGAIN = "Start the installer again to continue where it stopped."
STOPPED_WROTE = "What this step was writing to the headset was finished first."
STOPPED_UNWRITTEN = "This step had not written to the headset yet."


@dataclass(frozen=True)
class Field:
    """A field of a form ask."""

    name: str
    label: str
    secret: bool = False  #: masked when typed, never written anywhere
    optional: bool = False


@dataclass(frozen=True)
class Ask:
    """A question to the owner: a consent (confirm), something to do (action) or a form."""

    id: str
    kind: str  #: confirm, action or form
    title: str
    text: str
    fields: tuple[Field, ...] = ()
    accept: str = ""  #: the accepting button; empty: "Done" for an action, "Continue" otherwise
    decline: str = "Cancel"

    def event(self) -> dict:
        """Return the ask as its event."""
        if self.kind not in KINDS:
            msg = f"ask {self.id}: kind {self.kind!r} is not one of {', '.join(KINDS)}"
            raise ValueError(msg)
        return {
            "event": "ask",
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "text": self.text,
            "fields": [
                {"name": f.name, "label": f.label, "secret": f.secret, "optional": f.optional} for f in self.fields
            ],
            "accept": self.accept or ("Done" if self.kind == "action" else "Continue"),
            "decline": self.decline,
        }


class Failed(RuntimeError):  # noqa: N818  # a task's outcome, as the protocol names it
    """A task failed: the message says what, in plain words; where says the headset's state and what to do."""

    def __init__(self, message: str, where: str = "") -> None:
        """Fail with @p message; @p where replaces the task's own where."""
        super().__init__(message)
        self.where = where


class Stopped(Exception):  # noqa: N818  # the run's outcome, as the protocol names it, not an error
    """The run stops: a declined ask, Ctrl-C or a closed window."""


class StopClock:
    """The clock tasks get (ctx.clock): a sleep looks for a stop request every STOP_SLICE s and ends in Stopped.

    So every wait a task makes through it (until() and the like) ends promptly when the run is
    asked to stop, whatever it waits for.
    """

    def __init__(self, clock: Timing, stopping: Callable[[], bool], piece: float = STOP_SLICE) -> None:
        """Wait with @p clock; @p stopping tells whether the run has been asked to stop."""
        self.clock = clock
        self.stopping = stopping
        self.piece = piece

    def monotonic(self) -> float:
        """Return the clock's time."""
        return self.clock.monotonic()

    def sleep(self, seconds: float, /) -> None:
        """Wait @p seconds in slices; raise Stopped as soon as a stop is requested."""
        left = float(seconds)
        while True:
            if self.stopping():
                raise Stopped
            if left <= 1e-9:  # noqa: PLR2004  # what the slices' rounding leaves
                return
            piece = min(self.piece, left)
            self.clock.sleep(piece)
            left -= piece


@dataclass(frozen=True)
class Task:
    """One thing the install does, with the function that does it."""

    id: str  #: unique in the plan: the state's key and the log's name
    title: str
    run: Callable[[Context], None]
    estimate: str = ""  #: how long it takes, in words ("about 1 min")
    writes: bool = False  #: writes to the headset: its children are never killed midway
    done: Callable[[Context], bool] | None = None  #: True: its result is there, no run needed
    where: str = ""  #: after a failure: the headset's state and what to do next


@dataclass(frozen=True)
class Stage:
    """A group of tasks the owner sees as one step, with one main button in the window."""

    id: str
    title: str
    summary: str
    tasks: tuple[Task, ...]


class UI(Protocol):
    """Where the events go and the answers come from: the window (JSON lines) or a terminal."""

    def start(self, stop: Callable[[], None]) -> None:
        """Begin a run; call @p stop when the other side wants the run to stop."""
        ...

    def emit(self, event: dict) -> None:
        """Show one event."""
        ...

    def answer(self, ask: Ask) -> dict | None:
        """Return the answer to @p ask ({"accepted": ..., "values": {...}}), None when the run must stop."""
        ...


class JsonUI:
    """The window's side: one JSON object per line on stdout, answers one per line on stdin.

    A closed stdin, a {"stop": true} line or a closed stdout asks the run to stop.
    """

    def __init__(self, out: IO[str], inp: IO[str] | None) -> None:
        """Write events to @p out, read answers from @p inp."""
        self.out = out
        self.inp = inp
        self.answers: queue.Queue[dict | None] = queue.Queue()
        self.stop: Callable[[], None] = lambda: None
        self.closed = threading.Event()

    def start(self, stop: Callable[[], None]) -> None:
        """Read answers on a thread from now on."""
        self.stop = stop
        if self.inp is not None:
            threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.inp or ():
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            if message.get("stop"):
                break
            if "answer" in message:
                self.answers.put(message)
        self._closed()

    def _closed(self) -> None:
        if not self.closed.is_set():
            self.closed.set()
            self.answers.put(None)
            self.stop()

    def emit(self, event: dict) -> None:
        """Write @p event as one line; a closed stdout stops the run."""
        try:
            self.out.write(json.dumps(event) + "\n")
            self.out.flush()
        except (OSError, ValueError):
            self._closed()

    def answer(self, ask: Ask) -> dict | None:
        """Wait for the answer to @p ask; None once stdin is closed."""
        while True:
            message = self.answers.get()
            if message is None:
                self.answers.put(None)  # every later ask sees it too
                return None
            if message.get("answer") == ask.id:
                return message


class TextUI:
    """A terminal: stage headings, a line per task status, the child programs' output indented."""

    MARKS: ClassVar[dict[str, str]] = {PENDING: "[ ]", RUNNING: "[>]", DONE: "[x]", SKIPPED: "[x]", FAILED: "[!]"}

    def __init__(
        self,
        out: IO[str] | None = None,
        read: Callable[[str], str] = input,
        secret: Callable[[str], str] = getpass.getpass,
    ) -> None:
        """Write to @p out (stdout), read answers with @p read and secrets with @p secret."""
        self.out = out or sys.stdout
        self.read = read
        self.secret = secret
        self.tasks: dict[str, dict] = {}
        self.stages: dict[str, dict] = {}
        self.stage = ""
        self.progress = ""

    def start(self, stop: Callable[[], None]) -> None:
        """Nothing to start: Ctrl-C stops the run."""

    def say(self, text: str) -> None:
        """Write one line."""
        self.out.write(text + "\n")
        self.out.flush()

    def emit(self, event: dict) -> None:  # noqa: C901  # one branch per kind of event
        """Write @p event as text."""
        kind = event.get("event")
        if kind == "plan":
            headset = event["headset"]
            self.say(f"overpressure installer: {headset['name']} ({headset['codename']})")
            for number, stage in enumerate(event["stages"], 1):
                self.stages[stage["id"]] = stage
                self.say(f"  {number}. {stage['title']}: {stage['summary']}")
                for task in stage["tasks"]:
                    self.tasks[task["id"]] = task
        elif kind == "task":
            if event["stage"] != self.stage:
                self.stage = event["stage"]
                self.say("\n== " + self.stages.get(self.stage, {}).get("title", self.stage) + " ==")
            task = self.tasks.get(event["task"], {})
            status = event["status"]
            extra = {
                PENDING: (f" ({task['estimate']})" if task.get("estimate") else "")
                + (" (writes to the headset)" if task.get("writes") else ""),
                RUNNING: " ...",
                SKIPPED: " (already there)",
                FAILED: " failed",
            }.get(status, "")
            self.say(f"{self.MARKS.get(status, '[?]')} {task.get('title', event['task'])}{extra}")
            self.progress = ""
        elif kind == "log":
            self.say("      " + event["line"])
        elif kind == "progress":
            if event.get("text") and event["text"] != self.progress:
                self.progress = event["text"]
                fraction = event.get("fraction")
                self.say(f"    ... {event['text']}" + (f" ({fraction:.0%})" if fraction is not None else ""))
        elif kind == "note":
            self.say(("  ! " if event.get("level") == "warning" else "  i ") + event["text"])
        elif kind == "finished":
            self.finished(event)

    def finished(self, event: dict) -> None:
        """Write how the run ended."""
        if event["status"] == DONE:
            self.say("\nFinished.")
        elif event["status"] == STOPPED:
            self.say("\nStopped." + (f" {event['where']}" if event.get("where") else ""))
        else:
            self.say(f"\nFailed: {event['error']}")
            if event.get("where"):
                self.say(event["where"])

    def answer(self, ask: Ask) -> dict | None:
        """Ask in the terminal; None at the end of input."""
        event = ask.event()
        self.say(f"\n{ask.title}\n{ask.text}")
        try:
            if ask.kind == "confirm":
                self.out.write(f"Type yes to {event['accept'].lower()}, anything else to {event['decline'].lower()}: ")
                self.out.flush()
                return {"accepted": self.read("").strip().lower() in ("y", "yes"), "values": {}}
            if ask.kind == "action":
                self.out.write(f"Press Enter when done ({event['accept']}), or type q to {event['decline'].lower()}: ")
                self.out.flush()
                return {"accepted": self.read("").strip().lower() not in ("q", "quit"), "values": {}}
            values = {}
            for item in ask.fields:
                self.out.write(f"{item.label}{' (optional)' if item.optional else ''}: ")
                self.out.flush()
                values[item.name] = (self.secret if item.secret else self.read)("")
        except EOFError:
            return None
        return {"accepted": True, "values": values}


class _Lines(io.TextIOBase):
    """A text stream that hands each complete line to a function: in-process output becomes log lines."""

    def __init__(self, line: Callable[[str], None]) -> None:
        self.line = line
        self.buffer_text = ""

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self.buffer_text += text
        *lines, self.buffer_text = self.buffer_text.split("\n")
        for item in lines:
            self.line(item.rstrip("\r"))
        return len(text)

    def flush(self) -> None:
        """Lines go out when complete; nothing is buffered elsewhere."""

    def close_lines(self) -> None:
        """Hand over what is left without a newline."""
        if self.buffer_text:
            self.line(self.buffer_text)
            self.buffer_text = ""


class Lock:
    """One engine at a time on this computer: an OS lock on out/install/lock, released when the process ends."""

    def __init__(self, path: Path) -> None:
        """Lock @p path."""
        self.path = path
        self.stream: IO[bytes] | None = None

    def acquire(self) -> bool:
        """Take the lock; False when another engine holds it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        try:
            if sys.platform == "win32":
                import msvcrt  # noqa: PLC0415  # Windows only

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl  # noqa: PLC0415  # POSIX only

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            return False
        self.stream = stream
        return True

    def release(self) -> None:
        """Give the lock up."""
        if self.stream is None:
            return
        with contextlib.suppress(OSError):
            if sys.platform == "win32":
                import msvcrt  # noqa: PLC0415  # Windows only

                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        self.stream.close()
        self.stream = None


#: Runs a child program: (argv, stdin bytes or None, own process group, line handler) -> exit status.
Spawn = Callable[[list[str], "bytes | None", bool, Callable[[str], None]], int]
#: Runs a short program: (argv, timeout) -> (exit status, output).
Capture = Callable[[list[str], float], "tuple[int, str]"]


def workflow_folder(root: Path, codename: str, workflow: str) -> Path:
    """Return where WORKFLOW keeps its state and logs: the install directly in the headset's folder."""
    return root / codename if workflow == INSTALL else root / codename / workflow


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _read_state(path: Path) -> dict | None:
    """Return the state in @p path, or None when it is missing, unreadable or of another schema."""
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict) or state.get("schema") != SCHEMA or not isinstance(state.get("tasks"), dict):
        return None
    return state


def _capture(argv: list[str], timeout: float) -> tuple[int, str]:
    try:
        process = subprocess.run(  # noqa: S603  # a task's own short command line, no shell
            argv,
            cwd=REPO,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, f"{Path(argv[0]).name}: no answer within {timeout:.0f} s"
    except OSError as exc:
        return 127, f"{Path(argv[0]).name}: {exc}"
    return process.returncode, process.stdout.decode("utf-8", "replace").strip()


class Context:
    """What a task gets: events, asks, child programs, the settings and the state."""

    def __init__(self, engine: Engine, stage: Stage, task: Task) -> None:
        """Run @p task of @p stage in @p engine."""
        self.engine = engine
        self.stage = stage
        self.task = task
        #: Values tasks hand on to later tasks of this run (answers, secrets): never written anywhere.
        self.memory = engine.memory
        #: The clock for waits: a stop request ends a sleep on it (Stopped).
        self.clock: Timing = engine.task_clock
        #: A child that writes to the headset has run in this task (what a stop's where tells).
        self.wrote = False

    @property
    def stopping(self) -> bool:
        """Tell whether the run has been asked to stop."""
        return self.engine.stopping

    def log(self, line: str) -> None:
        """Add a line to the task's log (the details view and out/install/<codename>/<task>.log)."""
        line = self.engine.redact(line)
        self.engine.write_log(self.task, line)
        self.engine.emit({"event": "log", "task": self.task.id, "line": line})

    def progress(self, fraction: float | None, text: str = "") -> None:
        """Show how far the task is: @p fraction from 0 to 1 (None: unknown) and a short text."""
        fraction = None if fraction is None else max(0.0, min(1.0, float(fraction)))
        self.engine.emit(
            {"event": "progress", "task": self.task.id, "fraction": fraction, "text": self.engine.redact(text)}
        )

    def output_line(self, text: str, progress: Callable[[str], tuple[float | None, str] | None] | None = None) -> None:
        """Log a line a program printed; a progress line, or one @p progress reads, shows how far.

        Progress lines (overpressure.progress) go to the log file only: they come often.
        """
        found = progress_lines.parse(text)
        if found is not None:
            self.engine.write_log(self.task, self.engine.redact(text))
        else:
            self.log(text)
            found = progress(text) if progress is not None else None
        if found is not None:
            self.progress(*found)

    def note(self, text: str, level: str = "info") -> None:
        """Tell the owner something in plain words: info, or a warning."""
        text = self.engine.redact(text)
        self.engine.write_log(self.task, f"note: {text}")
        self.engine.emit({"event": "note", "task": self.task.id, "level": level, "text": text})

    def ask(self, ask: Ask) -> dict[str, str]:
        """Ask the owner and wait; return a form's values. A declined ask stops the run."""
        while True:
            if self.stopping:
                raise Stopped
            event = ask.event()
            event["title"], event["text"] = self.engine.redact(event["title"]), self.engine.redact(event["text"])
            self.engine.emit(event)
            answer = self.engine.ui.answer(ask)
            if answer is None or answer.get("accepted") is not True:
                self.engine.write_log(self.task, f"ask {ask.id}: declined")
                msg = f"{ask.title}: declined"
                raise Stopped(msg)
            raw = answer.get("values") if isinstance(answer.get("values"), dict) else {}
            values = {f.name: str((raw or {}).get(f.name) or "") for f in ask.fields}
            for item in ask.fields:
                if item.secret and values[item.name]:
                    self.engine.secrets.add(values[item.name])
            missing = [f.label for f in ask.fields if not f.optional and not values[f.name].strip()]
            self.engine.write_log(self.task, f"ask {ask.id}: accepted")
            if not missing:
                return values
            self.note("Please fill in: " + ", ".join(missing), "warning")

    def choose(self, ask: Ask) -> bool:
        """Ask a yes-or-no question whose no goes on (@p ask's decline); return True for its accept.

        No answer at all (the window closed, no one to ask) stops the run, as with ask().
        """
        if self.stopping:
            raise Stopped
        event = ask.event()
        event["title"], event["text"] = self.engine.redact(event["title"]), self.engine.redact(event["text"])
        self.engine.emit(event)
        answer = self.engine.ui.answer(ask)
        if answer is None:
            self.engine.write_log(self.task, f"ask {ask.id}: no answer")
            msg = f"{ask.title}: no answer"
            raise Stopped(msg)
        chosen = answer.get("accepted") is True
        self.engine.write_log(self.task, f"ask {ask.id}: {'accepted' if chosen else 'declined'}")
        return chosen

    def run(
        self,
        argv: Sequence[str | os.PathLike[str]],
        *,
        stdin: bytes | None = None,
        progress: Callable[[str], tuple[float | None, str] | None] | None = None,
    ) -> str:
        """Run a child program from the repository root, its output streamed to the log; return its output.

        @p stdin goes to its standard input (the only way a secret reaches a program); @p progress
        turns an output line into a progress event (fraction, text), or None. A nonzero exit raises
        Failed with its last error line.
        """
        words = [str(a) for a in argv]
        self.engine.check_argv(words)
        if self.stopping:
            raise Stopped
        self.log("$ " + runner.display(words))
        lines: list[str] = []

        def line(text: str) -> None:
            lines.append(text)
            self.output_line(text, progress)

        code = self.engine.spawn(words, stdin, self.task.writes, line)
        if self.task.writes:
            self.wrote = True  # it ran to its end, stop or not: its result counts
        elif self.stopping:
            raise Stopped  # it was ended midway: its result does not count
        if code:
            raise Failed(_error_line(lines) or f"{Path(words[0]).name} ended with exit status {code}")
        return "\n".join(lines)

    def output(self, argv: Sequence[str | os.PathLike[str]], timeout: float = 60, *, check: bool = True) -> str:
        """Run a short program (a poll, a query) without streaming; its output goes to the log file only."""
        words = [str(a) for a in argv]
        self.engine.check_argv(words)
        if self.stopping:
            raise Stopped
        code, out = self.engine.capture(words, timeout)
        self.engine.write_log(self.task, self.engine.redact(f"$ {runner.display(words)} -> {code}\n{out}"))
        if check and code:
            raise Failed(_error_line(out.splitlines()) or f"{Path(words[0]).name} ended with exit status {code}")
        return out

    def settings(self, cls: type[S]) -> S:
        """Return the settings of @p cls from .env as they are now (earlier tasks may have written it)."""
        return localconfig.load(cls, self.engine.env)

    def set_setting(self, key: str, value: str) -> None:
        """Set KEY=VALUE in .env: what the installer finds out (a serial), never a secret."""
        if any(secret in value for secret in self.engine.secrets):
            msg = f"refusing to write a secret into .env ({key})"
            raise Failed(msg)
        localconfig.update({key: value}, self.engine.env)
        self.engine.write_log(self.task, f"saved {key} in .env")

    def completed(self, task: str) -> bool:
        """Tell whether the task @p task is done or skipped, in this run or an earlier one."""
        return self.engine.status(task) in COMPLETE


def _error_line(lines: Sequence[str]) -> str:
    """Return the line that says why a program failed: its last error: line, else its last line."""
    texts = [line.strip() for line in lines if line.strip()]
    for text in reversed(texts):
        for prefix in ("error:", "STOPPED:", "refused:"):
            if text.startswith(prefix):
                return text[len(prefix) :].strip()
    return texts[-1] if texts else ""


class Engine:
    """Runs a plan: events to a UI, the state file, the lock, the tasks' logs."""

    def __init__(  # noqa: PLR0913  # the plan and its UI; where things live and the host's hooks keyword-only for tests
        self,
        info: Info,
        stages: Sequence[Stage],
        ui: UI,
        *,
        root: Path = OUT,
        env: Path | None = None,
        spawn: Spawn | None = None,
        capture: Capture | None = None,
        clock: Timing = time,
        workflow: str = INSTALL,
        others: Sequence[str] = (),
        battery: Callable[[], str] | None = None,
    ) -> None:
        """Run @p stages of the headset @p info through @p ui; tests pass the root folder, .env and fakes.

        The lock is @p root/lock, the state and the logs are in @p root/<codename> for the install and
        in @p root/<codename>/<workflow> for another @p workflow; finished, it forgets the state of the
        @p others. Before each task that writes, @p battery (this computer's, overpressure.power) may refuse it.
        """
        check_plan(stages)
        self.info = info
        self.stages = tuple(stages)
        self.ui = ui
        self.root = root
        self.workflow = workflow
        self.others = tuple(o for o in others if o != workflow)
        self.battery = battery or power.battery_problem
        self.folder = workflow_folder(root, info.codename, workflow)
        self.env = env if env is not None else localconfig.ROOT / ".env"
        self.spawn: Spawn = spawn or self._spawn
        self.capture: Capture = capture or _capture
        self.clock = clock
        self.task_clock = StopClock(clock, lambda: self.stopping)
        self.memory: dict = {}
        self.secrets: set[str] = set()
        self.stopping = False
        self.state: dict = {"schema": SCHEMA, "tasks": {}}
        self._child: tuple[subprocess.Popen, bool, list[str]] | None = None  #: the child, whether it writes, its argv
        self._logs: dict[str, IO[str]] = {}

    # -- the plan and the state ---------------------------------------------------------------
    def plan_event(self) -> dict:
        """Return the plan as its event."""
        return {
            "event": "plan",
            "headset": {"codename": self.info.codename, "name": self.info.name},
            "workflow": self.workflow,
            "stages": [
                {
                    "id": stage.id,
                    "title": stage.title,
                    "summary": stage.summary,
                    "tasks": [
                        {
                            "id": t.id,
                            "title": t.title,
                            "estimate": t.estimate,
                            "seconds": progress_lines.estimate_seconds(t.estimate),
                            "writes": t.writes,
                        }
                        for t in stage.tasks
                    ],
                }
                for stage in self.stages
            ],
        }

    @property
    def state_path(self) -> Path:
        """Return the state file."""
        return self.folder / STATE

    def load_state(self) -> None:
        """Read the state file; a missing or unreadable one is a fresh start."""
        self.state = _read_state(self.state_path) or {"schema": SCHEMA, "tasks": {}}

    def save_state(self) -> None:
        """Write the state file whole."""
        self.folder.mkdir(parents=True, exist_ok=True)
        part = self.state_path.with_name(STATE + ".part")
        part.write_text(json.dumps(self.state, indent=2) + "\n", encoding="utf-8")
        part.replace(self.state_path)

    def status(self, task: str) -> str:
        """Return a task's recorded status (pending when it has none)."""
        record = self.state["tasks"].get(task)
        return record.get("status", PENDING) if isinstance(record, dict) else PENDING

    def set_status(self, stage: Stage, task: Task, status: str, *, record: bool = True) -> None:
        """Show a task's status and record it in the state file."""
        if record:
            if status == PENDING:
                self.state["tasks"].pop(task.id, None)
            else:
                self.state["tasks"][task.id] = {
                    "status": status,
                    ("started" if status == RUNNING else "finished"): _now(),
                }
            self.save_state()
        self.emit({"event": "task", "stage": stage.id, "task": task.id, "status": status})

    def forget_others(self) -> None:
        """Forget what the other workflows had done: the headset is where this one left it, not where they did."""
        for other in self.others:
            workflow_folder(self.root, self.info.codename, other).joinpath(STATE).unlink(missing_ok=True)

    def forget(self, stage_id: str) -> None:
        """Forget what was done from stage @p stage_id on (--restart)."""
        ids = [s.id for s in self.stages]
        if stage_id not in ids:
            msg = f"no stage {stage_id!r} (stages: {', '.join(ids)})"
            raise ValueError(msg)
        for stage in self.stages[ids.index(stage_id) :]:
            for task in stage.tasks:
                self.state["tasks"].pop(task.id, None)
        self.save_state()

    # -- what leaves the engine -----------------------------------------------------------------
    def redact(self, text: str) -> str:
        """Return @p text with every secret value masked."""
        for secret in sorted(self.secrets, key=len, reverse=True):
            text = text.replace(secret, MASK)
        return text

    def check_argv(self, argv: Sequence[str]) -> None:
        """Refuse a command line that carries a secret value: secrets go on stdin only."""
        if any(secret in word for word in argv for secret in self.secrets):
            msg = "refusing to put a secret value into a command line"
            raise Failed(msg)

    def emit(self, event: dict) -> None:
        """Hand @p event to the UI."""
        self.ui.emit(event)

    def write_log(self, task: Task, text: str) -> None:
        """Append @p text to out/install/<codename>/<task>.log."""
        stream = self._logs.get(task.id)
        if stream is None:
            self.folder.mkdir(parents=True, exist_ok=True)
            stream = (self.folder / f"{task.id}.log").open("a", encoding="utf-8")
            stream.write(f"== {_now()} {task.title}\n")
            self._logs[task.id] = stream
        stream.write(text + "\n")
        stream.flush()

    def close_logs(self) -> None:
        """Close the tasks' log files."""
        for stream in self._logs.values():
            stream.close()
        self._logs.clear()

    def finish(self, status: str, task: Task | None, error: str = "", where: str = "") -> str:
        """Emit the finished event; return @p status."""
        self.emit(
            {
                "event": "finished",
                "status": status,
                "task": task.id if task else None,
                "error": self.redact(error),
                "where": self.redact(where),
            }
        )
        return status

    # -- stopping and child programs -------------------------------------------------------
    def request_stop(self) -> None:
        """Stop at the next wait; a running child that does not write to the headset is ended, one that writes not.

        A build's container is removed with its child (runner.stop): it would outlive a killed client.
        """
        self.stopping = True
        child = self._child
        if child is not None and not child[1]:
            runner.stop(child[0], child[2])

    def _spawn(self, argv: list[str], stdin: bytes | None, writes: bool, line: Callable[[str], None]) -> int:  # noqa: FBT001  # the Spawn signature, positional for the fakes
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        if sys.platform == "win32":
            env.setdefault("MSYS_NO_PATHCONV", "1")
        # A child that writes to the headset gets its own process group: Ctrl-C reaches the engine only.
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if writes and sys.platform == "win32" else 0
        proc = subprocess.Popen(  # noqa: S603  # a task's own command line, no shell
            argv,
            cwd=REPO,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=flags,
            start_new_session=writes and sys.platform != "win32",
        )
        self._child = (proc, writes, argv)
        if self.stopping and not writes:  # the stop came while it started
            runner.stop(proc, argv)
        if stdin is not None:
            threading.Thread(target=_feed, args=(proc, stdin), daemon=True).start()
        with proc:  # closes its pipes and waits for it
            try:
                self._pump(proc, line, writes=writes)
            finally:
                self._child = None
        return proc.returncode

    def _pump(self, proc: subprocess.Popen, line: Callable[[str], None], *, writes: bool) -> None:
        stream = proc.stdout
        if stream is None:
            return
        # Its output is read to the end whatever happens: a closed pipe could end a writing child midway.
        while True:
            try:
                raw = stream.readline()
                if not raw:
                    break
                for part in raw.decode("utf-8", "replace").rstrip("\r\n").split("\r"):
                    if part.strip():
                        line(part.rstrip())
            except KeyboardInterrupt:  # Ctrl-C: a writing child goes on, another one ends
                if writes and not self.stopping:
                    line("Stopping once this step has finished: it writes to the headset.")
                self.request_stop()
        if self.stopping and not writes and proc.poll() is None:
            try:
                proc.wait(TERMINATE_GRACE)
            except subprocess.TimeoutExpired:
                proc.kill()

    # -- running ---------------------------------------------------------------------------------
    @contextlib.contextmanager
    def _output_as_log(self, ctx: Context) -> Generator[None, None, None]:
        """Turn what in-process code prints into the task's log lines, so stdout carries only events."""
        out, err = _Lines(ctx.output_line), _Lines(ctx.log)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                yield
            finally:
                out.close_lines()
                err.close_lines()

    def _is_done(self, ctx: Context, task: Task) -> bool:
        if task.done is None:
            return False
        try:
            with self._output_as_log(ctx):
                return bool(task.done(ctx))
        except Exception as exc:  # noqa: BLE001  # a done check that cannot tell means: run the task
            self.write_log(task, f"done check: {exc}")
            return False

    def _needs_no_run(self, ctx: Context, stage: Stage, task: Task) -> bool:
        """Show a task that needs no run as done (earlier) or skipped (its result is there); tell which."""
        recorded = self.status(task.id)
        if recorded in COMPLETE and (task.done is None or self._is_done(ctx, task)):
            self.set_status(stage, task, recorded, record=False)
            return True
        if recorded not in COMPLETE and self._is_done(ctx, task):
            self.set_status(stage, task, SKIPPED)
            return True
        return False

    def _refused(self, stage: Stage, task: Task) -> str | None:
        """Return the run's end status when TASK must not start: a stop asked for, or too little power to write."""
        if self.stopping:
            self.set_status(stage, task, PENDING)
            return self.finish(STOPPED, task, "", STOPPED_BETWEEN)
        problem = self.battery() if task.writes else ""
        if problem:  # the computer went on its battery during the run (an install takes a while)
            self.set_status(stage, task, FAILED)
            return self.finish(
                FAILED, task, problem, "Nothing was written to the headset by this step. " + STOPPED_AGAIN
            )
        return None

    def step(self, stage: Stage, task: Task) -> str | None:
        """Run one task unless it needs no run; return the run's end status when it ends the run."""
        ctx = Context(self, stage, task)
        if self._needs_no_run(ctx, stage, task):
            return None
        refused = self._refused(stage, task)
        if refused is not None:
            return refused
        self.set_status(stage, task, RUNNING)
        try:
            with self._output_as_log(ctx):
                task.run(ctx)
        except (Stopped, KeyboardInterrupt):
            self.write_log(task, "stopped")
            self.set_status(stage, task, PENDING)
            return self.finish(STOPPED, task, "", stopped_where(task, wrote=ctx.wrote))
        except Failed as exc:
            self.write_log(task, f"failed: {self.redact(str(exc))}")
            self.set_status(stage, task, FAILED)
            return self.finish(FAILED, task, str(exc), exc.where or task.where)
        except Exception as exc:  # noqa: BLE001  # whatever ends a task is its failure, said in words
            self.write_log(task, self.redact(traceback.format_exc()))
            self.set_status(stage, task, FAILED)
            return self.finish(FAILED, task, str(exc) or type(exc).__name__, task.where)
        self.set_status(stage, task, DONE)
        return None

    def run(self, restart: str | None = None) -> str:
        """Run the plan from where it stopped (from stage @p restart on, with it); return how it ended."""
        self.ui.start(self.request_stop)
        self.emit(self.plan_event())
        lock = Lock(self.root / "lock")
        if not lock.acquire():
            return self.finish(
                FAILED, None, "Another installer is running on this computer.", "Let it finish, or close it first."
            )
        try:
            self.load_state()
            if restart:
                self.forget(restart)
            for stage in self.stages:
                for task in stage.tasks:
                    try:
                        end = self.step(stage, task)
                    except KeyboardInterrupt:
                        return self.finish(STOPPED, task, "", STOPPED_BETWEEN)
                    if end is not None:
                        return end
            self.forget_others()
            return self.finish(DONE, None)
        finally:
            self.close_logs()
            lock.release()

    def show_plan(self) -> None:
        """Emit the plan and every task's recorded status (--plan): nothing runs."""
        self.ui.start(lambda: None)
        self.emit(self.plan_event())
        self.load_state()
        for stage in self.stages:
            for task in stage.tasks:
                self.emit({"event": "task", "stage": stage.id, "task": task.id, "status": self.status(task.id)})


def stopped_where(task: Task, *, wrote: bool) -> str:
    """Return what a run stopped in @p task says: the headset's state and what to do.

    A task that does not write changed nothing; one that writes says whether its write was under
    way (it was finished first, @p wrote) and the headset's state (its where).
    """
    if not task.writes:
        return task.where or STOPPED_NOTHING
    return f"{STOPPED_WROTE if wrote else STOPPED_UNWRITTEN} {task.where or STOPPED_AGAIN}"


def _feed(proc: subprocess.Popen, data: bytes) -> None:
    """Write @p data to the child's stdin and close it."""
    if proc.stdin is None:
        return
    with contextlib.suppress(OSError):
        proc.stdin.write(data)
    with contextlib.suppress(OSError):
        proc.stdin.close()


def check_plan(stages: Sequence[Stage]) -> None:
    """Refuse a plan whose stage or task ids repeat: the state and the logs are keyed by them."""
    seen: set[str] = set()
    for stage in stages:
        if stage.id in seen:
            msg = f"stage id {stage.id!r} repeats"
            raise ValueError(msg)
        seen.add(stage.id)
    tasks: set[str] = set()
    for stage in stages:
        for task in stage.tasks:
            if task.id in tasks:
                msg = f"task id {task.id!r} repeats"
                raise ValueError(msg)
            tasks.add(task.id)


def main(
    headset: Headset,
    argv: list[str],
    *,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    root: Path = OUT,
) -> int:
    """Run python -m overpressure install for @p headset."""
    ap = argparse.ArgumentParser(
        prog="python -m overpressure install",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--workflow", default=INSTALL, help="what to do: install (the default), or another of the headset's"
    )
    ap.add_argument("--json", action="store_true", help="events as JSON lines, answers on stdin (the window)")
    ap.add_argument("--plan", action="store_true", help="show the plan and what is done; run nothing")
    ap.add_argument("--restart", metavar="STAGE", help="forget what was done from STAGE on, then run")
    args = ap.parse_args(argv)
    stdout = stdout or sys.stdout

    def refuse(error: str) -> int:
        if args.json:
            event = {"event": "finished", "status": FAILED, "task": None, "error": error, "where": ""}
            print(json.dumps(event), file=stdout, flush=True)
        else:
            print(f"error: {error}", file=stdout, flush=True)
        return EXIT_USAGE

    workflows = [w.key for w in headset.workflows()]
    if not workflows:
        return refuse(f"The {headset.info.name} has no installer: its steps are in the window's Advanced mode.")
    if args.workflow not in workflows:
        known = ", ".join(workflows)
        return refuse(f"The {headset.info.name}'s installer has no {args.workflow!r} (it has: {known}).")
    stages = tuple(headset.plan(args.workflow))
    if args.restart and args.restart not in [s.id for s in stages]:
        return refuse(f"No stage {args.restart!r} (stages: {', '.join(s.id for s in stages)}).")
    problem = "" if args.plan else headset.workflow_problem(args.workflow)
    if problem:
        return refuse(problem)
    ui: UI = JsonUI(stdout, stdin if stdin is not None else sys.stdin) if args.json else TextUI(stdout)
    engine = Engine(headset.info, stages, ui, root=root, workflow=args.workflow, others=workflows)
    if args.plan:
        engine.show_plan()
        return 0
    return EXIT[engine.run(args.restart)]


def upgrade(headset: Headset, argv: list[str]) -> int:
    """Run python -m overpressure upgrade: the installer's upgrade."""
    return main(headset, ["--workflow", "upgrade", *argv])


def restore(headset: Headset, argv: list[str]) -> int:
    """Run python -m overpressure restore: the installer's way back to the headset's own system."""
    return main(headset, ["--workflow", "restore", *argv])
