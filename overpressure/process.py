# SPDX-License-Identifier: Unlicense
"""Running the host's programs against a headset, and bounded waits for its states.

Every wait is bounded: a headset that does not answer ends a step with an error naming what was
awaited, never with a hang.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Callable, Protocol, TypeVar

from . import REPO

if TYPE_CHECKING:
    from collections.abc import Sequence

T = TypeVar("T")


class Final(Exception):  # noqa: N818  # a marker mixed into the errors it applies to
    """An error that waiting does not cure: until() raises it at once instead of retrying."""


class CommandFailed(RuntimeError):  # noqa: N818  # a RuntimeError, as the callers catch it
    """A program that exited nonzero: the message ends with its output's last part, @p output holds all of it."""

    def __init__(self, msg: str, output: str) -> None:
        """Keep the whole @p output for a log; the message stays short."""
        super().__init__(msg)
        self.output = output


def run(
    argv: Sequence[str | os.PathLike[str]],
    timeout: float = 60,
    stdin: int | IO[bytes] | None = None,
    *,
    check: bool = True,
    log: str | os.PathLike[str] | None = None,
) -> str:
    """Run a program from the repository root; its output goes to LOG too.

    A nonzero exit raises unless check is off. Without @p stdin it reads nothing: it does not inherit this
    process's stdin, which on Windows can be the installer's answer pipe with a read pending (the window's
    engine), and a program that inherits it, such as ssh, then waits behind that read for good.
    """
    process = subprocess.run(  # noqa: S603  # the host tools' fixed command lines, no shell
        [str(a) for a in argv],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        stdin=subprocess.DEVNULL if stdin is None else stdin,
        cwd=REPO,
        check=False,
    )
    if log is not None:
        Path(log).write_bytes(process.stdout)
    output = process.stdout.decode("utf-8", "replace").strip()
    if check and process.returncode:
        where = f"; see {Path(log).name}" if log is not None else f": {output[-800:]}"
        msg = f"{Path(str(argv[0])).name} failed ({process.returncode}){where}"
        raise CommandFailed(msg, output)
    return output


class _Lines:
    """run_streamed's reader: a program's output, its lines handed on until the handler fails, then only drained."""

    def __init__(self, proc: subprocess.Popen[bytes], on_line: Callable[[str], None]) -> None:
        """Read @p proc's output for @p on_line."""
        self.proc, self.on_line = proc, on_line
        self.chunks: list[bytes] = []
        self.failed: list[Exception] = []

    def deliver(self, raw: bytes) -> None:
        """Hand one line on; a handler that raises ends the program, and the caller raises its error."""
        if self.failed or not raw.strip():
            return
        try:
            self.on_line(raw.decode("utf-8", "replace").rstrip())
        except Exception as exc:  # noqa: BLE001  # any failure of the caller's handling: raised in run_streamed
            self.failed.append(exc)
            self.proc.kill()

    def read(self) -> None:
        """Read the output to its end; a carriage return ends a line too."""
        if self.proc.stdout is None:
            return
        pending = b""
        pipe = self.proc.stdout.fileno()
        for block in iter(lambda: os.read(pipe, 1 << 16), b""):
            self.chunks.append(block)
            pending += block
            *done, pending = re.split(rb"[\r\n]", pending)
            for raw in done:
                self.deliver(raw)
        self.deliver(pending)


def run_streamed(
    argv: Sequence[str | os.PathLike[str]],
    timeout: float,
    on_line: Callable[[str], None],
    *,
    check: bool = True,
    stdin: IO[bytes] | None = None,
) -> str:
    """Run a program as run() does, handing each line to @p on_line as it comes; return its output.

    A carriage return ends a line too: a progress display redraws its line with one. @p stdin is
    its standard input (none by default). An @p on_line that raises ends the program and raises a
    RuntimeError from it here: the reader goes on emptying the pipe, so the program never waits on
    a full one (a line the console's encoding cannot write hung an install that way).
    """
    proc = subprocess.Popen(  # noqa: S603  # the host tools' fixed command lines, no shell
        [str(a) for a in argv],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=stdin or subprocess.DEVNULL,
        cwd=REPO,
    )
    lines = _Lines(proc, on_line)
    reader = threading.Thread(target=lines.read, daemon=True)
    reader.start()
    with proc:  # closes its pipe
        try:
            proc.wait(timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise
        finally:
            reader.join(10)
    if lines.failed:
        msg = f"{Path(str(argv[0])).name}'s output could not be handled: {lines.failed[0]}"
        raise RuntimeError(msg) from lines.failed[0]
    output = b"".join(lines.chunks).decode("utf-8", "replace").strip()
    if check and proc.returncode:
        msg = f"{Path(str(argv[0])).name} failed ({proc.returncode}): {output[-800:]}"
        raise CommandFailed(msg, output)
    return output


class Timing(Protocol):
    """What a wait needs: the time module, a Clock or a test's fake."""

    def monotonic(self) -> float:
        """Return a clock that only moves forward, in seconds."""
        ...

    def sleep(self, seconds: float, /) -> None:
        """Wait SECONDS."""
        ...


@dataclass
class Clock:
    """The time a wait uses; tests pass a fake one."""

    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep


def until(what: str, seconds: float, operation: Callable[[], T], clock: Timing, interval: float = 3) -> T:
    """Return @p operation's result once it returns.

    It is retried every @p interval s while it raises, for at most @p seconds; a Final error ends
    the wait at once.
    """
    end = clock.monotonic() + seconds
    while True:
        try:
            return operation()
        except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:  # noqa: PERF203  # a retry loop
            if isinstance(exc, Final):
                raise
            if clock.monotonic() > end:
                msg = f"timed out waiting for {what}: {exc}"
                raise RuntimeError(msg) from exc
            clock.sleep(interval)
