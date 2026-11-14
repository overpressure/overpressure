# SPDX-License-Identifier: Unlicense
"""Headset interface 1: the pinned host tools (overpressure.tools)."""

import sys

from overpressure import tools as _same
from overpressure.tools import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
