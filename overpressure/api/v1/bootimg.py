# SPDX-License-Identifier: Unlicense
"""Headset interface 1: Android boot images (overpressure.bootimg)."""

import sys

from overpressure import bootimg as _same
from overpressure.bootimg import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
