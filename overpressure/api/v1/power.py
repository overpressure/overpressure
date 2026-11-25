# SPDX-License-Identifier: Unlicense
"""Headset interface 1: this computer's power, and the headset's battery before writes (overpressure.power), 1.6."""

import sys

from overpressure import power as _same
from overpressure.power import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
