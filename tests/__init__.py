# SPDX-License-Identifier: Unlicense
"""Host tests of the overpressure package (python -m unittest from the repository root)."""

# Tools fetched into this checkout (python -m overpressure tools) must not stand in for the tests'
# fakes: every test sees an empty tools cache.
import atexit as _atexit
import os as _os
import shutil as _shutil
import sys as _sys
import tempfile as _tempfile
from pathlib import Path as _Path

from overpressure import tools as _tools

_tools.CACHE = _Path(_tempfile.mkdtemp(prefix="overpressure-tools-"))
_atexit.register(_shutil.rmtree, _tools.CACHE, ignore_errors=True)

# The tests read no terminal: stdin is the null device, for this process and every child a test starts. On
# Windows a child that inherits a pipe another thread has a read pending on waits for good (overpressure#41).
_null = _os.open(_os.devnull, _os.O_RDONLY)
_os.dup2(_null, 0)
_os.close(_null)
_sys.stdin = open(0, encoding="utf-8", closefd=False)  # noqa: SIM115  # the null device, for the whole run
