# SPDX-License-Identifier: Unlicense
"""Headset interface 1: the compressions images use (overpressure.compress)."""

import sys

from overpressure import compress as _same
from overpressure.compress import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
