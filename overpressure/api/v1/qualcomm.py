# SPDX-License-Identifier: Unlicense
"""Headset interface 1: Qualcomm: SoC ID, EDL, partition maps, verified writes (overpressure.qualcomm)."""

import sys

from overpressure import qualcomm as _same
from overpressure.qualcomm import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
