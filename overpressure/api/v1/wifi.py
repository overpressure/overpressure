# SPDX-License-Identifier: Unlicense
"""Headset interface 1: the owner's Wi-Fi for a headset's installed system (overpressure.wifi), since 1.6."""

import sys

from overpressure import wifi as _same
from overpressure.wifi import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
