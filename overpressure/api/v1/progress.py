# SPDX-License-Identifier: Unlicense
"""Headset interface 1: progress lines (overpressure.progress)."""

import sys

from overpressure import progress as _same
from overpressure.progress import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
