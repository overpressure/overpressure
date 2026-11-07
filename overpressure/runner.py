# SPDX-License-Identifier: Unlicense
"""Running a headset's steps: the process each one is, on this host.

A command step runs this package's command line for the headset; a script step runs a program
from the headset's folder. Steps that need Linux as root (the builds mount and chroot) run in the
build container (overpressure/linux.py), always from the repository root, so relative paths in
their parameters mean the same on the host and in the container.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import subprocess
import sys
from typing import IO, TYPE_CHECKING, Callable, cast

from . import REPO, linux

if TYPE_CHECKING:
    from .headset import Headset, Step


def process(
    headset: Headset,
    step: Step,
    values: dict[str, str],
    *,
    python: str = sys.executable,
) -> list[str]:
    """Return the argv that runs @p step with @p values; its working directory is the repository."""
    words = step.command(values)
    if step.script:
        program = headset.root / words[0]
        argv = ["bash", str(program.relative_to(REPO)).replace("\\", "/"), *words[1:]]
    else:
        cli = ["-m", "overpressure", "--device", headset.info.codename, *words]
        argv = [python if not step.linux_root else "python3", *cli]
    if not step.linux_root:
        return argv
    return [python, *linux.RUN, "--", *argv]


def stop(proc: subprocess.Popen, argv: list[str]) -> None:
    """End a step's program @p proc; a build's container first, which outlives a client that is killed."""
    if linux.is_build(argv):
        with contextlib.suppress(OSError):
            linux.stop_build(proc.pid)
    with contextlib.suppress(OSError):
        proc.terminate()


def display(argv: list[str], platform: str = sys.platform) -> str:
    """Return the command line as one would type it in this host's terminal."""
    argv = ["python" if word == sys.executable else word for word in argv]
    if platform == "win32":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def run(
    argv: list[str], output: Callable[[str], None], started: Callable[[subprocess.Popen], None] | None = None
) -> int:
    """Run @p argv from the repository root, handing each line of its output to @p output; return its exit status."""
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    if sys.platform == "win32":
        env.setdefault("MSYS_NO_PATHCONV", "1")
    proc = subprocess.Popen(  # noqa: S603  # a step's own command line, no shell
        argv,
        cwd=REPO,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=env,
        bufsize=1,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if started is not None:
        started(proc)
    for line in cast("IO[str]", proc.stdout):  # stdout=PIPE
        output(line.rstrip("\n"))
    return proc.wait()
