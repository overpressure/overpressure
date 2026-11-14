# SPDX-License-Identifier: Unlicense
"""Headset interface 1: FAT images, read without mounting (overpressure.fat)."""

import sys

from overpressure import fat as _same
from overpressure.fat import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
