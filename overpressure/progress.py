# SPDX-License-Identifier: Unlicense
"""How far a task is: the progress lines tools print, and the time a task's estimate stands for.

A tool the installer runs tells it how far it is with lines of its own on stdout:
``progress 42%: writing super``, ``progress 3/8: building pico4-monado`` (three of eight done)
or ``progress: reading the partition table`` (how far is unknown). The installer turns them into
progress events; a task without them is shown against its estimate ("about 20 min"), which
estimate_seconds reads.
"""

from __future__ import annotations

import contextlib
import os
import re
import threading
from typing import IO, TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from collections.abc import Generator

PREFIX = "progress"
CHUNK = 1 << 20
_LINE = re.compile(r"progress(?:\s+(?:(\d+(?:\.\d+)?)%|(\d+)/(\d+)))?:\s*(.*)")
_UNITS = {"s": 1, "sec": 1, "second": 1, "min": 60, "minute": 60, "h": 3600, "hour": 3600}
_AMOUNT = r"(\d+(?:\.\d+)?|an?|a few)"
_ESTIMATE = re.compile(rf"{_AMOUNT}(?:\s+to\s+{_AMOUNT})?\s+(s|sec|second|min|minute|h|hour)s?\b")
#: What "a few" stands for, by unit: a few seconds, minutes or hours.
FEW = {1: 10, 60: 5, 3600: 3}


def line(text: str, fraction: float | None = None) -> str:
    """Return the progress line for @p text at @p fraction (0 to 1; None: how far is unknown)."""
    if fraction is None:
        return f"{PREFIX}: {text}"
    return f"{PREFIX} {max(0.0, min(1.0, fraction)) * 100:.1f}%: {text}"


def say(text: str, fraction: float | None = None) -> None:
    """Print the progress line for @p text at @p fraction, at once (the installer reads it as it comes)."""
    print(line(text, fraction), flush=True)


def parse(text: str) -> tuple[float | None, str] | None:
    """Return (fraction, text) of a progress line; None for any other line."""
    found = _LINE.fullmatch(text.strip())
    if found is None:
        return None
    percent, done, total, rest = found.groups()
    if percent is not None:
        fraction: float | None = float(percent) / 100
    elif done is not None:
        fraction = int(done) / max(int(total), 1)
    else:
        fraction = None
    return (None if fraction is None else max(0.0, min(1.0, fraction))), rest.strip()


def _amount(word: str, unit: int) -> float:
    if word in ("a", "an"):
        return 1.0
    if word == "a few":
        return float(FEW[unit])
    return float(word)


def estimate_seconds(words: str) -> float | None:
    """Return the seconds an estimate in words stands for, its upper end ("1 to 3 h": 3 h); None without one.

    "under a minute" is a minute, "a few seconds" ten seconds, "a few minutes" five.
    """
    found = _ESTIMATE.search(words.lower())
    if found is None:
        return None
    low, high, unit = found.groups()
    scale = _UNITS[unit]
    seconds = _amount(high or low, scale) * scale
    return seconds if seconds > 0 else None


def clock(seconds: float) -> str:
    """Return @p seconds as a clock reading: 0:05, 12:34, 1:02:03."""
    whole = max(int(seconds), 0)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def duration(seconds: float) -> str:
    """Return @p seconds in rounded words: under a minute, 4 min, 1 h 20 min."""
    minutes = round(seconds / 60)
    if minutes < 1:
        return "under a minute"
    if minutes < 60:  # noqa: PLR2004  # minutes in an hour
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest} min" if rest else f"{hours} h"


class Meter:
    """Count bytes and print a progress line for each percent they advance (and the last).

    The share of the whole is mapped onto @p start to @p end, so that one meter can stand for
    a part of a task (the writes of an install, followed by its shorter steps).
    """

    def __init__(  # the count, its words and where it lies in the task keyword-only
        self,
        total: int,
        text: Callable[[int, int], str],
        *,
        start: float = 0.0,
        end: float = 1.0,
        out: Callable[[str], None] | None = None,
    ) -> None:
        """Count up to @p total bytes; @p text says what done of total means; @p out takes each line."""
        self.total = max(total, 1)
        self.text = text
        self.start = start
        self.end = end
        self.out = out or (lambda text: print(text, flush=True))
        self.done = 0
        self._shown = -1
        self._lock = threading.Lock()

    @property
    def fraction(self) -> float:
        """Return how far the task is, from start to end."""
        return self.start + (self.end - self.start) * min(self.done / self.total, 1.0)

    def add(self, count: int) -> None:
        """Count @p count more bytes; print a line when a new percent of the task is reached."""
        with self._lock:
            self.done += count
            percent = int(self.fraction * 100 + 1e-6)  # 0.29 * 100 is 28.999...
            if percent != self._shown or self.done >= self.total:
                self._shown = percent
                self.out(line(self.text(min(self.done, self.total), self.total), self.fraction))


def mib(done: int, total: int) -> str:
    """Return a byte count of a total in MiB: "1200 of 2900 MiB"."""
    return f"{done >> 20} of {total >> 20} MiB"


@contextlib.contextmanager
def feed(source: IO[bytes], meter: Meter) -> Generator[int, None, None]:
    """Yield the read end of a pipe that carries @p source, for a child's stdin, counting on @p meter.

    The bytes are counted as they enter the pipe: the count leads the child by the pipe's buffer.
    """
    read, write = os.pipe()

    def pump() -> None:
        try:
            with os.fdopen(write, "wb") as sink:
                for chunk in iter(lambda: source.read(CHUNK), b""):
                    sink.write(chunk)
                    meter.add(len(chunk))
        except OSError:
            pass  # the child stopped reading: its exit status tells why

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    try:
        yield read
    finally:
        os.close(read)
        thread.join(10)
