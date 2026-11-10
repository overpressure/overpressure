# SPDX-License-Identifier: Unlicense
"""The installer view's model and engines: the install engine's events as five stages, one main button at a time.

The window draws and asks; it does nothing itself. It runs ``python -m overpressure install
--json`` as a child, reads its events (docs/tool.md, "The installer") line by line on a thread
and answers its asks on the child's stdin, so whatever the window does a terminal does too, and
the engine alone decides when it is safe to stop. Nothing here needs Tk or CustomTkinter: tests
feed the model lines, and app.py draws it.
"""

from __future__ import annotations

import collections
import contextlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Union

from overpressure import REPO, progress
from overpressure.headset import INSTALL
from overpressure.install import STOPPED_BETWEEN, STOPPED_NOTHING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

#: Task states as the engine sends them; skipped means its output was already there.
PENDING, RUNNING, DONE, SKIPPED, FAILED = "pending", "running", "done", "skipped", "failed"
FINISHED = (DONE, SKIPPED)
LOG_LINES = 2000  # the details view keeps the last lines only
#: How the details view tells a task's status change; a task done before this run is "done earlier".
STATUS_WORDS = {RUNNING: "started", DONE: "done", SKIPPED: "already there, skipped", FAILED: "failed"}
STOP_GRACE = 5.0  # seconds a stopped engine gets to exit by itself before it is ended
STOPPED = "stopped"
#: The window's question before it stops the engine, by what runs: the question, then each answer.
STOP_TITLE = "Stop the installer?"
STOP_NOW = "It stops now; nothing on the headset changes. Starting again picks up here."
STOP_AFTER_WRITE = "This step writes to the headset and finishes first; the installer stops right after."
#: An engine's queue item: ("line", text) or ("exit", return code), also a step's program's.
Item = tuple[str, Union[str, int]]


@dataclass
class Task:
    """One task of a stage, as the plan names it, and its state as the events say."""

    id: str
    title: str
    estimate: str = ""
    writes: bool = False
    status: str = PENDING
    fraction: float | None = None  # the last progress event's fraction; None while unknown
    progress: str = ""  # the last progress event's text
    seconds: float | None = None  # what the estimate stands for, its upper end; None without one
    started: float | None = None  # the model's clock when it began running


#: Where a bar that follows the estimate stops and moves to and fro: how long is left stays vague.
ESTIMATE_CAP = 0.9


def bar_state(task: Task, now: float) -> tuple[float | None, str]:
    """Return how a running task's bar stands: its share (None: to and fro) and the status line under it.

    A task that reports its progress shows it, with the time it has run and, once that says
    something, what is left; another one follows its estimate up to ESTIMATE_CAP; one without
    either counts its time.
    """
    elapsed = 0.0 if task.started is None else max(now - task.started, 0.0)
    ran = progress.clock(elapsed)
    if task.fraction is not None:
        status = f"{task.fraction:.0%} · {ran}"
        if 0.05 <= task.fraction < 1 and elapsed >= 20:  # noqa: PLR2004  # too early to say before
            status += f" · about {progress.duration(elapsed * (1 - task.fraction) / task.fraction)} left"
        return task.fraction, status
    if task.seconds:
        share = elapsed / task.seconds
        return (share if share < ESTIMATE_CAP else None), f"{ran} of {task.estimate}"
    return None, ran


@dataclass
class Stage:
    """One of the plan's stages and its tasks."""

    id: str
    title: str
    summary: str = ""
    tasks: list[Task] = field(default_factory=list)
    notes: list[tuple[str, str]] = field(default_factory=list)  # (level, text) of its tasks' notes

    @property
    def status(self) -> str:
        """Return the stage's state for the stepper: failed, done, running (begun) or pending."""
        states = [t.status for t in self.tasks]
        if FAILED in states:
            return FAILED
        if states and all(s in FINISHED for s in states):
            return DONE
        if any(s != PENDING for s in states):
            return RUNNING
        return PENDING

    @property
    def writes(self) -> bool:
        """Tell whether a task of the stage writes to the headset."""
        return any(t.writes for t in self.tasks)


def joined(words: list[str]) -> str:
    """Return WORDS as a list in prose: "a", "a and b", "a, b and c"."""
    return " and ".join(words) if len(words) < 3 else ", ".join(words[:-1]) + " and " + words[-1]  # noqa: PLR2004  # two need no comma


def _seconds(task: dict[str, Any]) -> float | None:
    """Return the seconds a plan's task is estimated at: the plan's, else read from its estimate's words."""
    seconds = task.get("seconds")
    if isinstance(seconds, (int, float)) and seconds > 0:
        return float(seconds)
    return progress.estimate_seconds(str(task.get("estimate") or ""))


class Model:
    """The installer's state from the engine's events: no widgets, so tests feed it lines."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """Start without a plan; @p clock times the running tasks (tests pass their own)."""
        self.clock = clock
        self.headset: dict[str, str] = {}
        self.workflow = INSTALL  # the plan's workflow: install, or another of the headset's
        self.stages: list[Stage] = []
        self.current = ""  # the stage the window shows
        self.ask: dict[str, Any] | None = None  # the ask the engine waits for
        self.finished: dict[str, Any] | None = None  # the run's end, until the next run starts
        self.log: collections.deque[str] = collections.deque(maxlen=LOG_LINES)
        self.running = False  # an engine run is under way
        self.started = False  # a run has been started in this window
        self.start_page = False  # the plan probe found a run recorded before: the start page shows its choices
        self.interrupted = False  # the probe found a task recorded as running: that run ended without its end
        self.stopping = False  # the window has asked the engine to stop; its finished event ends this
        self.stop_after_write = False  # the stop waits for a step that writes to the headset

    # -- lookups -----------------------------------------------------------------------
    def task(self, task_id: str) -> Task | None:
        """Return the plan's task TASK_ID, or None."""
        return next((t for s in self.stages for t in s.tasks if t.id == task_id), None)

    def stage_of(self, task_id: str) -> Stage | None:
        """Return the stage that holds the task TASK_ID, or None."""
        return next((s for s in self.stages if any(t.id == task_id for t in s.tasks)), None)

    def stage(self) -> Stage | None:
        """Return the stage the window shows, or None before the plan."""
        return next((s for s in self.stages if s.id == self.current), self.stages[0] if self.stages else None)

    def view(self) -> str:
        """Return what the right side shows: ask, start (the start page), done, failed, or stage (also when stopped)."""
        if self.ask is not None:
            return "ask"
        if self.start_page and not self.running:
            return "start"
        if self.finished is not None and self.finished.get("status") in (DONE, FAILED):
            return str(self.finished["status"])
        return "stage"

    def button(self) -> str:
        """Return the primary button's label for the stage view, or "" while the engine works."""
        if self.running:
            return ""
        if self.view() == "start":
            return "Continue where you left off"
        if self.finished is not None and self.finished.get("status") == STOPPED:
            return "Continue"
        if any(t.status == FAILED for s in self.stages for t in s.tasks):
            return "Retry"
        return "Start"

    def stop_label(self) -> str:
        """Return the Stop button's label: Stop while a run goes, Stopping… once asked, "" otherwise."""
        if not self.running or self.finished is not None:
            return ""
        return "Stopping\N{HORIZONTAL ELLIPSIS}" if self.stopping else "Stop"

    # -- the start page --------------------------------------------------------------------
    def recorded(self) -> bool:
        """Tell whether a run was recorded before: a task done, skipped or failed, or one that never ended."""
        return self.interrupted or any(t.status in (*FINISHED, FAILED) for s in self.stages for t in s.tasks)

    def probed(self) -> None:
        """Take the end of the plan probe (install --plan): the stage it stopped in, and the start page if due.

        A task the state still has as running belongs to a run that ended without its end (the
        engine was killed): nothing runs it now, so it is pending again.
        """
        for stage in self.stages:
            for task in stage.tasks:
                if task.status == RUNNING:
                    task.status, self.interrupted = PENDING, True
        failed = next((s.id for s in self.stages if s.status == FAILED), "")
        self.current = failed or next(
            (s.id for s in self.stages if s.status != DONE), self.stages[-1].id if self.stages else ""
        )
        self.start_page = not self.started and self.recorded()

    def progress_text(self) -> str:
        """Return what the start page says was done so far and where it stopped."""
        done = [s.title for s in self.stages if s.status == DONE]
        stage = self.stage()
        text = f"Done so far: {joined(done)}." if done else "Nothing has been finished yet."
        if stage is None or stage.status == DONE:
            return text + " Every stage is done; continuing checks that it all is still there."
        if stage.status == FAILED:
            return text + f" A step of {stage.title} failed. Continuing tries it again."
        return text + f" It stopped in {stage.title}."

    def restart_stages(self) -> list[Stage]:
        """Return the stages the start page offers to start over from: each with something recorded."""
        return [s for s in self.stages if s.status != PENDING] or self.stages[:1]

    def restart_question(self, stage_id: str) -> tuple[str, str, str]:
        """Return the confirmation before starting over from STAGE_ID: its title, what is redone, a write's warning.

        The warning ("" for none) names the forgotten stages that write to the headset: done again on
        a headset that is already prepared, they repeat its backup and its writes.
        """
        ids = [s.id for s in self.stages]
        later = self.stages[ids.index(stage_id) :]
        forgotten = [s for s in later if s.status != PENDING] or later[:1]
        names = joined([s.title for s in forgotten])
        if len(forgotten) == 1:
            text = f"The installer forgets what was done in {names} and does it again from its start."
        else:
            text = f"The installer forgets what was done in {names} and does them again, from {later[0].title} on."
        writing = [s.title for s in forgotten if s.writes]
        warning = ""
        if writing:
            verb, them = ("writes", "it") if len(writing) == 1 else ("write", "them")
            warning = (
                f"{joined(writing)} {verb} to the headset. On a headset that is already prepared, doing {them} "
                "again repeats its backup and every write to it."
            )
        return f"Start over from {later[0].title}?", text, warning

    # -- stopping --------------------------------------------------------------------------
    def stop_question(self) -> tuple[str, str]:
        """Return the question before a stop: a step that writes finishes first, anything else stops now."""
        running = next((t for s in self.stages for t in s.tasks if t.status == RUNNING), None)
        if running is not None and running.writes and self.ask is None:
            return STOP_TITLE, STOP_AFTER_WRITE
        return STOP_TITLE, STOP_NOW

    def request_stop(self) -> None:
        """Record that the window asks the engine to stop: running, then stopping until its finished event."""
        if self.running and self.finished is None:
            self.stop_after_write = self.stop_question()[1] == STOP_AFTER_WRITE
            self.stopping, self.ask = True, None

    def phase(self) -> str:
        """Return where a run is: idle, running, stopping or stopped."""
        if self.stopping:
            return "stopping"
        if self.running and self.finished is None:
            return "running"
        if self.finished is not None and self.finished.get("status") == STOPPED:
            return STOPPED
        return "idle"

    # -- events ------------------------------------------------------------------------
    def feed(self, line: str) -> dict[str, Any]:
        """Apply one line of the engine's output and return its event; other text is a log line."""
        line = line.rstrip("\r\n")
        try:
            event = json.loads(line)
        except ValueError:
            event = None
        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            event = {"event": "log", "line": line}
        self.apply(event)
        return event

    def apply(self, event: dict[str, Any]) -> None:
        """Apply one event of the protocol."""
        handler = {
            "plan": self._plan,
            "task": self._task,
            "progress": self._progress,
            "log": self._log,
            "note": self._note,
            "ask": self._ask,
            "finished": self._finished,
        }.get(event["event"])
        if handler is not None:
            handler(event)

    def _plan(self, event: dict[str, Any]) -> None:
        # A new run sends the plan again: what was finished stays so (the engine resends it).
        before = {t.id: t.status for s in self.stages for t in s.tasks}
        self.headset = {k: str(v) for k, v in (event.get("headset") or {}).items()}
        self.workflow = str(event.get("workflow") or INSTALL)
        self.stages = []
        for raw in event.get("stages", []):
            tasks = []
            for t in raw.get("tasks", []):
                status = str(t.get("status") or before.get(t["id"], PENDING))
                tasks.append(
                    Task(
                        id=str(t["id"]),
                        title=str(t.get("title", t["id"])),
                        estimate=str(t.get("estimate") or ""),
                        writes=bool(t.get("writes")),
                        seconds=_seconds(t),
                        status=status if status in FINISHED else PENDING,
                    )
                )
            self.stages.append(
                Stage(str(raw["id"]), str(raw.get("title", raw["id"])), str(raw.get("summary", "")), tasks)
            )
        self.current = next((s.id for s in self.stages if s.status != DONE), self.stages[-1].id if self.stages else "")

    def _task(self, event: dict[str, Any]) -> None:
        task = self.task(str(event.get("task")))
        if task is None:
            return
        before, task.status = task.status, str(event.get("status", PENDING))
        words = STATUS_WORDS.get(task.status)
        if words is not None:
            earlier = task.status == DONE and before != RUNNING
            self.log.append(f"{task.title}: {'done earlier' if earlier else words}")
        if task.status != RUNNING:
            task.fraction, task.progress, task.started = None, "", None
        elif before != RUNNING or task.started is None:
            task.started = self.clock()
        stage = self.stage_of(task.id)
        if stage is not None and task.status in (RUNNING, FAILED):
            self.current = stage.id

    def _progress(self, event: dict[str, Any]) -> None:
        task = self.task(str(event.get("task")))
        if task is not None:
            fraction = event.get("fraction")
            task.fraction = None if fraction is None else min(max(float(fraction), 0.0), 1.0)
            task.progress = str(event.get("text") or "")

    def _log(self, event: dict[str, Any]) -> None:
        task = event.get("task")
        self.log.append(f"{task}: {event.get('line', '')}" if task else str(event.get("line", "")))

    def _note(self, event: dict[str, Any]) -> None:
        stage = self.stage_of(str(event.get("task"))) or self.stage()
        if stage is not None:
            stage.notes.append((str(event.get("level", "info")), str(event.get("text", ""))))
        self.log.append(f"Note: {event.get('text', '')}")

    def _ask(self, event: dict[str, Any]) -> None:
        self.ask = event
        self.log.append(f"Waiting for you: {event.get('title', '')}")

    def _finished(self, event: dict[str, Any]) -> None:
        self.finished, self.ask, self.stopping = event, None, False
        status = event.get("status")
        if status == DONE:
            self.log.append("Finished.")
        elif status == FAILED:
            self.log.append(f"Failed: {event.get('error', '')}" + (f" {event['where']}" if event.get("where") else ""))
        else:
            self.log.append("Stopped." + (f" {event['where']}" if event.get("where") else ""))
        task = self.task(str(event.get("task")))
        if task is not None and event.get("status") == FAILED:
            task.status = FAILED

    # -- the window's side ---------------------------------------------------------------
    def begin(self) -> None:
        """Mark a new run: the last one's end and its notes go."""
        self.running, self.started, self.finished, self.ask = True, True, None, None
        self.start_page = self.stopping = self.stop_after_write = False
        for stage in self.stages:
            stage.notes.clear()
            for task in stage.tasks:
                if task.status not in FINISHED:
                    task.status = PENDING

    def exited(self, code: int) -> None:
        """Record that the engine has exited; without a finished event that is a failure."""
        self.running, self.ask, self.stopping = False, None, False
        if self.finished is None:
            self.finished = {
                "event": "finished",
                "status": FAILED,
                "error": f"The installer stopped unexpectedly (exit status {code}).",
                "where": "Nothing more was started. Show details has its last output; Retry starts again.",
            }

    def answer(self, *, accepted: bool, values: dict[str, str] | None = None) -> str:
        """Return the answer line to the pending ask and clear it; values only for a form."""
        if self.ask is None:
            message = "no ask is pending"
            raise RuntimeError(message)
        reply = {"answer": self.ask.get("id"), "accepted": accepted, "values": values or {}}
        self.ask = None
        return json.dumps(reply)


# -- engines: the child process, or the demo -------------------------------------------------
class ChildEngine:
    """The install engine as a child process: its output lines into a queue, answers on stdin."""

    def __init__(self, codename: str | None, workflow: str = INSTALL) -> None:
        """Run the engine's WORKFLOW for the headset CODENAME (else the engine's own choice)."""
        self.device = ["--device", codename] if codename else []
        self.workflow = ["--workflow", workflow] if workflow != INSTALL else []
        self.items: queue.Queue[Item] = queue.Queue()
        self.proc: subprocess.Popen[str] | None = None

    def argv(self, extra: list[str]) -> list[str]:
        """Return the engine's command line."""
        return [sys.executable, "-m", "overpressure", *self.device, "install", *self.workflow, "--json", *extra]

    def start(self, extra: list[str]) -> None:
        """Start a run with EXTRA arguments (a mode, a restart)."""
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        # Its own hidden console on Windows: no window of its own, and the terminal's Ctrl-C stays out.
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(  # noqa: S603  # this Python and fixed arguments, no shell
            self.argv(extra),
            cwd=REPO,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            creationflags=flags,
        )
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc: subprocess.Popen[str]) -> None:
        for line in proc.stdout or ():
            self.items.put(("line", line))
        code = proc.wait()
        for pipe in (proc.stdout, proc.stdin):
            if pipe is not None:
                with contextlib.suppress(OSError):
                    pipe.close()
        self.items.put(("exit", code))

    def send(self, line: str) -> None:
        """Send one line (an answer) to the engine."""
        if self.proc is not None and self.proc.stdin is not None:
            with contextlib.suppress(OSError, ValueError):  # it has exited: its exit event follows
                self.proc.stdin.write(line + "\n")
                self.proc.stdin.flush()

    def stop(self) -> None:
        """Ask the engine to stop: a closed stdin; it stops at its next wait, after a write under way."""
        if self.proc is not None and self.proc.stdin is not None:
            with contextlib.suppress(OSError, ValueError):
                self.proc.stdin.close()

    def end(self) -> None:
        """End an engine that has confirmed it stopped (its finished event) but not exited."""
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()


#: The demo's estimate of a task that does not report its progress: its bar reaches the cap and moves to and fro.
DEMO_SECONDS = 1.2


class DemoEngine:
    """A fake engine for --demo: every stage, a note, each kind of ask, progress, a failure and a retry.

    RECORDED starts as if an earlier run had done the first four stages (the start page shows);
    it stops as the engine does: at once, except in a task that writes, which finishes first.
    """

    STAGES = (
        (
            "computer",
            "Check this computer",
            "What the install needs on this computer, fetched or explained.",
            (
                ("doctor", "Programs this computer needs", "a few seconds", False),
                ("tools", "Host tools", "about 1 min", False),
                ("unlock-files", "Unlock files", "about 2 min", False),
            ),
        ),
        (
            "build",
            "Build the system",
            "Every image, built on this computer. It takes hours; the headset is not needed.",
            (
                ("kernel", "Kernel", "about 40 min", False),
                ("userspace", "System", "about 2 h", False),
                ("images", "Boot images", "about 5 min", False),
            ),
        ),
        (
            "connect",
            "Connect your headset",
            "Turn on developer mode and USB debugging, then plug it in.",
            (
                ("find", "Find the headset", "", False),
                ("identity", "Check the headset", "a few seconds", False),
            ),
        ),
        (
            "prepare",
            "Prepare your headset",
            "A backup first, then the bootloader is unlocked.",
            (
                ("backup", "Back up the headset", "about 20 min", True),
                ("abl", "Engineering bootloader", "about 1 min", True),
                ("unlock", "Unlock", "about 1 min", True),
                ("stage1", "Maintenance system", "about 2 min", True),
            ),
        ),
        (
            "install",
            "Install",
            "The system goes onto the headset, then a check that it starts.",
            (
                ("wifi", "Wi-Fi", "optional", True),
                ("system", "Install the system", "about 15 min", True),
                ("check", "Acceptance check", "about 2 min", False),
            ),
        ),
    )

    #: The other workflows' stages, shorter: what each does in the demo.
    WORKFLOWS: ClassVar[dict[str, tuple]] = {
        "upgrade": (
            (
                "build",
                "Build the system",
                "This version of the system, built on this computer.",
                (("userspace", "System", "minutes to hours", False),),
            ),
            (
                "install",
                "Install the upgrade",
                "The new system goes on; your data stays.",
                (
                    ("system", "Install the system", "about 10 min", True),
                    ("check", "Acceptance check", "about 2 min", False),
                ),
            ),
        ),
        "restore": (
            (
                "backup",
                "Check the backup",
                "Every file of the backup, checked.",
                (("verify-backup", "Check the backup", "about 5 min", False),),
            ),
            (
                "restore",
                "Restore PICO OS",
                "PICO OS's system goes back from the backup.",
                (("system", "PICO OS's system", "about 20 min", True),),
            ),
            (
                "pico-os",
                "Set PICO OS up",
                "PICO OS's setup and USB debugging, on the headset.",
                (("pico-os", "PICO OS's setup", "about 10 min", False),),
            ),
            (
                "bootloader",
                "Stock bootloader",
                "PICO OS's own bootloader goes back.",
                (("restore-abl", "Stock bootloader", "about 10 min", True),),
            ),
        ),
    }

    def __init__(
        self,
        *,
        pace: float = 1.0,
        recorded: bool = False,
        codename: str = "demo",
        name: str = "headset",
        workflow: str = INSTALL,
    ) -> None:
        """Walk through the plan; PACE scales the pauses (0 in tests); RECORDED: the first four stages done.

        The plan names the headset CODENAME and NAME: the window's chosen one; WORKFLOW picks the
        install's stages or another workflow's (never recorded).
        """
        self.headset = {"codename": codename, "name": name}
        self.workflow = workflow
        self.stages = self.STAGES if workflow == INSTALL else self.WORKFLOWS[workflow]
        self.pace = pace
        self.items: queue.Queue[Item] = queue.Queue()
        self.answers: queue.Queue[str | None] = queue.Queue()
        recorded = recorded and workflow == INSTALL
        self.done: set[str] = {t[0] for s in self.stages[:4] for t in s[3]} if recorded else set()
        self.writes = {t[0] for s in self.stages for t in s[3] if t[3]}
        self.failed_once = False
        self.stopping = threading.Event()

    def start(self, extra: list[str]) -> None:
        """Start a run; EXTRA ["--plan"] only shows the plan, ["--restart", STAGE] forgets from STAGE on."""
        self.stopping.clear()
        # An answer left from a stop (or an unanswered ask) belongs to the last run, not to this one's asks.
        self.answers = queue.Queue()
        threading.Thread(target=self._run, args=(extra,), daemon=True).start()

    def send(self, line: str) -> None:
        """Take an answer."""
        self.answers.put(line)

    def stop(self) -> None:
        """Stop at the next pause, as the engine does at its next wait; a task that writes finishes first."""
        self.stopping.set()
        self.answers.put(None)

    def end(self) -> None:
        """Nothing to end: the demo's thread exits by itself."""

    def _emit(self, event: str, **fields: object) -> None:
        self.items.put(("line", json.dumps({"event": event, **fields})))

    def _pause(self, seconds: float, *, writes: bool = False) -> bool:
        if writes:  # a child that writes is never cut off
            time.sleep(seconds * self.pace)
            return True
        return not self.stopping.wait(seconds * self.pace)

    def _ask(self, ask_id: str, kind: str, **fields: object) -> dict[str, Any] | None:
        self._emit("ask", id=ask_id, kind=kind, **fields)
        line = self.answers.get()
        reply = json.loads(line) if line else None
        return reply if reply and reply.get("accepted") else None

    def _run(self, extra: list[str]) -> None:
        plan = [
            {
                "id": s,
                "title": t,
                "summary": m,
                # A writing task reports its progress; another one follows its estimate, a short one here.
                "tasks": [
                    {"id": i, "title": n, "estimate": e, "writes": w, "seconds": None if w else DEMO_SECONDS}
                    for i, n, e, w in tasks
                ],
            }
            for s, t, m, tasks in self.stages
        ]
        self._emit("plan", headset=self.headset, workflow=self.workflow, stages=plan)
        if extra == ["--plan"]:
            for stage, _title, _summary, tasks in self.stages:
                for task, *_ in tasks:
                    self._emit("task", stage=stage, task=task, status=DONE if task in self.done else PENDING)
            self.items.put(("exit", 0))
            return
        if extra[:1] == ["--restart"]:
            ids = [stage[0] for stage in self.stages]
            for stage in self.stages[ids.index(extra[1]) :]:
                self.done -= {task[0] for task in stage[3]}
        status, where = self._install(plan)
        if status == DONE:
            self._emit("finished", status=DONE)
        elif status == STOPPED:
            self._emit("finished", status=STOPPED, task=where[0], where=where[1])
        else:
            self._emit("finished", status=FAILED, task=status, error=where[0], where=where[1])
        self.items.put(("exit", 0))

    def _install(self, plan: list[dict[str, Any]]) -> tuple[str, tuple[str, str]]:
        for stage in plan:
            for task in stage["tasks"]:
                result = self._task(stage["id"], task["id"])
                if result is not None:
                    return result
        return DONE, ("", "")

    def _stopped(self, stage: str, task: str, where: str) -> tuple[str, tuple[str, str]]:
        """Return a stop in TASK, as the engine records it: the task pending again."""
        self._emit("task", stage=stage, task=task, status=PENDING)
        return STOPPED, (task, where)

    def _task(self, stage: str, task: str) -> tuple[str, tuple[str, str]] | None:  # noqa: C901, PLR0911  # the demo's script
        if task in self.done:
            self._emit("task", stage=stage, task=task, status=DONE)
            return None
        if not self._pause(0.3):
            return self._stopped(stage, task, STOPPED_BETWEEN)
        writes = task in self.writes
        self._emit("task", stage=stage, task=task, status=RUNNING)
        if task == "find" and not self._ask(
            "plug-in",
            "action",
            title="Plug in your headset",
            text="On the headset, turn on developer mode and USB debugging, then connect it with its USB cable.",
            accept="Done",
            decline="Cancel",
        ):
            return self._stopped(stage, task, STOPPED_NOTHING)
        if task == "backup" and not self._ask(
            "consent",
            "confirm",
            title="Erase the headset?",
            text="Everything on the headset is erased: apps, games, accounts and settings. A backup of its system "
            "is saved on this computer first.",
            accept="Erase and continue",
            decline="Cancel",
        ):
            return self._stopped(stage, task, STOPPED_NOTHING)
        if task == "wifi":
            reply = self._ask(
                "wifi",
                "form",
                title="Wi-Fi",
                text="Optional: the headset joins this network when it starts.",
                fields=[
                    {"name": "ssid", "label": "Network name", "secret": False, "optional": True},
                    {"name": "password", "label": "Password", "secret": True, "optional": True},
                ],
                accept="Continue",
                decline="Cancel",
            )
            if reply is None:
                return self._stopped(stage, task, STOPPED_NOTHING)
            self._emit("log", task=task, line="network: " + (reply["values"].get("ssid") or "(none)"))
        if task == "unlock":
            self._emit("note", task=task, level="warning", text="Keep the cable in until the headset restarts.")
        steps = 10
        for n in range(1, steps + 1):
            if not self._pause(0.3 if writes else 0.15, writes=writes):
                return self._stopped(stage, task, STOPPED_NOTHING)
            if writes:
                self._emit("progress", task=task, fraction=n / steps, text=f"{n * 3} of {steps * 3} MB")
            else:
                self._emit("progress", task=task, fraction=None, text=f"part {n} of {steps}")
            self._emit("log", task=task, line=f"demo output {n}")
        if task == "unlock" and not self.failed_once:
            self.failed_once = True
            self._emit("task", stage=stage, task=task, status=FAILED)
            return task, (
                "The headset did not come back in fastboot mode.",
                "The headset is in fastboot mode and its backup is saved; nothing on it has changed. Press Retry.",
            )
        self.done.add(task)
        self._emit("task", stage=stage, task=task, status=DONE)
        return None


Engine = Union[ChildEngine, DemoEngine]


class BackupJob:
    """A backup's export or import as a child process (python -m overpressure backup): its lines, then its end.

    The window shows its last line while it runs and its result when it has ended; like the
    engine, the command line does the same.
    """

    def __init__(self, codename: str, action: str, path: str) -> None:
        """Export the headset CODENAME's backup to PATH, or import PATH (ACTION export or import)."""
        self.action = action
        self.argv = [sys.executable, "-m", "overpressure", "--device", codename, "backup", action, path]
        self.items: queue.Queue[Item] = queue.Queue()
        self.lines: list[str] = []
        self.code: int | None = None
        self.started = False
        self.begun: float | None = None  # time.monotonic() at its start
        self.fraction: float | None = None  # its last progress line's share; None before the first
        self.progress = ""  # its last progress line's text

    @property
    def running(self) -> bool:
        """Tell whether it has started and not ended."""
        return self.started and self.code is None

    def start(self) -> None:
        """Start the child."""
        self.started, self.begun = True, time.monotonic()
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        proc = subprocess.Popen(  # noqa: S603  # this Python and fixed arguments, no shell
            self.argv,
            cwd=REPO,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        def read() -> None:
            for line in proc.stdout or ():
                self.items.put(("line", line))
            code = proc.wait()
            if proc.stdout is not None:
                with contextlib.suppress(OSError):
                    proc.stdout.close()
            self.items.put(("exit", code))

        threading.Thread(target=read, daemon=True).start()

    def receive(self) -> bool:
        """Take what has arrived; return whether anything did."""
        changed = False
        for kind, value in drain(self.items):
            changed = True
            found = progress.parse(str(value)) if kind == "line" else None
            if kind == "exit":
                self.code = int(value)
            elif found is not None:
                self.fraction, self.progress = found
            elif str(value).strip():
                self.lines.append(str(value).rstrip())
        return changed

    def status(self, now: float) -> str:
        """Return its status line: how far it is and how long it has run."""
        ran = progress.clock(0 if self.begun is None else now - self.begun)
        return ran if self.fraction is None else f"{self.fraction:.0%} · {ran}"

    def last(self) -> str:
        """Return its last line, or "" before the first."""
        return self.lines[-1] if self.lines else ""

    def error(self) -> str:
        """Return why it failed: its error line, else its last line."""
        found = next((line for line in reversed(self.lines) if line.startswith("error:")), self.last())
        return found.removeprefix("error:").strip()


class DemoBackupJob(BackupJob):
    """--demo's backup job: a few lines over a few seconds, nothing read or written."""

    def __init__(self, codename: str, action: str, path: str, *, pace: float = 1.0) -> None:
        """Pretend to export or import PATH; PACE scales its pauses (0 in tests)."""
        super().__init__(codename, action, path)
        self.pace = pace

    def start(self) -> None:
        """Send the demo's lines on a thread."""
        self.started, self.begun = True, time.monotonic()
        verb = "Exporting" if self.action == "export" else "Importing"
        lines = [f"Checking the backup ({self.argv[-1]})"] + [f"{verb} lun{n}/demo.bin ({n} of 3)" for n in (1, 2, 3)]

        def work() -> None:
            for n, line in enumerate(lines):
                time.sleep(0.4 * self.pace)
                self.items.put(("line", line))
                self.items.put(("line", progress.line(line, (n + 1) / (len(lines) + 1))))
            self.items.put(("line", f"{'Exported to' if self.action == 'export' else 'Imported:'} {self.argv[-1]}"))
            self.items.put(("exit", 0))

        threading.Thread(target=work, daemon=True).start()


def drain(items: queue.Queue[Item]) -> Iterator[Item]:
    """Yield what has arrived from an engine without waiting."""
    for _ in range(items.qsize()):  # the window is the only reader: what is counted is there
        yield items.get_nowait()
