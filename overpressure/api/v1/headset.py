# SPDX-License-Identifier: Unlicense
"""Headset interface 1: the headset, its steps, workflows and finished page (overpressure.headset)."""

import sys

from overpressure import headset as _same
from overpressure.headset import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
