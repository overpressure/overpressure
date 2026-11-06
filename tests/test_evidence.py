# SPDX-License-Identifier: Unlicense
"""overpressure.evidence: a run's results folder, its recorded commands and steps, its bounded waits and fetches."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from overpressure import evidence


class FakeClock:
    """Virtual time: a wait's sleeps pass at once."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class RunnerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name) / "run"
        self.runner = evidence.Runner(self.folder)

    def result(self) -> dict:
        return json.loads((self.folder / "result.json").read_text(encoding="utf-8"))

    def test_results_are_never_overwritten(self):
        with self.assertRaises(FileExistsError):
            evidence.Runner(self.folder)

    def test_commands_keep_their_command_line_and_output(self):
        with patch.object(evidence.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b" fine \n")):
            self.assertEqual(self.runner.command(["tool", Path("arg")], timeout=5), "fine")
        recorded = json.loads((self.folder / "command-0001.json").read_text(encoding="utf-8"))
        self.assertEqual((recorded["argv"], recorded["timeout"]), (["tool", "arg"], 5))
        self.assertEqual((self.folder / "command-0001.log").read_bytes(), b" fine \n")

    def test_subprocess_failure_and_timeout_are_logged(self):
        for output in (
            subprocess.CompletedProcess([], 1, b"device missing"),
            subprocess.CompletedProcess([], 0, b"FAILED"),
        ):
            with (
                patch.object(evidence.subprocess, "run", return_value=output),
                self.assertRaisesRegex(ValueError, r"Command failed \(\d\); see command-000\d.log"),
            ):
                self.runner.command(["fake"])
        self.assertIn("device missing", (self.folder / "command-0001.log").read_text())
        with (
            patch.object(
                evidence.subprocess, "run", side_effect=subprocess.TimeoutExpired("fake", 1, output=b"partial")
            ),
            self.assertRaisesRegex(ValueError, "timed out; see command-0003.log"),
        ):
            self.runner.command(["fake"], timeout=1)
        self.assertEqual((self.folder / "command-0003.log").read_bytes(), b"partial\nTIMEOUT\n")

    def test_steps_are_recorded_whether_they_pass_or_fail(self):
        def broken() -> None:
            msg = "no answer"
            raise ValueError(msg)

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.runner.step("first", lambda: 42), 42)
            self.assertEqual(self.result()["checks"][0]["status"], "passed")
            with self.assertRaises(ValueError):
                self.runner.step("second", broken)
        self.assertEqual(out.getvalue(), ">> first\n>> second\n")
        second = self.result()["checks"][1]
        self.assertEqual((second["name"], second["status"], second["error"]), ("second", "failed", "no answer"))
        self.assertIn("finished", second)

    def test_waits_are_bounded_and_say_what_failed_last(self):
        clock, attempts = FakeClock(), []

        def ready(timeout: float) -> str:
            attempts.append(timeout)
            if len(attempts) < 3:
                msg = f"attempt {len(attempts)}"
                raise ValueError(msg)
            return "up"

        with patch.object(evidence, "time", clock):
            self.assertEqual(self.runner.wait("thing", 60, ready), "up")
            self.assertEqual(attempts[0], 10)  # at most ten seconds per attempt
            with self.assertRaisesRegex(ValueError, "Timed out waiting for thing: attempt 1"):
                attempts.clear()
                self.runner.wait("thing", 1, ready)


class Handler(BaseHTTPRequestHandler):
    bodies: ClassVar[dict[str, tuple[int, bytes]]] = {
        "/ok": (200, b"stage 1"),
        "/moved": (302, b""),
        "/big": (200, b"x" * 2048),
    }

    def do_GET(self) -> None:
        status, body = self.bodies[self.path]
        self.send_response(status)
        if status == 302:
            self.send_header("Location", "/ok")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002  # http.server's signature
        """Keep the test's output quiet."""


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def test_bounded_fetch_without_redirects(self):
        self.assertEqual(evidence.http_get(self.base + "/ok", 5), b"stage 1")
        with self.assertRaises(OSError):  # urllib's HTTPError: the redirect is not followed
            evidence.http_get(self.base + "/moved", 5)
        with self.assertRaisesRegex(ValueError, "exceeds"):
            evidence.http_get(self.base + "/big", 5, limit=1024)


if __name__ == "__main__":
    unittest.main()
