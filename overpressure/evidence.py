# SPDX-License-Identifier: Unlicense
"""Runs on a headset that keep their evidence: a results folder that says what was done and found.

An acceptance run (a test image flashed, its checks, the way back) must show afterwards what
happened even when it ended in the middle: every step is in result.json the moment it starts,
which is rewritten whole after every change, and every host program it ran left its command line
and output in a numbered pair of files beside it. A headset's runner derives from Runner and adds
its steps; the waits are bounded and the HTTP fetches go to the headset only.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import time
from http import HTTPStatus
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Callable, TypeVar
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

if TYPE_CHECKING:
    import os
    from collections.abc import Sequence
    from email.message import Message

T = TypeVar("T")
#: What one HTTP fetch may return.
HTTP_LIMIT = 32 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    """Follow no redirect: the headset's address is the only one a run talks to."""

    def redirect_request(  # noqa: PLR0913, PLR0917  # urllib's signature
        self,
        req: Request,  # noqa: ARG002  # urllib's signature
        fp: IO[bytes],  # noqa: ARG002  # urllib's signature
        code: int,  # noqa: ARG002  # urllib's signature
        msg: str,  # noqa: ARG002  # urllib's signature
        headers: Message,  # noqa: ARG002  # urllib's signature
        newurl: str,  # noqa: ARG002  # urllib's signature
    ) -> None:
        """Refuse every redirect."""
        return


def utc() -> str:
    """Return the current time in UTC, as the result records it."""
    return dt.datetime.now(dt.timezone.utc).isoformat()


def require(condition: object, message: str) -> None:
    """Raise ValueError with @p message unless @p condition holds."""
    if not condition:
        raise ValueError(message)


def http_get(url: str, timeout: float = 15, limit: int = HTTP_LIMIT) -> bytes:
    """Fetch @p url, bounded in time and size, without a proxy and without following a redirect."""
    # The USB address must bypass desktop/environment HTTP proxies.
    deadline = time.monotonic() + timeout
    with build_opener(ProxyHandler({}), NoRedirect()).open(url, timeout=timeout) as response:
        require(response.status == HTTPStatus.OK and response.url == url, "Unexpected HTTP response/redirect")
        data = bytearray()
        while True:
            require(time.monotonic() < deadline, "HTTP transfer exceeded its deadline")
            block = response.read1(65536)
            if not block:
                return bytes(data)
            data.extend(block)
            require(len(data) <= limit, "HTTP response exceeds test limit")


class Runner:
    """A run whose steps, commands and outputs are recorded in a new results folder."""

    def __init__(self, folder: Path) -> None:
        """Start a run with its results in @p folder, which must not exist yet: results are never overwritten."""
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=False)
        self.result: dict[str, Any] = {"checks": []}  #: a subclass adds its fields; step() records in checks
        self.count = 0

    def save(self) -> None:
        """Write the result file, replacing the last one whole."""
        temp = self.folder / "result.json.tmp"
        temp.write_text(json.dumps(self.result, indent=2) + "\n", encoding="utf-8")
        temp.replace(self.folder / "result.json")

    def command(self, argv: Sequence[str | os.PathLike[str]], timeout: float = 30) -> str:
        """Run a host program, its command line and output kept as evidence; return its output.

        A nonzero exit, a FAILED in the output or the timeout raises ValueError naming the log.
        """
        self.count += 1
        prefix = self.folder / f"command-{self.count:04d}"
        Path(str(prefix) + ".json").write_text(
            json.dumps({"argv": list(map(str, argv)), "timeout": timeout, "started": utc()}, indent=2), encoding="utf-8"
        )
        try:
            process = subprocess.run(  # noqa: S603  # adb, fastboot, ssh and the NCM helper by name, no shell
                list(map(str, argv)), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired as exc:
            Path(str(prefix) + ".log").write_bytes((exc.stdout or b"") + b"\nTIMEOUT\n")
            msg = f"Command timed out; see {prefix.name}.log"
            raise ValueError(msg) from exc
        output = process.stdout
        Path(str(prefix) + ".log").write_bytes(output)
        text = output.decode("utf-8", errors="replace").strip()
        require(
            process.returncode == 0 and not re.search(r"\bFAILED\b", text),
            f"Command failed ({process.returncode}); see {prefix.name}.log",
        )
        return text

    def step(self, name: str, operation: Callable[[], T]) -> T:
        """Run one step of the run, recorded in the result whether it passes or fails."""
        print(f">> {name}", flush=True)
        record = {"name": name, "started": utc(), "status": "running"}
        self.result["checks"].append(record)
        self.save()
        try:
            value = operation()
        except (Exception, KeyboardInterrupt) as exc:
            record.update(status="failed", error=str(exc) or "Interrupted")
            raise
        else:
            record["status"] = "passed"
            return value
        finally:
            record["finished"] = utc()
            self.save()

    def wait(self, name: str, seconds: float, operation: Callable[[float], T]) -> T:
        """Retry @p operation, given the time left for each attempt, until it succeeds or @p seconds pass."""
        deadline = time.monotonic() + seconds
        last = "no response"
        while time.monotonic() < deadline:
            try:
                return operation(max(0.1, min(10, deadline - time.monotonic())))
            except (ValueError, OSError) as exc:  # noqa: PERF203  # each attempt may fail until the deadline
                last = str(exc)
                time.sleep(min(2, max(0, deadline - time.monotonic())))
        msg = f"Timed out waiting for {name}: {last}"
        raise ValueError(msg)
