# SPDX-License-Identifier: Unlicense
"""Headset interface 1: the headset runtime's contract on the PC's side (overpressure.contract), since 1.8."""

import sys

from overpressure import contract as _same
from overpressure.contract import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
