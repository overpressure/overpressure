# SPDX-License-Identifier: Unlicense
"""overpressure refuses administrator rights, except inside the build container and on GitHub Actions."""

from __future__ import annotations

import contextlib
import ctypes
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from overpressure import __main__ as cli
from overpressure import elevated


class ElevatedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.marker = Path(self.tmp.name) / ".dockerenv"
        for patcher in (
            mock.patch.object(elevated, "CONTAINER_MARKERS", (self.marker,)),
            mock.patch.dict(os.environ),  # these tests run on GitHub Actions too
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        os.environ.pop(elevated.GITHUB_ACTIONS, None)

    def posix(self, euid: int) -> bool:
        with (
            mock.patch.object(elevated.sys, "platform", "linux"),
            mock.patch.object(elevated.os, "geteuid", return_value=euid, create=True),
        ):
            return elevated.elevated()

    def test_root_is_refused(self) -> None:
        self.assertTrue(self.posix(0))

    def test_an_ordinary_user_runs(self) -> None:
        self.assertFalse(self.posix(1000))

    def test_root_in_the_build_container_runs(self) -> None:
        self.marker.touch()
        self.assertFalse(self.posix(0))

    def windows(self, admin: int) -> bool:
        windll = SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=lambda: admin))
        with (
            mock.patch.object(elevated.sys, "platform", "win32"),
            mock.patch.object(ctypes, "windll", windll, create=True),
        ):
            return elevated.elevated()

    def test_run_as_administrator_is_refused(self) -> None:
        self.assertTrue(self.windows(1))
        self.assertFalse(self.windows(0))

    def test_github_actions_runs_as_administrator(self) -> None:
        os.environ[elevated.GITHUB_ACTIONS] = "true"  # its Windows runners always have the rights
        self.assertFalse(self.windows(1))
        self.assertFalse(self.posix(0))

    def test_the_message_says_how_to_start_instead(self) -> None:
        self.assertIn("Install.cmd", elevated.message("win32"))
        self.assertIn("without sudo", elevated.message("linux"))
        for platform in ("win32", "linux", "darwin"):
            self.assertIn("administrator rights", elevated.message(platform))


class MainRefusesTests(unittest.TestCase):
    def run_main(self, argv: list[str]) -> tuple[int, str, mock.Mock]:
        stderr = io.StringIO()
        with (
            mock.patch.object(elevated, "elevated", return_value=True),
            mock.patch.object(elevated, "show") as show,
            mock.patch.object(cli, "discover") as discover,
            contextlib.redirect_stderr(stderr),
        ):
            status = cli.main(argv)
        discover.assert_not_called()  # refused before anything else runs
        return status, stderr.getvalue(), show

    def test_a_command_is_refused_with_the_message(self) -> None:
        status, err, show = self.run_main(["--accept-disclaimer", "devices"])
        self.assertEqual(status, elevated.REFUSED)
        self.assertIn("error: overpressure does not run with administrator rights", err)
        show.assert_not_called()

    def test_the_window_shows_it_in_a_dialog_too(self) -> None:
        _, _, show = self.run_main(["gui"])
        show.assert_called_once_with(elevated.message())


if __name__ == "__main__":
    unittest.main()
