"""Running the host's programs against a headset, and bounded waits for its states.

Every wait is bounded: a headset that does not answer ends a step with an error naming what was
awaited, never with a hang.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import REPO


def run(argv, timeout=60, stdin=None, check=True, log=None) -> str:
    """Run a program from the repository root; its output goes to LOG too. A nonzero exit raises
    unless check is off."""
    process = subprocess.run([str(a) for a in argv], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             timeout=timeout, stdin=stdin, cwd=REPO)
    if log is not None:
        Path(log).write_bytes(process.stdout)
    output = process.stdout.decode("utf-8", "replace").strip()
    if check and process.returncode:
        where = f"; see {Path(log).name}" if log is not None else f": {output[-800:]}"
        raise RuntimeError(f"{Path(str(argv[0])).name} failed ({process.returncode}){where}")
    return output


@dataclass
class Clock:
    """The time a wait uses; tests pass a fake one."""

    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep


def until(what: str, seconds: float, operation: Callable[[], object], clock: Clock, interval: float = 3):
    """@p operation until it returns, retried every @p interval s while it raises, for at most
    @p seconds."""
    end = clock.monotonic() + seconds
    while True:
        try:
            return operation()
        except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
            if clock.monotonic() > end:
                raise RuntimeError(f"timed out waiting for {what}: {exc}") from exc
            clock.sleep(interval)


