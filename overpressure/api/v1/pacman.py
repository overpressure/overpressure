# SPDX-License-Identifier: Unlicense
"""Headset interface 1: pacman's repositories on any host (overpressure.pacman), since 1.7."""

import sys

from overpressure import pacman as _same
from overpressure.pacman import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
