# SPDX-License-Identifier: Unlicense
"""Advanced mode's model: the headset's steps, their command lines, one run at a time, the checks.

Every step is a command line of overpressure or a script of the headset (overpressure.headset.
Step); Advanced mode shows that command line as it will run, so whatever it does can be repeated
in a terminal. One step runs at a time, and never while the installer runs (builds share their
caches); its output goes to the log, and a step that writes to the headset asks first. Nothing
here needs Tk: advanced_view.py draws these classes, and tests drive them without a display.
"""

from __future__ import annotations

import collections
import contextlib
import os
import queue
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Callable, Protocol

from overpressure import REPO, doctor, runner
from overpressure.window.installer import Item, drain

if TYPE_CHECKING:
    import subprocess

    from overpressure.headset import Headset, Param, Registry, Step

GROUPS = ("Setup", "Image", "Headset")
DOCTOR = "doctor"  # the Setup group's first item: the dependency checks
LOG_LINES = 5000  # the log keeps the last lines only
#: A run's states. Stopping: stop was asked for and the program has not exited yet.
IDLE, RUNNING, STOPPING, DONE, FAILED, STOPPED = "idle", "running", "stopping", "done", "failed", "stopped"
#: What pressing Run does: refused (something else runs), ask first (the step writes), or start.
REFUSED, ASK, START = "refused", "ask", "start"
#: What wants to start, for the busy rule: a step, the installer, or another headset.
STEP, INSTALLER, HEADSET = "step", "installer", "headset"
#: What the build container sees of paths in Linux: /var/tmp (the volume) and /work (the repository).
CONTAINER_TOPS = (PurePosixPath("/var/tmp"), PurePosixPath("/work"))  # noqa: S108  # the container's volume, not a temporary file


def busy(want: str, *, installer_running: bool, step_running: bool) -> str:
    """Return why WANT (STEP, INSTALLER or HEADSET) cannot start now, or "" when it can: one thing at a time."""
    if want == HEADSET and installer_running:
        return "The installer is running. Let it finish, or stop it in the installer, then change the headset."
    if want == HEADSET and step_running:
        return "A step of Advanced mode is running. Let it finish, or stop it, then change the headset."
    if want == STEP and installer_running:
        return (
            "The installer is running, and builds share their caches. Let it finish, or stop it in the "
            "installer, then run the step."
        )
    if want == STEP and step_running:
        return "A step is running. Let it finish, or stop it, then run the next one."
    if want == INSTALLER and step_running:
        return (
            "A step of Advanced mode is running, and builds share their caches. Let it finish, or stop it in "
            "Advanced mode, then start the installer."
        )
    return ""


@dataclass(frozen=True)
class Entry:
    """An entry of the steps list: a step's key (or DOCTOR) and its title."""

    key: str
    title: str


class Steps:
    """The chosen headset's steps: which one is selected, the values of their parameters, the command line."""

    def __init__(self, registry: Registry, headset: Headset | None = None) -> None:
        """Show HEADSET's steps, else the first headset of REGISTRY's; the checks are selected first."""
        self.registry = registry
        self.headset = headset or next(iter(registry.headsets.values()), None)
        self.values: dict[tuple[str, str], dict[str, str]] = {}  # (codename, step) -> its parameters' values
        self.selected = DOCTOR

    def choose(self, codename: str) -> None:
        """Show the headset CODENAME's steps (the window's choice); a step it lacks gives way to the checks."""
        self.headset = self.registry.get(codename)
        if self.step() is None:
            self.selected = DOCTOR

    def codename(self) -> str:
        """Return the chosen headset's codename, or "" without one."""
        return self.headset.info.codename if self.headset is not None else ""

    def about(self) -> str:
        """Return the line under the headset's name: its SoC and summary."""
        if self.headset is None:
            return ""
        return " · ".join(x for x in (self.headset.info.soc, self.headset.info.summary) if x)

    def steps(self) -> list[Step]:
        """Return the chosen headset's steps, in its order."""
        return list(self.headset.steps()) if self.headset is not None else []

    def groups(self) -> list[tuple[str, list[Entry]]]:
        """Return the list's groups in order, each with its entries; the checks come first in Setup."""
        steps = self.steps()
        groups = []
        for group in GROUPS:
            entries = [Entry(DOCTOR, "Check dependencies")] if group == GROUPS[0] else []
            entries += [Entry(s.key, s.title) for s in steps if s.group == group]
            if entries:
                groups.append((group, entries))
        return groups

    def has(self, key: str) -> bool:
        """Tell whether KEY is the checks or one of the chosen headset's steps."""
        return key == DOCTOR or any(s.key == key for s in self.steps())

    def select(self, key: str) -> bool:
        """Select KEY (a step, or DOCTOR); False, and nothing changes, when there is none."""
        if not self.has(key):
            return False
        self.selected = key
        return True

    def step(self) -> Step | None:
        """Return the selected step, or None while the checks are selected."""
        return next((s for s in self.steps() if s.key == self.selected), None)

    def values_of(self, step: Step) -> dict[str, str]:
        """Return STEP's values, its defaults until they are changed; kept per headset and step."""
        return self.values.setdefault((self.codename(), step.key), {p.name: p.default for p in step.params})

    def set_value(self, name: str, value: str) -> None:
        """Set the selected step's parameter NAME."""
        step = self.step()
        if step is not None:
            self.values_of(step)[name] = value

    def argv(self, step: Step | None = None) -> list[str]:
        """Return the process STEP (else the selected one) is on this host, or [] without one.

        A Linux-root step's paths inside the repository are passed relative to it, as the container sees them.
        """
        step = step or self.step()
        if step is None or self.headset is None:
            return []
        values = dict(self.values_of(step))
        if step.linux_root:
            for param in step.params:
                found = container_path(values.get(param.name, "")) if param.kind == "path" else None
                if found:
                    values[param.name] = found
        return runner.process(self.headset, step, values)

    def problem(self, step: Step | None = None) -> str:
        """Return why STEP (else the selected one) cannot run with its values, or "" when it can.

        The build container sees the repository (at /work) and /var/tmp only: a Linux-root step's
        path elsewhere is refused before the run.
        """
        step = step or self.step()
        if step is None or not step.linux_root:
            return ""
        values = self.values_of(step)
        for param in step.params:
            value = values.get(param.name, "").strip()
            if param.kind == "path" and value and container_path(value) is None:
                return (
                    f"{param.label}: {value} is outside the repository. The build container sees only the "
                    f"repository and /var/tmp: choose a path inside {REPO}, or one under /var/tmp."
                )
        return ""

    def command(self) -> str:
        """Return the selected step's command line as one would type it in this host's terminal."""
        argv = self.argv()
        return runner.display(argv) if argv else ""

    def help(self, step: Step) -> str:
        """Return what STEP does, and where it runs and whether it writes to the headset."""
        notes = [step.help] if step.help else []
        if step.linux_root:
            notes.append("Runs on Linux as root, in the build container.")
        if step.writes:
            notes.append("Writes to the headset: asks before it starts.")
        return " ".join(notes)


def browse(step: Step, param: Param) -> str:
    """Return what PARAM's browse button picks: "file", "folder", or "" for none.

    A Linux-root step's path under /var/tmp is in the build container's volume, which this host's
    dialog cannot pick; a default whose last part has a dot names a file.
    """
    if param.kind != "path" or (step.linux_root and param.default.startswith("/")):
        return ""
    return "file" if "." in param.default.rsplit("/", 1)[-1] else "folder"


def container_path(value: str, root: Path = REPO) -> str | None:
    """Return VALUE as the build container sees it, or None when it cannot.

    A path under /var/tmp (the volume) or /work (the repository) stays as it is; one in the repository
    becomes relative to it; anything else is outside what the container sees.
    """
    value = value.strip()
    posix = PurePosixPath(value.replace("\\", "/"))
    if value.startswith("/") and any(posix == top or top in posix.parents for top in CONTAINER_TOPS):
        return value
    path = Path(value)
    if not path.is_absolute() and not value.startswith("/"):
        path = root / path
    if not path.is_absolute():
        return None
    # Lexically (.. folded, links not followed): the container mounts the repository's folder as it is.
    with contextlib.suppress(ValueError):
        return Path(os.path.abspath(path)).relative_to(os.path.abspath(root)).as_posix()  # noqa: PTH100  # resolve() follows links
    return None


def relative(path: str, root: Path = REPO) -> str:
    """Return a picked PATH relative to the repository with forward slashes, when it is inside it."""
    with contextlib.suppress(ValueError):
        return Path(path).relative_to(root).as_posix()
    return path


class Run:
    """One step's run at a time: its state, the log and the status line."""

    def __init__(self) -> None:
        """Start idle, with an empty log."""
        self.state = IDLE
        self.key = self.title = ""
        self.writes = False
        self.code: int | None = None
        self.log: collections.deque[str] = collections.deque(maxlen=LOG_LINES)
        self.unseen: list[str] = []  # lines the view has not shown yet
        self.overflowed = False  # more lines came than the log keeps since the view last took them

    @property
    def running(self) -> bool:
        """Tell whether a step's program runs (also while it is being stopped)."""
        return self.state in (RUNNING, STOPPING)

    def add(self, line: str) -> None:
        """Add one line to the log."""
        self.log.append(line)
        self.unseen.append(line)
        if len(self.unseen) > LOG_LINES:
            self.unseen, self.overflowed = [], True

    def take(self) -> tuple[list[str], bool]:
        """Return the lines the view has not shown yet, once, and whether they replace what it shows."""
        if self.overflowed:
            self.unseen, self.overflowed = [], False
            return list(self.log), True
        lines, self.unseen = self.unseen, []
        return lines, False

    def begin(self, step: Step, command: str) -> None:
        """Record that STEP's program starts as COMMAND."""
        self.state, self.key, self.title, self.writes, self.code = RUNNING, step.key, step.title, step.writes, None
        self.add(f"$ {command}")

    def stop(self) -> bool:
        """Record that stop was asked for; False when nothing runs."""
        if self.state != RUNNING:
            return False
        self.state = STOPPING
        return True

    def ended(self, code: int) -> None:
        """Record the program's exit status: done, failed, or stopped when stop was asked for."""
        self.code = code
        self.state = STOPPED if self.state == STOPPING else DONE if code == 0 else FAILED
        self.add(f"[exit {code}]")

    def status(self) -> str:
        """Return the status line: what runs, or how the last run ended."""
        texts = {
            IDLE: "Nothing is running.",
            RUNNING: f"Running: {self.title}",
            STOPPING: f"Stopping: {self.title}",
            DONE: f"{self.title}: done",
            FAILED: f"{self.title}: failed (exit status {self.code})",
            STOPPED: f"{self.title}: stopped",
        }
        return texts[self.state]


class StepProcess:
    """A step's program on this host (runner.run on a thread): its lines and its exit into a queue."""

    def __init__(self) -> None:
        """Start with nothing running."""
        self.items: queue.Queue[Item] = queue.Queue()
        self.proc: subprocess.Popen | None = None
        self.argv: list[str] = []
        self.stopping = False
        self.lock = threading.Lock()

    def start(self, argv: list[str]) -> None:
        """Run ARGV from the repository root; an exit item follows its last line."""
        with self.lock:
            self.proc, self.argv, self.stopping = None, argv, False

        def started(proc: subprocess.Popen) -> None:
            with self.lock:
                self.proc = proc
                if self.stopping:  # stop came before the program had started
                    threading.Thread(target=runner.stop, args=(proc, argv), daemon=True).start()

        def work() -> None:
            try:
                code = runner.run(argv, lambda line: self.items.put(("line", line)), started)
            except OSError as exc:
                self.items.put(("line", f"error: {exc}"))
                code = -1
            with self.lock:
                if self.proc is not None and self.proc.stdout is not None:
                    self.proc.stdout.close()  # read to its end: runner.run leaves it open
            self.items.put(("exit", code))

        threading.Thread(target=work, daemon=True).start()

    def stop(self) -> None:
        """End the running program; a build's container with it (runner.stop, off the window's thread)."""
        with self.lock:
            self.stopping = True
            proc = self.proc if self.proc is not None and self.proc.poll() is None else None
        if proc is not None:
            threading.Thread(target=runner.stop, args=(proc, self.argv), daemon=True).start()


class DemoProcess:
    """--demo's stand-in for a step's program: a few lines of output, then success; nothing runs."""

    LINES = 8

    def __init__(self, *, pace: float = 1.0) -> None:
        """Print a line every PACE quarter seconds (0 in tests)."""
        self.pace = pace
        self.items: queue.Queue[Item] = queue.Queue()
        self.stopping = threading.Event()

    def start(self, argv: list[str]) -> None:
        """Pretend to run ARGV."""
        self.stopping.clear()

        def work() -> None:
            for n in range(1, self.LINES + 1):
                if self.stopping.wait(0.25 * self.pace):
                    self.items.put(("exit", 1))
                    return
                self.items.put(("line", f"demo: line {n} of {self.LINES} of {argv[-1]} (nothing runs in the demo)"))
            self.items.put(("exit", 0))

        threading.Thread(target=work, daemon=True).start()

    def stop(self) -> None:
        """End the pretence at its next line."""
        self.stopping.set()


class Process(Protocol):
    """What runs a step's program: StepProcess, DemoProcess, a test's fake."""

    items: queue.Queue[Item]

    def start(self, argv: list[str]) -> None:
        """Start ARGV; its lines, then its exit, arrive in items."""

    def stop(self) -> None:
        """End it; its exit arrives in items."""


def demo_checks(_headset: Headset | None) -> list[doctor.Check]:
    """Return --demo's checks: one of each status, nothing is looked at."""
    return [
        doctor.Check("Python", "everything", doctor.OK, "3.12.15"),
        doctor.Check("Tk", "the window", doctor.OK, "Tk 8.6"),
        doctor.Check("ssh", doctor.HEADSET, doctor.OK, "/usr/bin/ssh"),
        doctor.Check(".env", doctor.HEADSET, doctor.MISSING, "no .env", "copy .env.example to .env"),
        doctor.Check("adb", doctor.HEADSET, doctor.OK, "Android Debug Bridge version 1.0.41"),
        doctor.Check("clang and lld", "the userspace build", doctor.WARNING, "none: packages compile under QEMU"),
        doctor.Check("NCM Python and UsbDk", "the USB network link on Windows", doctor.OPTIONAL, "not needed here"),
    ]


class Checks:
    """The dependency checks per headset, run off the window's thread: grouped, summed up, one selected."""

    def __init__(self, run: Callable[[Headset | None], list[doctor.Check]] = doctor.run) -> None:
        """Run the checks with RUN (doctor.run; tests and the demo pass their own)."""
        self.run = run
        self.results: dict[str, list[doctor.Check]] = {}  # codename -> its last checks
        self.checking: set[str] = set()
        self.selected: dict[str, int] = {}  # codename -> the selected check's index
        self.done: queue.Queue[tuple[str, list[doctor.Check]]] = queue.Queue()

    def start(self, headset: Headset | None) -> bool:
        """Check again for HEADSET on a thread (the container engine takes seconds to answer); False while it runs."""
        codename = headset.info.codename if headset is not None else ""
        if codename in self.checking:
            return False
        self.checking.add(codename)

        def work() -> None:
            try:
                result = self.run(headset)
            except Exception as exc:  # noqa: BLE001  # the page says so instead of staying empty
                result = [doctor.Check("doctor", "?", doctor.WARNING, f"the checks failed: {exc}")]
            self.done.put((codename, result))

        threading.Thread(target=work, daemon=True).start()
        return True

    def receive(self) -> list[str]:
        """Take the finished checks; return the codenames that have new ones."""
        updated = []
        while True:
            try:
                codename, checks = self.done.get_nowait()
            except queue.Empty:
                return updated
            self.results[codename] = checks
            self.checking.discard(codename)
            first = next((i for i, c in enumerate(checks) if c.status != doctor.OK), None)
            self.selected.pop(codename, None)
            if first is not None:
                self.selected[codename] = first
            updated.append(codename)

    def groups(self, codename: str) -> list[tuple[str, list[tuple[int, doctor.Check]]]]:
        """Return CODENAME's checks grouped by what they are needed for, in the order they came, with their index."""
        groups: dict[str, list[tuple[int, doctor.Check]]] = {}
        for i, check in enumerate(self.results.get(codename, [])):
            groups.setdefault(check.needed_for, []).append((i, check))
        return list(groups.items())

    def summary(self, codename: str) -> str:
        """Return the line above the checks: what is missing, the warnings, and that nothing is installed."""
        if codename in self.checking:
            return "Checking ..."
        checks = self.results.get(codename)
        if checks is None:
            return ""
        missing = doctor.failed(checks)
        warnings = [c for c in checks if c.status == doctor.WARNING]
        text = (
            f"{len(missing)} missing: " + ", ".join(c.name for c in missing) + "."
            if missing
            else "Everything needed is there."
        )
        if warnings:
            text += f" {len(warnings)} warning{'s' if len(warnings) > 1 else ''}."
        return text + " The checks only look; they install nothing."

    def label(self, codename: str) -> str:
        """Return the list entry's title: how many are missing, once checked."""
        checks = self.results.get(codename)
        if checks is None:
            return "Check dependencies"
        missing = len(doctor.failed(checks))
        return "Check dependencies" + (f" ({missing} missing)" if missing else " (all there)")

    def select(self, codename: str, index: int) -> None:
        """Select CODENAME's check INDEX."""
        if 0 <= index < len(self.results.get(codename, [])):
            self.selected[codename] = index

    def fix(self, codename: str) -> str:
        """Return how to fix the selected check, or "" when it is fine or none is selected."""
        index = self.selected.get(codename)
        checks = self.results.get(codename, [])
        if index is None or index >= len(checks) or checks[index].status == doctor.OK:
            return ""
        return checks[index].fix


class Advanced:
    """Advanced mode: the steps, the run and the program behind it, the checks."""

    def __init__(
        self,
        registry: Registry,
        headset: Headset | None = None,
        *,
        process: Process | None = None,
        checks: Callable[[Headset | None], list[doctor.Check]] = doctor.run,
    ) -> None:
        """Work on HEADSET's steps (else REGISTRY's first); PROCESS and CHECKS are fakes in tests and the demo."""
        self.steps = Steps(registry, headset)
        self.run = Run()
        self.process: Process = process or StepProcess()
        self.checks = Checks(checks)

    def press(self, *, installer_running: bool) -> tuple[str, str, str]:
        """Return what Run does now: (REFUSED, ASK or START, the panel's title, its text)."""
        step = self.steps.step()
        if step is None:
            return REFUSED, "No step selected", "Select a step in the list first."
        refusal = busy(STEP, installer_running=installer_running, step_running=self.run.running)
        if refusal:
            return REFUSED, "One thing at a time", refusal
        problem = self.steps.problem(step)
        if problem:
            return REFUSED, "Path outside the repository", problem
        if step.writes:
            text = f"{step.title}: {step.help}" if step.help else step.title
            return ASK, "Write to the headset?", f"{text}\n\n{self.steps.command()}"
        return START, "", ""

    def start(self) -> bool:
        """Start the selected step's program; False when there is none or something runs."""
        step = self.steps.step()
        if step is None or self.run.running:
            return False
        argv = self.steps.argv(step)
        self.run.begin(step, runner.display(argv))
        self.process.start(argv)
        return True

    def stop(self) -> None:
        """Stop the running step's program."""
        if self.run.stop():
            self.process.stop()

    def receive(self) -> bool:
        """Take the program's lines and its exit; return whether the run has ended."""
        ended = False
        for kind, value in drain(self.process.items):
            if kind == "exit":
                self.run.ended(int(value))
                ended = True
            else:
                self.run.add(str(value))
        return ended
