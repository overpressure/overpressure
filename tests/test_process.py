# SPDX-License-Identifier: Unlicense
"""The host tools' children read no stdin of this process's unless they are given one."""

from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from overpressure import android, fastboot, process


def finished(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess:
    """Stand in for subprocess.run: a child that wrote ok and ended."""
    return subprocess.CompletedProcess([], 0, b"ok", b"")


class ChildStdinTests(unittest.TestCase):
    """On Windows a child that inherits the installer's answer pipe, with a read pending, waits behind it for good.

    The window's engine reads its answers from a pipe on a thread; ssh, started in the engine's own process
    (the installer's waits), inherited it and never answered.
    """

    def test_run_gives_a_child_no_stdin_unless_asked(self):
        with mock.patch.object(subprocess, "run", side_effect=finished) as run:
            process.run(["tool"])
            self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
            process.run(["tool"], stdin=subprocess.PIPE)
            self.assertIs(run.call_args.kwargs["stdin"], subprocess.PIPE)

    def test_adb_and_fastboot_read_no_stdin(self):
        text = subprocess.CompletedProcess([], 0, "ok", "")
        with mock.patch.object(subprocess, "run", return_value=text) as run:
            android.run(["adb", "devices"])
            self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        with mock.patch.object(subprocess, "run", side_effect=finished) as run:
            fastboot.run(["fastboot", "devices"], 10)
            self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
