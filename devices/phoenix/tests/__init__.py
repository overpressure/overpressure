# SPDX-License-Identifier: Unlicense
"""Host tests of the PICO 4's tooling, sources and image recipes."""

# Tools fetched into this checkout (python -m overpressure tools) must not stand in for the tests'
# fakes: every test sees an empty tools cache.
import atexit as _atexit
import shutil as _shutil
import tempfile as _tempfile
from pathlib import Path as _Path

from overpressure import tools as _tools

_tools.CACHE = _Path(_tempfile.mkdtemp(prefix="overpressure-tools-"))
_tools.NCM_VENV = _tools.CACHE / _tools.NCM_VENV.name
_atexit.register(_shutil.rmtree, _tools.CACHE, ignore_errors=True)
