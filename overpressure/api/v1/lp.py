# SPDX-License-Identifier: Unlicense
"""Headset interface 1: dynamic partitions: super's logical partitions, a new layout's metadata (overpressure.lp)."""

import sys

from overpressure import lp as _same
from overpressure.lp import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
