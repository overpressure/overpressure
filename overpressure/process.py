# SPDX-License-Identifier: Unlicense
"""Running the host's programs against a headset, and bounded waits for its states.

Every wait is bounded: a headset that does not answer ends a step with an error naming what was
awaited, never with a hang.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Callable, Protocol, TypeVar

from . import REPO

if TYPE_CHECKING:
    import os
    from collections.abc import Sequence

T = TypeVar("T")


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

    It is retried every @p interval s while it raises, for at most @p seconds.
    """
    end = clock.monotonic() + seconds
    while True:
        try:
            return operation()
        except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:  # noqa: PERF203  # a retry loop
            if clock.monotonic() > end:
                msg = f"timed out waiting for {what}: {exc}"
                raise RuntimeError(msg) from exc
            clock.sleep(interval)
