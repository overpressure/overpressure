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


def run(
    argv: Sequence[str | os.PathLike[str]],
    timeout: float = 60,
    stdin: int | IO[bytes] | None = None,
    *,
    check: bool = True,
    log: str | os.PathLike[str] | None = None,
) -> str:
    """Run a program from the repository root; its output goes to LOG too.

    A nonzero exit raises unless check is off.
    """
    process = subprocess.run(  # noqa: S603  # the host tools' fixed command lines, no shell
        [str(a) for a in argv],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        stdin=stdin,
        cwd=REPO,
        check=False,
    )
    if log is not None:
        Path(log).write_bytes(process.stdout)
    output = process.stdout.decode("utf-8", "replace").strip()
    if check and process.returncode:
        where = f"; see {Path(log).name}" if log is not None else f": {output[-800:]}"
        msg = f"{Path(str(argv[0])).name} failed ({process.returncode}){where}"
        raise RuntimeError(msg)
    return output


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
    its standard input (none by default).
    """
    proc = subprocess.Popen(  # noqa: S603  # the host tools' fixed command lines, no shell
        [str(a) for a in argv],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=stdin or subprocess.DEVNULL,
        cwd=REPO,
    )
    chunks: list[bytes] = []

    def read() -> None:
        pending = b""
        if proc.stdout is None:
            return
        pipe = proc.stdout.fileno()
        for block in iter(lambda: os.read(pipe, 1 << 16), b""):
            chunks.append(block)
            pending += block
            *done, pending = re.split(rb"[\r\n]", pending)
            for raw in done:
                if raw.strip():
                    on_line(raw.decode("utf-8", "replace").rstrip())
        if pending.strip():
            on_line(pending.decode("utf-8", "replace").rstrip())

    reader = threading.Thread(target=read, daemon=True)
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
    output = b"".join(chunks).decode("utf-8", "replace").strip()
    if check and proc.returncode:
        msg = f"{Path(str(argv[0])).name} failed ({proc.returncode}): {output[-800:]}"
        raise RuntimeError(msg)
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
